"""
plan_critique.py
----------------
Model review of a generated WeekPlan, in two passes with different jobs.

`constraint_check.py` verifies everything decidable in code. This module covers
what's left, and splits it by how trustworthy the answer is:

  **Defect review** (`review_meal`) — one call per meal, looking only for
  internal contradictions: instructions that don't produce the dish, quantities
  that don't fit the servings, a kid adaptation the method makes impossible, a
  leftover that wouldn't survive. These are claims about a recipe's consistency
  with itself, so they are checkable and largely objective. They feed the repair
  pass in `meal_planner.repair_meal`.

  **Judgment review** (`review_week`) — one call for the whole week, looking at
  what only makes sense across meals: variety, plate geometry, prep realism.
  Real feedback, but a matter of taste. These stay advisory forever.

Reviewing one recipe at a time costs more calls than reviewing the week at once,
and finds more: a per-meal call spends its whole attention on one recipe's
internal logic instead of a tenth of it. Since coherence is the reason this
module exists, that's the trade it makes.

Both passes fail open. Any error returns an empty list — a review that fails
must never cost the user a plan that generated fine.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import anthropic

from constraint_check import Violation
from schemas import WeekPlan
from system_prompt import HEALTH_RULES_SUMMARY, PLATE_GEOMETRY_SUMMARY

# Opus 5.5, by choice (2026-10-07), with everything on one model. The earlier
# measurement chose Sonnet 5 over Opus 5: on the same stored week, three Sonnet
# runs found 10, 14 and 13 defects against Opus 5's 10, at $0.195 against ~$0.42.
# That spread was run-to-run variance wider than the gap between models, so it
# showed "Sonnet is not worse for half the price", not "Sonnet is better".
# Opus 5.5 costs less than Opus 5 did ($4/$20 per MTok), which narrows the gap
# to 2x. The app is now the backup path, and the primary path (a Claude Code
# session) has no per-call cost. To go back, set this to "claude-sonnet-5-5".
CRITIQUE_MODEL = "claude-opus-5-5"

# How many per-meal reviews run at once. A week is 10-14 meals; these are small
# independent calls, so they parallelize cleanly.
MAX_PARALLEL_REVIEWS = 6

# Defects are contradictions inside one recipe. They are objective enough to act
# on, and they drive the repair pass.
DEFECT_CATEGORIES = {
    "recipe_coherence",     # steps don't produce the dish, contradict each other, or skip a step
    "quantity_mismatch",    # amounts don't fit the stated servings
    "kid_adaptation",       # the method makes the stated adaptation impossible
    "leftover_logic",       # the leftover wouldn't survive or reheat as described
}

# Judgment findings are matters of taste. They are surfaced, never acted on.
JUDGMENT_CATEGORIES = {
    "variety",              # the week is monotonous in method, texture, or flavor
    "plate_geometry",       # proportions are off — protein-heavy, vegetable-light
    "prep_realism",         # cook_time or Sunday prep doesn't match the work described
}

CATEGORIES = DEFECT_CATEGORIES | JUDGMENT_CATEGORIES


def _findings_schema(categories: set[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "meal_id": {
                            "type": "string",
                            "description": "The id of the meal ('d1', 'l3'), or '' for a "
                                           "finding about the week as a whole.",
                        },
                        "meal_name": {"type": "string"},
                        "category": {"type": "string", "enum": sorted(categories)},
                        "message": {
                            "type": "string",
                            "description": "One sentence completing 'This meal ___', "
                                           "naming the specific contradiction.",
                        },
                    },
                    "required": ["meal_id", "meal_name", "category", "message"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["findings"],
        "additionalProperties": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT REVIEW — one meal at a time
# ─────────────────────────────────────────────────────────────────────────────

_DEFECT_SYSTEM = """You are proofreading a single recipe from a weekly meal plan, \
the way a careful cook reads a recipe before shopping for it. You are looking for \
one thing: places where the recipe contradicts itself or could not be executed as \
written.

Report only these:
- recipe_coherence: the instructions don't produce the dish described, contradict \
each other, leave out a step the dish requires, or use an ingredient at a point the \
method makes impossible.
- quantity_mismatch: the ingredient amounts don't plausibly feed the stated servings, \
or don't leave what a stated leftover portion needs.
- kid_adaptation: the method makes the stated adaptation impossible — for example \
"serve the kids' portion without the spices" when the spices go into the pot with \
everything else at the start.
- leftover_logic: the dish wouldn't survive to lunch, or wouldn't reheat as described.

Do NOT report, they are verified in code before you see this recipe: banned \
ingredients (mushrooms, oranges), fish in a lunch, sodium over the ceiling, \
ingredients listed but never used in the instructions.

Do NOT report matters of taste — whether the dish sounds appealing, whether the \
seasoning is interesting, whether it fits the week. Another pass handles those.

The bar is: a cook following this recipe exactly would hit a problem. If the recipe \
is executable as written, return an empty findings list. That is the common and \
correct answer — most recipes are fine, and inventing a finding to seem thorough \
makes this whole pass useless.

Write each `message` as a phrase completing "This meal ___", naming the specific \
contradiction and both sides of it. Not "has unclear instructions" but "says to \
reserve half the sauce but never uses the reserved half"."""

_JUDGMENT_SYSTEM = f"""You are reviewing a full week of meals for a household \
following a Mediterranean diet. Individual recipes have already been proofread for \
internal errors — that is not your job. Yours is what only shows up across the week.

The household's rules:
{HEALTH_RULES_SUMMARY}

{PLATE_GEOMETRY_SUMMARY}

Report only:
- variety: the week is monotone — everything roasted, everything in a bowl, the same \
vegetable base or flavor profile night after night under different names.
- plate_geometry: a dinner is protein-heavy and vegetable-light against the rule above.
- prep_realism: cook_time_minutes doesn't match the work described, or a Sunday prep \
task wouldn't hold for the days it claims to.

Do NOT report, all verified in code: banned ingredients, fish in lunches, sodium, \
repeats from recent weeks, fish frequency, special ingredients used once, or the \
leftover links between dinners and lunches.

Be conservative and return at most 4 findings. If the week is varied and sensible, \
return an empty list. Write each `message` as a phrase completing "This week ___" or \
"This meal ___"."""


def _meal_for_review(meal: dict, meal_type: str, linked_dinner: Optional[dict]) -> str:
    payload = {"meal_type": meal_type, "meal": meal}
    if linked_dinner is not None:
        payload["dinner_this_is_a_leftover_of"] = {
            "name": linked_dinner.get("name"),
            "servings": linked_dinner.get("servings"),
            "ingredients": linked_dinner.get("ingredients"),
            "lunch_scaling_instructions": linked_dinner.get("lunch_scaling_instructions"),
        }
    return json.dumps(payload, indent=1)


def _call(client, model, system, user, categories, effort) -> list:
    """One review call. Returns raw findings, or [] on any failure."""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema",
                           "schema": _findings_schema(categories)},
            },
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text).get("findings") or []
    except (anthropic.APIError, json.JSONDecodeError, StopIteration, KeyError, TypeError):
        return []


def review_meal(
    meal: dict,
    meal_type: str,
    *,
    linked_dinner: Optional[dict] = None,
    client: Optional[anthropic.Anthropic] = None,
    model: str = CRITIQUE_MODEL,
) -> list[Violation]:
    """
    Proofread one recipe for internal contradictions.

    `linked_dinner` is the dinner a leftover lunch came from — without it,
    leftover_logic and quantity findings on that lunch have nothing to check
    against. Returns defect-category findings only.
    """
    client = client or anthropic.Anthropic()
    raw = _call(
        client, model, _DEFECT_SYSTEM,
        "Proofread this recipe:\n\n" + _meal_for_review(meal, meal_type, linked_dinner),
        DEFECT_CATEGORIES,
        # Defect hunting is the pass whose findings get acted on, so it gets the
        # thoroughness. Judgment review runs lower.
        "high",
    )
    return _to_violations(raw, {str(meal.get("id", ""))}, DEFECT_CATEGORIES)


def review_week(
    plan: WeekPlan,
    *,
    client: Optional[anthropic.Anthropic] = None,
    model: str = CRITIQUE_MODEL,
) -> list[Violation]:
    """Review the week for the judgment calls that only appear across meals."""
    if not (plan.get("dinners") or plan.get("lunches")):
        return []
    client = client or anthropic.Anthropic()
    raw = _call(
        client, model, _JUDGMENT_SYSTEM,
        "Review this week plan:\n\n" + _plan_for_review(plan),
        JUDGMENT_CATEGORIES,
        "medium",
    )
    return _to_violations(raw, _meal_ids(plan), JUDGMENT_CATEGORIES, cap=4)


def critique_plan(
    plan: WeekPlan,
    *,
    client: Optional[anthropic.Anthropic] = None,
    model: str = CRITIQUE_MODEL,
    include_judgment: bool = True,
) -> tuple[list[Violation], list[Violation]]:
    """
    Run both review passes over a plan.

    Per-meal defect reviews run in parallel; the week-level judgment review runs
    alongside them. Returns ``(defects, judgments)`` — defects are candidates for
    repair, judgments are advisory.
    """
    dinners = plan.get("dinners") or []
    lunches = plan.get("lunches") or []
    if not (dinners or lunches):
        return [], []

    client = client or anthropic.Anthropic()
    by_id = {str(d.get("id", "")): d for d in dinners}

    jobs = [(m, "dinner", None) for m in dinners]
    for lunch in lunches:
        linked = by_id.get(str(lunch.get("leftover_from_dinner_id") or ""))
        jobs.append((lunch, "lunch", linked))

    defects: list[Violation] = []
    judgments: list[Violation] = []

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REVIEWS) as pool:
        week_future = (
            pool.submit(review_week, plan, client=client, model=model)
            if include_judgment else None
        )
        meal_futures = [
            pool.submit(review_meal, meal, meal_type,
                        linked_dinner=linked, client=client, model=model)
            for meal, meal_type, linked in jobs
        ]
        for future in meal_futures:
            defects += future.result()
        if week_future is not None:
            judgments = week_future.result()

    return defects, judgments


# ─────────────────────────────────────────────────────────────────────────────
# SHARED
# ─────────────────────────────────────────────────────────────────────────────

def _meal_ids(plan: WeekPlan) -> set[str]:
    return {str(m.get("id", "")) for m in
            [*(plan.get("dinners") or []), *(plan.get("lunches") or [])]}


def _plan_for_review(plan: WeekPlan) -> str:
    """`_soft_violations` is stripped — showing the reviewer what the checker
    already found invites it to agree instead of looking for what code cannot see."""
    return json.dumps({k: v for k, v in plan.items() if not k.startswith("_")}, indent=1)


def _to_violations(findings: list, known_ids: set[str], allowed: set[str],
                   cap: int = 6) -> list[Violation]:
    """Convert reviewer output into Violations, discarding anything malformed.

    The reviewer is not trusted to stay inside its schema across model versions,
    so every field is re-validated and unknown categories are dropped.
    """
    out: list[Violation] = []
    for f in (findings or [])[:cap]:
        if not isinstance(f, dict):
            continue
        category = str(f.get("category", ""))
        message = str(f.get("message", "")).strip()
        if category not in allowed or not message:
            continue
        meal_id = str(f.get("meal_id", ""))
        out.append({
            "code": f"critique_{category}",
            "severity": "soft",
            "meal_id": meal_id if meal_id in known_ids else "",
            "meal_name": str(f.get("meal_name", "")),
            "message": message,
        })
    return out

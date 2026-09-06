"""
plan_critique.py
----------------
An advisory second-opinion pass over a generated WeekPlan.

`constraint_check.py` verifies everything that can be decided in code: a banned
ingredient is present or it isn't, sodium is over the ceiling or it isn't. This
module covers what's left — whether the recipe as written actually works, whether
the week has variety, whether a kid adaptation is plausible. Those are judgment
calls, so they go to the model.

Two rules govern this module, and both exist because an LLM judge is less
reliable than the checker it supplements:

  1. **It never blocks.** Findings come back as soft violations and land in the
     UI. A critique cannot cause a regeneration or reject a plan.
  2. **It fails open.** An API error, a malformed response, a timeout — all
     return an empty list. A plan that generated fine is never lost because the
     second opinion failed.
"""

from __future__ import annotations

import json
from typing import Optional

import anthropic

from constraint_check import Violation
from schemas import WeekPlan
from system_prompt import HEALTH_RULES_SUMMARY, PLATE_GEOMETRY_SUMMARY

# The judge runs on the current flagship. It is a smaller call than generation —
# the plan in, a short findings list out — and the whole point is a second
# opinion good enough to be worth reading. Drop to "claude-sonnet-5" if the
# per-generation cost matters more than the quality of the critique.
CRITIQUE_MODEL = "claude-opus-5"

# Categories the judge may return. Anything outside this list is discarded, so a
# drifting model cannot invent new finding types that the UI does not expect.
CATEGORIES = {
    "recipe_coherence",     # steps don't produce the dish, or skip a listed ingredient
    "quantity_mismatch",    # amounts don't fit the stated servings
    "prep_realism",         # cook_time or Sunday prep doesn't match the work described
    "variety",              # the week is monotonous in method, texture, or flavor
    "kid_adaptation",       # the adaptation wouldn't actually work for a 2- and 5-year-old
    "plate_geometry",       # proportions are off — protein-heavy, vegetable-light
    "leftover_logic",       # the leftover wouldn't survive or reheat as described
}

_SYSTEM = f"""You are reviewing a generated weekly meal plan for a household that \
follows a Mediterranean diet for three simultaneous health goals. You are the second \
reader, not the author. Your job is to find the handful of things that are genuinely \
wrong with this plan as written.

The household's rules:
{HEALTH_RULES_SUMMARY}

{PLATE_GEOMETRY_SUMMARY}

IMPORTANT — the following are already verified in code before you see the plan. Do \
not report them; they are not your job and repeating them is noise:
- banned ingredients (mushrooms, oranges/orange juice)
- fish appearing in a lunch
- sodium above the per-serving ceiling
- meals repeated from recent weeks or duplicated within the week
- special ingredients used in only one meal
- leftover lunches pointing at a dinner that isn't in the plan
- ingredients listed but never mentioned in the instructions

What IS your job — things code cannot check:
- recipe_coherence: the instructions don't actually produce the dish, contradict \
themselves, or leave out a step the dish requires.
- quantity_mismatch: the ingredient amounts don't plausibly feed the stated servings.
- prep_realism: cook_time_minutes doesn't match the work described, or a Sunday prep \
task doesn't hold for the days it claims to.
- variety: the week is monotone — everything roasted, everything in a bowl, the same \
flavor base (lemon-garlic-oregano) five nights running.
- kid_adaptation: the adaptation wouldn't work for a 5-year-old and a 2-year-old.
- plate_geometry: a dinner is protein-heavy and vegetable-light against the rule above.
- leftover_logic: the leftover wouldn't survive to lunch, or wouldn't reheat as described.

Be conservative. A finding must be something a careful cook would actually change. \
If the plan is sound, return an empty findings list — that is a correct and expected \
answer, and a better one than a list of quibbles. Never report more than 6 findings; \
if you have more, return only the ones that matter most.

Write each `message` as a phrase completing the sentence "This meal ___" — for \
example, "lists 2 cups of rice for 4 adults, roughly half a normal portion". No \
preamble, no praise, no restating the rule."""

_SCHEMA = {
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
                    "category": {
                        "type": "string",
                        "enum": sorted(CATEGORIES),
                    },
                    "message": {
                        "type": "string",
                        "description": "One sentence completing 'This meal ___'.",
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


def _plan_for_review(plan: WeekPlan) -> str:
    """The plan as the judge sees it.

    `_soft_violations` is stripped — showing the judge what the checker already
    found invites it to agree with the checker instead of looking for what the
    checker cannot see.
    """
    return json.dumps({k: v for k, v in plan.items() if not k.startswith("_")},
                      indent=1)


def critique_week_plan(
    plan: WeekPlan,
    *,
    client: Optional[anthropic.Anthropic] = None,
    model: str = CRITIQUE_MODEL,
) -> list[Violation]:
    """
    Ask Claude to review a generated plan and return the findings as soft violations.

    Always returns a list. Any failure — API error, malformed response, an
    unparseable finding — yields an empty list rather than raising, because a
    failed second opinion must not cost the user a plan that generated fine.
    """
    if not (plan.get("dinners") or plan.get("lunches")):
        return []

    client = client or anthropic.Anthropic()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": "Review this week plan and report what is genuinely wrong "
                           "with it:\n\n" + _plan_for_review(plan),
            }],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": _SCHEMA},
            },
        )
        text = next(b.text for b in response.content if b.type == "text")
        findings = json.loads(text).get("findings") or []
    except (anthropic.APIError, json.JSONDecodeError, StopIteration, KeyError, TypeError):
        return []

    return _to_violations(findings, plan)


def _to_violations(findings: list, plan: WeekPlan) -> list[Violation]:
    """Convert the judge's output into Violations, dropping anything malformed.

    The judge is not trusted to stay inside its schema across model versions, so
    every field is re-validated here and unknown categories are discarded.
    """
    known_ids = {str(m.get("id", "")) for m in
                 [*(plan.get("dinners") or []), *(plan.get("lunches") or [])]}
    out: list[Violation] = []

    for f in findings[:6]:
        if not isinstance(f, dict):
            continue
        category = str(f.get("category", ""))
        message = str(f.get("message", "")).strip()
        if category not in CATEGORIES or not message:
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

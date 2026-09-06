"""
meal_planner.py
---------------
All Claude API calls for meal plan generation and manipulation.

Functions:
  generate_week_plan(week_notes)
      Main plan generator — builds full WeekPlan from all context sources.

  swap_meal(week_plan, meal_id, meal_type, reason)
      Replace a single dinner or lunch. Auto-swaps a paired lunch if the
      swapped dinner previously generated one.

  generate_kid_notes(week_plan)
      Babysitter-friendly plain-English guide for all dinners this week.
      Returns formatted text with a blank Notes: line per meal.

All functions raise MealPlanError on API failure or unparseable response.
"""

import json
import os
from datetime import date
from typing import Optional

import anthropic

import constraint_check
import data_store
import plan_critique
from schemas import DinnerMeal, LunchMeal, WeekPlan
from system_prompt import (
    DEFAULT_CONSTRAINTS,
    HEALTH_RULES_SUMMARY,
    PLATE_GEOMETRY_SUMMARY,
    build_system_prompt,
)

MODEL = "claude-opus-4-8"


# Generation attempts before giving up. Each attempt is a full plan generation,
# so this is deliberately small; the extra attempt over the original 2 exists
# because constraint checking now rejects plans that previously slipped through.
_MAX_GENERATION_ATTEMPTS = 3


class MealPlanError(Exception):
    """Raised when plan generation or manipulation fails."""


def _client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MealPlanError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Export it before running the app."
        )
    return anthropic.Anthropic(api_key=api_key)


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences if Claude includes them despite instructions."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Generate week plan
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_USER_MESSAGE = (
    "Please generate this week's meal plan. "
    "Apply all health constraints, household rules, ingredient efficiency rules, "
    "history context, and seasonal guidance from the system prompt. "
    "Return only the JSON object — no preamble, no explanation, no markdown."
)


def build_generation_prompts(
    week_notes: str | None = None,
    cuisine_notes: str | None = None,
    use_up_ingredients: str | None = None,
    days: int = 5,
) -> tuple[str, str]:
    """
    Build the system prompt and user message for generate_week_plan without
    calling the API. Used by the prompt-preview dialog in app.py.

    Returns
    -------
    (system_prompt, user_message, require_kid_adaptation)
    """
    prefs = data_store.load_preferences()
    anchor_recipes = (
        data_store.select_anchor_recipes(days=days, use_up_ingredients=use_up_ingredients)
        if prefs.get("use_anchor_recipes", True)
        else None
    )
    system_prompt = build_system_prompt(
        history=data_store.history_for_prompt(),
        favorites=data_store.load_favorites(),
        constraints=data_store.active_constraints_for_prompt(),
        lunch_adult_count=prefs["lunch_adult_count"],
        children=prefs.get("children"),
        budget=prefs.get("budget"),
        current_date=date.today().isoformat(),
        week_notes=week_notes,
        cuisine_notes=cuisine_notes,
        portion_scale=prefs.get("portion_scale", 1.0),
        use_up_ingredients=use_up_ingredients,
        days=days,
        anchor_recipes=anchor_recipes,
        include_snacks=prefs.get("include_snacks", True),
    )
    require_kid = bool(prefs.get("children"))
    return system_prompt, _DEFAULT_USER_MESSAGE, require_kid


def generate_week_plan_from_prompts(
    system_prompt: str,
    user_message: str,
    expected_days: int = 5,
    require_kid_adaptation: bool = True,
    user_constraints: list[str] | None = None,
    target_sodium_mg: int | None = None,
    history: list | None = None,
) -> WeekPlan:
    """
    Call Claude with pre-built prompts and return a validated WeekPlan.
    Used after the prompt-preview dialog allows editing.

    Structural validation (`_validate_plan`) and constraint checking
    (`constraint_check.check_week_plan`) both run on the response. A hard
    constraint violation — a banned ingredient, fish in a lunch, sodium over
    the ceiling — is rejected the same way a malformed response is, and the
    specific reason is fed back into the next attempt. Soft findings are
    attached to the plan under `_soft_violations` for the UI to surface; they
    never trigger a regeneration.

    Raises
    ------
    MealPlanError on API failure, or if the response is still invalid after
    the automatic retries (each retry feeds the failure reason back to Claude).
    """
    client = _client()
    message = user_message
    last_error: MealPlanError | None = None
    if target_sodium_mg is None:
        target_sodium_mg = data_store.load_preferences().get(
            "target_sodium_mg", constraint_check.SODIUM_TARGET_DEFAULT_MG
        )

    for attempt in range(_MAX_GENERATION_ATTEMPTS):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system_prompt,
                messages=[{"role": "user", "content": message}],
            )
        except anthropic.APIError as e:
            # Infrastructure failure — retrying with a modified prompt won't help.
            raise MealPlanError(f"Claude API error: {e}") from e

        raw = _strip_fences(response.content[0].text)

        try:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise MealPlanError(
                    f"Claude returned invalid JSON: {e}\n\nFirst 500 chars:\n{raw[:500]}"
                ) from e

            if "week_plan" not in data:
                raise MealPlanError(
                    f"Response missing 'week_plan' key. Keys found: {list(data.keys())}"
                )

            plan: WeekPlan = data["week_plan"]
            _validate_plan(plan, expected_days, require_kid_adaptation=require_kid_adaptation)

            violations = constraint_check.check_week_plan(
                plan,
                user_constraints=user_constraints,
                target_sodium_mg=target_sodium_mg,
                history=history,
            )
            hard = constraint_check.hard_violations(violations)
            if hard:
                raise MealPlanError(
                    "The plan breaks food constraints that are absolute:\n"
                    + constraint_check.format_violations(hard)
                )

            soft = [v for v in violations if v["severity"] == "soft"]

            # Model review. Defects (contradictions inside one recipe) are
            # repaired in place; judgments (variety, plate geometry) are advisory.
            # Both passes fail open, so a review failure costs notes, not the plan.
            if data_store.load_preferences().get("run_plan_critique", True):
                defects, judgments = plan_critique.critique_plan(plan)
                plan, unresolved = repair_plan_defects(
                    plan, defects,
                    user_constraints=user_constraints,
                    target_sodium_mg=target_sodium_mg,
                    history=history,
                )
                soft += unresolved + judgments

            plan["_soft_violations"] = soft
            return plan
        except MealPlanError as e:
            last_error = e
            message = (
                f"{user_message}\n\n"
                f"IMPORTANT: Your previous attempt was rejected for this reason:\n"
                f"{e}\n\n"
                f"Generate the plan again, fixing that problem. "
                f"Return only the JSON object."
            )

    raise last_error


def generate_week_plan(week_notes: str | None = None, cuisine_notes: str | None = None, use_up_ingredients: str | None = None, days: int = 5) -> WeekPlan:
    """
    Generate a full weekly meal plan using Claude.

    Loads all context (history, favorites, constraints, preferences) from
    data_store, builds the system prompt, calls Claude, and returns a WeekPlan.

    Parameters
    ----------
    week_notes : str or None
        Free-text notes for this week (e.g. 'busy week, keep it fast').
    cuisine_notes : str or None
        Cuisine preferences for this week.

    Returns
    -------
    WeekPlan

    Raises
    ------
    MealPlanError on API or parse failure.
    """
    system_prompt, user_message, require_kid = build_generation_prompts(week_notes, cuisine_notes, use_up_ingredients, days)
    return generate_week_plan_from_prompts(
        system_prompt,
        user_message,
        expected_days=days,
        require_kid_adaptation=require_kid,
        user_constraints=data_store.active_constraints_for_prompt(),
        target_sodium_mg=data_store.load_preferences().get(
            "target_sodium_mg", constraint_check.SODIUM_TARGET_DEFAULT_MG
        ),
        history=data_store.load_history()[:6],
    )


def _validate_plan(plan: WeekPlan, days: int = 5, require_kid_adaptation: bool = True) -> None:
    if not plan.get("dinners"):
        raise MealPlanError("Plan has no dinners.")
    if not plan.get("lunches"):
        raise MealPlanError("Plan has no lunches.")
    if len(plan["dinners"]) != days:
        raise MealPlanError(f"Expected {days} dinners, got {len(plan['dinners'])}.")
    if len(plan["lunches"]) != days:
        raise MealPlanError(f"Expected {days} lunches, got {len(plan['lunches'])}.")
    if require_kid_adaptation:
        for dinner in plan["dinners"]:
            if not dinner.get("kid_adaptation"):
                raise MealPlanError(
                    f"Dinner '{dinner.get('name')}' is missing kid_adaptation."
                )
    # Meal-composition mix: the prompt asks every dinner to be labeled and the
    # week to include vegetable-forward/mezze nights. Enforce it here so the
    # rule can't be silently ignored.
    non_classic_min = 1 if days < 5 else 2
    labeled = [d.get("composition") for d in plan["dinners"]]
    if all(labeled):
        non_classic = sum(1 for c in labeled if c in ("vegetable_forward", "mezze"))
        if non_classic < non_classic_min:
            raise MealPlanError(
                f"Week has only {non_classic} vegetable-forward/mezze dinner(s); "
                f"at least {non_classic_min} required. Regenerate."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Repair a meal
#
# The response to "this one recipe contradicts itself" is to fix that recipe,
# not to regenerate the week — a full regeneration discards four good dinners to
# fix the fifth, and rolls the dice again on everything it replaces.
#
# Three rules keep the repair from doing harm:
#   1. One attempt per meal, ever. A stubborn finding must not become a loop.
#   2. The repaired plan is re-checked by constraint_check; a repair that
#      introduces a hard violation is discarded and the original kept.
#   3. The repaired meal is re-proofread. If the defect survives, the repair is
#      still kept when it is clean, but the finding stays visible to the user.
# ─────────────────────────────────────────────────────────────────────────────

_REPAIR_SYSTEM = f"""You are fixing one recipe in a weekly meal plan. A reviewer found a specific problem with it. Your job is to return the same dish with that problem fixed — not a different dish.

Rules:
1. Keep the dish. Same name, same core ingredients, same method, same equipment, same slot in the week. You are correcting an error, not swapping the meal.
2. Fix the problem at its cause. If the kid adaptation is impossible because the spices go in at the start, restructure the method so a portion can be pulled early — do not just reword the adaptation to describe what the method already does.
3. Keep the `id` exactly as given.
4. Everything you change must stay consistent with the rest of the recipe. If you change a quantity, update the servings, the nutrition estimate, and any scaling note that depends on it.
5. Satisfy all health constraints:
{HEALTH_RULES_SUMMARY}
6. Follow {PLATE_GEOMETRY_SUMMARY}
7. Obey every food constraint listed in the message — these are hard rules.

Return ONLY valid JSON for a single meal object matching the schema provided. No preamble, no markdown fences."""


def repair_meal(
    meal: DinnerMeal | LunchMeal,
    meal_type: str,
    findings: list[dict],
    linked_dinner: Optional[dict] = None,
) -> DinnerMeal | LunchMeal:
    """
    Ask Claude to fix specific defects in one meal, keeping the dish the same.

    Parameters
    ----------
    meal : DinnerMeal or LunchMeal
        The meal to repair.
    meal_type : str
        'dinner' or 'lunch'.
    findings : list of Violation
        The defects to fix, from plan_critique.review_meal.
    linked_dinner : dict, optional
        For a leftover lunch, the dinner it comes from — a quantity or leftover
        finding usually cannot be fixed without it.

    Returns
    -------
    The repaired meal.

    Raises
    ------
    MealPlanError on API failure, parse failure, or a response that changed the id.
    """
    prefs = data_store.load_preferences()
    constraints = DEFAULT_CONSTRAINTS + data_store.active_constraints_for_prompt()
    problems = "\n".join(f"- {f['message']}" for f in findings)

    linked_block = ""
    if linked_dinner is not None:
        linked_block = (
            f"\n\nThis lunch is the leftover of this dinner. Its portions and scaling "
            f"note are the constraint you must fit:\n"
            f"{json.dumps({k: linked_dinner.get(k) for k in ('name', 'servings', 'ingredients', 'lunch_scaling_instructions')}, indent=1)}"
        )

    user_message = f"""Fix this {meal_type}. A reviewer found these problems with it:
{problems}

The recipe as it stands:
{json.dumps(meal, indent=1)}{linked_block}

Food constraints — HARD RULES (never violate):
{chr(10).join(f"- {c}" for c in constraints)}

Household: {prefs['lunch_adult_count']} adult(s) for lunch; family of {2 + len(prefs.get('children') or [])} for dinner (2 adults + {len(prefs.get('children') or [])} kids).

Return the corrected {meal_type} as a single JSON object with exactly the same keys \
as the recipe above, including "id": "{meal.get('id', '')}". Change only what the \
problems require, plus whatever must change to stay consistent with it."""

    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=4000,
            system=_REPAIR_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        raise MealPlanError(f"Claude API error during repair: {e}") from e

    raw = _strip_fences(response.content[0].text)
    try:
        repaired = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MealPlanError(f"Repair response was not valid JSON: {e}") from e

    if not isinstance(repaired, dict) or not repaired.get("name"):
        raise MealPlanError("Repair response was not a meal object.")
    if str(repaired.get("id", "")) != str(meal.get("id", "")):
        raise MealPlanError(
            f"Repair changed the meal id ({meal.get('id')} -> {repaired.get('id')})."
        )
    return repaired


def repair_plan_defects(
    plan: WeekPlan,
    defects: list[dict],
    *,
    user_constraints: list[str] | None = None,
    target_sodium_mg: int | None = None,
    history: list | None = None,
    # Re-proofing stays on. It was the obvious thing to cut when the review model
    # was Opus 5 and it cost about a third of the run; on Sonnet 5 that is roughly
    # $0.12, and it buys the one thing this pipeline exists for — knowing whether
    # a fix actually landed. On the real stored week it caught two defects the
    # repairs introduced or left behind, which would otherwise have shipped
    # silently under the impression they were fixed.
    reproof: bool = True,
) -> tuple[WeekPlan, list[dict]]:
    """
    Repair every meal a defect was found in, one attempt each.

    A repaired meal is spliced into the plan only if the plan still passes
    `constraint_check` with zero hard violations. Anything else — an API failure,
    a malformed response, a repair that breaks a constraint — leaves the original
    meal untouched.

    Returns ``(plan, unresolved)``: the plan with accepted repairs applied, and
    the findings that were not fixed, so they can still be shown to the user.
    """
    if not defects:
        return plan, []

    if target_sodium_mg is None:
        target_sodium_mg = data_store.load_preferences().get(
            "target_sodium_mg", constraint_check.SODIUM_TARGET_DEFAULT_MG
        )

    def hard_set(candidate: WeekPlan) -> set[tuple[str, str]]:
        """Hard violations as (meal_id, code) pairs."""
        return {
            (v["meal_id"], v["code"])
            for v in constraint_check.hard_violations(
                constraint_check.check_week_plan(
                    candidate,
                    user_constraints=user_constraints,
                    target_sodium_mg=target_sodium_mg,
                    history=history,
                )
            )
        }

    # The bar is "this repair introduced nothing new", not "the plan is clean".
    # A plan can arrive here already carrying a hard violation — repair_plan_defects
    # is callable on a stored plan, not just on one fresh out of the generation
    # gate — and measuring against zero would reject every repair on such a plan.
    baseline_hard = hard_set(plan)

    by_meal: dict[str, list[dict]] = {}
    for finding in defects:
        if finding.get("meal_id"):
            by_meal.setdefault(finding["meal_id"], []).append(finding)

    unresolved: list[dict] = [f for f in defects if not f.get("meal_id")]
    dinners_by_id = {str(d.get("id", "")): d for d in plan.get("dinners") or []}

    for meal_id, findings in by_meal.items():
        for meal_type, key in (("dinner", "dinners"), ("lunch", "lunches")):
            meals = plan.get(key) or []
            idx = next((i for i, m in enumerate(meals)
                        if str(m.get("id", "")) == meal_id), None)
            if idx is None:
                continue

            original = meals[idx]
            linked = (dinners_by_id.get(str(original.get("leftover_from_dinner_id") or ""))
                      if meal_type == "lunch" else None)
            try:
                repaired = repair_meal(original, meal_type, findings, linked_dinner=linked)
            except MealPlanError:
                unresolved += findings
                break

            meals[idx] = repaired
            introduced = hard_set(plan) - baseline_hard
            if introduced:
                # The repair broke a rule the original satisfied. Keep the original.
                meals[idx] = original
                unresolved += findings
                break

            if reproof:
                still_wrong = plan_critique.review_meal(
                    repaired, meal_type, linked_dinner=linked
                )
                # The repair is kept either way — it passed every hard check. But a
                # defect the fix did not resolve stays visible rather than silently
                # disappearing because we tried.
                unresolved += still_wrong
            break

    return plan, unresolved


# ─────────────────────────────────────────────────────────────────────────────
# Swap a meal
# ─────────────────────────────────────────────────────────────────────────────

_SWAP_SYSTEM = f"""You are a Mediterranean diet meal planner replacing a single meal in an existing \
weekly plan. Your job is to generate exactly one replacement meal that:

1. Fits the same slot type (dinner or lunch) as the meal being replaced.
2. Does not repeat any meal already in the current week (listed below).
3. Does not repeat meals from recent history (listed below).
4. Satisfies all health constraints:
{HEALTH_RULES_SUMMARY}
5. Follows {PLATE_GEOMETRY_SUMMARY}
6. Respects ingredient efficiency — if the replacement is a dinner, try to reuse \
any special ingredients already purchased this week rather than introducing new ones.
7. Follows every food constraint listed in the message — these are hard rules.

Return ONLY valid JSON for a single meal object matching the schema provided. \
No preamble, no markdown fences."""


def swap_meal(
    week_plan: WeekPlan,
    meal_id: str,
    meal_type: str,
    reason: str = "",
) -> WeekPlan:
    """
    Replace one dinner or lunch with a Claude-generated alternative.

    If the replaced dinner had generates_lunch=True, a replacement lunch is
    also generated and swapped in automatically.

    Parameters
    ----------
    week_plan : WeekPlan
        The current week's plan (modified in place and returned).
    meal_id : str
        The id of the meal to replace (e.g. 'd2' or 'l3').
    meal_type : str
        'dinner' or 'lunch'.
    reason : str
        The user's reason for swapping (passed to Claude for context).

    Returns
    -------
    WeekPlan with the replacement meal inserted.

    Raises
    ------
    MealPlanError on API failure, parse error, or meal_id not found.
    """
    prefs = data_store.load_preferences()

    # Find the meal being replaced and its index
    if meal_type == "dinner":
        meals = week_plan["dinners"]
    else:
        meals = week_plan["lunches"]

    idx = next((i for i, m in enumerate(meals) if m["id"] == meal_id), None)
    if idx is None:
        raise MealPlanError(f"Meal '{meal_id}' not found in {meal_type}s.")

    replaced_meal = meals[idx]
    has_paired_lunch = meal_type == "dinner" and any(
        l.get("leftover_from_dinner_id") == meal_id for l in week_plan["lunches"]
    )

    # Build context: what's already in the week
    other_dinners = [d["name"] for d in week_plan["dinners"] if d["id"] != meal_id]
    other_lunches = [l["name"] for l in week_plan["lunches"]]
    history_names = [
        name
        for entry in data_store.history_for_prompt()
        for name in entry.get("meal_names", [])
    ]
    special_already = week_plan.get("week_summary", {}).get("special_ingredients", [])

    reason_note = f'\nReason for swap: "{reason}"' if reason else ""

    # Schema for the response
    if meal_type == "dinner":
        schema = {
            "replacement_dinner": {
                "id": replaced_meal["id"],
                "name": "string",
                "composition": "string — 'classic', 'vegetable_forward', or 'mezze'. Match the plate archetype of the replaced dinner unless the swap reason calls for a change, so the week keeps its vegetable-forward/mezze mix.",
                "cook_time_minutes": "integer",
                "primary_equipment": "string",
                "servings": {"adults": 2, "kids": 2},
                "generates_lunch": "boolean",
                "lunch_scaling_instructions": "string or null",
                "ingredients": [{"name": "str", "quantity": "str", "unit": "str", "pantry_staple": "bool", "special": "bool"}],
                "instructions": ["string"],
                "sunday_prep": "string or null",
                "kid_adaptation": "string — required" if bool(prefs.get("children")) else "string or null",
                "health_highlights": ["string"],
                "uric_acid_tip": "string or null",
                "nutrition_estimate": {"calories_per_adult_serving": "int", "protein_g": "int", "fiber_g": "int", "fat_g": "int", "sodium_mg": "int", "saturated_fat_note": "string or null"},
            }
        }
        if has_paired_lunch:
            schema["replacement_lunch"] = {
                "id": "string — use the id of the lunch being replaced",
                "name": "string",
                "source": "string — 'leftover' if this dinner naturally yields leftovers, 'standalone' if not",
                "leftover_from_dinner_id": f"string — '{replaced_meal['id']}' if source is 'leftover', null if standalone",
                "reheat": "string",
                "prep_at_lunchtime_minutes": "integer",
                "servings": 1,
                "ingredients": [{"name": "str", "quantity": "str", "unit": "str", "pantry_staple": "bool"}],
                "pack_instructions": "string",
                "health_highlights": ["string"],
                "nutrition_estimate": {"calories_per_adult_serving": "int", "protein_g": "int", "fiber_g": "int", "fat_g": "int", "sodium_mg": "int", "saturated_fat_note": "string or null"},
            }
    else:
        schema = {
            "replacement_lunch": {
                "id": replaced_meal["id"],
                "name": "string",
                "source": "string — 'standalone' or 'leftover'",
                "leftover_from_dinner_id": "string or null",
                "reheat": "string",
                "prep_at_lunchtime_minutes": "integer",
                "servings": 1,
                "ingredients": [{"name": "str", "quantity": "str", "unit": "str", "pantry_staple": "bool"}],
                "pack_instructions": "string",
                "health_highlights": ["string"],
                "nutrition_estimate": {"calories_per_adult_serving": "int", "protein_g": "int", "fiber_g": "int", "fat_g": "int", "sodium_mg": "int", "saturated_fat_note": "string or null"},
            }
        }

    _lunch_slot_note = (
        f"\nA paired lunch slot exists for this dinner. If your replacement dinner naturally yields a leftover lunch, "
        f"return replacement_lunch with source: 'leftover' and leftover_from_dinner_id: '{replaced_meal['id']}'. "
        f"If it does not, return a standalone lunch (source: 'standalone', leftover_from_dinner_id: null). "
        f"Always return replacement_lunch when it appears in the schema."
    ) if has_paired_lunch else ""

    swap_constraints = DEFAULT_CONSTRAINTS + data_store.active_constraints_for_prompt()
    swap_constraints_block = "\n".join(f"- {c}" for c in swap_constraints)

    user_message = f"""Replace this {meal_type}: "{replaced_meal['name']}"{reason_note}{_lunch_slot_note}

Food constraints — HARD RULES (never violate):
{swap_constraints_block}

Other meals already in this week (do not repeat):
Dinners: {", ".join(other_dinners)}
Lunches: {", ".join(other_lunches)}

Recent history to avoid (last 6 weeks):
{", ".join(history_names[:30])}

Special ingredients already purchased this week (reuse if possible to avoid new one-off buys):
{", ".join(special_already) if special_already else "None"}

Household: {prefs['lunch_adult_count']} adult(s) for lunch; family of {2 + len(prefs.get('children') or [])} for dinner (2 adults + {len(prefs.get('children') or [])} kids).

Return ONLY this JSON structure:
{json.dumps(schema, indent=2)}"""

    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=3000,
            system=_SWAP_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        raise MealPlanError(f"Claude API error during swap: {e}") from e

    raw = _strip_fences(response.content[0].text)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MealPlanError(f"Swap response was not valid JSON: {e}") from e

    # Insert replacement dinner
    if meal_type == "dinner":
        new_dinner = data.get("replacement_dinner")
        if not new_dinner:
            raise MealPlanError("Swap response missing 'replacement_dinner'.")
        week_plan["dinners"][idx] = new_dinner

        if has_paired_lunch:
            lunch_idx = next(
                (i for i, l in enumerate(week_plan["lunches"])
                 if l.get("leftover_from_dinner_id") == meal_id),
                None,
            )
            new_lunch = data.get("replacement_lunch")
            if new_lunch and lunch_idx is not None:
                new_lunch["id"] = week_plan["lunches"][lunch_idx]["id"]
                is_leftover = new_lunch.get("source") == "leftover"
                new_dinner["generates_lunch"] = is_leftover
                if not is_leftover:
                    new_lunch["leftover_from_dinner_id"] = None
                    new_dinner["lunch_scaling_instructions"] = None
                week_plan["lunches"][lunch_idx] = new_lunch
            elif lunch_idx is not None:
                # Fallback: keep old lunch but update its dinner reference
                week_plan["lunches"][lunch_idx]["leftover_from_dinner_id"] = new_dinner["id"]
        else:
            new_dinner["generates_lunch"] = False
            new_dinner["lunch_scaling_instructions"] = None
    else:
        new_lunch = data.get("replacement_lunch")
        if not new_lunch:
            raise MealPlanError("Swap response missing 'replacement_lunch'.")
        week_plan["lunches"][idx] = new_lunch

    return week_plan


# ─────────────────────────────────────────────────────────────────────────────
# Substitute an ingredient
# ─────────────────────────────────────────────────────────────────────────────

_SUBSTITUTE_SYSTEM = f"""You are revising a single recipe to remove one disliked ingredient. \
Replace the named ingredient with the most appropriate substitute that preserves the dish's \
character, cuisine, and ALL health constraints — the same rules used for full meal generation:

{HEALTH_RULES_SUMMARY}

{PLATE_GEOMETRY_SUMMARY}

Adjust only what the substitution requires: update the `ingredients` array, any affected \
`instructions`, `health_highlights`, `nutrition_estimate`, `cost_estimate`, and `serving_sizes`. \
Keep the meal `id`, the `name` (unless the name directly references the removed ingredient), and \
all unrelated fields unchanged. Honor all active food constraints (listed in the message).

Return ONLY the full updated meal object(s) as JSON — no preamble, no markdown fences."""


def substitute_ingredient(
    week_plan: WeekPlan,
    meal_id: str,
    meal_type: str,
    ingredient_name: str,
    reason: str = "",
) -> WeekPlan:
    """
    Replace a single disliked ingredient in one dinner or lunch with a
    Mediterranean-appropriate substitute, returning the full updated meal.

    Claude rewrites the whole meal object (keeping everything else as close to the
    original as possible) so instructions, nutrition, cost, and serving sizes stay
    coherent after the swap.

    If the target is a dinner that has a paired leftover lunch, that lunch is sent
    along and updated in the same call (its ingredients may now be stale).

    Parameters
    ----------
    week_plan : WeekPlan
        The current week's plan (modified in place and returned).
    meal_id : str
        The id of the meal to revise (e.g. 'd2' or 'l3').
    meal_type : str
        'dinner' or 'lunch'.
    ingredient_name : str
        The ingredient to remove.
    reason : str
        Optional free-text reason (passed to Claude for context).

    Returns
    -------
    WeekPlan with the revised meal inserted.

    Raises
    ------
    MealPlanError on API failure, parse error, or meal_id not found.
    """
    if meal_type == "dinner":
        meals = week_plan["dinners"]
    else:
        meals = week_plan["lunches"]

    idx = next((i for i, m in enumerate(meals) if m["id"] == meal_id), None)
    if idx is None:
        raise MealPlanError(f"Meal '{meal_id}' not found in {meal_type}s.")

    meal = meals[idx]

    # Food constraints: hardcoded defaults + active user constraints
    all_constraints = DEFAULT_CONSTRAINTS + data_store.active_constraints_for_prompt()

    # Lunch coherence: if this dinner yields a paired leftover lunch, update it too.
    paired_lunch_idx = None
    paired_lunch = None
    if meal_type == "dinner":
        paired_lunch_idx = next(
            (i for i, l in enumerate(week_plan["lunches"])
             if l.get("leftover_from_dinner_id") == meal_id),
            None,
        )
        if paired_lunch_idx is not None:
            paired_lunch = week_plan["lunches"][paired_lunch_idx]

    reason_note = f'\nWhy they dislike it: "{reason}"' if reason else ""

    if paired_lunch is not None:
        response_instruction = (
            "This dinner generates a leftover lunch (included below). The substitution may make "
            "the leftover lunch's ingredients stale, so update it to stay consistent. "
            "Return BOTH full objects:\n"
            '{"updated_meal": { ...full dinner... }, "updated_lunch": { ...full lunch... }}'
        )
        payload = {"dinner": meal, "paired_leftover_lunch": paired_lunch}
    else:
        response_instruction = (
            "Return the full updated meal object:\n"
            '{"updated_meal": { ...full meal... }}'
        )
        payload = {"meal": meal}

    constraints_block = "\n".join(f"- {c}" for c in all_constraints)
    paired_note = " (and paired lunch)" if paired_lunch is not None else ""

    user_message = f"""Remove this ingredient from the {meal_type} below: "{ingredient_name}"{reason_note}

Find the best Mediterranean-appropriate substitute and revise the recipe so it stays coherent.

Food constraints to honor:
{constraints_block}

{response_instruction}

Current {meal_type}{paired_note} as JSON:
{json.dumps(payload, indent=2)}"""

    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=3000,
            system=_SUBSTITUTE_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        raise MealPlanError(f"Claude API error during substitution: {e}") from e

    raw = _strip_fences(response.content[0].text)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MealPlanError(f"Substitution response was not valid JSON: {e}") from e

    updated_meal = data.get("updated_meal")
    if not updated_meal:
        raise MealPlanError("Substitution response missing 'updated_meal'.")

    # Preserve the original id no matter what Claude returns
    updated_meal["id"] = meal["id"]
    meals[idx] = updated_meal

    # Update the paired leftover lunch if one was sent
    if paired_lunch is not None and paired_lunch_idx is not None:
        updated_lunch = data.get("updated_lunch")
        if updated_lunch:
            updated_lunch["id"] = paired_lunch["id"]
            updated_lunch["leftover_from_dinner_id"] = meal_id
            week_plan["lunches"][paired_lunch_idx] = updated_lunch

    return week_plan


# ─────────────────────────────────────────────────────────────────────────────
# Scale a meal up or down
# ─────────────────────────────────────────────────────────────────────────────

_SCALE_SYSTEM = """You are resizing a single recipe to make more or fewer servings. \
The dish, ingredients, cuisine, and cooking method stay exactly the same — only the \
QUANTITIES change to match the new serving count.

Rules:
- Rescale every ingredient quantity proportionally to the new serving target, then round \
  to amounts a home cook would actually use. Never produce awkward fractions of \
  whole-unit items: "1 can" scaled by 1.5 becomes "1–2 cans", not "1.5 cans"; \
  "2 cloves garlic" might become "3 cloves". Pantry staples (oil, salt, spices) can stay \
  approximate ("to taste") rather than being scaled precisely.
- Update the `servings` field to the new target.
- Update `cost_estimate.total_ingredient_cost_usd` to reflect the new quantities. \
  Keep `cost_per_serving_usd` roughly the same (it is per serving).
- Keep `nutrition_estimate` UNCHANGED — those numbers are per adult serving and do not \
  change when you scale the whole recipe.
- Adjust cooking `instructions` only where the amount genuinely matters (e.g. pan size, \
  liquid volume, cook time for a larger tray). Otherwise leave them as-is.
- Keep the meal `id`, `name`, and all unrelated fields unchanged.

Return ONLY the full updated meal object as JSON — no preamble, no markdown fences:
{"updated_meal": { ...full meal... }}"""


def _describe_servings(meal_type: str, target) -> str:
    if meal_type == "dinner":
        adults = target.get("adults", 0)
        kids = target.get("kids", 0)
        parts = []
        if adults:
            parts.append(f"{adults} adult{'s' if adults != 1 else ''}")
        if kids:
            parts.append(f"{kids} kid{'s' if kids != 1 else ''}")
        return " + ".join(parts) if parts else "0 servings"
    n = int(target)
    return f"{n} serving{'s' if n != 1 else ''}"


def scale_meal(
    week_plan: WeekPlan,
    meal_id: str,
    meal_type: str,
    new_servings,
    reason: str = "",
) -> WeekPlan:
    """
    Resize one dinner or lunch to a new serving count, returning the full updated meal.

    Claude rewrites the whole meal object — ingredient quantities and totals scale to the
    new serving target while the dish, method, and per-serving nutrition stay the same.

    Parameters
    ----------
    week_plan : WeekPlan
        The current week's plan (modified in place and returned).
    meal_id : str
        The id of the meal to resize (e.g. 'd2' or 'l3').
    meal_type : str
        'dinner' or 'lunch'.
    new_servings : dict | int
        For a dinner: {"adults": int, "kids": int}. For a lunch: an int serving count.
    reason : str
        Optional free-text reason (e.g. 'kids eating elsewhere', 'guest joining').

    Returns
    -------
    WeekPlan with the resized meal inserted.

    Raises
    ------
    MealPlanError on API failure, parse error, or meal_id not found.
    """
    meals = week_plan["dinners"] if meal_type == "dinner" else week_plan["lunches"]

    idx = next((i for i, m in enumerate(meals) if m["id"] == meal_id), None)
    if idx is None:
        raise MealPlanError(f"Meal '{meal_id}' not found in {meal_type}s.")

    meal = meals[idx]
    current_desc = _describe_servings(meal_type, meal.get("servings", 0))
    target_desc = _describe_servings(meal_type, new_servings)
    reason_note = f'\nReason: "{reason}"' if reason else ""

    user_message = f"""Resize this {meal_type} from {current_desc} to {target_desc}.{reason_note}

Set the `servings` field to: {json.dumps(new_servings)}

Current {meal_type} as JSON:
{json.dumps({"meal": meal}, indent=2)}"""

    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=3000,
            system=_SCALE_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        raise MealPlanError(f"Claude API error during resize: {e}") from e

    raw = _strip_fences(response.content[0].text)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MealPlanError(f"Resize response was not valid JSON: {e}") from e

    updated_meal = data.get("updated_meal")
    if not updated_meal:
        raise MealPlanError("Resize response missing 'updated_meal'.")

    # Preserve the original id and force the requested servings no matter what.
    updated_meal["id"] = meal["id"]
    updated_meal["servings"] = new_servings
    meals[idx] = updated_meal

    return week_plan


# ─────────────────────────────────────────────────────────────────────────────
# Kid meal notes
# ─────────────────────────────────────────────────────────────────────────────

_KID_NOTES_SYSTEM_TEMPLATE = """You are writing a babysitter/caregiver meal guide for a family with {kids_desc}. \
You will receive a list of dinner recipes and rewrite each one \
as a simple, plain-English guide for someone who may not be a confident cook.

Rules:
- Use plain language. No culinary jargon. Replace "sauté over medium-high heat" with \
  "cook in the pan on medium heat, stirring occasionally."
- 3–5 clear, short steps per meal.
- Focus only on the kids' portions and the kid adaptation — the adults handle their own plates.
- Include the relevant food constraints for the kids: no mushrooms.
- Each meal card ends with a blank "Notes:" section for the caregiver to write on.
- Keep a friendly but straightforward tone — like instructions left on the fridge.

Return a plain text document (not JSON, not markdown) formatted exactly like this for each dinner:

═══════════════════════════════════════
[MEAL NAME — simplified if needed]
Approx. time: [X] minutes
═══════════════════════════════════════

FOR THE KIDS:
[Kid adaptation note — what's different from the adult version]

WHAT YOU'LL NEED (kids' portions — 2 children):
• [ingredient and simple amount]
• [ingredient and simple amount]

STEPS:
1. [Plain step]
2. [Plain step]
3. [Plain step]

ALLERGY/CONSTRAINT NOTE:
No mushrooms in any form.

Notes:
_____________________________________________
_____________________________________________

[blank line between meals]"""


def generate_kid_notes(week_plan: WeekPlan) -> str:
    """
    Generate a babysitter-friendly plain-English guide for this week's dinners.

    Parameters
    ----------
    week_plan : WeekPlan

    Returns
    -------
    str — formatted plain text, ready to copy or download.

    Raises
    ------
    MealPlanError on API failure.
    """
    dinners = week_plan.get("dinners", [])
    if not dinners:
        raise MealPlanError("No dinners in the plan to generate kid notes for.")

    from system_prompt import _describe_children
    prefs = data_store.load_preferences()
    children = prefs.get("children") or [{"name": "", "age": 5}, {"name": "", "age": 2}]
    kids_desc = _describe_children(children)
    kid_notes_system = _KID_NOTES_SYSTEM_TEMPLATE.format(kids_desc=kids_desc)

    # Build compact dinner summaries to send to Claude
    dinner_summaries = []
    for dinner in dinners:
        kids_ingredients = [
            f"{ing['quantity']} {ing['unit']} {ing['name']}"
            for ing in dinner.get("ingredients", [])
            if not ing.get("pantry_staple")
        ]
        summary = {
            "name": dinner["name"],
            "cook_time_minutes": dinner["cook_time_minutes"],
            "kid_adaptation": dinner.get("kid_adaptation", ""),
            "non_pantry_ingredients": kids_ingredients,
            "instructions": dinner.get("instructions", []),
        }
        dinner_summaries.append(summary)

    user_message = (
        "Here are this week's dinners. Write the babysitter guide as specified.\n\n"
        + json.dumps(dinner_summaries, indent=2)
    )

    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=3000,
            system=kid_notes_system,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        raise MealPlanError(f"Claude API error during kid notes generation: {e}") from e

    header = (
        f"KIDS' MEAL GUIDE — Week of {date.today().isoformat()}\n"
        f"Family: {kids_desc}\n"
        f"Food constraints: No mushrooms in any form\n"
        f"{'=' * 47}\n\n"
    )

    return header + response.content[0].text.strip()

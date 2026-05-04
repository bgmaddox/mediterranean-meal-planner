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

import anthropic

import data_store
from schemas import DinnerMeal, LunchMeal, WeekPlan
from system_prompt import build_system_prompt

MODEL = "claude-sonnet-4-5"


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
) -> tuple[str, str]:
    """
    Build the system prompt and user message for generate_week_plan without
    calling the API. Used by the prompt-preview dialog in app.py.

    Returns
    -------
    (system_prompt, user_message)
    """
    prefs = data_store.load_preferences()
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
    )
    return system_prompt, _DEFAULT_USER_MESSAGE


def generate_week_plan_from_prompts(system_prompt: str, user_message: str) -> WeekPlan:
    """
    Call Claude with pre-built prompts and return a validated WeekPlan.
    Used after the prompt-preview dialog allows editing.

    Raises
    ------
    MealPlanError on API or parse failure.
    """
    try:
        response = _client().messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        raise MealPlanError(f"Claude API error: {e}") from e

    raw = _strip_fences(response.content[0].text)

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
    _validate_plan(plan)
    return plan


def generate_week_plan(week_notes: str | None = None, cuisine_notes: str | None = None) -> WeekPlan:
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
    system_prompt, user_message = build_generation_prompts(week_notes, cuisine_notes)
    return generate_week_plan_from_prompts(system_prompt, user_message)


def _validate_plan(plan: WeekPlan) -> None:
    if not plan.get("dinners"):
        raise MealPlanError("Plan has no dinners.")
    if not plan.get("lunches"):
        raise MealPlanError("Plan has no lunches.")
    if len(plan["dinners"]) != 5:
        raise MealPlanError(f"Expected 5 dinners, got {len(plan['dinners'])}.")
    if len(plan["lunches"]) != 5:
        raise MealPlanError(f"Expected 5 lunches, got {len(plan['lunches'])}.")
    for dinner in plan["dinners"]:
        if not dinner.get("kid_adaptation"):
            raise MealPlanError(
                f"Dinner '{dinner.get('name')}' is missing kid_adaptation."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Swap a meal
# ─────────────────────────────────────────────────────────────────────────────

_SWAP_SYSTEM = """You are a Mediterranean diet meal planner replacing a single meal in an existing \
weekly plan. Your job is to generate exactly one replacement meal that:

1. Fits the same slot type (dinner or lunch) as the meal being replaced.
2. Does not repeat any meal already in the current week (listed below).
3. Does not repeat meals from recent history (listed below).
4. Satisfies all health constraints: Mediterranean diet, uric acid management \
   (avoid organ meats, sardines as staple, fructose/juice, meat stocks; \
   encourage dairy, vitamin C foods, cherries), cholesterol improvement \
   (salmon 2–3x/week across the full week, legumes, oats/barley, EVOO), \
   weight loss (high fiber, high protein, low glycemic).
5. Respects ingredient efficiency — if the replacement is a dinner, try to reuse \
   any special ingredients already purchased this week rather than introducing new ones.
6. Follows all food constraints (no mushrooms, no oranges, no fish in lunches).

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
    generates_lunch = meal_type == "dinner" and replaced_meal.get("generates_lunch", False)

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
                "cook_time_minutes": "integer",
                "primary_equipment": "string",
                "servings": {"adults": 2, "kids": 2},
                "generates_lunch": "boolean",
                "lunch_scaling_instructions": "string or null",
                "ingredients": [{"name": "str", "quantity": "str", "unit": "str", "pantry_staple": "bool", "special": "bool"}],
                "instructions": ["string"],
                "sunday_prep": "string or null",
                "kid_adaptation": "string — required",
                "health_highlights": ["string"],
                "uric_acid_tip": "string or null",
                "nutrition_estimate": {"calories_per_adult_serving": "int", "protein_g": "int", "fiber_g": "int", "fat_g": "int", "saturated_fat_note": "string or null"},
            }
        }
        if generates_lunch:
            schema["replacement_lunch"] = {
                "id": "string — use the id of the lunch being replaced",
                "name": "string",
                "source": "leftover",
                "leftover_from_dinner_id": replaced_meal["id"],
                "reheat": "string",
                "prep_at_lunchtime_minutes": "integer",
                "servings": 1,
                "ingredients": [{"name": "str", "quantity": "str", "unit": "str", "pantry_staple": "bool"}],
                "pack_instructions": "string",
                "health_highlights": ["string"],
                "nutrition_estimate": {"calories_per_adult_serving": "int", "protein_g": "int", "fiber_g": "int", "fat_g": "int", "saturated_fat_note": "string or null"},
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
                "nutrition_estimate": {"calories_per_adult_serving": "int", "protein_g": "int", "fiber_g": "int", "fat_g": "int", "saturated_fat_note": "string or null"},
            }
        }

    user_message = f"""Replace this {meal_type}: "{replaced_meal['name']}"{reason_note}

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

        # Auto-swap paired lunch if the original generated one
        if generates_lunch:
            new_lunch = data.get("replacement_lunch")
            if new_lunch:
                lunch_idx = next(
                    (i for i, l in enumerate(week_plan["lunches"])
                     if l.get("leftover_from_dinner_id") == meal_id),
                    None,
                )
                if lunch_idx is not None:
                    new_lunch["id"] = week_plan["lunches"][lunch_idx]["id"]
                    week_plan["lunches"][lunch_idx] = new_lunch
    else:
        new_lunch = data.get("replacement_lunch")
        if not new_lunch:
            raise MealPlanError("Swap response missing 'replacement_lunch'.")
        week_plan["lunches"][idx] = new_lunch

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

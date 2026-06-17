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

MODEL = "claude-opus-4-8"


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
        data_store.select_anchor_recipes(days=days)
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
    )
    require_kid = bool(prefs.get("children"))
    return system_prompt, _DEFAULT_USER_MESSAGE, require_kid


def generate_week_plan_from_prompts(system_prompt: str, user_message: str, expected_days: int = 5, require_kid_adaptation: bool = True) -> WeekPlan:
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
    _validate_plan(plan, expected_days, require_kid_adaptation=require_kid_adaptation)
    return plan


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
    return generate_week_plan_from_prompts(system_prompt, user_message, expected_days=days, require_kid_adaptation=require_kid)


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
                "nutrition_estimate": {"calories_per_adult_serving": "int", "protein_g": "int", "fiber_g": "int", "fat_g": "int", "saturated_fat_note": "string or null"},
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

    _lunch_slot_note = (
        f"\nA paired lunch slot exists for this dinner. If your replacement dinner naturally yields a leftover lunch, "
        f"return replacement_lunch with source: 'leftover' and leftover_from_dinner_id: '{replaced_meal['id']}'. "
        f"If it does not, return a standalone lunch (source: 'standalone', leftover_from_dinner_id: null). "
        f"Always return replacement_lunch when it appears in the schema."
    ) if has_paired_lunch else ""

    user_message = f"""Replace this {meal_type}: "{replaced_meal['name']}"{reason_note}{_lunch_slot_note}

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

_SUBSTITUTE_SYSTEM = """You are revising a single recipe to remove one disliked ingredient. \
Replace the named ingredient with the most appropriate substitute that preserves the dish's \
character, cuisine, and ALL health constraints — the same rules used for full meal generation:

- Uric acid management: avoid organ meats, sardines as a staple, fructose/juice, meat stocks; \
  encourage dairy, vitamin C foods, cherries.
- Cholesterol improvement: salmon 2–3x/week, legumes, oats/barley, EVOO, walnuts.
- Weight loss: high fiber, high protein, low glycemic, olive oil as the primary fat.

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
    all_constraints = [
        "No mushrooms",
        "No oranges or orange juice",
        "No fish in lunches",
    ] + data_store.active_constraints_for_prompt()

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

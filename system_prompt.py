"""
system_prompt.py
----------------
Builds the Claude system prompt for weekly meal plan generation.

Combines static health/household rules with dynamic context:
  - meal history (last 6 weeks, to avoid repetition)
  - favorites (with ratings and feedback)
  - active food constraints (beyond the hard-coded defaults)
  - user preferences (lunch adult count, budget)
  - current date / season (for seasonal meal adaptation)
  - week notes (free-text from the user, e.g. "busy week, keep it fast")

Usage:
    from system_prompt import build_system_prompt, get_season
    prompt = build_system_prompt(history, favorites, constraints, preferences,
                                  current_date="2026-03-31", week_notes="hot week")
    # Pass as the system= argument in the Claude API call
"""

import json
from datetime import date
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# PANTRY STAPLES
# Items the family always has on hand. Never appear on the shopping list.
# ─────────────────────────────────────────────────────────────────────────────
PANTRY_STAPLES = [
    # Oils
    "extra virgin olive oil",
    "olive oil",
    # Canned / jarred goods
    "canned chickpeas",
    "chickpeas",
    "canned white beans",
    "cannellini beans",
    "white beans",
    "canned lentils",
    "dry lentils",
    "red lentils",
    "green lentils",
    "canned diced tomatoes",
    "diced tomatoes",
    "tomato paste",
    "kalamata olives",
    "capers",
    "roasted red peppers",
    # Grains
    "rolled oats",
    "oats",
    "quinoa",
    "farro",
    "barley",
    "brown rice",
    "whole wheat pasta",
    "legume pasta",
    # Nuts / seeds
    "walnuts",
    "almonds",
    "ground flaxseed",
    "flaxseed",
    "chia seeds",
    # Spices and dried herbs
    "oregano",
    "dried oregano",
    "cumin",
    "ground cumin",
    "coriander",
    "ground coriander",
    "smoked paprika",
    "paprika",
    "turmeric",
    "ground turmeric",
    "cinnamon",
    "ground cinnamon",
    "red pepper flakes",
    "thyme",
    "dried thyme",
    "rosemary",
    "dried rosemary",
    "bay leaves",
    "black pepper",
    "salt",
    "kosher salt",
    # Condiments / acids
    "red wine vinegar",
    "apple cider vinegar",
    "dijon mustard",
    "tahini",
    # Semi-stable produce (last 1–2 weeks refrigerated or on counter)
    "garlic",
    "onion",
    "onions",
    "yellow onion",
    "red onion",
    "shallots",
    "lemons",
    "lemon",
    # Freezer staples
    "frozen spinach",
    "frozen peas",
    "frozen edamame",
    "frozen berries",
]

# ─────────────────────────────────────────────────────────────────────────────
# SHARED RULE SUMMARIES
# Single source of truth for the condensed rules injected into the smaller
# edit prompts in meal_planner.py (swap / substitute). The full generation
# prompt states these rules at length below; these summaries exist so the
# edit calls can never drift out of sync when a rule changes.
# ─────────────────────────────────────────────────────────────────────────────
HEALTH_RULES_SUMMARY = """\
- Uric acid management: avoid organ meats, sardines as a staple, fructose/juice, \
meat stocks; encourage low-fat dairy, vitamin C foods, cherries; legumes and \
leafy greens are safe and encouraged.
- Cholesterol improvement: salmon 2–3x/week across the full week, legumes, \
oats/barley, EVOO, walnuts.
- Weight loss: high fiber, high protein, low glycemic, olive oil as the primary fat."""

PLATE_GEOMETRY_SUMMARY = """\
Mediterranean plate geometry: vegetables are roughly half the plate by volume \
(1.5–2+ cups per adult), animal protein is a modest 3–4 oz cooked portion \
(fish may be 4–5 oz), whole grain is an optional ~1/2-cup side — legumes can \
serve as the starch. Aim for ≥10 g fiber per dinner."""

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT JSON SCHEMA
# Defines the exact structure Claude must return.
# ─────────────────────────────────────────────────────────────────────────────
OUTPUT_SCHEMA = {
    "week_plan": {
        "dinners": [
            {
                "id": "string — 'd1' through 'd5'",
                "name": "string — descriptive recipe name",
                "composition": "string — exactly one of 'classic', 'vegetable_forward', 'mezze'. Which meal-composition archetype this dinner follows (see Meal Composition rules).",
                "cook_time_minutes": "integer",
                "primary_equipment": "string — e.g. 'sheet pan / oven', 'Instant Pot', 'Blackstone griddle', 'slow cooker', 'stovetop'",
                "servings": {
                    "adults": 2,
                    "kids": 2
                },
                "generates_lunch": "boolean — true if this dinner is scaled up to provide one adult lunch",
                "lunch_scaling_instructions": "string or null",
                "ingredients": [
                    {
                        "name": "string",
                        "quantity": "string",
                        "unit": "string",
                        "pantry_staple": "boolean",
                        "special": "boolean — true if non-staple purchased this week; must appear in 2+ meals"
                    }
                ],
                "instructions": ["string — numbered steps for a home cook"],
                "sunday_prep": "string or null",
                "kid_adaptation": "string — always required",
                "health_highlights": ["string"],
                "uric_acid_tip": "string or null",
                "nutrition_estimate": {
                    "calories_per_adult_serving": "integer",
                    "protein_g": "integer",
                    "fiber_g": "integer",
                    "fat_g": "integer",
                    "saturated_fat_note": "string or null — e.g. 'Low — salmon fat is mostly unsaturated'"
                },
                "cost_estimate": {
                    "total_ingredient_cost_usd": "float — estimated cost of non-pantry ingredients for this recipe at a typical US grocery store (Publix/Kroger pricing). For shared special ingredients, prorate the cost across the meals that use them.",
                    "cost_per_serving_usd": "float — total_ingredient_cost_usd divided by total servings (adults + kids)"
                },
                "serving_sizes": [
                    {
                        "component": "string — name of this meal component (e.g. 'salmon fillet', 'quinoa', 'roasted vegetables')",
                        "adult_portion": "string — prescribed portion for one adult (e.g. '4 oz (113g) cooked', '1/2 cup cooked', '1 cup')",
                        "kid_portion": "string — appropriate portion for a young child (e.g. '2 oz', '1/4 cup') — scale down from adult"
                    }
                ]
            }
        ],
        "lunches": [
            {
                "id": "string — 'l1' through 'l5'",
                "name": "string",
                "source": "string — 'standalone' or 'leftover'",
                "leftover_from_dinner_id": "string or null",
                "reheat": "string — 'microwave' or 'none (cold)'",
                "prep_at_lunchtime_minutes": "integer — target ≤5",
                "servings": 1,
                "ingredients": [
                    {
                        "name": "string",
                        "quantity": "string",
                        "unit": "string",
                        "pantry_staple": "boolean"
                    }
                ],
                "pack_instructions": "string",
                "health_highlights": ["string"],
                "nutrition_estimate": {
                    "calories_per_adult_serving": "integer",
                    "protein_g": "integer",
                    "fiber_g": "integer",
                    "fat_g": "integer",
                    "saturated_fat_note": "string or null"
                },
                "cost_estimate": {
                    "total_ingredient_cost_usd": "float — cost of non-pantry ingredients for this lunch. For leftovers, estimate only the incremental cost (packaging, add-ons) since dinner ingredients already counted.",
                    "cost_per_serving_usd": "float — same as total for lunches since servings=1"
                },
                "serving_sizes": [
                    {
                        "component": "string — name of this lunch component",
                        "adult_portion": "string — prescribed portion for one adult",
                        "kid_portion": ""
                    }
                ]
            }
        ],
        "sunday_prep_list": [
            {
                "task": "string",
                "yields_for": ["string"],
                "storage": "string"
            }
        ],
        "snacks": [
            {
                "name": "string — e.g. 'Greek yogurt with cherries and walnuts'",
                "description": "string — 1-2 sentences: how to assemble it and why it fits the health goals",
                "ingredients": [
                    {
                        "name": "string",
                        "quantity": "string",
                        "unit": "string",
                        "pantry_staple": "boolean"
                    }
                ]
            }
        ],
        "week_summary": {
            "fish_meal_count": "integer",
            "red_meat_meal_count": "integer",
            "vegetarian_meal_count": "integer",
            "special_ingredients": ["string"],
            "ingredient_overlap_notes": "string",
            "estimated_weekly_grocery_cost_usd": "float — total estimated grocery spend for the week (non-pantry items, shared ingredients counted once)"
        }
    }
}


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT FOOD CONSTRAINTS — always enforced
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONSTRAINTS = [
    "No mushrooms — avoid entirely in all forms: whole, sliced, dried, or as part of sauces, broths, or umami pastes.",
    "No oranges or orange juice — wife's constraint. Lemons and limes are fine and encouraged.",
    "No fish in lunches — fish should not be reheated at the office (smell). Avoid fish-based lunches entirely.",
]


# ─────────────────────────────────────────────────────────────────────────────
# SEASON HELPER
# ─────────────────────────────────────────────────────────────────────────────
def get_season(date_str: str) -> str:
    """
    Return the Northern Hemisphere season for a given ISO date string.
    Atlanta, GA context: summers are hot and humid; winters are mild.
    """
    month = int(date_str[5:7])
    if month in (12, 1, 2):
        return "winter"
    elif month in (3, 4, 5):
        return "spring"
    elif month in (6, 7, 8):
        return "summer"
    else:
        return "fall"


def _describe_children(children: list[dict]) -> str:
    """Return a natural-language description of the children, e.g. 'Emma (age 5) and a 2-year-old'."""
    if not children:
        return "no children"
    parts = []
    for child in children:
        name = child.get("name", "").strip()
        age = child.get("age", 0)
        if name:
            parts.append(f"{name} (age {age})")
        else:
            parts.append(f"{age}-year-old")
    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    else:
        return ", ".join(parts[:-1]) + f", and {parts[-1]}"


_PROTEIN_TARGETS = {
    3: "1–2 fish, ≤1 red meat, 1 vegetarian",
    4: "2 fish, ≤1 red meat, 1–2 vegetarian",
    5: "2–3 fish, ≤2 red meat, 1–2 vegetarian",
    6: "3 fish, ≤2 red meat, 1–2 vegetarian",
    7: "3–4 fish, ≤2 red meat, 2 vegetarian",
}


def build_system_prompt(
    history: list[dict] | None = None,
    favorites: list[dict] | None = None,
    constraints: list[str] | None = None,
    lunch_adult_count: int = 1,
    children: list[dict] | None = None,
    budget: Optional[str] = None,
    current_date: Optional[str] = None,
    week_notes: Optional[str] = None,
    cuisine_notes: Optional[str] = None,
    portion_scale: float = 1.0,
    use_up_ingredients: Optional[str] = None,
    days: int = 5,
    anchor_recipes: list[dict] | None = None,
    include_snacks: bool = True,
) -> str:
    """
    Build the full system prompt for weekly meal plan generation.

    Parameters
    ----------
    history : list[dict]
        Recent meal plan summaries, newest first. Each dict:
            week_start: str, meal_names: list[str]
    favorites : list[dict]
        Saved favorites with rating, tags, feedback, last_made.
    constraints : list[str]
        Additional food constraints beyond hardcoded defaults.
    lunch_adult_count : int
        How many adults bring lunch to the office. Default 1.
    children : list[dict] or None
        Each dict has 'name' (str) and 'age' (int). Defaults to one 5-year-old and one 2-year-old.
    budget : str or None
        Optional weekly grocery budget, e.g. '$150/week'.
    current_date : str or None
        ISO date string e.g. '2026-03-31'. Defaults to today.
    week_notes : str or None
        Free-text notes from the user for this week's generation
        (e.g. 'busy week — keep it fast', 'hot weather, avoid oven meals').
    cuisine_notes : str or None
        User's cuisine preference for this week
        (e.g. 'all Mediterranean', 'one Thai meal', '2 Mexican dinners').
    """
    if current_date is None:
        current_date = date.today().isoformat()

    if children is None:
        children = [{"name": "", "age": 5}, {"name": "", "age": 2}]

    kids_count = len(children)
    has_kids = kids_count > 0
    kids_desc = _describe_children(children)
    family_size = 2 + kids_count

    # Youngest child's age drives portion and texture guidance
    youngest_age = min(c.get("age", 99) for c in children) if children else 99

    season = get_season(current_date)

    # Atlanta-specific seasonal guidance
    season_guidance = {
        "summer": (
            "It is summer in Atlanta, GA — hot and humid (often 90°F+). "
            "Strongly favor the gas grill and Blackstone griddle over the oven. "
            "Lean toward cold or room-temperature lunches. "
            "Choose lighter proteins (fish, shrimp, chicken breast) and fresh, raw preparations. "
            "Minimize dishes that require the oven on hot nights."
        ),
        "fall": (
            "It is fall in Atlanta, GA — pleasant temperatures, cooling down. "
            "Introduce heartier soups, roasted root vegetables, and warming spices. "
            "Slow cooker and Instant Pot meals are appropriate. "
            "A good season for grain bowls, lentil stews, and baked dishes."
        ),
        "winter": (
            "It is winter in Atlanta, GA — mild but variable; occasional cold snaps. "
            "Favor Instant Pot, slow cooker, and oven-based meals. "
            "Braises, lentil and bean stews, and baked fish are ideal. "
            "Fewer cold lunches — warm, hearty options preferred."
        ),
        "spring": (
            "It is spring in Atlanta, GA — pleasant and mild. "
            "Fresh herbs, lighter proteins, and transitional dishes work well. "
            "Good season to mix grilling with oven and stovetop cooking. "
            "Cold and warm lunches are equally appropriate."
        ),
    }[season]

    # --- History section ---
    if history:
        recent = history[:6]
        lines = [
            f"  - Week of {w['week_start']}: {', '.join(w.get('meal_names', []))}"
            for w in recent
        ]
        history_block = (
            "## Recent Meal History\n"
            "Avoid repeating these exact meals. Variations and twists on a base recipe are acceptable "
            "(e.g., 'Greek Chicken Bowls' → 'Moroccan Chicken Bowls'). "
            "Avoid the same core protein+grain combination within 3 weeks.\n\n"
            + "\n".join(lines)
        )
    else:
        history_block = (
            "## Recent Meal History\n"
            "No history yet — first week. Start with crowd-pleasing Mediterranean staples."
        )

    # --- Favorites section ---
    if favorites:
        lines = []
        for fav in favorites:
            stars = "★" * fav.get("rating", 3) + "☆" * (5 - fav.get("rating", 3))
            tags = ", ".join(fav.get("tags", []))
            feedback = fav.get("feedback", "")
            last_made = fav.get("last_made", "unknown")
            line = f"  - {fav['name']} {stars} (last made: {last_made})"
            if tags:
                line += f" | Tags: {tags}"
            if feedback:
                line += f" | Note: {feedback}"
            lines.append(line)
        favorites_block = (
            "## Saved Favorites\n"
            "Work these in naturally based on ratings and recency. "
            "5★: replicate closely. 3–4★: vary more freely. 1–2★: include rarely.\n\n"
            + "\n".join(lines)
        )
    else:
        favorites_block = (
            "## Saved Favorites\n"
            "No favorites yet. Focus on variety and building the family's repertoire."
        )

    # --- Anchor recipes section ---
    if anchor_recipes:
        lines = []
        for r in anchor_recipes:
            ings = ", ".join(r.get("key_ingredients", []))
            cuisine = r.get("cuisine", "")
            mtype = r.get("meal_type", "either")
            star = "⭐ " if r.get("matches_use_up") else ""
            line = f"  - {star}{r['name']} ({cuisine}, {mtype})"
            if ings:
                line += f" — key: {ings}"
            if r.get("summary"):
                line += f". {r['summary']}"
            if r.get("technique_notes"):
                line += f"\n      • Technique: {r['technique_notes']}"
            lines.append(line)
        anchor_block = (
            "## Anchor Recipes (real-world inspiration)\n"
            "A rotating selection of established recipes. Use several as STARTING POINTS rather than "
            "inventing from scratch. ADAPT FREELY — keep each dish's spirit, flavor profile, and "
            "recognizable identity, but rework ingredients and methods to satisfy the health "
            "constraints, household, season, and ingredient efficiency; vary them so the week isn't a "
            "literal copy, and include unlisted dishes when they serve the plan better. The health "
            "rules above always override fidelity to the original.\n\n"
            "**Side/mezze anchors** (salads, dips, braised/roasted vegetables) build the vegetable "
            "components of vegetable-forward and mezze-style dinners — use them for real, "
            "technique-driven character instead of generic treatments.\n\n"
            "A **Technique** note is the tested, non-obvious procedure from the real recipe (how to "
            "cut/time an ingredient so it cooks evenly). Follow it for the relevant ingredients in "
            "your `instructions` — adapting only as far as your swaps require — rather than improvising "
            "or inventing a plausible-sounding method.\n\n"
            + (
                "Anchors marked ⭐ feature ingredients the user needs to use up this week — "
                "strongly prefer adapting one of those for the meals that consume them, rather "
                "than forcing the ingredient into an unrelated recipe.\n\n"
                if any(r.get("matches_use_up") for r in anchor_recipes)
                else ""
            )
            + "\n".join(lines)
        )
    else:
        anchor_block = ""

    # --- Constraints section ---
    all_constraints = DEFAULT_CONSTRAINTS + (constraints or [])
    constraints_block = (
        "## Food Constraints — HARD RULES (never violate)\n"
        + "\n".join(f"  - {c}" for c in all_constraints)
    )

    # --- Budget section ---
    if budget:
        budget_block = (
            f"## Budget\nTarget: {budget}/week. Favor cost-efficient proteins (lentils, eggs, "
            f"chicken thighs) and limit expensive items (salmon, shrimp, pine nuts) while still "
            f"hitting 2–3 fish meals/week."
        )
    else:
        budget_block = "## Budget\nNo budget constraint. Prioritize nutrition and variety."

    # --- Cuisine notes section ---
    _default_cuisine = (
        "The plan should feel like a well-rounded rotation, not exclusively Mediterranean. "
        "Aim for **3–4 Mediterranean-style dinners** and **1–2 from other cuisines** — Asian "
        "(Japanese, Thai, Korean, Vietnamese), Latin American (Mexican, Peruvian), Indian, American, etc. "
        "The health constraints below apply to ALL meals regardless of cuisine — the cooking style changes, "
        "the nutritional rules do not (e.g. a teriyaki salmon bowl, a Thai chicken-and-vegetable stir-fry "
        "with brown rice, or a black bean taco night are all fully compatible)."
    )
    if cuisine_notes and cuisine_notes.strip():
        cuisine_block = (
            f"The user has specified their cuisine preference for this week: \"{cuisine_notes.strip()}\"\n"
            "Follow this preference closely. All health constraints still apply regardless of cuisine style."
        )
    else:
        cuisine_block = _default_cuisine

    # --- Week notes section ---
    if week_notes and week_notes.strip():
        notes_block = (
            f"## Notes for This Week\n"
            f"The user has provided the following context for this week's plan. "
            f"Adapt meal choices accordingly:\n"
            f"  \"{week_notes.strip()}\""
        )
    else:
        notes_block = ""

    # --- Use-up ingredients section ---
    if use_up_ingredients and use_up_ingredients.strip():
        lines = [f"  - {line.strip()}" for line in use_up_ingredients.strip().splitlines() if line.strip()]
        use_up_block = (
            "## Ingredients to Use Up\n"
            "The following items are already on hand and should be prioritized in this week's recipes. "
            "Work them into at least 1–2 meals. Do not add them to the shopping list.\n"
            + "\n".join(lines)
        )
    else:
        use_up_block = ""

    # --- Portion scale instruction ---
    if abs(portion_scale - 1.0) > 0.01:
        pct = int(round(abs(portion_scale - 1.0) * 100))
        direction = "down" if portion_scale < 1.0 else "up"
        portion_scale_instruction = (
            f"Scale all ingredient quantities {direction} by {pct}% from a standard serving. "
            f"Apply this proportionally — e.g., '3/4 lb salmon' becomes "
            f"{'approx. 11 oz' if direction == 'down' else 'approx. 13 oz'}. "
            f"Use your judgment for items like '1 large onion' (no change needed) vs. measured proteins and grains."
        )
    else:
        portion_scale_instruction = ""

    protein_targets = _PROTEIN_TARGETS.get(days, _PROTEIN_TARGETS[5])
    # Mediterranean pattern: legumes near-daily. Scale ~60% of dinners, min 2.
    legume_target = max(2, round(days * 0.6))

    # Pre-computed conditional strings for kid-related prompt sections
    _children_profile_line = (
        f"- **Children:** {kids_desc}. Need milder versions of the same dishes — same meal, less spice, "
        f"sauces on the side, familiar textures."
        + ("  The youngest is under 3 — prioritize soft textures and finger-food adaptations." if youngest_age < 3 else "")
    ) if has_kids else ""

    _kid_adaptation_rule = (
        "- **Kid adaptation is mandatory on every dinner.** Specific and practical — not a placeholder."
    ) if has_kids else ""

    _kid_output_rule_5 = (
        "5. `kid_adaptation` is required on every dinner. Never null or empty."
    ) if has_kids else ""

    _kid_portion_sentence = (
        "Kid portions should be roughly half of adult portions. For lunches, kid_portion can be an empty string."
    ) if has_kids else "kid_portion should always be an empty string."

    _snacks_rules_block = (
        """
## Snacks — 2–3 suggestions for the week
Weight loss fails between meals, not at them. Suggest 2–3 simple snacks (a `snacks` array in the JSON) \
that bridge the gap between the planned lunch+dinner calories and the full daily target. These are \
assemblies, not recipes — no instructions, no nutrition estimate, just a name, a 1–2 sentence description, \
and an ingredients list (which feeds the shopping list; mark pantry staples).
- Lean on the snacks that actively serve the health goals: **Greek yogurt** (lowers uric acid) with fruit or \
walnuts, **cherries** (clinical evidence for uric acid), **hummus or another legume dip with cut vegetables**, \
a small handful of **walnuts or almonds** with fruit, olives + feta + cucumber.
- Portion-aware: each snack should be roughly 100–200 kcal as described.
- No added-sugar snacks, no juice, no processed snack foods. Fresh fruit is fine; fruit juice is not.
- At least one should be zero-prep (grab-and-eat); Sunday-preppable snacks (cut vegetables, portioned yogurt) are welcome."""
        if include_snacks else ""
    )

    _snacks_output_rule = (
        "13. `snacks` is required: 2–3 snack suggestions following the Snacks rules. "
        "Ingredients listed with quantities for the week so they can be shopped.\n"
        if include_snacks else ""
    )

    import copy
    schema_with_kids = copy.deepcopy(OUTPUT_SCHEMA)
    if not include_snacks:
        schema_with_kids["week_plan"].pop("snacks", None)
    schema_with_kids["week_plan"]["dinners"][0]["servings"]["kids"] = kids_count
    schema_with_kids["week_plan"]["dinners"][0]["id"] = f"string — 'd1' through 'd{days}'"
    schema_with_kids["week_plan"]["lunches"][0]["id"] = f"string — 'l1' through 'l{days}'"
    if not has_kids:
        schema_with_kids["week_plan"]["dinners"][0].pop("kid_adaptation", None)
        for ss in schema_with_kids["week_plan"]["dinners"][0].get("serving_sizes", []):
            ss["kid_portion"] = ""
    schema_str = json.dumps(schema_with_kids, indent=2)
    staples_str = json.dumps(PANTRY_STAPLES, indent=2)

    cost_block = """\
## Cost Estimation Guidelines
For each meal, provide a `cost_estimate` with `total_ingredient_cost_usd` and `cost_per_serving_usd`.
Use typical Publix/Kroger prices in Atlanta, GA as your baseline. Non-pantry ingredients only.

Key pricing guidelines:
- Salmon fillet: ~$10–12/lb. Chicken thighs (boneless skinless): ~$4–5/lb. Cod: ~$8–10/lb.
- Eggs: ~$4/dozen. Greek yogurt (32 oz): ~$6. Feta: ~$4–5 for 6 oz.
- Fresh herbs (bunch): ~$2. Lemons: ~$0.75 each. Baby spinach (5 oz): ~$4.
- Canned chickpeas/beans: ~$1.50. Farro/barley (bulk): ~$2–3/lb.
- Produce (peppers, zucchini, tomatoes, cucumbers): ~$1–3 per item or bag.
- Prorate shared special ingredients across the meals that use them.
- For leftover-based lunches, count only incremental add-on ingredients (e.g., fresh add-ins, yogurt), not the dinner proteins/grains already purchased.
- The `week_summary.estimated_weekly_grocery_cost_usd` should reflect actual unique shopping list cost — shared ingredients counted once, not per-recipe."""

    return f"""You are an expert healthy meal planner with deep knowledge of nutrition science. \
Your job is to generate a complete, practical weekly meal plan for a specific family, optimized for three simultaneous health goals: \
weight loss, improving cholesterol (lower LDL, raise HDL), and managing uric acid levels to prevent gout.

You understand the nuances where these goals create tension (e.g., sardines are excellent for cholesterol but high-purine), \
and you apply current clinical evidence — not outdated dietary myths — when making decisions.

Today's date: {current_date} (Season: {season})

---

# SEASONAL CONTEXT

{season_guidance}

---

# HOUSEHOLD PROFILE

- **Adults:** 2. Both are adventurous eaters who welcome bold flavors from any cuisine — Mediterranean (harissa, za'atar, ras el hanout, \
preserved lemon, sumac), Asian (soy, miso, ginger, sesame, fish sauce), Latin (cumin, chipotle, lime, cilantro), Indian (turmeric, garam masala, \
curry), and beyond.
{_children_profile_line}
- **Dinner servings:** 2 adults + {kids_count} kid{"s" if kids_count != 1 else ""} (family of {family_size}).
- **Lunch servings:** {lunch_adult_count} adult(s) bringing lunch to a weekday office.

---

# HEALTH FRAMEWORK

## Cuisine Variety
{cuisine_block}

## Mediterranean Diet Principles (apply to all meals)
- **Primary fat:** Extra virgin olive oil (or avocado oil for high-heat non-Mediterranean dishes).
- **Base:** Vegetables, whole grains, legumes, fruit, nuts.
- **Fish:** 2–3 dinners per week. Salmon is the top choice. Cod, tilapia, and other white fish are excellent.
- **Poultry:** Lean, skinless chicken and turkey are unlimited.
- **Eggs:** Fine in moderation.
- **Red meat:** ≤2 meals/week. Lean cuts, 3–4 oz cooked per adult.
- **Dairy:** Low-fat Greek yogurt and low-fat milk are encouraged.
- **No organ meats, no processed meats, no deep-fried food.**

## Meal Composition — vary the structure across the week
Do not default every dinner to the same plate shape. Rotate among three archetypes: (1) **Classic plate** — protein + vegetables + optional whole grain/starch (valid, but not the only mode). (2) **Vegetable-forward** — a vegetable dish is the centerpiece with protein and/or grain secondary or folded in (e.g. white-bean-and-greens braise, stuffed vegetables, chickpea-and-vegetable tagine over a little couscous), with legumes, eggs, and dairy carrying the protein. This is the Greek **lathera** tradition — vegetables braised in generous olive oil served as the main course — and the named dishes from the longest-lived Mediterranean populations are your models: briam and soufico (layered baked vegetables), fasolakia yiahni (green bean stew), anginares à la polita (artichokes, peas, and potatoes in lemon-dill broth), imam bayildi (braised stuffed eggplant). Feta, Greek yogurt, or crusty bread traditionally completes the protein. (3) **Mezze / small-plate spread** — 3–4 smaller dishes served tapas-style (e.g. a dip + a vegetable dish + a legume dish + a small protein) instead of one plated entrée. At least 2 dinners should be vegetable-forward or mezze-style, not all classic plates (scale gently: ≥1 for a 3-day week, ≥2 for a 5+-day week). On those nights a grain is **optional**.

Label every dinner with its archetype in the `composition` field (`classic`, `vegetable_forward`, or `mezze`) — the app validates that the week meets the mix above.

## Plate geometry — vegetables are half the plate
This is the core of the Mediterranean diet pattern, not a suggestion: on a **classic plate**, vegetables occupy roughly **half the plate by volume** (1.5–2+ cups per adult, ideally as **two distinct vegetable components** — e.g. a cooked vegetable dish plus a salad), animal protein is a **modest 3–4 oz cooked portion** (fish may run 4–5 oz), and whole grain/starch is a ~1/2-cup side that is **optional on any night** — legumes can serve as the starch instead. Do not drift to a large protein center with a single small vegetable side; when in doubt, add vegetable volume, not protein or grain.

## Vegetables are dishes, not afterthoughts
Every vegetable component must be a deliberately seasoned, technique-driven dish — never "plain steamed/grilled/raw with no seasoning." Build in acid, fat (EVOO), aromatics, herbs/spices, and a finishing element (a sauce, a sprinkle of dukkah/za'atar/feta/toasted nuts, a yogurt-tahini drizzle, a quick pickle, charring). **Vary the treatment across the week** — rotate contrasting techniques (charring/blistering, spice-rub roasting, braising, raw salad with a bright dressing, quick-pickling, grill-then-dress, purée); the same vegetable must not get the same treatment twice in one week. Still respect the health rules (EVOO as the fat, no deep-frying).

## Protein floor on every dinner
Whatever the composition, every dinner must still meet the household's per-adult protein and satiety needs (see the nutrition targets below). On vegetable-forward and mezze nights, hit those targets through legumes, eggs, Greek yogurt/dairy, nuts/seeds, and modest protein portions rather than one large central protein — structure may change, but macros are never sacrificed. Satiety comes from vegetable volume, fiber, and adequate protein together — not from a larger meat portion.

## Fiber floor on every dinner
Every dinner should deliver **at least 10–12 g of fiber per adult serving**. This is the counterweight to the protein floor: if a draft dinner estimates under 10 g, rework it (add a legume component, a second vegetable dish, or swap a refined side for beans or an extra vegetable) before finalizing.

---

# HEALTH CONSTRAINT: URIC ACID MANAGEMENT

## AVOID ENTIRELY:
- Organ meats; processed and deli meats
- Beer; all alcohol raises uric acid — avoid or minimize
- Fruit juice, HFCS, sweetened drinks (fructose directly triggers uric acid production)
- Meat and fish stocks, gravies, bone broths (purines concentrate in boiling liquid)

## LIMIT:
- Sardines, anchovies, mackerel, herring — at most once in the plan; not weekly staples
- Canned tuna — fine 1–2x/week, not daily
- Shellfish — 1–2x/week fine
- Red meat — already limited to ≤2x/week; keep portions to 3–4 oz cooked

## ACTIVELY RECOMMEND (lower uric acid):
- Low-fat Greek yogurt and dairy — dairy proteins promote uric acid excretion; one of the strongest interventions
- Bell peppers, lemon/lime, strawberries, tomatoes — vitamin C lowers serum uric acid
- Cherries — strong clinical evidence; suggest as snack or topping
- Celery — inhibits the enzyme that produces uric acid

## CORRECTED GUIDANCE (do NOT apply outdated myths):
- Spinach and leafy greens are SAFE — plant-source purines do not raise uric acid
- Legumes are SAFE and ENCOURAGED — fiber/protein benefit far outweighs any theoretical purine concern

## COOKING TECHNIQUE:
When boiling or poaching chicken or fish, purines leach into the cooking liquid. Discarding it reduces purine load. \
Include this as a `uric_acid_tip` in relevant recipes.

---

# HEALTH CONSTRAINT: CHOLESTEROL

## Emphasize:
- Salmon 2–3x/week (omega-3s raise HDL)
- Extra virgin olive oil (raises HDL, anti-inflammatory)
- Oats and barley (beta-glucan — strongest dietary LDL intervention)
- Legumes (soluble fiber lowers LDL)
- Walnuts, avocado, ground flaxseed, chia seeds

## Limit:
- Saturated fat: red meat already limited; use low-fat dairy
- Full-fat cheese and cream: occasional accents only

---

# HEALTH CONSTRAINT: WEIGHT LOSS

- High-fiber, high-protein, low-glycemic meals
- Avoid refined carbs, added sugars, white rice as sole carb
- Whole grain or legume-based pasta preferred over white
- Olive oil is the fat — use it, not excessively
- Meals should be filling at normal portions. For classic-plate dinners the DEFAULT is half vegetables, a quarter protein, a quarter whole grain (grain optional) — deviate only with a reason, never toward more protein and less vegetable. Vegetable-forward and mezze nights distribute their components differently while hitting the same calorie, fiber, and protein targets.

---

{constraints_block}

---

# COOKING EQUIPMENT

The following equipment is available. Use whatever makes the best recipe — do NOT try to spread meals across different equipment or ensure each tool gets used. It is completely fine to have all stovetop meals, all sheet pan meals, or any other combination. Equipment variety is never a goal.

- **Normal oven + convection toaster oven:** Sheet pan meals, roasting, baking
- **Instant Pot / pressure cooker:** Dried beans in 25 min, stews in 20 min, pulled chicken in 15 min
- **Slow cooker / Crockpot:** Set before work (6–8 hr); dinner ready at 6pm — ≤10 min active prep
- **Gas grill:** Grilled proteins and vegetables
- **Blackstone griddle:** High-heat searing for fish, chicken, vegetables
- **Rice cooker:** Hands-free grains
- **Microwave:** Reheating only

---

# MEAL PLANNING RULES

## Dinners — {days} per week
- Family of {family_size} (2 adults + {kids_count} kid{"s" if kids_count != 1 else ""})
- 30–45 min active cook/prep time. Slow cooker and Instant Pot meals may take longer but are fine.
{_kid_adaptation_rule}
- Protein targets: {protein_targets}.
- **Legume target:** legumes (beans, lentils, chickpeas, edamame) feature in at least {legume_target} of the {days} dinners — as the main protein or a substantial component (≥1/2 cup per adult), not a garnish. This is a positive target, not just what's left after the meat is planned.{chr(10) + "- " + portion_scale_instruction if portion_scale_instruction else ""}

## Lunches — {days} per week, {lunch_adult_count} adult(s)
- Office, weekdays. Microwave available; cold lunches are equally welcome.
- **No fish in any form.**
- ≤5 min to assemble/reheat at the office.
- Mix of standalone recipes and dinner leftovers.
- Cold lunches (grain salads, wraps, hummus plates) don't need reheating notes.

## Sunday Prep — 3–5 tasks
- Only items that hold well for 5 days without degrading.
- Good: cooked grains, marinated raw protein (2–3 days), sauces (tzatziki, hummus), roasted garlic.
- Avoid: cooked fish, couscous, dressed salads.
{_snacks_rules_block}

---

# INGREDIENT EFFICIENCY

## Pantry Staples
Items the family always has on hand. They must still appear in each recipe's `ingredients` array with `pantry_staple: true` (see Output rule 1) so the cook knows how much to use; the app filters them from the shopping list.

{staples_str}

## Special Ingredient Rule:
Non-staple perishables or specialty items must appear in ≥2 meals this week (confirm in `ingredient_overlap_notes`).

---

{history_block}

---

{favorites_block}

---
{(chr(10) + anchor_block + chr(10) + "---" + chr(10)) if anchor_block else ""}
{budget_block}

---
{(chr(10) + notes_block + chr(10) + "---") if notes_block else ""}{(chr(10) + chr(10) + use_up_block + chr(10) + chr(10) + "---") if use_up_block else ""}

# NUTRITION ESTIMATES

For every dinner and lunch, include a `nutrition_estimate` object with:
- `calories_per_adult_serving` (integer)
- `protein_g` (integer)
- `fiber_g` (integer)
- `fat_g` (integer)
- `saturated_fat_note` (string or null — note only when saturated fat is meaningfully high or low)

These are your best estimates for a typical adult portion of this recipe. \
Accuracy of ±15–20% is acceptable and expected. Do not include a disclaimer in the JSON — \
the app handles that in the UI.

---

{cost_block}

---

# OUTPUT FORMAT

Return **only valid JSON** — no preamble, no explanation, no markdown fences. \
The response must begin with `{{` and end with `}}`.

{schema_str}

## Output rules:
1. Every ingredient — pantry or not — must appear in the `ingredients` array with a quantity and unit. Set `pantry_staple: true` for items in the staples list; the app uses this flag to separate them from the shopping list. Never omit a pantry staple from the ingredient list.
2. `special: true` means non-staple purchased this week — must appear in 2+ meals.
3. `generates_lunch: true` dinners must include `lunch_scaling_instructions`.
4. `sunday_prep` is the task string (or null).
{_kid_output_rule_5}
6. `uric_acid_tip` when a technique meaningfully reduces purines.
7. `ingredient_overlap_notes` must account for every special ingredient.
8. `cook_time_minutes` reflects realistic home-cook active time.
9. `nutrition_estimate` is required on every dinner and lunch.
10. `cost_estimate` is required on every dinner and lunch.
11. `serving_sizes` is required on every dinner and lunch. List whatever components the chosen composition actually has — this may be a protein + vegetables + optional grain, several vegetable dishes with no grain, or a mezze of small plates. \
Base adult portions on a ~500–550 kcal dinner for weight loss. Portion guidance per adult: **3–4 oz cooked for a main animal protein (fish may be 4–5 oz)**, **1.5–2+ cups vegetables total — on classic plates split across two vegetable components**, ~1/2 cup cooked grain when a grain is included at all, smaller portions for mezze plates. Vegetables are the volume of the meal; do not compensate for a smaller protein with more grain. \
{_kid_portion_sentence}
12. `composition` is required on every dinner: 'classic', 'vegetable_forward', or 'mezze'. The week must include at least {"1" if days < 5 else "2"} vegetable_forward or mezze dinner{"" if days < 5 else "s"}.
{_snacks_output_rule}"""

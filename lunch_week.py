"""
lunch_week.py
-------------
Planning a week of office lunches on their own, as one Sunday batch cook.

A dinner week produces lunches as a by-product: mostly leftovers, with five
lines of rules. A lunch week is the other way round. Its unit is a batch
recipe: cooked on Sunday, portioned, and eaten over the days it is assigned.
Two contrasting recipes normally cover the week (Mon-Wed and Thu-Fri).

This module holds everything specific to that:

  build_lunch_prompt()    the generation prompt. Health rules, constraints,
                          pantry, favorites and anchors are the same sections
                          the dinner prompt uses (imported from system_prompt),
                          so the two cannot drift.
  lunch_plan_problems()   structural checks on what comes back.

Rules that can be verified from the plan itself (fish only in a cold lunch, a
batch that outlives its fridge life) live in constraint_check.py with the other
checks, and use the constants below.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Optional

import system_prompt as sp


DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = DAY_ORDER[:5]

REHEAT_MICROWAVE = "microwave"
REHEAT_COLD = "none (cold)"
REHEAT_VALUES = (REHEAT_MICROWAVE, REHEAT_COLD)

# Cooked or mixed fish is at its limit after three days in the fridge, whatever
# a recipe claims for itself.
FISH_LUNCH_MAX_KEEP_DAYS = 3

# Per-portion targets. The stored lunches average 461 kcal, 28 g protein and
# 11 g fiber; the dinner prompt aims at 500-550 kcal.
LUNCH_KCAL_RANGE = (450, 550)
LUNCH_PROTEIN_MIN_G = 30
LUNCH_FIBER_MIN_G = 10

HISTORY_WEEKS_OF_LUNCHES = 6


def day_number(day: str) -> Optional[int]:
    """Days after the Sunday cook: Mon = 1 ... Fri = 5. None for anything else."""
    key = str(day or "").strip()[:3].title()
    return DAY_ORDER.index(key) + 1 if key in DAY_ORDER else None


def last_day_number(lunch: dict) -> Optional[int]:
    """The latest day this batch is eaten, or None if it has no usable `days`."""
    numbers = [n for n in (day_number(d) for d in lunch.get("days") or []) if n]
    return max(numbers) if numbers else None


def is_cold(lunch: dict) -> bool:
    """True only when the lunch says it is eaten cold. A missing or unclear
    `reheat` counts as heated, which is the safe reading for the fish rule."""
    reheat = str(lunch.get("reheat") or "").strip().lower()
    return reheat.startswith("none") or reheat.startswith("cold")


def split_days(days: list[str], recipes: int) -> list[list[str]]:
    """Share the days between the recipes as consecutive blocks, the earlier
    blocks taking the extra day: 5 days over 2 recipes is Mon-Wed, Thu-Fri."""
    days = sorted(days, key=lambda d: day_number(d) or 99)
    recipes = max(1, min(recipes, len(days)))
    base, extra = divmod(len(days), recipes)
    blocks, start = [], 0
    for i in range(recipes):
        size = base + (1 if i < extra else 0)
        blocks.append(days[start:start + size])
        start += size
    return blocks


def recent_lunch_names(history: list[dict] | None) -> list[tuple[str, list[str]]]:
    """(week_start, lunch names) for recent weeks that had lunches, newest first."""
    out = []
    for entry in history or []:
        names = [str(l.get("name", "")) for l in (entry.get("plan") or {}).get("lunches") or []
                 if l.get("name")]
        if names:
            out.append((entry.get("week_start", ""), names))
    return out[:HISTORY_WEEKS_OF_LUNCHES]


# ─────────────────────────────────────────────────────────────────────────────
# Output schema — derived from the dinner week's lunch schema, so a field
# renamed there is renamed here.
# ─────────────────────────────────────────────────────────────────────────────

def lunch_output_schema(ids: list[str], adults: int = 1) -> dict:
    base = copy.deepcopy(sp.OUTPUT_SCHEMA["week_plan"]["lunches"][0])
    dinner = sp.OUTPUT_SCHEMA["week_plan"]["dinners"][0]
    ingredient = dict(base["ingredients"][0])
    ingredient["quantity"] = "string — for the WHOLE batch, not one portion"
    ingredient["special"] = dinner["ingredients"][0]["special"]
    per_day = "1 portion per day" if adults == 1 else f"{adults} portions per day"
    lunch = {
        "id": f"string — one of {', '.join(repr(i) for i in ids)}",
        "name": "string — descriptive recipe name",
        "source": "string — always 'standalone'",
        "leftover_from_dinner_id": None,
        "days": ["string — 'Mon', 'Tue', ... the days this batch is eaten, exactly as assigned"],
        "servings": f"integer — portions the batch makes: {per_day} it covers",
        "keeps_days": "integer — how many days after the Sunday cook the packed lunch is still good "
                      "(Monday = 1, Friday = 5). Be honest: this is checked against `days`.",
        "reheat": f"string — exactly '{REHEAT_MICROWAVE}' or '{REHEAT_COLD}'",
        "cook_time_minutes": "integer — active time on Sunday for this recipe",
        "primary_equipment": dinner["primary_equipment"],
        "prep_at_lunchtime_minutes": base["prep_at_lunchtime_minutes"],
        "ingredients": [ingredient],
        "instructions": ["string — the Sunday cook, step by step, ending with cooling and portioning"],
        "pack_instructions": "string — what goes in each container, what is packed separately "
                             "(dressing, crunchy toppings, fresh herbs, yogurt), and the lunchtime "
                             "step: microwave time and how to finish, or 'eat cold'",
        "health_highlights": base["health_highlights"],
        "nutrition_estimate": base["nutrition_estimate"],
        "cost_estimate": {
            "total_ingredient_cost_usd": "float — non-pantry ingredients for the whole batch",
            "cost_per_serving_usd": "float — total divided by `servings`",
        },
        "serving_sizes": base["serving_sizes"],
    }
    return {
        "week_plan": {
            "lunches": [lunch],
            "sunday_prep_list": [
                {
                    "task": "string — one line per recipe, in the order to cook them on Sunday",
                    "yields_for": ["string — the lunch name"],
                    "storage": "string — containers, fridge or freezer, and how long it holds",
                    "part": "lunch",
                }
            ],
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

def _day_span(days: list[str]) -> str:
    return ", ".join(days)


def build_lunch_prompt(
    history: list[dict] | None = None,
    favorites: list[dict] | None = None,
    constraints: list[str] | None = None,
    lunch_adult_count: int = 1,
    current_date: Optional[str] = None,
    week_notes: Optional[str] = None,
    cuisine_notes: Optional[str] = None,
    use_up_ingredients: Optional[str] = None,
    days: list[str] | None = None,
    recipes: int = 2,
    ids: list[str] | None = None,
    anchor_recipes: list[dict] | None = None,
    week_dinners: list[dict] | None = None,
    kept_lunches: list[dict] | None = None,
) -> str:
    """
    Build the system prompt for a lunch week.

    Parameters beyond the dinner prompt's
    -------------------------------------
    history : full history entries (their plans carry the lunch names).
    days : the days to cover, e.g. ['Mon', ..., 'Fri']. Defaults to Mon-Fri.
    recipes : how many different batch recipes share those days. Default 2.
    ids : the ids the new lunches must use. Defaults to l1..lN; a session
        passes the ids left free by lunches it is keeping.
    week_dinners : the dinners already planned for the same week, if any, so
        lunches don't repeat them and can share their shopping.
    kept_lunches : lunches already covering other days (dinner leftovers the
        user is keeping). Listed so the new recipes contrast with them.
    """
    if current_date is None:
        current_date = date.today().isoformat()
    days = days or list(WEEKDAYS)
    blocks = split_days(days, recipes)
    ids = ids or [f"l{i + 1}" for i in range(len(blocks))]
    if len(ids) != len(blocks):
        raise ValueError(f"{len(blocks)} recipes need {len(blocks)} ids, got {len(ids)}")
    adults = max(1, lunch_adult_count)
    season = sp.get_season(current_date)

    block_lines = "\n".join(
        f"- `{lid}`: {_day_span(block)} — {len(block) * adults} portions, "
        f"must keep at least {day_number(block[-1])} days"
        for lid, block in zip(ids, blocks)
    )
    last = day_number(blocks[-1][-1]) or 5

    fish_days = [d for d in days if (day_number(d) or 99) <= FISH_LUNCH_MAX_KEEP_DAYS]
    fish_ok_ids = [lid for lid, block in zip(ids, blocks)
                   if (day_number(block[-1]) or 99) <= FISH_LUNCH_MAX_KEEP_DAYS]
    if fish_ok_ids:
        fish_where = (f"That limits fish to {', '.join(f'`{i}`' for i in fish_ok_ids)} "
                      f"({_day_span(fish_days)}).")
    else:
        fish_where = ("No recipe this week ends by day "
                      f"{FISH_LUNCH_MAX_KEEP_DAYS}, so plan no fish lunch.")

    if week_dinners:
        dinner_lines = "\n".join(f"  - {d.get('name', '')}" for d in week_dinners)
        specials = sorted({str(i.get("name", "")).strip().lower()
                           for d in week_dinners for i in d.get("ingredients") or []
                           if i.get("special") and i.get("name")})
        dinners_block = (
            "## This Week's Dinners (already planned)\n"
            "The lunches are shopped in the same trip. Do not repeat a dinner's dish or its "
            "main protein-and-flavor combination at lunch. Do reuse what the dinners already "
            "buy where it is natural.\n\n" + dinner_lines
            + (f"\n\nSpecial ingredients the dinners already buy: {', '.join(specials)}."
               if specials else "")
        )
    else:
        dinners_block = (
            "## This Week's Dinners\n"
            "No dinner plan for this week. The lunches are shopped on their own, so the two "
            "recipes should share ingredients where it is natural."
        )

    if kept_lunches:
        kept_lines = "\n".join(
            f"  - {l.get('name', '')}"
            + (f" ({_day_span(l['days'])})" if l.get("days") else "")
            for l in kept_lunches)
        kept_block = ("\n\n## Lunches Already Covering Other Days\n"
                      "Dinner leftovers the user is keeping. Make the new recipes different "
                      "from these.\n\n" + kept_lines)
    else:
        kept_block = ""

    recent = recent_lunch_names(history)
    if recent:
        history_block = (
            "## Recent Lunches\n"
            "Variety is the reason this plan exists: the same chickpea-and-grain salad week "
            "after week is how packed lunches stop getting packed. Do not repeat any of these, "
            "and do not return to the same base (same legume + same grain + same dressing "
            "family) as the last three weeks.\n\n"
            + "\n".join(f"  - Week of {week}: {', '.join(names)}" for week, names in recent)
        )
    else:
        history_block = "## Recent Lunches\nNo lunch history yet."

    favorites_block = sp._favorites_block(favorites)
    constraints_block = sp._constraints_block(constraints)
    notes_block = sp._notes_block(week_notes)
    use_up_block = sp._use_up_block(use_up_ingredients)

    if anchor_recipes:
        anchor_block = (
            "## Anchor Recipes (real-world inspiration)\n"
            "Established recipes chosen for this week. Build each lunch from one of them rather "
            "than inventing from scratch: keep the dish's identity and flavor, and rework it to "
            "satisfy the health rules, to scale to a batch, and to survive the days it is "
            "assigned. The health rules always override fidelity to the original.\n\n"
            "An **Actual per serving** line is the real published nutrition for the dish as "
            "written. Use it to calibrate your own `nutrition_estimate`. A **Technique** note is "
            "the tested procedure from the real recipe. Follow it in your `instructions`.\n\n"
            + "\n".join(sp._anchor_lines(anchor_recipes))
        )
    else:
        anchor_block = ""

    if cuisine_notes and cuisine_notes.strip():
        cuisine_line = (f"The user's cuisine preference for these lunches: "
                        f"\"{cuisine_notes.strip()}\". Follow it closely.")
    else:
        cuisine_line = ("Mediterranean is the home base, but the two recipes should not both be "
                        "Mediterranean every week. Asian, Latin American, Indian and American "
                        "dishes all fit the health rules.")

    schema_str = json.dumps(lunch_output_schema(ids, adults), indent=2)
    kcal_lo, kcal_hi = LUNCH_KCAL_RANGE
    optional = "".join("\n\n---\n\n" + b for b in (anchor_block, notes_block, use_up_block) if b)

    return f"""You are an expert healthy meal planner with deep knowledge of nutrition science. \
Your job is to plan one week of office lunches, cooked in a single batch session on Sunday, \
optimized for three simultaneous health goals: weight loss, improving cholesterol (lower LDL, \
raise HDL), and managing uric acid levels to prevent gout.

Today's date: {current_date} (Season: {season})

{sp.SEASON_GUIDANCE[season]}

The cooking guidance above is for the Sunday session. At the office there is only a microwave.

---

# WHO AND WHERE

- **Eaters:** {adults} adult{"s" if adults != 1 else ""}, adventurous, who welcome{"" if adults != 1 else "s"} bold flavors from any cuisine.
- **Where:** a weekday office, at a desk.
- **Heat:** a microwave, for as long as needed. That is the only heat. No oven, toaster, stovetop or air fryer, so every lunch is either microwaved or eaten cold.
- **At lunchtime:** ≤5 minutes to heat or assemble.

---

# THE SHAPE OF THE WEEK

One cooking session on Sunday makes everything: about 60–90 minutes of active time for the \
whole session, not per recipe. It produces **{len(blocks)} different recipe{"s" if len(blocks) != 1 else ""}**, each a batch \
portioned into containers and eaten on its assigned days:

{block_lines}

The recipes must contrast: different cuisine, different main protein, different texture. A \
soup or stew beside a grain or legume salad is the classic pairing, and one microwaved recipe \
beside one cold one is ideal unless the season argues otherwise. Plan the session so the two \
overlap sensibly (one simmers or roasts while the other is assembled).

---

# WHAT SURVIVES THE WEEK

This is the main design problem. A lunch eaten on day {last} was cooked {last} days earlier.

- **Set `keeps_days` honestly**, and choose each recipe for the days it has to cover. A recipe whose last day is Friday needs `keeps_days` ≥ 5.
- **Holds 5 days:** bean and lentil soups, stews, curries, chili, braises; sturdy grain and legume salads (farro, barley, lentils, chickpeas) dressed with oil and acid; roasted vegetables; marinated beans; shredded cabbage and kale salads; hard-boiled eggs left in the shell.
- **Does not:** dressed leafy salads, cut avocado, cucumber or tomato cut on Sunday for late in the week, pasta sitting in sauce, anything crisp, cooked fish past day {FISH_LUNCH_MAX_KEEP_DAYS}.
- **The freezer counts.** Late-week portions of a dish that freezes well (soups, stews, curries, cooked beans and grains) may be frozen on Sunday and moved to the fridge the night before. Say so in `pack_instructions`; `keeps_days` then covers those days.
- **Pack apart what suffers together:** dressing, crunchy toppings, fresh herbs, tender greens and yogurt sauces go in a small separate container and are added at lunchtime.
- **Microwave reality:** wet dishes reheat well (soups, stews, saucy grain bowls). Nothing that depends on staying crisp, and nothing that needs an oven to revive. Cold toppings such as feta, yogurt or herbs are added after heating.

---

# FISH

Fish is allowed only in a lunch eaten cold (`reheat: '{REHEAT_COLD}'`), never in a microwaved \
one: the office rule is about smell. A fish lunch also keeps at most \
{FISH_LUNCH_MAX_KEEP_DAYS} days. {fish_where} Fish at lunch is welcome when it fits (it counts \
toward the salmon target) but never required. Canned tuna stays at 1–2 times a week.

---

# HEALTH FRAMEWORK

## Cuisine
{cuisine_line}

{sp.MEDITERRANEAN_PRINCIPLES}

## Every lunch portion
- About **{kcal_lo}–{kcal_hi} kcal**, at least **{LUNCH_PROTEIN_MIN_G} g protein** and at least **{LUNCH_FIBER_MIN_G} g fiber**.
- Vegetables are the volume: 1.5 cups or more per portion, seasoned and cooked with intent, never plain.
- Legumes (beans, lentils, chickpeas, edamame) are a substantial part of at least one of the recipes.
- High fiber, high protein, low glycemic. Whole grains or legumes rather than refined starch; no white rice as the only carbohydrate; no added sugar to speak of.
- Filling at a normal portion. An afternoon of hunger is how the snack drawer wins.

---

{sp.URIC_ACID_AND_CHOLESTEROL_RULES}

---

{constraints_block}

---

{sp.EQUIPMENT_SECTION}

All of it is available on Sunday. At the office there is only the microwave.

---

{sp.INGREDIENT_EFFICIENCY_SECTION}

For a lunch week, "≥2 meals" means both lunch recipes, or one lunch recipe and one of this \
week's dinners listed below.

---

{dinners_block}{kept_block}

---

{history_block}

---

{favorites_block}{optional}

---

# NUTRITION ESTIMATES

Every lunch needs a `nutrition_estimate` **per portion**, with the fields in the schema. \
Accuracy of ±15–20% is expected. Sodium is a design constraint, not just a reported number: aim \
for **≤{sp.SODIUM_TARGET_DEFAULT_MG} mg per portion** and treat ~{sp.SODIUM_HARD_CEILING_MG:,} mg as a hard ceiling. \
If a dish would exceed it, change the recipe (rinse canned legumes, unsalted broth, less added \
salt, less feta and olives) and reflect that in `instructions`.

Costs use typical Publix/Kroger prices in Atlanta, GA, non-pantry ingredients only.

---

# OUTPUT FORMAT

Return **only valid JSON** — no preamble, no explanation, no markdown fences.

{schema_str}

## Output rules:
1. Exactly {len(blocks)} lunch{"es" if len(blocks) != 1 else ""}, with the ids and `days` assigned above. `servings` is the number of portions the batch makes.
2. Every ingredient, pantry or not, appears in `ingredients` with a quantity **for the whole batch** and a unit. Set `pantry_staple: true` for items in the staples list.
3. `instructions` is the complete Sunday cook for that recipe, ending with cooling and portioning. `pack_instructions` covers the containers and the lunchtime step.
4. `reheat` is exactly '{REHEAT_MICROWAVE}' or '{REHEAT_COLD}'. Fish only with '{REHEAT_COLD}'.
5. `keeps_days` is at least the number of days to that recipe's last day.
6. `sunday_prep_list` has one task per recipe, in cooking order, each with `"part": "lunch"`.
7. A component made in another recipe this week is listed where it is used with `made_in` set to that meal's id, so it stays off the shopping list.
8. `nutrition_estimate`, `cost_estimate` and `serving_sizes` are required on every lunch. `kid_portion` is an empty string."""


# ─────────────────────────────────────────────────────────────────────────────
# Structural checks
# ─────────────────────────────────────────────────────────────────────────────

def lunch_plan_problems(plan: dict, days: list[str] | None = None, adults: int = 1) -> list[str]:
    """
    Why a lunch-week plan is malformed. Empty list when it is fine.

    Only the batch recipes are examined: a leftover lunch kept from a dinner
    week has none of these fields and is checked by constraint_check instead.
    `days` is the set of days the batch recipes were asked to cover; when given,
    they must cover exactly those.
    """
    lunches = plan.get("lunches") or []
    batches = [l for l in lunches if l.get("source") != "leftover"]
    if not batches:
        return ["Plan has no lunch recipes."]

    problems: list[str] = []
    ids = [str(l.get("id", "")) for l in lunches]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"Lunch id '{dup}' is used more than once.")

    claimed: dict[str, str] = {}
    days_usable = True
    for lunch in batches:
        who = f"{lunch.get('id', '?')} '{lunch.get('name', '')}'"
        if not lunch.get("name"):
            problems.append(f"{who} has no name.")
        if not [s for s in lunch.get("instructions") or [] if str(s).strip()]:
            problems.append(f"{who} has no instructions: a lunch-week recipe needs its Sunday method.")
        if not str(lunch.get("pack_instructions") or "").strip():
            problems.append(f"{who} has no pack_instructions.")
        if lunch.get("reheat") not in REHEAT_VALUES:
            problems.append(f"{who} has reheat {lunch.get('reheat')!r}; it must be "
                            f"'{REHEAT_MICROWAVE}' or '{REHEAT_COLD}'.")
        if not lunch.get("nutrition_estimate"):
            problems.append(f"{who} has no nutrition_estimate.")

        lunch_days = lunch.get("days") or []
        bad = [d for d in lunch_days if day_number(d) is None]
        if not lunch_days:
            days_usable = False
            problems.append(f"{who} has no days.")
        elif bad:
            days_usable = False
            problems.append(f"{who} has unknown day(s): {', '.join(map(str, bad))}.")
        else:
            for d in lunch_days:
                key = DAY_ORDER[day_number(d) - 1]
                if key in claimed:
                    problems.append(f"{who} and {claimed[key]} both claim {key}.")
                claimed[key] = who
            expected = len(lunch_days) * max(1, adults)
            if lunch.get("servings") != expected:
                problems.append(f"{who} covers {len(lunch_days)} day(s) for {adults} adult(s) "
                                f"but makes {lunch.get('servings')!r} servings; expected {expected}.")

        keeps = lunch.get("keeps_days")
        if not isinstance(keeps, int) or isinstance(keeps, bool) or keeps < 1:
            problems.append(f"{who} needs keeps_days as a whole number of days.")

    if days is not None and days_usable:
        wanted = {DAY_ORDER[day_number(d) - 1] for d in days if day_number(d)}
        missing = [d for d in DAY_ORDER if d in wanted - set(claimed)]
        extra = [d for d in DAY_ORDER if d in set(claimed) - wanted]
        if missing:
            problems.append(f"No lunch covers {', '.join(missing)}.")
        if extra:
            problems.append(f"Lunches cover {', '.join(extra)}, which was not asked for.")
    return problems

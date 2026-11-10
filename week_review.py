"""
week_review.py
--------------
Two week-level review rubrics that only the Claude Code session path runs.

The app's review (plan_critique.py) proofreads one recipe at a time, plus one
pass for taste across the week. Two kinds of problem fall between those, and
were too costly to add per API call:

  **Walkthrough** — the week as it will actually be cooked. Nothing in the plan
  assigns a dinner to a day (ids are not days), so the reviewer proposes a
  cooking order that works — fish and delicate produce early, each leftover
  lunch the day after its dinner, Sunday prep still good when it's used — and
  reports what no order can fix.

  **Shopping** — the list as it will be bought. Package sizes against what the
  recipes use, quantities that don't add up to something you can buy, and
  ingredients the instructions use that aren't on the list.

cli.py review-packets writes both rubrics as packets for session reviewers.
Their findings join plan_critique's in the same `_soft_violations` notes, so
the app shows them under "Claude's review of the plan".
"""

from __future__ import annotations

import json

WALKTHROUGH_CATEGORIES = {
    "prep_timing",       # Sunday prep won't hold until the meal that uses it, or isn't made before it's needed
    "leftover_timing",   # no order puts a leftover lunch after its dinner within its fridge life
    "freshness",         # an ingredient bought at the start of the week won't last to the meal that uses it
}

SHOPPING_CATEGORIES = {
    "package_waste",     # buy a whole unit, use a fraction once, nothing else in the week uses the rest
    "unbuyable_quantity",  # the combined amount doesn't translate into something you can buy
    "missing_from_list",   # instructions use an ingredient that isn't listed and isn't a pantry staple
}

LUNCH_WEEK_CATEGORIES = {
    "batch_session",     # the Sunday cook doesn't fit the session: too long, or two steps need the same equipment at once
    "holds_poorly",      # a recipe won't be good on its last assigned day, or the packing doesn't protect it
    "reheat_quality",    # won't microwave well, or is marked cold but needs heat to be good
    "sameness",          # the recipes are too alike, or too like the lunches kept from dinners (a judgment)
}

CATEGORIES = WALKTHROUGH_CATEGORIES | SHOPPING_CATEGORIES | LUNCH_WEEK_CATEGORIES

WALKTHROUGH_SYSTEM = """You are walking through a week of family meals the way it \
will actually be cooked: groceries bought on Sunday, Sunday prep done that afternoon, \
dinners Monday to Friday, one adult's office lunches Monday to Friday.

The plan does not assign meals to days. Dinner ids (d1, d2, ...) are not days. Your \
first job is to propose the cooking order that works best:
- fish and the most perishable produce (herbs, greens, berries, fresh fish) early
- sturdy dishes (legumes, braises, roots, freezer-friendly) late
- each leftover lunch on a day AFTER its dinner, within ~3 days of it
- each Sunday prep item used while it still holds (the prep list states how long)

Then report only what no sensible order fixes:
- prep_timing: a Sunday prep task won't hold until the meal that needs it, or a meal \
needs something prepped that the prep list doesn't make.
- leftover_timing: a leftover lunch can't follow its dinner within its fridge life in \
any order, for example two leftover lunches that both need Monday's dinner.
- freshness: something bought Sunday won't survive until the meal that uses it, \
whatever the order.

Do NOT report recipe-internal problems (each recipe was proofread separately), taste, \
variety, nutrition, or banned ingredients.

The common, correct answer is a good order and no findings. Write each `message` as a \
phrase completing "This meal ___" or "This week ___", naming the specific day problem."""

SHOPPING_SYSTEM = """You are checking a week's shopping list the way someone would \
use it in a grocery store. Below are the list as the app prints it, and for each \
non-pantry ingredient, which meals use it and how much.

Report only:
- package_waste: something sold in a fixed unit (a bunch of herbs, a bag of spinach, \
a carton of buttermilk, a can) where the week uses a small fraction once and nothing \
else in the week uses the rest. Name the unit and what's used. Skip cheap, long-keeping \
items (a lemon, an onion, a can of beans).
- unbuyable_quantity: the combined quantity can't be turned into a purchase, for \
example mixed units that don't add up ("1 cup + 2 whole + 1 handful") or an amount \
given only as "to taste" for something that has to be bought.
- missing_from_list: a meal's instructions call for an ingredient that is in neither \
its ingredient list nor the pantry staples, so it won't be bought.

Do NOT report prices, brands, nutrition, taste, or banned ingredients.

Most weeks have one to three real findings at most. An empty list is fine. Write each \
`message` as a phrase completing "This week ___", naming the ingredient."""

LUNCH_WEEK_SYSTEM = """You are walking through a week of office lunches the way it \
will actually happen. Everything is cooked in one session on Sunday, portioned into \
containers, and eaten on the days each recipe lists. At the office there is a microwave, \
available for as long as needed, and nothing else: no oven, toaster or stovetop.

First lay out the Sunday session: the order to cook the recipes in, what overlaps, and \
the total active time. Return it as `session`, a short list of steps with minutes.

Then report only real problems:
- batch_session: the session runs well past 90 minutes of active time, or two steps need \
the same pot, oven or burner at the same time, or a step is missing (nothing says to cool \
or portion).
- holds_poorly: a recipe will not be good on its LAST assigned day (Friday is five days \
after the cook), whatever its `keeps_days` claims, or the packing puts together things \
that ruin each other (dressed greens, cut cucumber or tomato, crunchy toppings, pasta \
sitting in sauce). Say what fails and on which day.
- reheat_quality: a microwaved lunch depends on something a microwave ruins (crispness, \
a seared crust, bread), or a lunch marked cold is a dish that is poor cold.
- sameness: the recipes are too alike in protein, base or flavor to feel like two \
lunches, or too like the lunches listed under "kept". This one is a matter of taste.

Do NOT report recipe-internal contradictions (each recipe is proofread separately), \
nutrition, or banned ingredients, fish rules and fridge-life arithmetic, which code checks.

The common, correct answer is a sensible session and no findings. Write each `message` as \
a phrase completing "This lunch ___" or "This week ___"."""

FINDINGS_FORMAT = """

Reply with ONLY a JSON object, no prose around it:
{%s"findings": [{"meal_id": "...", "meal_name": "...", "category": "...", "message": "..."}]}
Use meal_id "" for a finding about the week as a whole. Allowed categories: %s."""


def walkthrough_packet(plan: dict) -> str:
    view = {
        "dinners": [{k: d.get(k) for k in ("id", "name", "cook_time_minutes", "ingredients",
                                           "instructions", "sunday_prep", "generates_lunch",
                                           "lunch_scaling_instructions")}
                    for d in plan.get("dinners") or []],
        "lunches": [{k: l.get(k) for k in ("id", "name", "source", "leftover_from_dinner_id",
                                           "reheat", "ingredients", "pack_instructions")}
                    for l in plan.get("lunches") or []],
        "sunday_prep_list": plan.get("sunday_prep_list") or [],
    }
    return (WALKTHROUGH_SYSTEM
            + FINDINGS_FORMAT % ('"order": ["Mon: d? + l?", "Tue: ...", ...], ',
                                 ", ".join(sorted(WALKTHROUGH_CATEGORIES)))
            + "\n\nThe week:\n\n" + json.dumps(view, indent=1))


def shopping_packet(plan: dict, sections: dict) -> str:
    listed = {sec: [f"{i.name}: {i.display_quantity()}" for i in items]
              for sec, items in sections.items()}
    usage: dict[str, list[str]] = {}
    for key in ("dinners", "lunches", "snacks"):
        for m in plan.get(key) or []:
            for ing in m.get("ingredients") or []:
                if ing.get("pantry_staple"):
                    continue
                usage.setdefault(ing["name"].lower(), []).append(
                    f"{m.get('id') or m.get('name')}: {ing.get('quantity', '')} {ing.get('unit', '')}".strip())
    instructions = {m.get("id"): (m.get("instructions") or m.get("pack_instructions"))
                    for key in ("dinners", "lunches") for m in plan.get(key) or []}
    return (SHOPPING_SYSTEM
            + FINDINGS_FORMAT % ("", ", ".join(sorted(SHOPPING_CATEGORIES)))
            + "\n\nShopping list:\n" + json.dumps(listed, indent=1)
            + "\n\nWho uses each ingredient:\n" + json.dumps(usage, indent=1)
            + "\n\nInstructions by meal (for missing_from_list):\n" + json.dumps(instructions, indent=1))


def lunch_week_packet(plan: dict, batch_ids: list[str]) -> str:
    """The lunch week as cooked and eaten. `plan` is the merged week; `batch_ids`
    are the lunch-week recipes in it. Other lunches are listed as kept."""
    lunches = plan.get("lunches") or []
    keys = ("id", "name", "days", "servings", "keeps_days", "reheat", "cook_time_minutes",
            "primary_equipment", "ingredients", "instructions", "pack_instructions")
    view = {
        "recipes": [{k: l.get(k) for k in keys} for l in lunches if str(l.get("id")) in batch_ids],
        "kept": [{"id": l.get("id"), "name": l.get("name")} for l in lunches
                 if str(l.get("id")) not in batch_ids],
        "sunday_prep_list": [t for t in plan.get("sunday_prep_list") or [] if t.get("part") == "lunch"],
    }
    return (LUNCH_WEEK_SYSTEM
            + FINDINGS_FORMAT % ('"session": ["0:00 start the lentils (25 min)", ...], ',
                                 ", ".join(sorted(LUNCH_WEEK_CATEGORIES)))
            + "\n\nThe lunch week:\n\n" + json.dumps(view, indent=1))

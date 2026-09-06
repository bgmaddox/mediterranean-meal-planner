"""
Tests for constraint_check.

Two kinds of test here:

1. Synthetic cases pinning the matcher's edges — the false positives that would
   make the checker worse than useless (rejecting good plans over an 'orange
   bell pepper') and the false negatives that would make it pointless.
2. A regression run over the real stored corpus (data/history.json,
   data/recipe_library.json). Real plans are the ground truth for whether the
   matcher is too eager; the only violation it may flag there is the known
   salmon lunch.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constraint_check as cc  # noqa: E402
import data_store  # noqa: E402
from system_prompt import SODIUM_HARD_CEILING_MG  # noqa: E402


def meal(name="Test Meal", ingredients=(), meal_id="d1", **extra):
    m = {
        "id": meal_id,
        "name": name,
        "ingredients": [{"name": i} for i in ingredients],
        "instructions": [],
    }
    m.update(extra)
    return m


def plan(dinners=(), lunches=()):
    return {"dinners": list(dinners), "lunches": list(lunches)}


def codes(violations):
    return {v["code"] for v in violations}


# Week-level rules (fish frequency, special-ingredient reuse) fire on almost any
# small synthetic plan. Tests aimed at a single check filter them out.
WEEK_LEVEL_CODES = {"fish_below_target", "fish_above_target", "special_ingredient_unused"}


def meal_level(violations):
    return [v for v in violations if v["code"] not in WEEK_LEVEL_CODES]


# ─────────────────────────────────────────────────────────────────────────────
# Mushrooms
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ingredient", [
    "mushrooms", "cremini mushrooms", "shiitake", "shiitakes", "portobello caps",
    "dried porcini", "mushroom broth",
])
def test_mushroom_forms_are_caught(ingredient):
    assert cc._check_banned(meal(ingredients=[ingredient]))


def test_mushroom_in_instructions_only_is_caught():
    """An ingredient can appear in the method but be left off the list."""
    m = meal(ingredients=["onion"], instructions=["Add the mushrooms and sauté."])
    assert codes(cc._check_banned(m)) == {"banned_ingredient"}


@pytest.mark.parametrize("ingredient", ["oyster sauce", "oyster crackers"])
def test_oyster_is_not_a_mushroom(ingredient):
    assert cc._check_banned(meal(ingredients=[ingredient])) == []


# ─────────────────────────────────────────────────────────────────────────────
# Oranges — the color/fruit ambiguity is the whole difficulty here
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ingredient", [
    "orange", "oranges", "orange juice", "orange zest", "orange segments",
    "clementines", "mandarin", "tangerine", "satsumas",
])
def test_orange_fruit_is_caught(ingredient):
    assert cc._check_banned(meal(ingredients=[ingredient]))


@pytest.mark.parametrize("ingredient", [
    "orange bell pepper", "orange peppers", "orange sweet potato",
    "orange-fleshed sweet potato", "orange carrots", "orange cauliflower",
])
def test_orange_as_a_color_is_not_a_violation(ingredient):
    assert cc._check_banned(meal(ingredients=[ingredient])) == []


def test_lemon_and_lime_are_fine():
    assert cc._check_banned(meal(ingredients=["lemon", "lime zest"])) == []


# ─────────────────────────────────────────────────────────────────────────────
# Fish at lunch
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ingredient", [
    "salmon", "leftover salmon", "canned tuna", "cod fillet", "tilapia", "sardines",
])
def test_fish_lunch_is_hard(ingredient):
    v = cc._check_lunch_fish(meal(ingredients=[ingredient], meal_id="l1"))
    assert [x["severity"] for x in v] == ["hard"]


@pytest.mark.parametrize("ingredient", ["anchovy paste", "fish sauce"])
def test_trace_fish_is_soft_not_blocking(ingredient):
    v = cc._check_lunch_fish(meal(ingredients=[ingredient], meal_id="l1"))
    assert [x["severity"] for x in v] == ["soft"]


def test_shellfish_lunch_is_soft():
    v = cc._check_lunch_fish(meal(ingredients=["shrimp"], meal_id="l1"))
    assert [x["severity"] for x in v] == ["soft"]


def test_fish_is_allowed_at_dinner():
    p = plan(dinners=[meal(name="Roast Salmon", ingredients=["salmon fillets"])])
    assert meal_level(cc.check_week_plan(p)) == []


def test_cocoa_does_not_match_cod():
    assert cc._check_lunch_fish(meal(ingredients=["cocoa powder"])) == []


# ─────────────────────────────────────────────────────────────────────────────
# Sodium
# ─────────────────────────────────────────────────────────────────────────────

def dinner_with_sodium(mg):
    return meal(ingredients=["chicken"], nutrition_estimate={"sodium_mg": mg})


def test_sodium_over_ceiling_is_hard():
    v = cc._check_sodium(dinner_with_sodium(SODIUM_HARD_CEILING_MG + 1), 700)
    assert [x["severity"] for x in v] == ["hard"]


def test_sodium_over_target_under_ceiling_is_soft():
    v = cc._check_sodium(dinner_with_sodium(900), 700)
    assert [x["severity"] for x in v] == ["soft"]


def test_sodium_at_target_passes():
    assert cc._check_sodium(dinner_with_sodium(700), 700) == []


def test_missing_sodium_is_not_invented():
    """Old plans predate the sodium field; absence is not a violation."""
    assert cc._check_sodium(meal(ingredients=["chicken"]), 700) == []


def test_sodium_target_is_user_configurable():
    assert cc._check_sodium(dinner_with_sodium(900), 1000) == []


# ─────────────────────────────────────────────────────────────────────────────
# User constraints
# ─────────────────────────────────────────────────────────────────────────────

def test_parses_negations_and_leaves_the_rest_to_the_prompt():
    enforceable, unenforceable = cc.parse_user_constraints([
        "No shellfish", "avoid cilantro.", "without eggplant",
        "Keep Tuesday fast", "no more than 2 red meat meals",
    ])
    assert enforceable == ["shellfish", "cilantro", "eggplant"]
    assert unenforceable == ["Keep Tuesday fast", "no more than 2 red meat meals"]


def test_parsed_user_constraint_is_enforced():
    p = plan(dinners=[meal(ingredients=["eggplant", "tomato"])])
    v = meal_level(cc.check_week_plan(p, user_constraints=["no eggplant"]))
    assert codes(v) == {"user_constraint"}


def test_unparseable_constraint_does_not_block():
    p = plan(dinners=[meal(ingredients=["beef"])])
    assert meal_level(cc.check_week_plan(p, user_constraints=["Keep Tuesday fast"])) == []


# ─────────────────────────────────────────────────────────────────────────────
# Repeats
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_within_week_is_hard():
    p = plan(dinners=[meal(name="Greek Lemon Chicken", meal_id="d1"),
                      meal(name="Greek Lemon Chicken", meal_id="d2")])
    assert "repeat_within_week" in codes(cc.check_week_plan(p))


def test_near_repeat_of_recent_history_is_soft_only():
    p = plan(dinners=[meal(name="Sheet Pan Salmon with Roasted Vegetables and Quinoa")])
    history = [{"week_start": "2026-03-30",
                "meal_names": ["Sheet Pan Salmon with Roasted Vegetables and Quinoa"]}]
    v = meal_level(cc.check_week_plan(p, history=history))
    assert [x["severity"] for x in v] == ["soft"]
    assert cc.hard_violations(v) == []


def test_different_dishes_are_not_repeats():
    p = plan(dinners=[meal(name="Greek Lemon Chicken")])
    history = [{"week_start": "2026-03-30", "meal_names": ["Turkey White Bean Chili"]}]
    assert meal_level(cc.check_week_plan(p, history=history)) == []


# ─────────────────────────────────────────────────────────────────────────────
# Ordering / API shape
# ─────────────────────────────────────────────────────────────────────────────

def test_hard_violations_sort_first():
    p = plan(dinners=[dinner_with_sodium(900)],
             lunches=[meal(ingredients=["salmon"], meal_id="l1")])
    v = meal_level(cc.check_week_plan(p))
    assert [x["severity"] for x in v] == ["hard", "soft"]


def test_format_violations_is_readable_feedback():
    p = plan(lunches=[meal(name="Tuna Bowl", ingredients=["tuna"], meal_id="l1")])
    text = cc.format_violations(cc.check_week_plan(p))
    assert "l1 'Tuna Bowl'" in text and "fish" in text


def test_empty_plan_does_not_crash():
    assert cc.check_week_plan({}) == []


# ─────────────────────────────────────────────────────────────────────────────
# Week-level rules
# ─────────────────────────────────────────────────────────────────────────────

def fish_week(n_fish, n_dinners=5):
    dinners = []
    for i in range(n_dinners):
        ing = "salmon fillets" if i < n_fish else "chicken thighs"
        dinners.append(meal(name=f"Dinner {i}", ingredients=[ing], meal_id=f"d{i}"))
    return plan(dinners=dinners)


@pytest.mark.parametrize("n_fish,expect", [(0, True), (1, True), (2, False), (3, False), (4, True)])
def test_fish_frequency_target(n_fish, expect):
    found = any(v["code"].startswith("fish_") for v in cc.check_week_plan(fish_week(n_fish)))
    assert found is expect


def test_fish_target_scales_down_for_short_weeks():
    assert not [v for v in cc.check_week_plan(fish_week(1, n_dinners=3))
                if v["code"].startswith("fish_")]


def special(name, flag=True):
    return {"name": name, "special": flag, "pantry_staple": False}


def test_special_ingredient_used_once_is_flagged():
    p = plan(dinners=[
        {"id": "d1", "name": "A", "ingredients": [special("harissa paste")], "instructions": []},
        {"id": "d2", "name": "B", "ingredients": [special("pomegranate molasses")], "instructions": []},
    ])
    msgs = [v["message"] for v in cc.check_week_plan(p) if v["code"] == "special_ingredient_unused"]
    assert len(msgs) == 2


def test_special_ingredient_reused_passes():
    p = plan(dinners=[
        {"id": "d1", "name": "A", "ingredients": [special("harissa paste")], "instructions": []},
        {"id": "d2", "name": "B", "ingredients": [special("harissa paste")], "instructions": []},
    ])
    assert not [v for v in cc.check_week_plan(p) if v["code"] == "special_ingredient_unused"]


def test_non_special_ingredient_is_not_expected_to_repeat():
    p = plan(dinners=[{"id": "d1", "name": "A",
                       "ingredients": [special("lemon", flag=False)], "instructions": []}])
    assert not [v for v in cc.check_week_plan(p) if v["code"] == "special_ingredient_unused"]


# ─────────────────────────────────────────────────────────────────────────────
# Leftover plumbing
# ─────────────────────────────────────────────────────────────────────────────

def test_leftover_pointing_at_missing_dinner_is_hard():
    p = plan(dinners=[meal(name="Roast", meal_id="d1")],
             lunches=[meal(name="Leftovers", meal_id="l1", source="leftover",
                           leftover_from_dinner_id="d9")])
    v = [x for x in cc.check_week_plan(p) if x["code"] == "leftover_broken_link"]
    assert v and v[0]["severity"] == "hard"


def test_leftover_without_a_link_is_soft():
    p = plan(dinners=[meal(name="Roast", meal_id="d1")],
             lunches=[meal(name="Leftovers", meal_id="l1", source="leftover")])
    v = [x for x in cc.check_week_plan(p) if x["code"] == "leftover_unlinked"]
    assert v and v[0]["severity"] == "soft"


def test_generates_lunch_with_no_claiming_lunch_is_flagged():
    p = plan(dinners=[meal(name="Roast", meal_id="d1", generates_lunch=True)],
             lunches=[meal(name="Salad", meal_id="l1", source="standalone")])
    assert "lunch_not_generated" in codes(cc.check_week_plan(p))


def test_correctly_linked_leftovers_pass():
    p = plan(dinners=[meal(name="Roast", meal_id="d1", generates_lunch=True)],
             lunches=[meal(name="Leftovers", meal_id="l1", source="leftover",
                           leftover_from_dinner_id="d1")])
    assert meal_level(cc.check_week_plan(p)) == []


# ─────────────────────────────────────────────────────────────────────────────
# Composition labels
# ─────────────────────────────────────────────────────────────────────────────

def test_vegetable_forward_with_a_meat_centerpiece_is_flagged():
    p = plan(dinners=[meal(name="Briam", ingredients=["chicken thighs", "zucchini"],
                           composition="vegetable_forward")])
    assert "composition_mislabeled" in codes(cc.check_week_plan(p))


def test_vegetable_forward_without_meat_passes():
    p = plan(dinners=[meal(name="Briam", ingredients=["zucchini", "potatoes", "feta"],
                           composition="vegetable_forward")])
    assert not [v for v in cc.check_week_plan(p) if v["code"] == "composition_mislabeled"]


def test_classic_dinner_may_have_a_meat_centerpiece():
    p = plan(dinners=[meal(name="Roast Chicken", ingredients=["chicken thighs"],
                           composition="classic")])
    assert not [v for v in cc.check_week_plan(p) if v["code"] == "composition_mislabeled"]


# ─────────────────────────────────────────────────────────────────────────────
# Unused ingredients — the false-positive-prone one
# ─────────────────────────────────────────────────────────────────────────────

def test_ingredient_never_mentioned_is_flagged():
    p = plan(dinners=[meal(name="Roast", ingredients=["pomegranate molasses"],
                           instructions=["Roast the chicken and serve."])])
    assert "ingredient_never_used" in codes(cc.check_week_plan(p))


@pytest.mark.parametrize("name,instruction", [
    ("boneless skinless chicken breast", "Grill the chicken 6 minutes per side."),
    ("feta cheese", "Crumble the feta over the top."),
    ("baby spinach", "Wilt the spinach in the pan."),
    ("low-sodium chickpeas", "Rinse the chickpeas and add them."),
    ("extra virgin olive oil", "Drizzle with olive oil."),
])
def test_loosely_named_ingredients_are_not_false_positives(name, instruction):
    """Recipes say 'the chicken', not 'the boneless skinless chicken breast'."""
    p = plan(dinners=[meal(name="X", ingredients=[name], instructions=[instruction])])
    assert not [v for v in cc.check_week_plan(p) if v["code"] == "ingredient_never_used"]


def test_pantry_staples_are_exempt():
    d = {"id": "d1", "name": "X", "instructions": ["Cook it."],
         "ingredients": [{"name": "kosher salt", "pantry_staple": True}]}
    assert not [v for v in cc.check_week_plan(plan(dinners=[d]))
                if v["code"] == "ingredient_never_used"]


# ─────────────────────────────────────────────────────────────────────────────
# Regression against the real stored corpus
# ─────────────────────────────────────────────────────────────────────────────

# The stored week genuinely breaks these rules; the point of the regression is
# that nothing NEW appears, not that the corpus is clean.
KNOWN_REAL_VIOLATIONS = {
    ("Salmon Quinoa Power Bowl", "fish_in_lunch"),
    ("", "fish_below_target"),
    ("", "special_ingredient_unused"),
}


def test_real_history_flags_only_known_violations():
    """Guards against an over-eager matcher: real, accepted plans should be clean."""
    found = set()
    for entry in data_store.load_history():
        for v in cc.check_week_plan(entry.get("plan") or {}):
            found.add((v["meal_name"], v["code"]))
    assert found <= KNOWN_REAL_VIOLATIONS, f"unexpected findings: {found - KNOWN_REAL_VIOLATIONS}"


def test_real_recipe_library_flags_only_known_violations():
    library = data_store.load_recipe_library()
    corpus = {
        "dinners": [e["meal"] for e in library if e.get("meal_type") == "dinner"],
        "lunches": [e["meal"] for e in library if e.get("meal_type") == "lunch"],
    }
    found = {(v["meal_name"], v["code"]) for v in cc.check_week_plan(corpus)}
    assert found <= KNOWN_REAL_VIOLATIONS, f"unexpected findings: {found - KNOWN_REAL_VIOLATIONS}"


def test_anchor_recipe_library_has_no_banned_ingredients():
    """The shipped anchors are injected into every generation, technique notes
    included — a banned ingredient there seeds it straight into the plan. Three
    anchors carried 'orange zest' in their notes when this check was written.
    """
    anchors = data_store.load_anchor_recipes()
    assert anchors, "anchor library failed to load"
    findings = []
    for a in anchors:
        as_meal = {
            "id": a.get("id", ""),
            "name": a.get("name", ""),
            "ingredients": [{"name": k} for k in a.get("key_ingredients", [])],
            "instructions": [a.get("summary", ""), a.get("technique_notes", "")],
        }
        findings += cc._check_banned(as_meal)
    assert not findings, [(v["meal_id"], v["message"]) for v in findings]

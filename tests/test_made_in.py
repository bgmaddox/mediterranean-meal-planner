"""
`made_in`: a component made in another recipe this week stays off the shopping
list. Found by the first session shopping review: a snack's "whipped feta (from
d3 prep)" was printed as something to buy.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constraint_check as cc  # noqa: E402
import shopping  # noqa: E402


def ing(name, made_in=None, staple=False):
    i = {"name": name, "quantity": "1", "unit": "cup", "pantry_staple": staple, "special": False}
    if made_in is not None:
        i["made_in"] = made_in
    return i


def plan(snack_ings, lunch_ings=None):
    return {
        "dinners": [{"id": "d3", "name": "Chickpea Soup with Whipped Feta Toasts",
                     "ingredients": [ing("feta cheese"), ing("chickpeas")]}],
        "lunches": [{"id": "l1", "name": "Grain Bowl", "source": "standalone",
                     "ingredients": lunch_ings or [ing("farro")]}],
        "snacks": [{"name": "Cucumbers with whipped feta", "ingredients": snack_ings}],
    }


def listed(p):
    return {i.name for items in shopping.build_shopping_list(p).values() for i in items}


def test_made_in_component_is_not_on_the_shopping_list():
    names = listed(plan([ing("whipped feta", made_in="d3"), ing("cucumber")]))
    assert "whipped feta" not in names
    assert {"feta cheese", "cucumber"} <= names


def test_unmarked_component_is_flagged():
    found = cc._check_made_in(plan([ing("whipped feta (from d3 prep)")]))
    assert [v["code"] for v in found] == ["made_elsewhere_unmarked"]


def test_marked_component_is_not_flagged():
    assert cc._check_made_in(plan([ing("whipped feta (from d3 prep)", made_in="d3")])) == []


def test_made_in_pointing_at_a_missing_meal_is_flagged():
    found = cc._check_made_in(plan([ing("whipped feta", made_in="d9")]))
    assert [v["code"] for v in found] == ["made_in_broken"]


def test_sunday_prep_is_a_valid_source():
    assert cc._check_made_in(plan([], lunch_ings=[ing("cooked farro", made_in="sunday_prep")])) == []


def test_ordinary_ingredients_are_not_flagged():
    assert cc._check_made_in(plan([ing("fresh dill"), ing("cucumber"), ing("lemon", staple=True)])) == []

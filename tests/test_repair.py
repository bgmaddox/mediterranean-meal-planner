"""
Tests for the repair pass.

Repair is the only part of the review pipeline that changes a plan, so the
tests are about damage control: a repair that fails, breaks a constraint, or
comes back malformed must leave the original meal exactly as it was, and must
never cost more than one attempt.
"""

import copy
import json
import sys
from pathlib import Path

import anthropic
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import meal_planner  # noqa: E402
import plan_critique  # noqa: E402
from meal_planner import MealPlanError  # noqa: E402


def dinner(meal_id="d1", name="Greek Lemon Chicken", **over):
    d = {
        "id": meal_id, "name": name, "composition": "classic",
        "cook_time_minutes": 40, "servings": {"adults": 2, "kids": 2},
        "generates_lunch": False, "kid_adaptation": "Serve plain.",
        "ingredients": [{"name": "chicken thighs", "quantity": "2", "unit": "lb",
                         "pantry_staple": False, "special": False}],
        "instructions": ["Roast the chicken 40 minutes."],
        "nutrition_estimate": {"sodium_mg": 500},
    }
    d.update(over)
    return d


def plan_with(*dinners):
    return {"dinners": list(dinners), "lunches": []}


def finding(meal_id="d1", message="reserves half the sauce but never uses it"):
    return {"code": "critique_recipe_coherence", "severity": "soft",
            "meal_id": meal_id, "meal_name": "Greek Lemon Chicken",
            "message": message}


@pytest.fixture(autouse=True)
def no_reproof(monkeypatch):
    """Re-proofing is a separate API call; silence it unless a test wants it."""
    monkeypatch.setattr(plan_critique, "review_meal", lambda *a, **k: [])


@pytest.fixture
def fake_repair(monkeypatch):
    """Replace repair_meal with a scripted result, recording every call."""
    calls = []

    def install(result):
        def fake(meal, meal_type, findings, linked_dinner=None):
            calls.append({"meal": meal, "meal_type": meal_type, "findings": findings,
                          "linked_dinner": linked_dinner})
            if isinstance(result, Exception):
                raise result
            return result(meal) if callable(result) else result
        monkeypatch.setattr(meal_planner, "repair_meal", fake)
        return calls

    return install


# ─────────────────────────────────────────────────────────────────────────────
# The happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_successful_repair_is_spliced_in(fake_repair):
    fixed = dinner(instructions=["Roast 40 min.", "Spoon over the reserved sauce."])
    fake_repair(fixed)
    plan, unresolved = meal_planner.repair_plan_defects(
        plan_with(dinner()), [finding()])
    assert plan["dinners"][0]["instructions"][-1].startswith("Spoon over")
    assert unresolved == []


def test_untouched_meals_are_left_alone(fake_repair):
    fake_repair(dinner(name="Fixed"))
    original_d2 = dinner("d2", "Turkey Chili")
    plan, _ = meal_planner.repair_plan_defects(
        plan_with(dinner(), original_d2), [finding()])
    assert plan["dinners"][1] == original_d2


def test_all_findings_for_one_meal_go_in_one_repair(fake_repair):
    calls = fake_repair(dinner())
    meal_planner.repair_plan_defects(
        plan_with(dinner()),
        [finding(message="problem one"), finding(message="problem two")])
    assert len(calls) == 1
    assert len(calls[0]["findings"]) == 2


def test_no_defects_makes_no_calls(fake_repair):
    calls = fake_repair(dinner())
    plan, unresolved = meal_planner.repair_plan_defects(plan_with(dinner()), [])
    assert calls == [] and unresolved == []


# ─────────────────────────────────────────────────────────────────────────────
# Damage control
# ─────────────────────────────────────────────────────────────────────────────

def test_failed_repair_keeps_the_original(fake_repair):
    fake_repair(MealPlanError("API blew up"))
    original = dinner()
    plan, unresolved = meal_planner.repair_plan_defects(
        plan_with(copy.deepcopy(original)), [finding()])
    assert plan["dinners"][0] == original
    assert len(unresolved) == 1


def test_repair_that_breaks_a_constraint_is_rejected(fake_repair):
    """A fix that introduces mushrooms is worse than the defect it fixed."""
    bad = dinner(ingredients=[{"name": "cremini mushrooms", "quantity": "8",
                               "unit": "oz", "pantry_staple": False, "special": False}])
    fake_repair(bad)
    original = dinner()
    plan, unresolved = meal_planner.repair_plan_defects(
        plan_with(copy.deepcopy(original)), [finding()])
    assert plan["dinners"][0] == original
    assert len(unresolved) == 1


def test_repair_that_blows_the_sodium_ceiling_is_rejected(fake_repair):
    fake_repair(dinner(nutrition_estimate={"sodium_mg": 3000}))
    original = dinner()
    plan, _ = meal_planner.repair_plan_defects(
        plan_with(copy.deepcopy(original)), [finding()])
    assert plan["dinners"][0] == original


def test_only_one_repair_attempt_per_meal(fake_repair):
    """A stubborn defect must never become a loop."""
    calls = fake_repair(dinner())
    meal_planner.repair_plan_defects(plan_with(dinner()), [finding()])
    assert len(calls) == 1


def test_week_level_finding_is_not_repairable(fake_repair):
    calls = fake_repair(dinner())
    plan, unresolved = meal_planner.repair_plan_defects(
        plan_with(dinner()), [finding(meal_id="")])
    assert calls == []
    assert len(unresolved) == 1


def test_finding_for_a_missing_meal_is_ignored(fake_repair):
    calls = fake_repair(dinner())
    meal_planner.repair_plan_defects(plan_with(dinner()), [finding(meal_id="d99")])
    assert calls == []


# ─────────────────────────────────────────────────────────────────────────────
# Re-proofing
# ─────────────────────────────────────────────────────────────────────────────

def test_surviving_defect_stays_visible(monkeypatch, fake_repair):
    """A repair that passes every hard check is kept — but if the defect is
    still there, the user must still hear about it."""
    fake_repair(dinner(name="Greek Lemon Chicken", cook_time_minutes=45))
    monkeypatch.setattr(plan_critique, "review_meal",
                        lambda *a, **k: [finding(message="still contradicts itself")])
    plan, unresolved = meal_planner.repair_plan_defects(
        plan_with(dinner()), [finding()], reproof=True)
    assert plan["dinners"][0]["cook_time_minutes"] == 45   # repair kept
    assert "still contradicts itself" in unresolved[0]["message"]


def test_resolved_defect_disappears(monkeypatch, fake_repair):
    fake_repair(dinner(cook_time_minutes=45))
    monkeypatch.setattr(plan_critique, "review_meal", lambda *a, **k: [])
    plan, unresolved = meal_planner.repair_plan_defects(
        plan_with(dinner()), [finding()], reproof=True)
    assert unresolved == []


# ─────────────────────────────────────────────────────────────────────────────
# repair_meal itself — the request and the response guard
# ─────────────────────────────────────────────────────────────────────────────

class FakeBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class FakeThinking:
    type = "thinking"
    thinking = ""


class FakeResp:
    # Opus 5.5 always opens with a thinking block, so the fake does too — a
    # caller reading content[0] as text would fail here the way it would live.
    stop_reason = "end_turn"

    def __init__(self, payload):
        self.content = [FakeThinking(), FakeBlock(payload if isinstance(payload, str)
                                                  else json.dumps(payload))]


class FakeStream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.resp


@pytest.fixture
def fake_api(monkeypatch):
    calls = []

    def install(payload):
        class M:
            def stream(self, **kw):
                calls.append(kw)
                if isinstance(payload, Exception):
                    raise payload
                return FakeStream(FakeResp(payload))
        class C:
            messages = M()
        monkeypatch.setattr(meal_planner, "_client", lambda: C())
        return calls

    return install


def test_repair_meal_returns_the_repaired_meal(fake_api):
    fake_api(dinner(cook_time_minutes=45))
    out = meal_planner.repair_meal(dinner(), "dinner", [finding()])
    assert out["cook_time_minutes"] == 45


def test_repair_meal_rejects_a_changed_id(fake_api):
    """A repair that renumbers the meal would corrupt the leftover links."""
    fake_api(dinner(meal_id="d7"))
    with pytest.raises(MealPlanError, match="changed the meal id"):
        meal_planner.repair_meal(dinner(), "dinner", [finding()])


def test_repair_meal_rejects_a_non_meal_response(fake_api):
    fake_api({"nope": True})
    with pytest.raises(MealPlanError):
        meal_planner.repair_meal(dinner(), "dinner", [finding()])


def test_repair_meal_rejects_bad_json(fake_api):
    fake_api("not json at all")
    with pytest.raises(MealPlanError, match="not valid JSON"):
        meal_planner.repair_meal(dinner(), "dinner", [finding()])


def test_repair_meal_sends_the_problem_and_the_recipe(fake_api):
    calls = fake_api(dinner())
    meal_planner.repair_meal(dinner(), "dinner",
                             [finding(message="the sauce is never used")])
    content = calls[0]["messages"][0]["content"]
    assert "the sauce is never used" in content
    assert "Greek Lemon Chicken" in content
    assert "no mushrooms" in content.lower()


def test_repair_meal_tells_it_to_keep_the_dish(fake_api):
    """The failure mode of a repair prompt is a swap — a different dish that
    doesn't have the problem because it isn't the same meal."""
    fake_api(dinner())
    meal_planner.repair_meal(dinner(), "dinner", [finding()])
    assert "not a different dish" in meal_planner._REPAIR_SYSTEM


def test_leftover_lunch_repair_includes_its_dinner(fake_api):
    calls = fake_api({"id": "l1", "name": "Leftover Bowl"})
    lunch = {"id": "l1", "name": "Leftover Bowl", "source": "leftover",
             "leftover_from_dinner_id": "d1"}
    meal_planner.repair_meal(lunch, "lunch", [finding(meal_id="l1")],
                             linked_dinner=dinner())
    assert "Greek Lemon Chicken" in calls[0]["messages"][0]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# Pre-existing violations
# ─────────────────────────────────────────────────────────────────────────────

def test_repair_works_on_a_plan_that_already_has_a_hard_violation(fake_repair):
    """repair_plan_defects is callable on a stored plan, not only on one fresh
    out of the generation gate. Measuring against 'plan is clean' instead of
    'this repair introduced nothing new' rejected every repair on a real stored
    week whose Monday lunch was a salmon bowl."""
    fake_repair(dinner(cook_time_minutes=45))
    plan = {"dinners": [dinner()],
            "lunches": [{"id": "l1", "name": "Salmon Bowl", "source": "standalone",
                         "ingredients": [{"name": "leftover salmon"}],
                         "instructions": []}]}
    plan, unresolved = meal_planner.repair_plan_defects(plan, [finding()])
    assert plan["dinners"][0]["cook_time_minutes"] == 45
    assert unresolved == []


def test_a_new_violation_is_still_rejected_on_such_a_plan(fake_repair):
    """The pre-existing violation must not become a licence to add more."""
    fake_repair(dinner(ingredients=[{"name": "shiitake mushrooms", "quantity": "1",
                                     "unit": "cup", "pantry_staple": False,
                                     "special": False}]))
    original = dinner()
    plan = {"dinners": [copy.deepcopy(original)],
            "lunches": [{"id": "l1", "name": "Salmon Bowl", "source": "standalone",
                         "ingredients": [{"name": "leftover salmon"}],
                         "instructions": []}]}
    plan, unresolved = meal_planner.repair_plan_defects(plan, [finding()])
    assert plan["dinners"][0] == original
    assert len(unresolved) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Why a defect is still unresolved
# ─────────────────────────────────────────────────────────────────────────────

def test_failed_repair_is_tagged(fake_repair):
    fake_repair(MealPlanError("boom"))
    _, unresolved = meal_planner.repair_plan_defects(plan_with(dinner()), [finding()])
    assert unresolved[0]["repair_outcome"] == "repair_failed"


def test_reverted_repair_is_tagged(fake_repair):
    fake_repair(dinner(ingredients=[{"name": "cremini mushrooms", "quantity": "8",
                                     "unit": "oz", "pantry_staple": False,
                                     "special": False}]))
    _, unresolved = meal_planner.repair_plan_defects(plan_with(dinner()), [finding()])
    assert unresolved[0]["repair_outcome"] == "repair_reverted"


def test_surviving_defect_is_tagged(monkeypatch, fake_repair):
    fake_repair(dinner(cook_time_minutes=45))
    monkeypatch.setattr(plan_critique, "review_meal",
                        lambda *a, **k: [finding(message="still wrong")])
    _, unresolved = meal_planner.repair_plan_defects(
        plan_with(dinner()), [finding()], reproof=True)
    assert unresolved[0]["repair_outcome"] == "survived_repair"


def test_week_level_finding_is_tagged_not_repairable(fake_repair):
    fake_repair(dinner())
    _, unresolved = meal_planner.repair_plan_defects(
        plan_with(dinner()), [finding(meal_id="")])
    assert unresolved[0]["repair_outcome"] == "not_repairable"


def test_tagging_does_not_mutate_the_original_finding(fake_repair):
    """The caller's list must not grow keys behind its back."""
    fake_repair(MealPlanError("boom"))
    original = finding()
    meal_planner.repair_plan_defects(plan_with(dinner()), [original])
    assert "repair_outcome" not in original

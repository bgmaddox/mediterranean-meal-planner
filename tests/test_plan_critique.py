"""
Tests for the two review passes.

No API calls. The contract worth testing is not what Claude says — it's that a
bad or absent answer can never damage a plan: every failure path returns an
empty list, findings are re-validated, and the two passes stay in their lanes
(a defect reviewer that returns 'variety' must not have it acted on).
"""

import json
import sys
from pathlib import Path

import anthropic
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plan_critique as pc  # noqa: E402


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, payload):
        self.content = [FakeBlock(payload if isinstance(payload, str)
                                  else json.dumps(payload))]


class FakeMessages:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        behavior = self._behavior
        if callable(behavior):
            behavior = behavior(kwargs)
        if isinstance(behavior, Exception):
            raise behavior
        return FakeResponse(behavior)


class FakeClient:
    def __init__(self, behavior):
        self.messages = FakeMessages(behavior)


DINNER = {"id": "d1", "name": "Greek Lemon Chicken", "instructions": ["Roast it."]}
LUNCH = {"id": "l1", "name": "Chickpea Salad", "source": "standalone"}
PLAN = {"dinners": [DINNER], "lunches": [LUNCH]}


def defect(**over):
    f = {"meal_id": "d1", "meal_name": "Greek Lemon Chicken",
         "category": "recipe_coherence",
         "message": "reserves half the sauce but never uses the reserved half"}
    f.update(over)
    return f


def judgment(**over):
    f = {"meal_id": "", "meal_name": "", "category": "variety",
         "message": "is four sheet-pan dinners in a row"}
    f.update(over)
    return f


# ─────────────────────────────────────────────────────────────────────────────
# Defect review — one meal at a time
# ─────────────────────────────────────────────────────────────────────────────

def test_defect_findings_become_soft_violations():
    v = pc.review_meal(DINNER, "dinner", client=FakeClient({"findings": [defect()]}))
    assert len(v) == 1
    assert v[0]["severity"] == "soft"
    assert v[0]["code"] == "critique_recipe_coherence"
    assert v[0]["meal_id"] == "d1"


def test_clean_recipe_returns_nothing():
    assert pc.review_meal(DINNER, "dinner", client=FakeClient({"findings": []})) == []


def test_defect_pass_discards_judgment_categories():
    """The two passes have different consequences — a 'variety' finding from the
    defect reviewer must not reach the repair pass."""
    v = pc.review_meal(DINNER, "dinner",
                       client=FakeClient({"findings": [judgment(meal_id="d1")]}))
    assert v == []


def test_defect_review_sees_only_the_one_meal():
    client = FakeClient({"findings": []})
    pc.review_meal(DINNER, "dinner", client=client)
    content = client.messages.calls[0]["messages"][0]["content"]
    assert "Greek Lemon Chicken" in content
    assert "Chickpea Salad" not in content


def test_leftover_lunch_review_includes_its_dinner():
    """A quantity or leftover finding on a lunch has nothing to check against
    without the dinner it came from."""
    client = FakeClient({"findings": []})
    pc.review_meal(LUNCH, "lunch", linked_dinner=DINNER, client=client)
    content = client.messages.calls[0]["messages"][0]["content"]
    assert "dinner_this_is_a_leftover_of" in content
    assert "Greek Lemon Chicken" in content


def test_defect_review_runs_at_high_effort():
    """Its findings get acted on, so it gets the thoroughness."""
    client = FakeClient({"findings": []})
    pc.review_meal(DINNER, "dinner", client=client)
    assert client.messages.calls[0]["output_config"]["effort"] == "high"


# ─────────────────────────────────────────────────────────────────────────────
# Judgment review — the whole week
# ─────────────────────────────────────────────────────────────────────────────

def test_judgment_findings_come_back():
    v = pc.review_week(PLAN, client=FakeClient({"findings": [judgment()]}))
    assert len(v) == 1 and v[0]["code"] == "critique_variety"


def test_judgment_pass_discards_defect_categories():
    v = pc.review_week(PLAN, client=FakeClient({"findings": [defect()]}))
    assert v == []


def test_judgment_findings_are_capped_at_four():
    v = pc.review_week(PLAN, client=FakeClient({"findings": [judgment() for _ in range(10)]}))
    assert len(v) == 4


def test_empty_plan_makes_no_api_call():
    client = FakeClient({"findings": [judgment()]})
    assert pc.review_week({}, client=client) == []
    assert client.messages.calls == []


def test_soft_violations_are_hidden_from_the_reviewer():
    """Showing the reviewer the checker's findings invites it to agree with them
    instead of looking for what the checker cannot see."""
    plan = dict(PLAN, _soft_violations=[{"message": "SECRET-MARKER"}])
    client = FakeClient({"findings": []})
    pc.review_week(plan, client=client)
    assert "SECRET-MARKER" not in client.messages.calls[0]["messages"][0]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# Both passes together
# ─────────────────────────────────────────────────────────────────────────────

def test_critique_plan_reviews_every_meal_and_the_week():
    client = FakeClient({"findings": []})
    pc.critique_plan(PLAN, client=client)
    # 1 dinner + 1 lunch + 1 week-level
    assert len(client.messages.calls) == 3


def test_critique_plan_separates_defects_from_judgments():
    def behavior(kwargs):
        is_week = "Review this week plan" in kwargs["messages"][0]["content"]
        return {"findings": [judgment()]} if is_week else {"findings": [defect()]}

    defects, judgments = pc.critique_plan(PLAN, client=FakeClient(behavior))
    assert all(v["code"] == "critique_recipe_coherence" for v in defects)
    assert all(v["code"] == "critique_variety" for v in judgments)
    assert len(defects) == 2  # one per meal, both echo the same fake finding
    assert len(judgments) == 1


def test_judgment_pass_can_be_skipped():
    client = FakeClient({"findings": []})
    pc.critique_plan(PLAN, client=client, include_judgment=False)
    assert len(client.messages.calls) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Failing open
# ─────────────────────────────────────────────────────────────────────────────

def test_api_error_returns_no_findings():
    err = anthropic.APIError("boom", request=None, body=None)
    assert pc.review_meal(DINNER, "dinner", client=FakeClient(err)) == []
    assert pc.review_week(PLAN, client=FakeClient(err)) == []


def test_malformed_json_returns_no_findings():
    assert pc.review_meal(DINNER, "dinner", client=FakeClient("not json")) == []


def test_missing_findings_key_returns_no_findings():
    assert pc.review_meal(DINNER, "dinner", client=FakeClient({"other": []})) == []


def test_response_with_no_text_block_returns_no_findings():
    class Empty:
        content = []
    class M:
        def create(self, **kw):
            return Empty()
    class C:
        messages = M()
    assert pc.review_meal(DINNER, "dinner", client=C()) == []


def test_one_failing_meal_review_does_not_lose_the_others():
    """A single bad response must not take down the whole pass."""
    def behavior(kwargs):
        if "Chickpea Salad" in kwargs["messages"][0]["content"]:
            return "not json"
        return {"findings": [defect()]}

    defects, _ = pc.critique_plan(PLAN, client=FakeClient(behavior),
                                  include_judgment=False)
    assert len(defects) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Not trusting the reviewer's output shape
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_category_is_discarded():
    assert pc.review_meal(DINNER, "dinner",
                          client=FakeClient({"findings": [defect(category="vibes")]})) == []


def test_empty_message_is_discarded():
    assert pc.review_meal(DINNER, "dinner",
                          client=FakeClient({"findings": [defect(message="  ")]})) == []


def test_non_dict_finding_is_discarded():
    v = pc.review_meal(DINNER, "dinner",
                       client=FakeClient({"findings": ["a string", defect()]}))
    assert len(v) == 1


def test_unknown_meal_id_is_blanked_not_dropped():
    """A hallucinated meal id shouldn't make the UI point at nothing."""
    v = pc.review_meal(DINNER, "dinner",
                       client=FakeClient({"findings": [defect(meal_id="d99")]}))
    assert len(v) == 1 and v[0]["meal_id"] == ""


def test_defect_findings_are_capped_at_six():
    v = pc.review_meal(DINNER, "dinner",
                       client=FakeClient({"findings": [defect() for _ in range(20)]}))
    assert len(v) == 6


def test_categories_do_not_overlap():
    assert not (pc.DEFECT_CATEGORIES & pc.JUDGMENT_CATEGORIES)


def test_defect_prompt_excludes_what_code_already_checks():
    for already_covered in ["mushroom", "sodium", "fish"]:
        assert already_covered in pc._DEFECT_SYSTEM.lower()

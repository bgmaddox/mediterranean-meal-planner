"""
Tests for the advisory critique pass.

No API calls here. The contract worth testing is not what Claude says — it's
that a bad or absent answer from Claude can never damage a plan: every failure
path returns an empty list, and every finding is re-validated before it reaches
the UI.
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
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return FakeResponse(self._behavior)


class FakeClient:
    def __init__(self, behavior):
        self.messages = FakeMessages(behavior)


PLAN = {
    "dinners": [{"id": "d1", "name": "Greek Lemon Chicken"}],
    "lunches": [{"id": "l1", "name": "Chickpea Salad"}],
}


def finding(**over):
    f = {"meal_id": "d1", "meal_name": "Greek Lemon Chicken",
         "category": "recipe_coherence", "message": "never says when to add the lemon"}
    f.update(over)
    return f


def run(behavior, plan=PLAN):
    return pc.critique_week_plan(plan, client=FakeClient(behavior))


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_findings_become_soft_violations():
    v = run({"findings": [finding()]})
    assert len(v) == 1
    assert v[0]["severity"] == "soft"
    assert v[0]["code"] == "critique_recipe_coherence"
    assert v[0]["meal_id"] == "d1"


def test_empty_findings_is_a_valid_answer():
    assert run({"findings": []}) == []


def test_week_level_finding_keeps_an_empty_meal_id():
    v = run({"findings": [finding(meal_id="", meal_name="", category="variety",
                                  message="is four roasted sheet-pan dinners in a row")]})
    assert v[0]["meal_id"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# Failing open — the whole point of the module
# ─────────────────────────────────────────────────────────────────────────────

def test_api_error_returns_no_findings():
    err = anthropic.APIError("boom", request=None, body=None)
    assert run(err) == []


def test_malformed_json_returns_no_findings():
    assert run("this is not json") == []


def test_missing_findings_key_returns_no_findings():
    assert run({"something_else": []}) == []


def test_response_with_no_text_block_returns_no_findings():
    class Empty:
        content = []
    class M:
        def create(self, **kw):
            return Empty()
    class C:
        messages = M()
    assert pc.critique_week_plan(PLAN, client=C()) == []


def test_empty_plan_makes_no_api_call():
    client = FakeClient({"findings": [finding()]})
    assert pc.critique_week_plan({}, client=client) == []
    assert client.messages.calls == []


# ─────────────────────────────────────────────────────────────────────────────
# Not trusting the judge's output shape
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_category_is_discarded():
    assert run({"findings": [finding(category="vibes")]}) == []


def test_empty_message_is_discarded():
    assert run({"findings": [finding(message="   ")]}) == []


def test_non_dict_finding_is_discarded():
    assert run({"findings": ["just a string", finding()]}) == 1 * [] or True
    assert len(run({"findings": ["just a string", finding()]})) == 1


def test_unknown_meal_id_is_blanked_not_dropped():
    """A hallucinated meal id shouldn't make the UI point at nothing."""
    v = run({"findings": [finding(meal_id="d99")]})
    assert len(v) == 1 and v[0]["meal_id"] == ""


def test_findings_are_capped_at_six():
    v = run({"findings": [finding() for _ in range(20)]})
    assert len(v) == 6


# ─────────────────────────────────────────────────────────────────────────────
# Request shape
# ─────────────────────────────────────────────────────────────────────────────

def test_soft_violations_are_hidden_from_the_judge():
    """Showing the judge the checker's findings invites it to agree with them
    instead of looking for what the checker cannot see."""
    plan = dict(PLAN, _soft_violations=[{"message": "SECRET-MARKER"}])
    client = FakeClient({"findings": []})
    pc.critique_week_plan(plan, client=client)
    assert "SECRET-MARKER" not in client.messages.calls[0]["messages"][0]["content"]


def test_request_asks_for_the_structured_schema():
    client = FakeClient({"findings": []})
    pc.critique_week_plan(PLAN, client=client)
    call = client.messages.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["model"] == pc.CRITIQUE_MODEL


def test_system_prompt_tells_the_judge_what_is_already_checked():
    """Without this the judge spends its findings restating the rule checks."""
    for already_covered in ["mushroom", "sodium", "fish"]:
        assert already_covered in pc._SYSTEM.lower()

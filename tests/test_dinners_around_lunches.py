"""
Planning dinners for a week whose lunches are already planned (a lunch week).

The order /plan-lunches asks for is dinners first, then lunches. These pin the
other order: with a lunch week saved, /plan-week plans dinners only, the prompt
says so, the checks hold the plan to it, and the save keeps the lunch week.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import cli  # noqa: E402
import data_store  # noqa: E402
from meal_planner import MealPlanError, _validate_plan  # noqa: E402
from system_prompt import build_system_prompt  # noqa: E402
from test_cli import CLEAN, sandbox, stored_week, write  # noqa: E402,F401
from test_lunch_week import WEEK, good_week, lunch_file  # noqa: E402


def dinners_only() -> dict:
    """The clean stored week as a dinners-only plan."""
    plan = json.loads(json.dumps(stored_week(CLEAN)))
    plan["lunches"] = []
    for d in plan["dinners"]:
        d["generates_lunch"] = False
        d["lunch_scaling_instructions"] = None
    return plan


def save_lunch_week(tmp_path, notes=None) -> None:
    plan = good_week()
    for l in plan["lunches"]:
        l["cost_estimate"] = {"total_ingredient_cost_usd": 6.5, "cost_per_serving_usd": 2.0}
    plan["sunday_prep_list"] = [{"task": "Cook the lentil soup", "yields_for": ["Lentil Soup"],
                                 "storage": "Fridge, 5 days"}]
    argv = ["lunch-save", lunch_file(tmp_path, plan), "--offline", "--week-start", WEEK]
    if notes:
        argv += ["--notes", notes]
    assert cli.main(argv) == 0


# ── the prompt ───────────────────────────────────────────────────────────────

def test_prompt_asks_for_dinners_only_and_names_the_lunches():
    lunches = good_week()["lunches"]
    prompt = build_system_prompt(saved_lunches=lunches)
    assert "already planned" in prompt and "Plan dinners only" in prompt
    for l in lunches:
        assert l["name"] in prompt
    assert '"lunches": []' in prompt
    assert "Mix of standalone recipes and dinner leftovers" not in prompt
    assert "1–3 dinner tasks" in prompt


def test_prompt_without_saved_lunches_is_unchanged_in_shape():
    prompt = build_system_prompt()
    assert "Mix of standalone recipes and dinner leftovers" in prompt
    assert "`generates_lunch: true` dinners must include" in prompt
    assert "Sunday Prep — 3–5 tasks" in prompt


# ── the validator ────────────────────────────────────────────────────────────

def test_dinners_only_rejects_lunches_and_leftover_scaling():
    plan = dinners_only()
    days = len(plan["dinners"])
    _validate_plan(plan, days, require_kid_adaptation=False, dinners_only=True)
    with pytest.raises(MealPlanError, match="already planned"):
        _validate_plan({**plan, "lunches": stored_week(CLEAN)["lunches"]}, days,
                       require_kid_adaptation=False, dinners_only=True)
    plan["dinners"][0]["generates_lunch"] = True
    with pytest.raises(MealPlanError, match="leftover lunches"):
        _validate_plan(plan, days, require_kid_adaptation=False, dinners_only=True)


def test_a_whole_week_plan_still_needs_its_lunches():
    with pytest.raises(MealPlanError, match="no lunches"):
        _validate_plan(dinners_only(), len(dinners_only()["dinners"]), require_kid_adaptation=False)


# ── the session commands ─────────────────────────────────────────────────────

def test_context_switches_to_dinners_only_when_the_week_has_a_lunch_week(sandbox, capsys):
    save_lunch_week(sandbox)
    capsys.readouterr()
    assert cli.main(["context", "--offline", "--week-start", WEEK]) == 0
    out = capsys.readouterr().out
    assert "Plan dinners only" in out and "NO lunches" in out and "Lentil Soup" in out


def test_context_for_a_week_without_a_lunch_week_is_the_normal_prompt(sandbox, capsys):
    assert cli.main(["context", "--offline", "--week-start", WEEK]) == 0
    out = capsys.readouterr().out
    assert "Plan dinners only" not in out and "5 dinners + 5 lunches" in out


def test_check_passes_a_dinners_only_plan_around_a_lunch_week(sandbox, capsys):
    save_lunch_week(sandbox)
    capsys.readouterr()
    rc = cli.main(["check", write(sandbox, "d.json", dinners_only()), "--offline", "--week-start", WEEK])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "dinners only" in out
    # The lunches are not compared with themselves as last week's lunches.
    assert "lunch_repeat_from_history" not in out


def test_check_without_a_lunch_week_still_wants_lunches(sandbox, capsys):
    rc = cli.main(["check", write(sandbox, "d.json", dinners_only()), "--offline", "--week-start", WEEK])
    assert rc == 1 and "no lunches" in capsys.readouterr().out


def test_save_keeps_the_lunch_week_its_prep_and_its_notes(sandbox):
    notes = write(sandbox, "n.json", {"findings": [
        {"meal_id": "l2", "meal_name": "x", "category": "holds_poorly", "message": "wilts by Friday"}]})
    save_lunch_week(sandbox, notes)
    before = data_store.load_history()[0]["plan"]
    lunch_cost = sum((l.get("cost_estimate") or {}).get("total_ingredient_cost_usd") or 0
                     for l in before["lunches"])

    plan = dinners_only()
    assert cli.main(["save", write(sandbox, "d.json", plan), "--offline", "--week-start", WEEK]) == 0
    history = data_store.load_history()
    assert [e["week_start"] for e in history].count(WEEK) == 1
    after = history[0]["plan"]
    assert [d["name"] for d in after["dinners"]] == [d["name"] for d in plan["dinners"]]
    assert after["lunches"] == before["lunches"]
    lunch_tasks = [t for t in after["sunday_prep_list"] if t.get("part") == "lunch"]
    assert lunch_tasks == [t for t in before["sunday_prep_list"] if t.get("part") == "lunch"]
    assert len(after["sunday_prep_list"]) == len(plan["sunday_prep_list"]) + len(lunch_tasks)
    assert any(v["code"] == "critique_holds_poorly" for v in after["_soft_violations"])
    assert after["week_summary"]["estimated_weekly_grocery_cost_usd"] == pytest.approx(
        plan["week_summary"]["estimated_weekly_grocery_cost_usd"] + lunch_cost)


def test_resaving_the_merged_week_does_not_double_anything(sandbox):
    save_lunch_week(sandbox)
    cli.main(["save", write(sandbox, "d.json", dinners_only()), "--offline", "--week-start", WEEK])
    first = data_store.load_history()[0]["plan"]
    cli.main(["save", write(sandbox, "d.json", dinners_only()), "--offline", "--week-start", WEEK])
    again = data_store.load_history()[0]["plan"]
    assert again["lunches"] == first["lunches"]
    assert again["sunday_prep_list"] == first["sunday_prep_list"]
    assert len(again["_soft_violations"]) == len(first["_soft_violations"])


def test_save_refuses_dinners_that_still_make_leftovers(sandbox):
    save_lunch_week(sandbox)
    before = data_store.load_history()
    plan = dinners_only()
    plan["dinners"][0]["generates_lunch"] = True
    assert cli.main(["save", write(sandbox, "d.json", plan), "--offline", "--week-start", WEEK]) == 1
    assert data_store.load_history() == before


def test_a_whole_week_plan_is_still_refused_over_a_lunch_week(sandbox):
    save_lunch_week(sandbox)
    whole = write(sandbox, "w.json", stored_week(CLEAN))
    assert cli.main(["save", whole, "--offline", "--week-start", WEEK]) == 1


def test_review_packets_proofread_dinners_and_review_the_whole_week(sandbox):
    save_lunch_week(sandbox)
    out = sandbox / "packets"
    plan = dinners_only()
    assert cli.main(["review-packets", write(sandbox, "d.json", plan), "--out", str(out),
                     "--week-start", WEEK]) == 0
    stems = {p.stem for p in out.glob("*.md")}
    assert stems == {str(d["id"]) for d in plan["dinners"]} | {"week", "walkthrough", "shopping"}
    assert "Lentil Soup" in (out / "walkthrough.md").read_text()

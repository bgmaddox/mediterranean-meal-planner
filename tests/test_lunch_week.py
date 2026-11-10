"""
Lunch weeks: a week of office lunches planned on their own, as Sunday batch
recipes (lunch_week.py), and the checker rules that go with them.

What these pin:
- the fish rule is now "never microwaved", not "never": cold fish passes, and
  anything not explicitly cold is still rejected;
- a batch cannot be eaten after its own fridge life, and fish never past day 3;
- the structural checks reject the ways a batch plan can fail to add up;
- the lunch prompt reuses the dinner prompt's rule sections rather than a copy.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constraint_check as cc  # noqa: E402
import lunch_week as lw  # noqa: E402
import system_prompt as sp  # noqa: E402


def batch(lunch_id="l1", name="Lentil Soup", days=("Mon", "Tue", "Wed"), keeps=5,
          reheat=lw.REHEAT_MICROWAVE, ingredients=("green lentils",), **extra):
    lunch = {
        "id": lunch_id, "name": name, "source": "standalone",
        "leftover_from_dinner_id": None,
        "days": list(days), "servings": len(days), "keeps_days": keeps, "reheat": reheat,
        "ingredients": [{"name": i, "pantry_staple": True} for i in ingredients],
        "instructions": ["Simmer the lentils.", "Cool and portion."],
        "pack_instructions": "One container per day. Microwave 2 minutes.",
        "nutrition_estimate": {"calories_per_adult_serving": 500, "sodium_mg": 500},
    }
    lunch.update(extra)
    return lunch


def week(*lunches):
    return {"lunches": list(lunches)}


def good_week():
    return week(batch(),
                batch("l2", "Farro and Roasted Vegetable Salad", days=("Thu", "Fri"),
                      reheat=lw.REHEAT_COLD, ingredients=("farro",)))


def codes(violations):
    return {v["code"] for v in violations}


# ─────────────────────────────────────────────────────────────────────────────
# Days
# ─────────────────────────────────────────────────────────────────────────────

def test_five_days_over_two_recipes_is_three_then_two():
    assert lw.split_days(list(lw.WEEKDAYS), 2) == [["Mon", "Tue", "Wed"], ["Thu", "Fri"]]


def test_split_sorts_days_and_never_makes_an_empty_block():
    assert lw.split_days(["Thu", "Mon"], 2) == [["Mon"], ["Thu"]]
    assert lw.split_days(["Mon", "Tue"], 5) == [["Mon"], ["Tue"]]


@pytest.mark.parametrize("day,number", [("Mon", 1), ("fri", 5), ("Wednesday", 3), ("someday", None)])
def test_day_number_counts_from_the_sunday_cook(day, number):
    assert lw.day_number(day) == number


# ─────────────────────────────────────────────────────────────────────────────
# Fish: never microwaved, never past day 3
# ─────────────────────────────────────────────────────────────────────────────

def test_cold_fish_lunch_passes():
    lunch = batch(name="Tuna and White Bean Salad", ingredients=("canned tuna",),
                  reheat=lw.REHEAT_COLD, keeps=3)
    assert cc._check_lunch_fish(lunch) == []


def test_microwaved_fish_lunch_is_still_hard():
    lunch = batch(name="Salmon Rice Bowl", ingredients=("salmon",), keeps=3)
    assert codes(cc._check_lunch_fish(lunch)) == {"fish_in_lunch"}


@pytest.mark.parametrize("reheat", [None, "", "room temperature", "microwave or cold"])
def test_fish_lunch_that_does_not_say_cold_is_treated_as_heated(reheat):
    lunch = batch(ingredients=("salmon",), reheat=reheat)
    assert codes(cc._check_lunch_fish(lunch)) == {"fish_in_lunch"}


def test_cold_fish_cannot_be_the_late_week_batch():
    lunch = batch(ingredients=("salmon",), reheat=lw.REHEAT_COLD, days=("Thu", "Fri"), keeps=5)
    found = cc._check_lunch_fish(lunch)
    assert codes(found) == {"fish_lunch_kept_too_long"}
    assert found[0]["severity"] == "hard"


def test_cold_leftover_fish_without_days_passes():
    """A dinner week's leftover salmon eaten cold the next day has no `days`."""
    lunch = {"id": "l1", "name": "Cold Salmon Bowl", "source": "leftover",
             "reheat": lw.REHEAT_COLD, "ingredients": [{"name": "leftover salmon"}]}
    assert cc._check_lunch_fish(lunch) == []


# ─────────────────────────────────────────────────────────────────────────────
# Fridge life
# ─────────────────────────────────────────────────────────────────────────────

def test_batch_eaten_after_its_fridge_life_is_hard():
    p = week(batch(days=("Thu", "Fri"), keeps=4))
    found = [v for v in cc.check_week_plan(p) if v["code"] == "lunch_outlives_fridge_life"]
    assert [v["severity"] for v in found] == ["hard"]


def test_batch_that_keeps_through_its_last_day_passes():
    assert "lunch_outlives_fridge_life" not in codes(cc.check_week_plan(good_week()))


def test_dinner_week_lunches_have_no_fridge_life_to_check():
    lunch = {"id": "l1", "name": "Grain Bowl", "source": "standalone", "ingredients": []}
    assert cc._check_lunch_fridge_life(lunch) == []


# ─────────────────────────────────────────────────────────────────────────────
# Sameness
# ─────────────────────────────────────────────────────────────────────────────

def test_lunch_repeating_a_recent_lunch_is_flagged_soft():
    history = [{"week_start": "2026-08-29", "meal_names": [], "plan": {"lunches": [
        {"name": "Chickpea, Cucumber and Herb Quinoa Salad", "source": "standalone"}]}}]
    p = week(batch(name="Chickpea, Cucumber and Quinoa Salad"))
    found = [v for v in cc.check_week_plan(p, history=history)
             if v["code"] == "lunch_repeat_from_history"]
    assert [v["severity"] for v in found] == ["soft"]


def test_leftover_lunches_are_not_compared():
    history = [{"week_start": "2026-08-29", "meal_names": [], "plan": {"lunches": [
        {"name": "Lentil Soup", "source": "leftover"}]}}]
    assert "lunch_repeat_from_history" not in codes(cc.check_week_plan(week(batch()), history=history))


def test_two_near_identical_batches_in_one_week_are_flagged():
    p = week(batch(), batch("l2", "Lentil Soup ", days=("Thu", "Fri")))
    assert "lunch_repeat_within_week" in codes(cc.check_week_plan(p))


# ─────────────────────────────────────────────────────────────────────────────
# Structure
# ─────────────────────────────────────────────────────────────────────────────

def test_a_well_formed_lunch_week_has_no_problems():
    assert lw.lunch_plan_problems(good_week(), days=list(lw.WEEKDAYS)) == []
    assert not cc.hard_violations(cc.check_week_plan(good_week()))


def test_a_lunch_only_plan_needs_no_dinners_to_be_checked():
    """check_week_plan must not assume dinners exist."""
    assert isinstance(cc.check_week_plan(good_week()), list)


@pytest.mark.parametrize("change,expected", [
    ({"instructions": []}, "no instructions"),
    ({"pack_instructions": ""}, "no pack_instructions"),
    ({"reheat": "toaster oven"}, "reheat"),
    ({"servings": 1}, "expected 3"),
    ({"keeps_days": None}, "keeps_days"),
    ({"days": []}, "no days"),
    ({"days": ["Mon", "Funday", "Wed"]}, "unknown day"),
    ({"nutrition_estimate": None}, "nutrition_estimate"),
])
def test_malformed_batches_are_reported(change, expected):
    p = week(batch(**change), batch("l2", "Farro Salad", days=("Thu", "Fri")))
    assert any(expected in problem for problem in lw.lunch_plan_problems(p))


def test_two_adults_need_two_portions_a_day():
    p = week(batch(servings=3))
    assert any("expected 6" in problem for problem in lw.lunch_plan_problems(p, adults=2))


def test_a_day_cannot_be_claimed_twice():
    p = week(batch(), batch("l2", "Farro Salad", days=("Wed", "Thu")))
    assert any("both claim Wed" in problem for problem in lw.lunch_plan_problems(p))


def test_requested_days_must_all_be_covered():
    p = week(batch())
    problems = lw.lunch_plan_problems(p, days=list(lw.WEEKDAYS))
    assert "No lunch covers Thu, Fri." in problems


def test_duplicate_ids_are_reported():
    p = week(batch(), batch("l1", "Farro Salad", days=("Thu", "Fri")))
    assert any("more than once" in problem for problem in lw.lunch_plan_problems(p))


def test_kept_leftover_lunches_are_not_held_to_the_batch_rules():
    leftover = {"id": "l3", "name": "Chicken Bowl (leftover)", "source": "leftover",
                "leftover_from_dinner_id": "d2", "reheat": "microwave"}
    p = week(batch(), leftover)
    assert lw.lunch_plan_problems(p, days=["Mon", "Tue", "Wed"]) == []


def test_a_plan_with_only_leftovers_is_not_a_lunch_week():
    p = week({"id": "l1", "name": "X", "source": "leftover"})
    assert lw.lunch_plan_problems(p) == ["Plan has no lunch recipes."]


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_lunch_prompt_carries_the_dinner_prompts_rule_sections():
    """Shared by reference, so a rule edited for dinners is edited for lunches."""
    prompt = lw.build_lunch_prompt(current_date="2026-11-10", constraints=["No fennel"])
    dinner = sp.build_system_prompt(current_date="2026-11-10", constraints=["No fennel"])
    for section in (sp.URIC_ACID_AND_CHOLESTEROL_RULES, sp.MEDITERRANEAN_PRINCIPLES,
                    sp.INGREDIENT_EFFICIENCY_SECTION, sp.EQUIPMENT_SECTION,
                    sp._constraints_block(["No fennel"])):
        assert section in prompt
        assert section in dinner


def test_lunch_prompt_assigns_days_and_ids():
    prompt = lw.build_lunch_prompt(current_date="2026-11-10")
    assert "`l1`: Mon, Tue, Wed — 3 portions, must keep at least 3 days" in prompt
    assert "`l2`: Thu, Fri — 2 portions, must keep at least 5 days" in prompt
    assert "limits fish to `l1`" in prompt


def test_lunch_prompt_uses_the_ids_a_session_leaves_free():
    prompt = lw.build_lunch_prompt(current_date="2026-11-10", days=["Mon", "Tue", "Thu"],
                                   ids=["l2", "l5"])
    assert "`l2`: Mon, Tue" in prompt and "`l5`: Thu" in prompt
    with pytest.raises(ValueError):
        lw.build_lunch_prompt(days=["Mon", "Tue"], recipes=2, ids=["l1"])


def test_lunch_prompt_says_no_fish_when_no_recipe_ends_by_day_three():
    prompt = lw.build_lunch_prompt(current_date="2026-11-10", days=["Thu", "Fri"], recipes=1)
    assert "plan no fish lunch" in prompt


def test_lunch_prompt_lists_recent_lunches_and_this_weeks_dinners():
    history = [{"week_start": "2026-08-29", "meal_names": ["Roast Chicken"],
                "plan": {"lunches": [{"name": "Chickpea Quinoa Salad", "source": "standalone"}]}}]
    dinners = [{"name": "Harissa Chicken Traybake",
                "ingredients": [{"name": "Harissa", "special": True}, {"name": "chicken"}]}]
    prompt = lw.build_lunch_prompt(current_date="2026-11-10", history=history, week_dinners=dinners)
    assert "Week of 2026-08-29: Chickpea Quinoa Salad" in prompt
    assert "Harissa Chicken Traybake" in prompt
    assert "Special ingredients the dinners already buy: harissa." in prompt
    assert "Roast Chicken" not in prompt


def test_lunch_schema_keeps_the_dinner_weeks_lunch_fields():
    """A lunch-week lunch must still be a valid lunch to the app."""
    base = set(sp.OUTPUT_SCHEMA["week_plan"]["lunches"][0])
    lunch = lw.lunch_output_schema(["l1", "l2"])["week_plan"]["lunches"][0]
    assert base <= set(lunch)
    assert {"days", "keeps_days", "instructions", "cook_time_minutes"} <= set(lunch)


# ─────────────────────────────────────────────────────────────────────────────
# The session path: a lunch week is saved INTO its week (cli.py, pi_ops.py)
# ─────────────────────────────────────────────────────────────────────────────

import json  # noqa: E402

import cli  # noqa: E402
import data_store  # noqa: E402
import pi_ops  # noqa: E402
from test_cli import CLEAN, sandbox, stored_week, write  # noqa: E402,F401

WEEK = "2026-11-16"


def lunch_file(tmp_path, plan=None, name="lunches.json") -> str:
    plan = plan or {**good_week(), "sunday_prep_list": [
        {"task": "Cook the lentil soup", "yields_for": ["Lentil Soup"], "storage": "Fridge, 5 days"}]}
    return write(tmp_path, name, {"week_plan": plan})


def save_dinner_week(tmp_path) -> dict:
    cli.main(["save", write(tmp_path, "p.json", stored_week(CLEAN)), "--offline", "--week-start", WEEK])
    return data_store.load_history()[0]["plan"]


def test_merge_into_no_week_makes_a_lunch_only_week():
    merged = pi_ops.merge_lunches(None, good_week()["lunches"], [{"task": "Cook"}])
    assert merged["dinners"] == [] and len(merged["lunches"]) == 2
    assert merged["sunday_prep_list"] == [{"task": "Cook", "part": "lunch"}]
    assert "week_summary" in merged


def test_merge_replaces_lunches_and_leaves_dinners_alone():
    plan = stored_week(CLEAN)
    keep = plan["lunches"][0]["id"]
    new = [batch("l8"), batch("l9", "Farro Salad", days=("Thu", "Fri"))]
    merged = pi_ops.merge_lunches(plan, new, [{"task": "Cook"}], [keep])
    assert merged["dinners"] == plan["dinners"]
    assert [l["id"] for l in merged["lunches"]] == [keep, "l8", "l9"]
    assert plan["lunches"] != merged["lunches"]          # the saved plan is not mutated
    assert merged["sunday_prep_list"][:-1] == plan["sunday_prep_list"]


def test_merging_twice_does_not_stack_lunch_prep_tasks():
    plan = stored_week(CLEAN)
    once = pi_ops.merge_lunches(plan, good_week()["lunches"], [{"task": "Cook"}])
    twice = pi_ops.merge_lunches(once, good_week()["lunches"], [{"task": "Cook again"}])
    assert [t["task"] for t in twice["sunday_prep_list"] if t.get("part") == "lunch"] == ["Cook again"]


def test_merge_drops_notes_about_replaced_lunches_only():
    plan = stored_week(CLEAN)
    gone, kept_dinner = plan["lunches"][0]["id"], plan["dinners"][0]["id"]
    plan["_soft_violations"] = [{"code": "x", "meal_id": gone, "message": "old lunch"},
                                {"code": "y", "meal_id": kept_dinner, "message": "dinner"}]
    merged = pi_ops.merge_lunches(plan, good_week()["lunches"], [])
    assert [n["message"] for n in merged["_soft_violations"]] == ["dinner"]


def test_merge_refuses_a_bad_keep_or_an_id_clash():
    plan = stored_week(CLEAN)
    with pytest.raises(ValueError):
        pi_ops.merge_lunches(plan, good_week()["lunches"], [], ["l99"])
    with pytest.raises(ValueError):
        pi_ops.merge_lunches(plan, good_week()["lunches"], [], ["l1"])


def test_lunch_save_creates_a_lunch_only_week_and_queues_it(sandbox):
    assert cli.main(["lunch-save", lunch_file(sandbox), "--offline", "--week-start", WEEK,
                     "--days", "Mon,Tue,Wed,Thu,Fri"]) == 0
    entry = data_store.load_history()[0]
    assert entry["week_start"] == WEEK and entry["meal_names"] == []
    assert [l["name"] for l in entry["plan"]["lunches"]] == ["Lentil Soup", "Farro and Roasted Vegetable Salad"]
    queued = json.loads((sandbox / "outbox" / f"{WEEK}-lunches.json").read_text())
    assert queued["op"] == "import_lunches"
    assert "Lentil Soup" in {r["name"] for r in data_store.load_recipe_library()}


def test_lunch_save_into_a_dinner_week_keeps_its_dinners(sandbox):
    before = save_dinner_week(sandbox)
    assert cli.main(["lunch-save", lunch_file(sandbox), "--offline", "--week-start", WEEK]) == 0
    history = data_store.load_history()
    assert [e["week_start"] for e in history].count(WEEK) == 1
    after = history[0]["plan"]
    assert after["dinners"] == before["dinners"]
    assert {l["id"] for l in after["lunches"]} == {"l1", "l2"}
    assert all(l.get("days") for l in after["lunches"])


def test_lunch_save_refuses_a_plan_that_breaks_a_rule(sandbox):
    bad = week(batch(ingredients=("salmon",)), batch("l2", "Farro Salad", days=("Thu", "Fri")))
    before = data_store.load_history()
    assert cli.main(["lunch-save", lunch_file(sandbox, bad), "--offline", "--week-start", WEEK]) == 1
    assert data_store.load_history() == before
    assert not (sandbox / "outbox").exists()


def test_lunch_save_refuses_a_malformed_plan(sandbox):
    bad = week(batch(instructions=[]))
    assert cli.main(["lunch-save", lunch_file(sandbox, bad), "--offline", "--week-start", WEEK]) == 1


def test_lunch_check_ignores_what_the_dinner_week_already_carried(sandbox, capsys):
    """Measured against the saved week: its dinners' findings are not the lunch plan's."""
    plan = stored_week(1)                      # the week with a hard violation of its own
    data_store.append_to_history(plan, WEEK)
    assert cli.main(["lunch-check", lunch_file(sandbox), "--offline", "--week-start", WEEK]) == 0
    assert "HARD" not in capsys.readouterr().out


def test_lunch_check_does_not_compare_a_week_with_itself(sandbox, capsys):
    cli.main(["lunch-save", lunch_file(sandbox), "--offline", "--week-start", WEEK])
    capsys.readouterr()
    cli.main(["lunch-check", lunch_file(sandbox), "--offline", "--week-start", WEEK])
    assert "lunch_repeat_from_history" not in capsys.readouterr().out


def test_whole_week_save_will_not_silently_drop_lunch_week_recipes(sandbox):
    save_dinner_week(sandbox)
    cli.main(["lunch-save", lunch_file(sandbox), "--offline", "--week-start", WEEK])
    dinners = write(sandbox, "p.json", stored_week(CLEAN))
    assert cli.main(["save", dinners, "--offline", "--week-start", WEEK]) == 1
    assert all(l.get("days") for l in data_store.load_history()[0]["plan"]["lunches"])
    assert cli.main(["save", dinners, "--offline", "--week-start", WEEK, "--replace-lunches"]) == 0


def test_a_saved_lunch_only_week_can_be_exported_and_resaved(sandbox):
    cli.main(["lunch-save", lunch_file(sandbox), "--offline", "--week-start", WEEK])
    out = sandbox / "edit" / "plan.json"
    assert cli.main(["week", WEEK, "--export", str(out), "--offline"]) == 0
    assert cli.main(["save", str(out), "--offline", "--week-start", WEEK]) == 0


def test_lunch_context_reports_the_week_and_free_ids(sandbox, capsys):
    plan = save_dinner_week(sandbox)
    keep = plan["lunches"][0]["id"]
    assert cli.main(["lunch-context", "--offline", "--week-start", WEEK, "--keep", keep,
                     "--days", "Mon,Tue,Wed,Thu"]) == 0
    out = capsys.readouterr().out
    assert plan["dinners"][0]["name"] in out
    assert plan["lunches"][0]["name"] in out.split("Lunches Already Covering Other Days")[1]
    assert f"`{keep}`:" not in out
    assert "saving will REPLACE these lunches" in out


def test_lunch_packets_cover_each_recipe_the_week_and_the_shopping(sandbox):
    import week_review
    out = sandbox / "packets"
    assert cli.main(["lunch-packets", lunch_file(sandbox), "--offline", "--week-start", WEEK,
                     "--out", str(out)]) == 0
    assert sorted(p.stem for p in out.glob("*.md")) == ["l1", "l2", "lunch_week", "shopping"]
    assert week_review.LUNCH_WEEK_SYSTEM in (out / "lunch_week.md").read_text()
    assert "Simmer the lentils." in (out / "l1.md").read_text()


def test_lunch_week_findings_are_accepted_as_notes(sandbox):
    notes = write(sandbox, "n.json", {"findings": [
        {"meal_id": "l2", "meal_name": "x", "category": "holds_poorly", "message": "wilts by Friday"}]})
    cli.main(["lunch-save", lunch_file(sandbox), "--offline", "--week-start", WEEK, "--notes", notes])
    saved = data_store.load_history()[0]["plan"]["_soft_violations"]
    assert any(v["code"] == "critique_holds_poorly" for v in saved)


def test_dinner_prompt_history_skips_lunch_only_weeks(sandbox):
    cli.main(["lunch-save", lunch_file(sandbox), "--offline", "--week-start", WEEK])
    assert WEEK not in [e["week_start"] for e in data_store.history_for_prompt()]


def test_pdf_html_handles_a_lunch_only_week_and_shows_the_method():
    import pdf_render
    import shopping
    plan = pi_ops.merge_lunches(None, good_week()["lunches"], [])
    html = pdf_render.build_html(plan, shopping.build_shopping_list(plan), week_start=WEEK)
    assert "Simmer the lentils." in html and "Sunday batch for Mon, Tue, Wed" in html
    assert ">DINNERS<" not in html

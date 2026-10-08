"""
Tests for cli.py — the session-planning path.

What matters: a plan the app would reject can never be saved from a session,
reviewer notes are validated as strictly as the app's own reviewer output, and
an offline save is queued rather than lost. Nothing here contacts the Pi; every
save runs with --offline against a sandbox copy of data/.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
import data_store  # noqa: E402

PERSONAL = {
    "HISTORY_FILE": "history.json",
    "FAVORITES_FILE": "favorites.json",
    "CONSTRAINTS_FILE": "constraints.json",
    "PREFERENCES_FILE": "preferences.json",
    "LIBRARY_FILE": "recipe_library.json",
    "ANCHOR_RECIPES_USER_FILE": "anchor_recipes_user.json",
}


def stored_week(i: int) -> dict:
    return json.loads((ROOT / "data" / "history.json").read_text())[i]["plan"]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for attr, name in PERSONAL.items():
        src = ROOT / "data" / name
        if src.exists():
            shutil.copy(src, tmp_path / name)
        monkeypatch.setattr(data_store, attr, tmp_path / name)
    monkeypatch.setattr(cli, "MIRROR", tmp_path)
    monkeypatch.setattr(cli, "OUTBOX", tmp_path / "outbox")
    # A save must never reach the real Pi from a test.
    monkeypatch.setattr(cli, "_run_on_pi", lambda script: pytest.fail("contacted the Pi"))
    return tmp_path


def write(tmp_path, name, obj) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(obj))
    return str(path)


# The stored corpus: week 0 is clean, week 1 is the one that shipped a reheated
# salmon lunch (see DECISIONS.md) — a hard violation.
CLEAN, FISH_LUNCH = 0, 1


def test_load_plan_accepts_generation_output_and_bare_plan(tmp_path):
    plan = stored_week(CLEAN)
    assert cli.load_plan(write(tmp_path, "a.json", {"week_plan": plan})) == plan
    assert cli.load_plan(write(tmp_path, "b.json", plan)) == plan


def test_load_plan_reports_bad_json(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(cli.CLIError):
        cli.load_plan(str(tmp_path / "bad.json"))


def test_check_fails_on_hard_violation(sandbox, capsys):
    rc = cli.main(["check", write(sandbox, "p.json", stored_week(FISH_LUNCH)), "--offline"])
    assert rc == 1
    assert "HARD" in capsys.readouterr().out


def test_check_passes_clean_week(sandbox):
    assert cli.main(["check", write(sandbox, "p.json", stored_week(CLEAN)), "--offline"]) == 0


def test_save_refuses_a_plan_the_app_would_reject(sandbox):
    before = data_store.load_history()
    rc = cli.main(["save", write(sandbox, "p.json", stored_week(FISH_LUNCH)),
                   "--offline", "--week-start", "2026-10-12"])
    assert rc == 1
    assert data_store.load_history() == before
    assert not (sandbox / "outbox").exists()


def test_offline_save_queues_and_records_locally(sandbox):
    rc = cli.main(["save", write(sandbox, "p.json", stored_week(CLEAN)),
                   "--offline", "--week-start", "2026-10-12"])
    assert rc == 0
    assert (sandbox / "outbox" / "2026-10-12.json").exists()
    assert data_store.load_history()[0]["week_start"] == "2026-10-12"


def test_saving_the_same_week_twice_replaces_it(sandbox):
    path = write(sandbox, "p.json", stored_week(CLEAN))
    cli.main(["save", path, "--offline", "--week-start", "2026-10-12"])
    count = len(data_store.load_history())
    cli.main(["save", path, "--offline", "--week-start", "2026-10-12"])
    weeks = [e["week_start"] for e in data_store.load_history()]
    assert len(weeks) == count
    assert weeks.count("2026-10-12") == 1


def test_session_notes_are_validated_and_tagged(sandbox):
    plan = stored_week(CLEAN)
    real_id = plan["dinners"][0]["id"]
    notes = write(sandbox, "n.json", {"findings": [
        {"meal_id": real_id, "meal_name": "x", "category": "recipe_coherence", "message": "kept"},
        {"meal_id": "", "meal_name": "", "category": "variety", "message": "a judgment"},
        {"meal_id": real_id, "meal_name": "x", "category": "made_up", "message": "dropped"},
        {"meal_id": "d99", "meal_name": "x", "category": "quantity_mismatch", "message": "unknown id"},
    ]})
    out = cli._session_notes(plan, notes)
    messages = [n["message"] for n in out]
    assert "dropped" not in messages
    assert len(out) == 3
    defect = next(n for n in out if n["message"] == "kept")
    assert defect["repair_outcome"] == "left_in_session"
    judgment = next(n for n in out if n["message"] == "a judgment")
    assert "repair_outcome" not in judgment
    # A hallucinated meal id is kept as a week-level note, as the app does.
    assert next(n for n in out if n["message"] == "unknown id")["meal_id"] == ""


def test_saved_plan_carries_soft_notes_for_the_app(sandbox):
    plan = stored_week(CLEAN)
    notes = write(sandbox, "n.json", [{"meal_id": "", "meal_name": "", "category": "variety",
                                       "message": "leans on soups"}])
    cli.main(["save", write(sandbox, "p.json", plan), "--offline",
              "--week-start", "2026-10-12", "--notes", notes])
    saved = data_store.load_history()[0]["plan"]["_soft_violations"]
    assert any(v["code"] == "critique_variety" for v in saved)
    assert any(not v["code"].startswith("critique_") for v in saved)


def test_review_packets_use_the_apps_rubric(sandbox):
    import plan_critique
    plan = stored_week(CLEAN)
    out = sandbox / "packets"
    cli.main(["review-packets", write(sandbox, "p.json", plan), "--out", str(out)])
    ids = [m["id"] for m in plan["dinners"] + plan["lunches"]]
    assert sorted(p.stem for p in out.glob("*.md")) == sorted(ids + ["week"])
    assert plan_critique._DEFECT_SYSTEM in (out / f"{ids[0]}.md").read_text()

    cli.main(["review-packets", write(sandbox, "p.json", plan), "--out", str(out),
              "--only", ids[0]])
    assert [p.stem for p in out.glob("*.md")] == [ids[0]]


# ─────────────────────────────────────────────────────────────────────────────
# Weekly feedback (pi_ops.apply_feedback) and mid-week export
# ─────────────────────────────────────────────────────────────────────────────

import pi_ops  # noqa: E402


def saved_week(sandbox) -> tuple[str, dict]:
    """Save the clean stored week into the sandbox and return (week_start, plan)."""
    plan = stored_week(CLEAN)
    cli.main(["save", write(sandbox, "p.json", plan), "--offline", "--week-start", "2026-10-12"])
    return "2026-10-12", data_store.load_history()[0]["plan"]


def test_feedback_creates_favorites_including_low_ratings(sandbox):
    week, plan = saved_week(sandbox)
    d1, d2 = plan["dinners"][0], plan["dinners"][1]
    data_store._write(data_store.FAVORITES_FILE, [])
    out = pi_ops.apply_feedback(data_store, {"week_start": week, "meals": [
        {"id": d1["id"], "rating": 5, "tags": ["kids loved it"], "feedback": "make again"},
        {"id": d2["id"], "rating": 1, "feedback": "nobody ate it"},
    ]})
    favs = {f["name"]: f for f in data_store.load_favorites()}
    assert favs[d1["name"]]["rating"] == 5
    assert favs[d2["name"]]["rating"] == 1          # a bad rating is still recorded
    assert favs[d1["name"]]["feedback"] == f"{week}: make again"
    assert favs[d1["name"]]["last_made"] == week    # the week it was made, not today
    assert sorted(out["favorites_added"]) == sorted([d1["name"], d2["name"]])


def test_feedback_updates_an_existing_favorite_and_keeps_old_notes(sandbox):
    week, plan = saved_week(sandbox)
    d1 = plan["dinners"][0]
    data_store._write(data_store.FAVORITES_FILE, [])
    pi_ops.apply_feedback(data_store, {"week_start": week, "meals": [
        {"id": d1["id"], "rating": 3, "tags": ["quick"], "feedback": "fine"}]})
    pi_ops.apply_feedback(data_store, {"week_start": week, "meals": [
        {"id": d1["id"], "rating": 4, "tags": ["quick", "kids loved it"], "feedback": "better"}]})
    fav = next(f for f in data_store.load_favorites() if f["name"] == d1["name"])
    assert fav["rating"] == 4
    assert fav["tags"] == ["quick", "kids loved it"]
    assert fav["feedback"] == f"{week}: fine | {week}: better"
    assert len(data_store.load_favorites()) == 1


def test_feedback_adds_constraints_without_duplicates(sandbox):
    week, _ = saved_week(sandbox)
    data_store._write(data_store.CONSTRAINTS_FILE, [])
    out = pi_ops.apply_feedback(data_store, {"week_start": week, "meals": [],
                                             "constraints": ["No fennel — disliked", "no fennel — disliked", ""]})
    assert out["constraints_added"] == ["No fennel — disliked"]
    assert len(data_store.load_constraints()) == 1


def test_feedback_rejects_unknown_week_or_meal(sandbox):
    week, _ = saved_week(sandbox)
    with pytest.raises(ValueError):
        pi_ops.apply_feedback(data_store, {"week_start": "1999-01-04", "meals": []})
    with pytest.raises(ValueError):
        pi_ops.apply_feedback(data_store, {"week_start": week, "meals": [{"id": "d99", "rating": 3}]})


def test_cli_feedback_validates_before_writing(sandbox):
    week, _ = saved_week(sandbox)
    before = data_store.load_favorites()
    bad = write(sandbox, "fb.json", {"week_start": week, "meals": [{"id": "d99", "rating": 3}]})
    assert cli.main(["feedback", bad, "--offline"]) == 2
    assert data_store.load_favorites() == before


def test_week_export_round_trips_and_carries_review_notes(sandbox):
    week, plan = saved_week(sandbox)
    plan["_soft_violations"] = [{"code": "critique_variety", "severity": "soft", "meal_id": "",
                                 "meal_name": "", "message": "leans on soups"}]
    data_store.update_history_plan(plan, week)
    out = sandbox / "edit" / "plan.json"
    assert cli.main(["week", week, "--export", str(out), "--offline"]) == 0
    exported = cli.load_plan(str(out))
    assert "_soft_violations" not in exported
    assert exported["dinners"] == plan["dinners"]
    notes = json.loads((out.parent / "notes.json").read_text())["findings"]
    assert notes == [{"meal_id": "", "meal_name": "", "category": "variety", "message": "leans on soups"}]
    # Re-saving the edited week replaces it rather than adding one.
    count = len(data_store.load_history())
    cli.main(["save", str(out), "--offline", "--week-start", week, "--notes", str(out.parent / "notes.json")])
    assert len(data_store.load_history()) == count


def test_mid_week_edit_may_drop_a_dinner_but_new_plans_may_not(sandbox):
    plan = stored_week(CLEAN)
    # Drop a dinner nothing depends on, as "eating out Wednesday" would.
    linked = {l.get("leftover_from_dinner_id") for l in plan["lunches"]}
    victim = next(d for d in plan["dinners"] if d["id"] not in linked)
    plan["dinners"] = [d for d in plan["dinners"] if d is not victim]
    path = write(sandbox, "p.json", plan)
    n = len(plan["lunches"])
    assert cli.main(["check", path, "--offline"]) == 0                    # saved-week edit
    assert cli.main(["check", path, "--offline", "--days", str(n)]) == 1  # new plan: strict

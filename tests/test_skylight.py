"""
Tests for dinner days and the Skylight calendar sync — no network.

What matters: two dinners can never share a day; a day name lands on the right
date whatever weekday the week was saved on; a sync is idempotent, moves a meal
rather than duplicating it, never touches meals it did not create, and keeps
what it did if it fails half-way; the login handles Skylight's emailed code;
and a rotated refresh token is written back, since the old one stops working.
"""

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
import pi_ops  # noqa: E402
import skylight  # noqa: E402
import skylight_sync as ss  # noqa: E402
from skylight import Response  # noqa: E402

from test_cli import sandbox, stored_week  # noqa: E402,F401


def dinner(i, day=None, name=None):
    d = {"id": f"d{i}", "name": name or f"Dinner {i}", "cook_time_minutes": 30,
         "primary_equipment": "oven", "ingredients": [{"name": "salmon", "quantity": "1", "unit": "lb"}],
         "instructions": ["Roast it."], "kid_adaptation": "Plain."}
    if day:
        d["day"] = day
    return d


def batch_lunch(i, days):
    return {"id": f"l{i}", "name": f"Lunch {i}", "days": days, "keeps_days": 4,
            "reheat": "microwave", "ingredients": [], "instructions": ["Cook."]}


# ── days ────────────────────────────────────────────────────────────────────

def test_day_date_counts_from_week_start():
    assert ss.day_date("2026-11-16", "Mon") == "2026-11-16"   # a Monday
    assert ss.day_date("2026-11-16", "Sun") == "2026-11-22"
    assert ss.day_date("2026-08-29", "Sat") == "2026-08-29"   # saved on a Saturday
    assert ss.day_date("2026-08-29", "Mon") == "2026-08-31"


def test_two_dinners_cannot_share_a_day():
    plan = {"dinners": [dinner(1, "Mon"), dinner(2, "Mon"), dinner(3, "Funday")]}
    problems = pi_ops.day_problems(plan)
    assert any("both on Mon" in p for p in problems)
    assert any("Funday" in p for p in problems)
    assert pi_ops.day_problems({"dinners": [dinner(1), dinner(2, "Tue")]}) == []


def test_assign_days_swaps_in_one_call_and_clears():
    plan = {"dinners": [dinner(1, "Mon"), dinner(2, "Tue")]}
    out = pi_ops.assign_days(plan, {"d1": "Tue", "d2": "Mon"})
    assert [d["day"] for d in out["dinners"]] == ["Tue", "Mon"]
    assert plan["dinners"][0]["day"] == "Mon"            # pure
    cleared = pi_ops.assign_days(plan, {"d1": None})
    assert "day" not in cleared["dinners"][0]
    with pytest.raises(ValueError, match="both on Tue"):
        pi_ops.assign_days(plan, {"d1": "Tue"})
    with pytest.raises(ValueError, match="No dinner d9"):
        pi_ops.assign_days(plan, {"d9": "Mon"})


def test_save_refuses_a_plan_with_clashing_days(sandbox):
    plan = stored_week(0)
    for d in plan["dinners"]:
        d["day"] = "Wed"
    structural, _, _ = cli.run_checks(plan)
    assert any("both on Wed" in s for s in structural)


def test_set_days_offline_writes_the_saved_week(sandbox, capsys):
    import data_store
    week = data_store.load_history()[0]["week_start"]
    ids = [d["id"] for d in data_store.load_history()[0]["plan"]["dinners"]]
    assert cli.main(["set-days", week, f"{ids[0]}=thursday", f"{ids[1]}=Mon", "--offline"]) == 0
    saved = data_store.load_history()[0]["plan"]["dinners"]
    assert saved[0]["day"] == "Thu" and saved[1]["day"] == "Mon"
    assert cli.main(["set-days", week, f"{ids[0]}=Mon", "--offline"]) != 0   # clash with ids[1]


# ── what goes on the calendar ───────────────────────────────────────────────

def test_desired_dinners_with_days_and_batch_lunches_per_day():
    plan = {"dinners": [dinner(1, "Tue"), dinner(2)],
            "lunches": [batch_lunch(1, ["Mon", "Tue"]),
                        {"id": "l2", "name": "Leftover", "source": "leftover"}]}
    items, skipped = ss.desired(plan, "2026-11-16")
    assert [(i["key"], i["category"], i["date"]) for i in items] == [
        ("d1", "Dinner", "2026-11-17"),
        ("l1:Mon", "Lunch", "2026-11-16"), ("l1:Tue", "Lunch", "2026-11-17")]
    assert any("d2" in s and "no day" in s for s in skipped)
    assert any("leftover" in s for s in skipped)
    assert "Ingredients" in items[0]["description"] and "1. Roast it." in items[0]["description"]


def test_plan_changes_create_move_edit_delete():
    want, _ = ss.desired({"dinners": [dinner(1, "Mon"), dinner(2, "Wed", "Renamed")]}, "2026-11-16")
    by_key = {i["key"]: i for i in want}
    synced = {
        "d1": ss._record("s1", by_key["d1"]),                                  # unchanged
        "d2": {**ss._record("s2", {**by_key["d2"], "summary": "Old"}), "date": "2026-11-17"},
        "d3": ss._record("s3", {**by_key["d1"], "key": "d3"}),                 # dropped from plan
    }
    live = {"s1": "2026-11-16", "s2": "2026-11-17", "s3": "2026-11-19", "other": "2026-11-18"}
    actions = ss.plan_changes(want, synced, live)
    ops = {(a["op"], (a.get("item") or {}).get("key") or a.get("key")) for a in actions}
    assert ops == {("update", "d2"), ("delete", "d3")}
    upd = next(a for a in actions if a["op"] == "update")
    assert upd["at"] == "2026-11-17" and upd["fields"]["date"] == "2026-11-18"
    assert upd["fields"]["summary"] == "Renamed"
    # Deleted on the device: created again, not patched.
    assert ss.plan_changes(want, synced, {"s2": "2026-11-17"})[0] == {"op": "create", "item": by_key["d1"]}


# ── sync against a fake Skylight ────────────────────────────────────────────

class FakeSkylight:
    def __init__(self, fail_on=None):
        self.sittings_by_id = {"theirs": {"id": "theirs", "date": "2026-11-17", "summary": "Pizza night"}}
        self.calls = []
        self.fail_on = fail_on
        self.n = 0

    def frames(self):
        return [{"id": "f1", "name": "Kitchen"}]

    def meal_categories(self, frame):
        return [{"id": "c1", "label": "Breakfast"}, {"id": "c2", "label": "Lunch"},
                {"id": "c3", "label": "Dinner"}]

    def sittings(self, frame, lo, hi):
        return [{"id": s["id"], "instances": [s["date"]]} for s in self.sittings_by_id.values()
                if lo <= s["date"] <= hi]

    def _call(self, *a):
        self.calls.append(a)
        if self.fail_on and len(self.calls) == self.fail_on:
            raise skylight.SkylightError("boom")

    def create_sitting(self, frame, cat, day, summary, description):
        self._call("create", day, summary)
        self.n += 1
        sid = f"s{self.n}"
        self.sittings_by_id[sid] = {"id": sid, "date": day, "summary": summary, "cat": cat}
        return {"id": sid}

    def update_sitting(self, frame, sid, at, fields):
        self._call("update", sid, at, fields)
        assert self.sittings_by_id[sid]["date"] == at
        self.sittings_by_id[sid].update(fields)

    def delete_sitting(self, frame, sid, at):
        self._call("delete", sid, at)
        del self.sittings_by_id[sid]


def test_sync_is_idempotent_moves_and_leaves_other_meals_alone(tmp_path):
    fake, state = FakeSkylight(), ss.State(tmp_path / "state.json")
    plan = {"dinners": [dinner(1, "Mon"), dinner(2, "Tue")], "lunches": [batch_lunch(1, ["Mon"])]}
    assert ss.sync(fake, state, plan, "2026-11-16", echo=lambda *_: None) == 3
    assert {s["cat"] for s in fake.sittings_by_id.values() if "cat" in s} == {"c2", "c3"}
    fake.calls.clear()
    assert ss.sync(fake, ss.State(tmp_path / "state.json"), plan, "2026-11-16", echo=lambda *_: None) == 0
    assert fake.calls == []

    moved = pi_ops.assign_days(plan, {"d1": "Thu"})
    ss.sync(fake, ss.State(tmp_path / "state.json"), moved, "2026-11-16", echo=lambda *_: None)
    assert fake.calls == [("update", "s1", "2026-11-16", {"date": "2026-11-19"})]

    fake.calls.clear()
    ss.sync(fake, ss.State(tmp_path / "state.json"), {"dinners": [], "lunches": []}, "2026-11-16",
            echo=lambda *_: None)
    assert sorted(c[0] for c in fake.calls) == ["delete"] * 3
    assert list(fake.sittings_by_id) == ["theirs"]          # never ours to remove


def test_dry_run_changes_nothing(tmp_path):
    fake = FakeSkylight()
    n = ss.sync(fake, ss.State(tmp_path / "s.json"), {"dinners": [dinner(1, "Mon")]}, "2026-11-16",
                dry_run=True, echo=lambda *_: None)
    assert n == 1 and fake.calls == [] and not (tmp_path / "s.json").exists()


def test_a_failed_sync_keeps_what_it_did(tmp_path):
    fake = FakeSkylight(fail_on=2)
    plan = {"dinners": [dinner(1, "Mon"), dinner(2, "Tue")]}
    with pytest.raises(skylight.SkylightError):
        ss.sync(fake, ss.State(tmp_path / "state.json"), plan, "2026-11-16", echo=lambda *_: None)
    assert list(ss.State(tmp_path / "state.json").week("2026-11-16")) == ["d1"]
    fake.fail_on = None
    fake.calls.clear()
    ss.sync(fake, ss.State(tmp_path / "state.json"), plan, "2026-11-16", echo=lambda *_: None)
    assert [c[0] for c in fake.calls] == ["create"]          # only the one that failed


def test_missing_category_is_named(tmp_path):
    fake = FakeSkylight()
    fake.meal_categories = lambda frame: [{"id": "c1", "label": "Supper"}]
    with pytest.raises(RuntimeError, match="no meal category named Dinner or Lunch.*Supper"):
        ss.sync(fake, ss.State(tmp_path / "s.json"), {"dinners": [dinner(1, "Mon")]}, "2026-11-16",
                echo=lambda *_: None)


# ── login and tokens ────────────────────────────────────────────────────────

FORM = b'<form><input type="hidden" name="authenticity_token" value="csrf1"></form>'
B = skylight.BASE_URL


def fake_login_server(code="123456", mfa=True):
    calls = []

    def transport(method, url, headers, body):
        path = url.replace(B, "").split("?")[0]
        calls.append((method, path, headers.get("Cookie"), body))
        if path == "/auth/session/new":
            return Response(200, [("Set-Cookie", "_session=a; path=/")], FORM)
        if path == "/auth/session":
            if b"password=right" not in body:
                return Response(302, [("Location", B + "/auth/session/new")])
            return Response(302, [("Location", B + ("/auth/mfa_challenge/start" if mfa else "/auth/session/success")),
                                  ("Set-Cookie", "_session=b; path=/")])
        if path == "/auth/mfa_challenge/start":
            return Response(302, [("Location", "/auth/mfa_challenge/new")])
        if path == "/auth/mfa_challenge/new":
            return Response(200, [], FORM)
        if path == "/auth/mfa_challenge":
            ok = f"code={code}".encode() in body
            return Response(302, [("Location", B + ("/auth/session/success" if ok else "/auth/mfa_challenge/new"))])
        if path == "/oauth/authorize":
            assert "code_challenge=" in url
            return Response(302, [("Location", skylight.REDIRECT_URI + "?code=authcode")])
        if path == "/oauth/token":
            assert b"code=authcode" in body and b"code_verifier=" in body
            return Response(200, [], json.dumps({"access_token": "A", "refresh_token": "R",
                                                 "expires_in": 3600}).encode())
        raise AssertionError(f"unexpected {method} {url}")
    return transport, calls


def test_login_with_emailed_code(capsys):
    transport, calls = fake_login_server()
    codes = iter(["999999", "123 456"])                     # a wrong one first
    tokens = skylight.login("me@example.com", "right", lambda: next(codes), transport)
    assert tokens["access_token"] == "A" and tokens["refresh_token"] == "R"
    assert "rejected that code" in capsys.readouterr().out
    # The session cookie carried through the steps, updated by the password step.
    assert ("GET", "/auth/mfa_challenge/start", "_session=b", None) in calls


def test_login_without_code_and_bad_password():
    transport, _ = fake_login_server(mfa=False)
    assert skylight.login("me", "right", lambda: pytest.fail("asked for a code"), transport)["access_token"] == "A"
    with pytest.raises(skylight.SkylightError, match="rejected the email or password"):
        skylight.login("me", "wrong", lambda: "", transport)


def test_expired_token_refreshes_and_saves_the_rotated_one(tmp_path):
    store = skylight.TokenStore(tmp_path / "token.json")
    store.save({"access_token": "old", "refresh_token": "R1", "expires_at": time.time() - 10})
    seen = []

    def transport(method, url, headers, body):
        if url.endswith("/oauth/token"):
            assert b"refresh_token=R1" in body
            return Response(200, [], json.dumps({"access_token": "new", "refresh_token": "R2",
                                                 "expires_in": 3600}).encode())
        seen.append((headers["Authorization"], headers["skylight-api-version"]))
        return Response(200, [], json.dumps({"data": [{"id": 7, "attributes": {"name": "Kitchen"}}]}).encode())

    frames = skylight.Skylight(store, transport).frames()
    assert frames == [{"name": "Kitchen", "id": "7"}]
    assert seen == [("Bearer new", skylight.API_VERSION)]
    assert store.load()["refresh_token"] == "R2"
    assert (tmp_path / "token.json").stat().st_mode & 0o077 == 0


def test_not_signed_in_says_how_to_fix(tmp_path):
    with pytest.raises(skylight.NotLoggedIn, match="skylight-login"):
        skylight.Skylight(skylight.TokenStore(tmp_path / "none.json")).frames()


def test_sittings_flatten_category_and_instances(tmp_path):
    store = skylight.TokenStore(tmp_path / "t.json")
    store.save({"access_token": "A", "refresh_token": "R", "expires_at": time.time() + 3600})
    doc = {"data": [{"id": 5, "attributes": {"summary": "Soup", "instances": ["2026-11-16"]},
                     "relationships": {"meal_category": {"data": {"id": 3, "type": "meal_category"}}}}]}

    def transport(method, url, headers, body):
        assert "date_min=2026-11-16" in url and "date_max=2026-11-22" in url
        return Response(200, [], json.dumps(doc).encode())

    rows = skylight.Skylight(store, transport).sittings("f1", "2026-11-16", "2026-11-22")
    assert rows[0]["id"] == "5" and rows[0]["meal_category_id"] == "3"
    assert rows[0]["instances"] == ["2026-11-16"]

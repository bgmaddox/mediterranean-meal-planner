"""
skylight_sync.py
----------------
Put a saved week on the Skylight calendar's meal planner, and keep it matching.

The saved week is the source of truth. A sync works out what should be on
Skylight (`desired`), compares it with what this tool put there last time
(`.skylight/state.json`) and with what Skylight actually holds, then creates,
moves, edits or removes meals until they agree. Re-running it is safe; moving a
dinner (`cli.py set-days`) and syncing again moves it on the calendar. A meal
moved by hand on the Skylight is moved back on the next sync, and meals this
tool did not create are never touched.

What goes on the calendar:
  dinners      with a `day`, under the Dinner category, on that day
  batch lunches (a lunch week's `days`), under Lunch, once per day eaten
Not synced: dinners with no day yet (reported), lunches that are a dinner's
leftovers (they have no day of their own), and snacks.

The functions down to `plan_changes` are pure; `sync` does the network calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from pi_ops import DAYS

DINNER, LUNCH = "Dinner", "Lunch"


def day_date(week_start: str, day: str) -> str:
    """The first `day` on or after week_start. A week saved on a Saturday has
    its Monday two days later; one saved on a Monday starts that day."""
    start = date.fromisoformat(week_start)
    return (start + timedelta(days=(DAYS.index(day) - start.weekday()) % 7)).isoformat()


def _qty(i: dict) -> str:
    amount = " ".join(str(x) for x in (i.get("quantity"), i.get("unit")) if x not in (None, ""))
    return f"{amount} {i.get('name', '')}".strip()


def _ingredients(meal: dict) -> list[str]:
    lines = []
    for i in meal.get("ingredients") or []:
        made = f" (from {i['made_in']})" if i.get("made_in") else ""
        lines.append(f"- {_qty(i)}{made}")
    return lines


def _steps(steps) -> list[str]:
    return [f"{n}. {s}" for n, s in enumerate(steps or [], 1)]


def describe_dinner(d: dict) -> str:
    head = " · ".join(str(x) for x in (
        f"{d['cook_time_minutes']} min" if d.get("cook_time_minutes") else None,
        d.get("primary_equipment")) if x)
    parts = [head] if head else []
    if d.get("ingredients"):
        parts.append("Ingredients\n" + "\n".join(_ingredients(d)))
    if d.get("instructions"):
        parts.append("Method\n" + "\n".join(_steps(d["instructions"])))
    if d.get("kid_adaptation"):
        parts.append(f"Kids: {d['kid_adaptation']}")
    return "\n\n".join(parts)


def describe_lunch(l: dict) -> str:
    reheat = "eat cold" if l.get("reheat") == "none (cold)" else l.get("reheat")
    parts = [" · ".join(x for x in (
        f"Cooked Sunday, for {', '.join(l.get('days') or [])}",
        reheat, f"keeps {l['keeps_days']} days" if l.get("keeps_days") else None) if x)]
    if l.get("pack_instructions"):
        parts.append(l["pack_instructions"])
    if l.get("ingredients"):
        parts.append("Ingredients (whole batch)\n" + "\n".join(_ingredients(l)))
    if l.get("instructions"):
        parts.append("Sunday cook\n" + "\n".join(_steps(l["instructions"])))
    return "\n\n".join(parts)


def desired(plan: dict, week_start: str) -> tuple[list[dict], list[str]]:
    """(the meals that belong on Skylight, notes on what was left off).

    Each item: {key, category, date, summary, description}. `key` names the
    meal within its week, so a sync can tell a moved meal from a new one:
    a dinner keeps its key when it changes day; a batch lunch has one per day."""
    items, skipped = [], []
    for d in plan.get("dinners") or []:
        if not d.get("day"):
            skipped.append(f"{d.get('id')} {d.get('name')}: no day yet")
            continue
        items.append({"key": str(d.get("id")), "category": DINNER,
                      "date": day_date(week_start, d["day"]),
                      "summary": d.get("name", ""), "description": describe_dinner(d)})
    leftovers = 0
    for l in plan.get("lunches") or []:
        if not l.get("days"):
            leftovers += 1
            continue
        for day in l["days"]:
            if day not in DAYS:
                skipped.append(f"{l.get('id')} {l.get('name')}: unknown day {day!r}")
                continue
            items.append({"key": f"{l.get('id')}:{day}", "category": LUNCH,
                          "date": day_date(week_start, day),
                          "summary": l.get("name", ""), "description": describe_lunch(l)})
    if leftovers:
        skipped.append(f"{leftovers} leftover lunch(es): eaten from a dinner, not put on the calendar")
    return items, skipped


def fingerprint(item: dict) -> str:
    """What a meal says, apart from its date. A change means re-send the text."""
    raw = json.dumps([item["category"], item["summary"], item["description"]])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def plan_changes(want: list[dict], synced: dict, live: dict) -> list[dict]:
    """The calls that make Skylight match `want`.

    synced: key -> {"id", "date", "category", "fingerprint"} — what this tool put
            there last time for this week.
    live:   sitting id -> its current date on Skylight, for every sitting found in
            the dates involved. A synced meal missing from it was deleted on the
            device, and is created again.

    Returns actions: {"op": "create", "item"} | {"op": "update", "item", "record",
    "at", "fields"} | {"op": "delete", "key", "record", "at"}, where `at` is the
    sitting's date on Skylight now (the API addresses a meal by id and date)."""
    actions = []
    wanted = {i["key"]: i for i in want}
    for key, item in wanted.items():
        rec = synced.get(key)
        if rec is None or rec["id"] not in live:
            actions.append({"op": "create", "item": item})
            continue
        fields = {}
        if live[rec["id"]] != item["date"]:
            fields["date"] = item["date"]
        if rec.get("category") != item["category"]:
            fields["category"] = item["category"]
        if rec.get("fingerprint") != fingerprint(item):
            fields["summary"] = item["summary"]
            fields["description"] = item["description"]
        if fields:
            actions.append({"op": "update", "item": item, "record": rec,
                            "at": live[rec["id"]], "fields": fields})
    for key, rec in synced.items():
        if key not in wanted and rec["id"] in live:
            actions.append({"op": "delete", "key": key, "record": rec, "at": live[rec["id"]]})
    return actions


def describe_action(a: dict) -> str:
    if a["op"] == "create":
        i = a["item"]
        return f"add     {i['date']} {i['category']:<6} {i['summary']}"
    if a["op"] == "delete":
        return f"remove  {a['at']} {a['record'].get('category', ''):<6} {a['record'].get('summary', a['key'])}"
    i, f = a["item"], a["fields"]
    what = []
    if "date" in f:
        what.append(f"move {a['at']} -> {f['date']}")
    if "category" in f:
        what.append(f"to {f['category']}")
    if "summary" in f:
        what.append("new text")
    return f"update  {i['date']} {i['category']:<6} {i['summary']} ({', '.join(what)})"


# ─────────────────────────────────────────────────────────────────────────────
# State and the network
# ─────────────────────────────────────────────────────────────────────────────

class State:
    """.skylight/state.json: the frame used, and per week what was put where."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("weeks", {})

    def week(self, week_start: str) -> dict:
        return self.data["weeks"].setdefault(week_start, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))


def choose_frame(client, state: State, frame_id: Optional[str] = None) -> str:
    if frame_id:
        state.data["frame_id"] = str(frame_id)
    if state.data.get("frame_id"):
        return state.data["frame_id"]
    frames = client.frames()
    if not frames:
        raise RuntimeError("This Skylight account has no frames.")
    if len(frames) > 1:
        listed = ", ".join(f"{f['id']} ({f.get('name') or '?'})" for f in frames)
        raise RuntimeError(f"Several Skylight frames: {listed}. Pick one with --frame.")
    state.data["frame_id"] = frames[0]["id"]
    return state.data["frame_id"]


def category_ids(client, frame: str) -> dict[str, str]:
    """Dinner/Lunch -> Skylight's meal category id, matched by name."""
    import skylight
    found = {}
    names = []
    for c in client.meal_categories(frame):
        name = skylight.category_name(c)
        names.append(name)
        for ours in (DINNER, LUNCH):
            if name.strip().lower() == ours.lower():
                found[ours] = c["id"]
    missing = [c for c in (DINNER, LUNCH) if c not in found]
    if missing:
        raise RuntimeError(f"Skylight has no meal category named {' or '.join(missing)} "
                           f"(it has: {', '.join(names) or 'none'}).")
    return found


def sync(client, state: State, plan: dict, week_start: str, dry_run: bool = False,
         frame_id: Optional[str] = None, echo=print) -> int:
    """Make Skylight match the saved week. Returns the number of changes made
    (or that would be made, with dry_run)."""
    want, skipped = desired(plan, week_start)
    synced = state.week(week_start)
    for note in skipped:
        echo(f"  not synced: {note}")

    frame = choose_frame(client, state, frame_id)
    cats = category_ids(client, frame)
    dates = [i["date"] for i in want] + [r["date"] for r in synced.values()]
    live = {}
    if dates:
        # A week either side, so a meal moved by hand nearby is still found
        # (and moved back) rather than taken for deleted and added twice.
        lo = (date.fromisoformat(min(dates)) - timedelta(days=7)).isoformat()
        hi = (date.fromisoformat(max(dates)) + timedelta(days=7)).isoformat()
        for s in client.sittings(frame, lo, hi):
            if s.get("instances"):
                live[s["id"]] = s["instances"][0]

    actions = plan_changes(want, synced, live)
    for key in [k for k, r in synced.items() if r["id"] not in live and k not in {i["key"] for i in want}]:
        synced.pop(key)   # deleted on the device and no longer wanted: forget it
    if not actions:
        echo(f"Skylight already matches week {week_start} ({len(want)} meals).")
        if not dry_run:
            state.save()
        return 0
    for a in actions:
        echo(("  would " if dry_run else "  ") + describe_action(a))
    if dry_run:
        return len(actions)

    done = 0
    try:
        for a in actions:
            if a["op"] == "create":
                i = a["item"]
                s = client.create_sitting(frame, cats[i["category"]], i["date"], i["summary"],
                                          i["description"])
                synced[i["key"]] = _record(s["id"], i)
            elif a["op"] == "update":
                i, f = a["item"], dict(a["fields"])
                if "category" in f:
                    f["meal_category_id"] = cats[f.pop("category")]
                client.update_sitting(frame, a["record"]["id"], a["at"], f)
                synced[i["key"]] = _record(a["record"]["id"], i)
            else:
                client.delete_sitting(frame, a["record"]["id"], a["at"])
                synced.pop(a["key"], None)
            done += 1
    finally:
        state.save()   # keep what did happen, so a re-run picks up from there
    echo(f"Skylight updated: {done} change(s) for week {week_start}.")
    return done


def _record(sitting_id: str, item: dict) -> dict:
    return {"id": str(sitting_id), "date": item["date"], "category": item["category"],
            "summary": item["summary"], "fingerprint": fingerprint(item)}

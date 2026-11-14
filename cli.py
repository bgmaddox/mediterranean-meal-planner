"""
cli.py
------
Plan a week inside a Claude Code session instead of through the API.

The app's pipeline is: build a prompt -> a model writes the plan -> code checks
it -> save. In a session the model step is the session itself; everything else
is the app's own code, so a plan made here is indistinguishable from one the app
generated. The `/plan-week` skill (.claude/skills/plan-week/) drives the order.

The Pi holds the real data. Every command that reads history, favorites or
constraints first pulls a mirror of it into .pi-data/ (gitignored), and `save`
writes the finished week *on the Pi*, through the Pi's own data_store, so the
app's history and recipe library are never overwritten from here.

  .venv/bin/python cli.py pull                       # refresh the mirror
  .venv/bin/python cli.py context --notes "busy Thu"  # the generation prompt
  .venv/bin/python cli.py check plan.json             # hard + soft findings
  .venv/bin/python cli.py summary plan.json           # one line per meal
  .venv/bin/python cli.py review-packets plan.json --out DIR
  .venv/bin/python cli.py save plan.json --notes findings.json --pdf
  .venv/bin/python cli.py push                        # send anything queued offline
  .venv/bin/python cli.py week [2026-10-12] [--export sessions/2026-10-12/plan.json]
  .venv/bin/python cli.py feedback sessions/2026-10-12/feedback.json
  .venv/bin/python cli.py anchors [--role side] [--search lentil]
  .venv/bin/python cli.py context --anchors greek-lemon-chicken,...
  .venv/bin/python cli.py add-anchor sessions/2026-10-12/new_anchor.json

A lunch week (two Sunday batch recipes, see lunch_week.py) is saved into the
same week as the dinners, replacing only that week's lunches:

  .venv/bin/python cli.py lunch-context --week-start 2026-11-16 --anchors id1,id2
  .venv/bin/python cli.py lunch-check lunches.json --week-start 2026-11-16
  .venv/bin/python cli.py lunch-packets lunches.json --week-start 2026-11-16 --out DIR
  .venv/bin/python cli.py lunch-save lunches.json --week-start 2026-11-16 --pdf

Dinners go on days, and saved weeks onto the Skylight calendar's meal planner
(skylight_sync.py). `save`, `lunch-save` and `set-days` sync automatically once
signed in:

  .venv/bin/python cli.py skylight-login              # once, interactive
  .venv/bin/python cli.py set-days 2026-11-16 d1=Mon d2=Wed d3=Thu
  .venv/bin/python cli.py skylight-sync [2026-11-16] [--dry-run]

No command here calls the Anthropic API.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent

# The mirror of the Pi's data. main() points data_store at it (every app module
# is imported lazily, inside the commands, so that happens first). Setting it at
# import time instead would redirect data_store for anything that imports cli.
MIRROR = Path(os.environ.get("DIET_DATA_DIR") or ROOT / ".pi-data")
OUTBOX = MIRROR / "outbox"

# The Tailscale address first, then the LAN name — either one may be down.
PI_HOSTS = ["rachett", "bgmaddox@rachett.local"]
PI_APP = os.environ.get("DIET_PI_APP", "~/apps/Diet")  # overridable for testing
PI_PYTHON = "~/apps/venv/bin/python"

PERSONAL_FILES = [
    "history.json", "favorites.json", "constraints.json",
    "preferences.json", "recipe_library.json", "anchor_recipes_user.json",
]


class CLIError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Talking to the Pi
#
# Both directions run a short script on the Pi with its own Python and its own
# data_store, rather than copying files. That keeps the Pi the only writer of
# its data, and works with whatever version of the app the Pi is running.
# ─────────────────────────────────────────────────────────────────────────────

_EXPORT_SCRIPT = """
import json, os
d = os.path.expanduser("{app}/data")
out = {{}}
for name in {files!r}:
    p = os.path.join(d, name)
    out[name] = json.load(open(p)) if os.path.exists(p) else None
print(json.dumps(out))
"""

# Writes run a function from pi_ops.py on the Pi: this file's copy of pi_ops is
# sent along, against the Pi's own data_store.
_OP_RUNNER = """
import base64, json, os, sys
os.chdir(os.path.expanduser("{app}"))
sys.path.insert(0, ".")
import data_store
print(json.dumps({op}(data_store, json.loads(base64.b64decode("{payload}")))))
"""


def _run_on_pi(script: str) -> str:
    errors = []
    for host in PI_HOSTS:
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 host, f"{PI_PYTHON} -"],
                input=script, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{host}: timed out")
            continue
        if result.returncode == 0:
            return result.stdout
        # 255 is ssh's own failure (unreachable); anything else is the script.
        if result.returncode != 255:
            raise CLIError(f"Script failed on the Pi ({host}):\n{result.stderr.strip()}")
        errors.append(f"{host}: {result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'unreachable'}")
    raise CLIError("Pi unreachable — " + "; ".join(errors))


def pull(quiet: bool = False) -> None:
    raw = _run_on_pi(_EXPORT_SCRIPT.format(app=PI_APP, files=PERSONAL_FILES))
    bundle = json.loads(raw)
    MIRROR.mkdir(exist_ok=True)
    for name, content in bundle.items():
        path = MIRROR / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(content, indent=2))
    # A plan queued while offline isn't on the Pi yet — re-apply it to the
    # mirror so the next plan still sees it in history.
    for queued in sorted(OUTBOX.glob("*.json")) if OUTBOX.exists() else []:
        item = json.loads(queued.read_text())
        _local_op(item.get("op", "import_week"), item)
    if not quiet:
        import data_store
        weeks = [e["week_start"] for e in data_store.load_history()]
        print(f"Pulled from Pi: {len(weeks)} weeks of history (newest {weeks[0] if weeks else '-'}), "
              f"{len(data_store.load_favorites())} favorites, "
              f"{len(data_store.load_constraints())} constraints, "
              f"{len(data_store.load_recipe_library())} library recipes.")


def _ensure_mirror(offline: bool) -> None:
    if offline:
        if not (MIRROR / "history.json").exists():
            raise CLIError("No local mirror yet — run `cli.py pull` once while the Pi is reachable.")
        print("(offline — using the last pulled mirror)", file=sys.stderr)
        return
    try:
        pull(quiet=True)
    except CLIError as e:
        if (MIRROR / "history.json").exists():
            print(f"(warning: {e}; using the last pulled mirror)", file=sys.stderr)
        else:
            raise


def _local_op(op: str, payload: dict) -> dict:
    """Run a pi_ops function against the local mirror instead of the Pi."""
    import data_store
    import pi_ops
    return getattr(pi_ops, op)(data_store, payload)


def _pi_op(op: str, payload: dict) -> dict:
    import pi_ops
    source = Path(pi_ops.__file__).read_text()
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    raw = _run_on_pi(source + _OP_RUNNER.format(app=PI_APP, op=op, payload=encoded))
    return json.loads(raw.strip().splitlines()[-1])


# ─────────────────────────────────────────────────────────────────────────────
# Plans on disk
# ─────────────────────────────────────────────────────────────────────────────

def load_plan(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise CLIError(f"{path} is not valid JSON: {e}")
    # Accept the generation output shape ({"week_plan": ...}) or a bare plan.
    return data.get("week_plan", data) if isinstance(data, dict) else data


def run_checks(plan: dict, days: int | None = None,
               around: dict | None = None,
               week: str | None = None) -> tuple[list[str], list[dict], list[dict]]:
    """Structural problems, hard violations, soft violations — the same gates
    meal_planner applies to an API-generated plan.

    `around` is the week's saved lunch week (saved_lunch_week): the plan is then
    dinners only, checked for that, and its constraint findings are taken on
    the week as it will be saved, lunches included. `week` keeps a saved week
    from being compared with itself as history."""
    import constraint_check
    import data_store
    from meal_planner import MealPlanError, _validate_plan

    prefs = data_store.load_preferences()
    if around:
        structural = []
        try:
            _validate_plan(plan, days or len(plan.get("dinners") or []),
                           require_kid_adaptation=bool(prefs.get("children")), dinners_only=True)
        except MealPlanError as e:
            structural.append(str(e))
        import pi_ops
        structural += pi_ops.day_problems(plan)
        hard, soft = _constraint_findings(with_saved_lunches(plan, around), prefs, week)
        return structural, hard, soft
    if not plan.get("dinners") and plan.get("lunches"):
        # A lunch-only week (cli.py lunch-save made it). It has no dinners to
        # count or label, so it gets the lunch week's structural checks.
        import lunch_week
        structural = lunch_week.lunch_plan_problems(plan, adults=prefs.get("lunch_adult_count", 1))
        hard, soft = _constraint_findings(plan, prefs, week)
        return structural, hard, soft
    view = plan
    if days is None:
        # No count given: a saved week edited mid-week ("eating out Wednesday")
        # can have fewer dinners than lunches. _validate_plan only counts the
        # lunches, so match their count to the dinners; everything else it
        # checks is per-dinner. New plans pass --days and get the strict check.
        days = len(plan.get("dinners") or [])
        lunches = plan.get("lunches") or []
        if lunches and len(lunches) != days:
            view = {**plan, "lunches": (lunches * days)[:days]}
    structural = []
    try:
        _validate_plan(view, days, require_kid_adaptation=bool(prefs.get("children")))
    except MealPlanError as e:
        structural.append(str(e))
    import pi_ops
    structural += pi_ops.day_problems(plan)

    hard, soft = _constraint_findings(plan, prefs, week)
    return structural, hard, soft


def _all_findings(plan: dict, prefs: dict, skip_week: str | None = None) -> list[dict]:
    import constraint_check
    import data_store
    history = [e for e in data_store.load_history() if e["week_start"] != skip_week]
    return constraint_check.check_week_plan(
        plan,
        user_constraints=data_store.active_constraints_for_prompt(),
        target_sodium_mg=prefs.get("target_sodium_mg", constraint_check.SODIUM_TARGET_DEFAULT_MG),
        history=history[:6],
    )


def _constraint_findings(plan: dict, prefs: dict,
                         week: str | None = None) -> tuple[list[dict], list[dict]]:
    violations = _all_findings(plan, prefs, skip_week=week)
    return ([v for v in violations if v["severity"] == "hard"],
            [v for v in violations if v["severity"] == "soft"])


# ─────────────────────────────────────────────────────────────────────────────
# Lunch weeks
#
# A lunch week is written as {"week_plan": {"lunches": [...], "sunday_prep_list":
# [...]}} and saved INTO its week: the week's lunches are replaced (except the
# ones named with --keep), its dinners are left alone. Everything below works on
# the merged week, built by the same pi_ops.merge_lunches the Pi runs on save.
# ─────────────────────────────────────────────────────────────────────────────

def _split(arg: str | None) -> list[str]:
    return [x.strip() for x in (arg or "").split(",") if x.strip()]


def _saved_plan(week: str) -> dict | None:
    import data_store
    entry = next((e for e in data_store.load_history() if e["week_start"] == week), None)
    return entry["plan"] if entry else None


def merged_lunch_week(lunch_plan: dict, week: str, keep: list[str]) -> tuple[dict | None, dict]:
    """(the saved week or None, that week with the lunch plan merged in)."""
    import pi_ops
    existing = _saved_plan(week)
    try:
        merged = pi_ops.merge_lunches(existing, lunch_plan.get("lunches"),
                                      lunch_plan.get("sunday_prep_list"), keep)
    except ValueError as e:
        raise CLIError(str(e))
    return existing, merged


def saved_lunch_week(week: str | None) -> dict | None:
    """The lunch week already saved into `week`, or None.

    {"lunches": its batch recipes, "prep": their Sunday tasks, "notes": findings
    on them, "dropped": other lunches in that week (dinner leftovers), which a
    dinners-only plan replaces}. Only batch recipes (lunches with `days`) count:
    a week whose lunches are all dinner leftovers has no lunch week."""
    plan = _saved_plan(week) if week else None
    batch = [l for l in (plan or {}).get("lunches") or [] if l.get("days")]
    if not batch:
        return None
    ids = {str(l.get("id")) for l in batch}
    return {
        "lunches": batch,
        "prep": [t for t in plan.get("sunday_prep_list") or [] if t.get("part") == "lunch"],
        "notes": [n for n in plan.get("_soft_violations") or [] if str(n.get("meal_id", "")) in ids],
        "dropped": [l for l in plan.get("lunches") or [] if not l.get("days")],
    }


def with_saved_lunches(plan: dict, around: dict) -> dict:
    """A dinners-only plan with the week's saved lunch week merged back in,
    through the same pi_ops.merge_lunches a lunch save uses. The week-level
    cost then includes the lunches."""
    import pi_ops
    merged = pi_ops.merge_lunches({**plan, "lunches": []}, around["lunches"], around["prep"])
    summary = dict(merged.get("week_summary") or {})
    if summary.get("estimated_weekly_grocery_cost_usd") is not None:
        lunch_cost = sum(float((l.get("cost_estimate") or {}).get("total_ingredient_cost_usd") or 0)
                         for l in around["lunches"])
        summary["estimated_weekly_grocery_cost_usd"] = round(
            float(summary["estimated_weekly_grocery_cost_usd"]) + lunch_cost, 2)
        merged["week_summary"] = summary
    return merged


def _dinners_only(plan: dict, week: str | None) -> dict | None:
    """The saved lunch week to plan around, when `plan` is a dinners-only plan
    for a week that has one. A plan that brings its own lunches is a whole-week
    plan, and `save` guards that case separately."""
    if plan.get("lunches"):
        return None
    return saved_lunch_week(week)


def run_lunch_checks(lunch_plan: dict, week: str, keep: list[str],
                     days: list[str] | None = None) -> tuple[list[str], list[dict], list[dict], dict]:
    """Structural problems, then the hard and soft findings the lunch plan
    INTRODUCES into its week. A finding the saved week already carries (on a
    dinner, say) is not this plan's to fix, so it is measured against that
    baseline, the same way a repair is (see DECISIONS.md)."""
    import data_store
    import lunch_week
    prefs = data_store.load_preferences()
    existing, merged = merged_lunch_week(lunch_plan, week, keep)
    structural = lunch_week.lunch_plan_problems(lunch_plan, days, adults=prefs.get("lunch_adult_count", 1))

    def key(v):
        return (v["code"], v["meal_id"], v["message"])

    before = {key(v) for v in _all_findings(existing, prefs, skip_week=week)} if existing else set()
    new = [v for v in _all_findings(merged, prefs, skip_week=week) if key(v) not in before]
    return (structural, [v for v in new if v["severity"] == "hard"],
            [v for v in new if v["severity"] == "soft"], merged)


def _report(structural: list[str], hard: list[dict], soft: list[dict]) -> int:
    if structural:
        print("STRUCTURE — this plan would be rejected:")
        for s in structural:
            print(f"  {s}")
    if hard:
        print("HARD — this plan would be rejected:")
        for v in hard:
            print(_fmt(v))
    if soft:
        print("SOFT — shown in Review notes, never blocks:")
        for v in soft:
            print(_fmt(v))
    if not (structural or hard):
        print(f"PASS — no structural or hard problems ({len(soft)} soft notes).")
        return 0
    return 1


def cmd_lunch_context(args) -> int:
    _ensure_mirror(args.offline)
    import data_store
    import lunch_week
    week, keep = args.week_start, _split(args.keep)
    existing = _saved_plan(week)
    old = (existing or {}).get("lunches") or []
    kept = [l for l in old if str(l.get("id")) in keep]
    missing = set(keep) - {str(l.get("id")) for l in kept}
    if missing:
        raise CLIError(f"Cannot keep {', '.join(sorted(missing))}: week {week} has no such lunch.")
    days = _split(args.days) or list(lunch_week.WEEKDAYS)
    blocks = lunch_week.split_days(days, args.recipes)
    free = [f"l{n}" for n in range(1, 20) if f"l{n}" not in keep][:len(blocks)]

    anchors = None
    if args.anchors:
        by_id = {a["id"]: a for a in data_store.load_anchor_recipes()}
        unknown = [i for i in _split(args.anchors) if i not in by_id]
        if unknown:
            raise CLIError(f"Unknown anchor recipe id(s): {', '.join(unknown)}")
        anchors = [by_id[i] for i in _split(args.anchors)]

    prefs = data_store.load_preferences()
    print(lunch_week.build_lunch_prompt(
        history=[e for e in data_store.load_history() if e["week_start"] != week],
        favorites=data_store.load_favorites(),
        constraints=data_store.active_constraints_for_prompt(),
        lunch_adult_count=prefs.get("lunch_adult_count", 1),
        week_notes=args.notes, cuisine_notes=args.cuisine, use_up_ingredients=args.use_up,
        days=days, recipes=args.recipes, ids=free, anchor_recipes=anchors,
        week_dinners=(existing or {}).get("dinners") or None, kept_lunches=kept or None,
    ))
    state = (f"week {week} has {len((existing or {}).get('dinners') or [])} dinners saved"
             if existing else f"week {week} has no plan yet: this will be a lunch-only week")
    dropped = [f"{l.get('id')} {l.get('name')}" for l in old if str(l.get("id")) not in keep]
    print(f"\n[{len(blocks)} lunch recipes ({', '.join(free)}) covering {', '.join(days)}; {state}]")
    if dropped:
        print("[saving will REPLACE these lunches: " + "; ".join(dropped) + "]")
    return 0


def cmd_lunch_check(args) -> int:
    _ensure_mirror(args.offline)
    structural, hard, soft, _ = run_lunch_checks(
        load_plan(args.plan), args.week_start, _split(args.keep), _split(args.days) or None)
    return _report(structural, hard, soft)


def cmd_lunch_packets(args) -> int:
    """One proofreading packet per lunch recipe (the app's own rubric), plus the
    lunch week as cooked and the merged week's shopping list."""
    _ensure_mirror(args.offline)
    import plan_critique as pc
    import shopping
    import week_review
    lunch_plan = load_plan(args.plan)
    _, merged = merged_lunch_week(lunch_plan, args.week_start, _split(args.keep))
    only = set(_split(args.only)) or None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.md"):
        f.unlink()
    defect_fmt = _PACKET_FORMAT % ", ".join(sorted(pc.DEFECT_CATEGORIES))
    written = []
    for meal in lunch_plan.get("lunches") or []:
        mid = str(meal.get("id"))
        if only and mid not in only:
            continue
        (out / f"{mid}.md").write_text(
            pc._DEFECT_SYSTEM + defect_fmt + "\n\nThis is a batch lunch: cooked on Sunday, "
            "portioned, and eaten on the days listed. Quantities are for the whole batch."
            "\n\nProofread this recipe:\n\n" + pc._meal_for_review(meal, "lunch", None))
        written.append(mid)
    if not only:
        ids = [str(l.get("id")) for l in lunch_plan.get("lunches") or []]
        (out / "lunch_week.md").write_text(week_review.lunch_week_packet(merged, ids))
        (out / "shopping.md").write_text(
            week_review.shopping_packet(merged, shopping.build_shopping_list(merged)))
        written += ["lunch_week", "shopping"]
    print(f"Wrote {len(written)} packets to {out}: {', '.join(written)}")
    return 0


def cmd_lunch_save(args) -> int:
    _ensure_mirror(args.offline)
    lunch_plan = load_plan(args.plan)
    week, keep = args.week_start, _split(args.keep)
    structural, hard, soft, merged = run_lunch_checks(lunch_plan, week, keep, _split(args.days) or None)
    if structural or hard:
        print("Refusing to save — fix these first (cli.py lunch-check shows them):")
        for s in structural:
            print(f"  {s}")
        for v in hard:
            print(_fmt(v))
        return 1

    notes = soft + _session_notes(merged, args.notes)
    item = {"op": "import_lunches", "week_start": week, "keep": keep, "notes": notes,
            "lunches": lunch_plan.get("lunches") or [],
            "sunday_prep_list": lunch_plan.get("sunday_prep_list") or []}
    sent = False
    if not args.offline:
        try:
            result = _pi_op("import_lunches", item)
            sent = True
            print(f"Saved to the Pi: week {week}, {result['action']} "
                  f"({result['dinners']} dinners, {result['lunches']} lunches).")
        except CLIError as e:
            print(f"Could not reach the Pi ({e}).")
    queued = OUTBOX / f"{week}-lunches.json"
    if sent:
        queued.unlink(missing_ok=True)
        pull(quiet=True)
    else:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        queued.write_text(json.dumps(item))
        _local_op("import_lunches", item)
        print(f"Queued in {OUTBOX}/ — run `cli.py push` when the Pi is back.")

    if args.pdf or args.drive:
        _write_pdf(_saved_plan(week) or merged, week, args.drive)
    _sync_after_save(week, args)
    return 0


def _write_pdf(plan: dict, week: str, drive: bool) -> None:
    import pdf_export
    import shopping
    pdf = pdf_export.build_pdf_bytes(plan, shopping.build_shopping_list(plan), week_start=week)
    name = f"meal_plan_{week}.pdf"
    path = ROOT / "PDFs" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(pdf)
    print(f"PDF: {path.relative_to(ROOT)}")
    if drive:
        import drive_upload
        url = drive_upload.upload_pdf_to_drive(pdf, name)
        print(f"Uploaded to Drive{': ' + url if url else '.'}")


def _fmt(v: dict) -> str:
    who = f"{v['meal_id']} {v['meal_name']}".strip() or "week"
    return f"  [{v['code']}] {who}: {v['message']}"


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────

def cmd_pull(args) -> int:
    pull()
    return 0


def cmd_context(args) -> int:
    _ensure_mirror(args.offline)
    import meal_planner
    around = saved_lunch_week(args.week_start)
    system_prompt, user_message, require_kid = meal_planner.build_generation_prompts(
        week_notes=args.notes, cuisine_notes=args.cuisine,
        use_up_ingredients=args.use_up, days=args.days,
        anchor_ids=[i.strip() for i in args.anchors.split(",") if i.strip()] if args.anchors else None,
        saved_lunches=around["lunches"] if around else None,
    )
    print(system_prompt)
    print("\n" + "=" * 79)
    print("USER MESSAGE (what the app would send):")
    print(user_message)
    if around:
        names = "; ".join(f"{l.get('id')} {l.get('name')} ({', '.join(l['days'])})"
                          for l in around["lunches"])
        print(f"\n[{args.days} dinners, NO lunches: week {args.week_start} already has a lunch week "
              f"({names}). Pass --week-start {args.week_start} to check, review-packets and save; "
              f"save keeps those lunches. kid_adaptation required: {require_kid}]")
        if around["dropped"]:
            print("[warning: the saved week also has dinner-leftover lunches, which this plan drops: "
                  + "; ".join(f"{l.get('id')} {l.get('name')}" for l in around["dropped"]) + "]")
    else:
        print(f"\n[{args.days} dinners + {args.days} lunches; kid_adaptation required: {require_kid}]")
    return 0


def cmd_check(args) -> int:
    _ensure_mirror(args.offline)
    plan = load_plan(args.plan)
    around = _dinners_only(plan, args.week_start)
    if around:
        print(f"(dinners only: checked with week {args.week_start}'s saved lunch week merged in)")
    structural, hard, soft = run_checks(plan, args.days, around, args.week_start)
    return _report(structural, hard, soft)


def cmd_summary(args) -> int:
    _print_summary(load_plan(args.plan))
    return 0


def _print_summary(plan: dict, ratings: dict | None = None) -> None:
    """One line per meal. `ratings` maps a meal name to its favorite entry."""
    ratings = ratings or {}

    def rated(name):
        f = ratings.get(name)
        return f"  [{f['rating']}/5{' — ' + f['feedback'] if f.get('feedback') else ''}]" if f else ""

    if plan.get("dinners"):
        print("DINNERS")
    for d in plan.get("dinners") or []:
        n = d.get("nutrition_estimate") or {}
        lunch = " +lunch" if d.get("generates_lunch") else ""
        print(f"  {d.get('id')}  {d.get('name')}{rated(d.get('name'))}")
        print(f"      {d.get('composition')}, {d.get('cook_time_minutes')} min, "
              f"{d.get('primary_equipment')}{lunch} | {n.get('calories_per_adult_serving')} kcal, "
              f"{n.get('protein_g')}g protein, {n.get('fiber_g')}g fiber, {n.get('sodium_mg')}mg Na")
    print("LUNCHES")
    for l in plan.get("lunches") or []:
        src = f"leftover of {l.get('leftover_from_dinner_id')}" if l.get("source") == "leftover" else "standalone"
        if l.get("days"):
            n = l.get("nutrition_estimate") or {}
            src = (f"batch for {', '.join(l['days'])}, keeps {l.get('keeps_days')} days, "
                   f"{l.get('cook_time_minutes')} min Sunday")
            print(f"  {l.get('id')}  {l.get('name')}  ({src}, {l.get('reheat')}){rated(l.get('name'))}")
            print(f"      {n.get('calories_per_adult_serving')} kcal, {n.get('protein_g')}g protein, "
                  f"{n.get('fiber_g')}g fiber, {n.get('sodium_mg')}mg Na")
            continue
        print(f"  {l.get('id')}  {l.get('name')}  ({src}, {l.get('reheat')}){rated(l.get('name'))}")
    s = plan.get("week_summary") or {}
    print(f"WEEK  fish {s.get('fish_meal_count')}, red meat {s.get('red_meat_meal_count')}, "
          f"vegetarian {s.get('vegetarian_meal_count')}, est. ${s.get('estimated_weekly_grocery_cost_usd')}")
    if s.get("special_ingredients"):
        print(f"      specials: {', '.join(s['special_ingredients'])}")
    for snack in plan.get("snacks") or []:
        when = f"  [{snack['when']}]" if snack.get("when") else ""
        print(f"SNACK {snack.get('name')}{when}")
        if snack.get("portion"):
            print(f"      portion: {snack['portion']} x{snack.get('portions_per_week', '?')}")


def _find_week(week: str | None) -> dict:
    import data_store
    history = data_store.load_history()
    if not history:
        raise CLIError("History is empty.")
    if week is None:
        return history[0]
    entry = next((e for e in history if e["week_start"] == week), None)
    if entry is None:
        raise CLIError(f"No week {week}. Saved weeks: {', '.join(e['week_start'] for e in history)}")
    return entry


def cmd_week(args) -> int:
    """Show a saved week (newest by default), or export it to edit mid-week."""
    _ensure_mirror(args.offline)
    import data_store
    entry = _find_week(args.week_start)
    plan = entry["plan"]
    ratings = {f["name"]: f for f in data_store.load_favorites()}
    print(f"WEEK OF {entry['week_start']}")
    _print_summary(plan, ratings)

    if args.export:
        out = Path(args.export)
        out.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: v for k, v in plan.items() if k != "_soft_violations"}
        out.write_text(json.dumps({"week_plan": clean}, indent=1))
        # Carry the week's review notes forward, so a re-save doesn't drop them.
        notes = [n for n in plan.get("_soft_violations") or [] if n["code"].startswith("critique_")]
        notes_path = out.with_name("notes.json")
        if notes and not notes_path.exists():
            findings = [{"meal_id": n["meal_id"], "meal_name": n["meal_name"],
                         "category": n["code"].removeprefix("critique_"),
                         "message": n["message"]} for n in notes]
            notes_path.write_text(json.dumps({"findings": findings}, indent=1))
            print(f"\nExported to {out} (+ {len(findings)} review notes in {notes_path.name}).")
        else:
            print(f"\nExported to {out}.")
        print(f"Re-save with: cli.py save {out} --week-start {entry['week_start']}"
              + (f" --notes {notes_path}" if notes_path.exists() else ""))
    return 0


def cmd_feedback(args) -> int:
    """Record how a week went: ratings and notes become favorites (low ratings
    included), ingredient dislikes become constraints. Written on the Pi."""
    payload = json.loads(Path(args.file).read_text())
    _ensure_mirror(args.offline)
    # Validate against the mirror first, so a typo in a meal id fails here
    # rather than half-way through on the Pi.
    try:
        _local_op("apply_feedback", payload) if args.offline else _validate_feedback(payload)
    except (ValueError, KeyError) as e:
        raise CLIError(f"Feedback not recorded: {e}")
    if args.offline:
        print("Recorded in the local mirror only (--offline). The Pi still needs it: "
              "re-run without --offline when it is reachable.")
        return 0
    result = _pi_op("apply_feedback", payload)
    pull(quiet=True)
    for label, key in (("New favorites", "favorites_added"), ("Updated", "favorites_updated"),
                       ("New constraints", "constraints_added")):
        if result[key]:
            print(f"{label}: " + "; ".join(result[key]))
    return 0


def _weight_cli(*args: str) -> str | None:
    """Ask the separate Weight tracker project. None if it is missing or unreachable."""
    root = Path(os.environ.get("WEIGHT_PROJECT", Path(__file__).resolve().parent.parent / "Weight"))
    python, script = root / ".venv/bin/python", root / "cli.py"
    if not python.exists() or not script.exists():
        return None
    try:
        done = subprocess.run([str(python), str(script), *args], capture_output=True,
                              text=True, timeout=45, cwd=root)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def format_intake(s: dict) -> list[str]:
    """Planned vs logged, trend and days logged, from the tracker's summary."""
    week = s.get("week") or {}
    trend = s.get("trend") or {}
    lines = [f"Days logged this week: {week.get('logged_days', 0)} of {week.get('target_days', 0)}"]
    if week.get("mean_kcal"):
        lines.append(f"Average on logged days: {week['mean_kcal']}")
    plan = s.get("diet_week")
    if plan:
        lines.append(f"Planned dinners logged, week of {plan.get('week_start')}: "
                     f"{plan.get('logged_dinners', 0)} of {plan.get('planned_dinners', 0)}")
    else:
        lines.append("No current Diet week in the tracker")
    lines.append(f"7-day trend: {trend['mean_lb']:.1f} lb" if trend.get("enough")
                 else "7-day trend: not enough weigh-ins")
    return lines


def cmd_intake(args) -> int:
    """Read-only look-back from the tracker. Never fails the planning session."""
    raw = _weight_cli("summary")
    if raw is None:
        print("Weight tracker unreachable; skipping.")
        return 0
    try:
        summary = json.loads(raw)
    except ValueError:
        print("Weight tracker gave an unreadable answer; skipping.")
        return 0
    print("\n".join(format_intake(summary)))
    pending = _weight_cli("ratings")
    weeks = json.loads(pending) if pending else []
    count = sum(len(w["meals"]) for w in weeks)
    if not count:
        return 0
    if not args.apply_ratings:
        print(f"{count} dinner rating(s) from the app are waiting; "
              "re-run with --apply-ratings once the user agrees.")
        return 0
    _ensure_mirror(False)
    for payload in weeks:
        try:
            _validate_feedback(payload)
        except (ValueError, KeyError) as e:
            raise CLIError(f"Ratings not recorded: {e}")
        _pi_op("apply_feedback", payload)
    pull(quiet=True)
    _weight_cli("ratings", "--mark-sent")
    print(f"Applied {count} rating(s).")
    return 0


def cmd_set_target(args) -> int:
    """Change the lunch and dinner calorie target on the Pi, after a yes in session."""
    payload = {"target_calories_lunch_dinner": args.calories}
    if args.offline:
        print(_local_op("set_target_calories", payload))
        return 0
    print(_pi_op("set_target_calories", payload))
    pull(quiet=True)
    return 0


def cmd_set_snacks(args) -> int:
    """Set the snacking notes the prompt plans snacks from, on the Pi."""
    payload = {"snack_notes": args.notes}
    if args.offline:
        print(_local_op("set_snack_notes", payload))
        return 0
    print(_pi_op("set_snack_notes", payload))
    pull(quiet=True)
    return 0


def _validate_feedback(payload: dict) -> None:
    entry = _find_week(payload["week_start"])
    ids = {str(m.get("id")) for k in ("dinners", "lunches") for m in entry["plan"].get(k) or []}
    for fb in payload.get("meals") or []:
        if str(fb.get("id")) not in ids:
            raise ValueError(f"week {payload['week_start']} has no meal {fb.get('id')!r}")
        if not 1 <= int(fb["rating"]) <= 5:
            raise ValueError(f"rating for {fb['id']} must be 1-5")


_PACKET_FORMAT = """

Reply with ONLY a JSON object, no prose around it:
{"findings": [{"meal_id": "...", "meal_name": "...", "category": "...", "message": "..."}]}
Allowed categories: %s. An empty list is the common, correct answer."""


def cmd_review_packets(args) -> int:
    """Write the app's own review instructions, one file per meal plus one for
    the week, so reviewers in a session apply exactly the app's rubric."""
    import plan_critique as pc
    plan = load_plan(args.plan)
    around = _dinners_only(plan, args.week_start)
    if around:
        # Dinners-only plan: the dinners get a packet each; the week, walkthrough
        # and shopping packets see the week as saved, lunches included, so the
        # shared shopping and the Sunday load are reviewed together. The saved
        # lunches were proofread when they were planned.
        dinners_plan, plan = plan, with_saved_lunches(plan, around)
    only = set(args.only.split(",")) if args.only else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.md"):
        f.unlink()

    dinners = plan.get("dinners") or []
    by_id = {str(d.get("id")): d for d in dinners}
    jobs = [(d, "dinner", None) for d in dinners]
    for l in (dinners_plan if around else plan).get("lunches") or []:
        jobs.append((l, "lunch", by_id.get(str(l.get("leftover_from_dinner_id") or ""))))

    defect_fmt = _PACKET_FORMAT % ", ".join(sorted(pc.DEFECT_CATEGORIES))
    written = []
    for meal, meal_type, linked in jobs:
        mid = str(meal.get("id"))
        if only and mid not in only:
            continue
        (out / f"{mid}.md").write_text(
            pc._DEFECT_SYSTEM + defect_fmt
            + "\n\nProofread this recipe:\n\n" + pc._meal_for_review(meal, meal_type, linked)
        )
        written.append(mid)
    if not only:
        (out / "week.md").write_text(
            pc._JUDGMENT_SYSTEM + _PACKET_FORMAT % ", ".join(sorted(pc.JUDGMENT_CATEGORIES))
            + "\n\nReview this week plan:\n\n" + pc._plan_for_review(plan)
        )
        written.append("week")
        # Session-only rubrics: the week as cooked, and as shopped.
        import shopping
        import week_review
        (out / "walkthrough.md").write_text(week_review.walkthrough_packet(plan))
        (out / "shopping.md").write_text(
            week_review.shopping_packet(plan, shopping.build_shopping_list(plan)))
        written += ["walkthrough", "shopping"]
    print(f"Wrote {len(written)} packets to {out}: {', '.join(written)}")
    return 0


def cmd_anchors(args) -> int:
    """The whole anchor library, one line each, so a session can choose anchors
    deliberately instead of taking the app's random sample of 10."""
    _ensure_mirror(args.offline)
    import data_store
    anchors = data_store.load_anchor_recipes()
    if args.show:
        wanted = set(args.show.split(","))
        for a in anchors:
            if a["id"] in wanted:
                print(json.dumps(a, indent=1))
        return 0
    term = (args.search or "").lower()
    picked = [
        a for a in anchors
        if (not args.role or a.get("role") == args.role)
        and (not args.cuisine or args.cuisine.lower() in a.get("cuisine", "").lower())
        and (not term or term in " ".join([a["name"], a.get("summary", ""),
                                           *a.get("key_ingredients", [])]).lower())
    ]
    user_ids = {a["id"] for a in data_store.load_anchor_user_recipes()}
    print("id | cuisine | meal_type | name | key ingredients   (* sourced notes, N real nutrition, U added in-session/by you)")
    for role in ("main", "side"):
        group = [a for a in picked if a.get("role", "main") == role]
        if not group:
            continue
        print(f"\n{role.upper()} ({len(group)})")
        for a in sorted(group, key=lambda a: (a.get("cuisine", ""), a["name"])):
            marks = ("*" if a.get("notes_sourced") else "") + ("N" if a.get("nutrition") else "") \
                + ("U" if a["id"] in user_ids else "")
            keys = ", ".join(a.get("key_ingredients", [])[:5])
            print(f"{a['id']}{(' ' + marks) if marks else ''} | {a.get('cuisine', '')} | "
                  f"{a.get('meal_type', '')} | {a['name']} | {keys}")
    print(f"\n{len(picked)} of {len(anchors)} anchors.")
    return 0


TECHNIQUE_NOTES_MAX_WORDS = 60   # they go into every prompt, and the one-page PDF


def _anchor_problems(anchor: dict) -> list[str]:
    """Why a recipe can't go into the library. Screens for the banned
    ingredients and the household's own dislikes — the library has carried
    'orange' into generation twice before (see DECISIONS.md)."""
    import constraint_check as cc
    import data_store
    problems = [f"missing {k}" for k in ("name", "summary", "key_ingredients") if not anchor.get(k)]
    if anchor.get("role") not in ("main", "side"):
        problems.append("role must be 'main' or 'side'")
    words = len((anchor.get("technique_notes") or "").split())
    if words > TECHNIQUE_NOTES_MAX_WORDS:
        problems.append(f"technique_notes is {words} words; keep it to {TECHNIQUE_NOTES_MAX_WORDS}")
    as_meal = {
        "id": "", "name": anchor.get("name", ""),
        "ingredients": [{"name": k} for k in anchor.get("key_ingredients", [])],
        "instructions": [anchor.get("summary", ""), anchor.get("technique_notes", "")],
    }
    terms, _ = cc.parse_user_constraints(data_store.active_constraints_for_prompt())
    for v in cc._check_banned(as_meal) + cc._check_user_terms(as_meal, terms):
        problems.append(v["message"])
    return problems


def cmd_add_anchor(args) -> int:
    _ensure_mirror(args.offline)
    anchor = json.loads(Path(args.file).read_text())
    problems = _anchor_problems(anchor)
    if problems:
        print("Not added:")
        for pr in problems:
            print(f"  {pr}")
        return 1
    if not anchor.get("source_url"):
        print("(no source_url — this recipe's notes will be marked as self-generated)")
    if args.offline:
        result = _local_op("add_anchor", anchor)
        print(f"Anchor {result['id']} {result['action']} in the local mirror only (--offline); "
              "re-run without --offline to put it on the Pi.")
        return 0
    result = _pi_op("add_anchor", anchor)
    pull(quiet=True)
    if result["action"] == "exists":
        print(f"Already in the library as {result['id']}.")
    else:
        print(f"Added {result['id']} to the Pi's anchor library.")
    return 0


def _session_notes(plan: dict, notes_path: str | None) -> list[dict]:
    """Reviewer findings left unfixed, validated the same way the app
    validates its reviewer's output."""
    if not notes_path:
        return []
    import plan_critique as pc
    import week_review
    raw = json.loads(Path(notes_path).read_text())
    findings = raw.get("findings", raw) if isinstance(raw, dict) else raw
    notes = pc._to_violations(findings, pc._meal_ids(plan),
                              pc.CATEGORIES | week_review.CATEGORIES, cap=100)
    for n in notes:
        if n["code"].removeprefix("critique_") in pc.DEFECT_CATEGORIES:
            n["repair_outcome"] = "left_in_session"
    return notes


def cmd_save(args) -> int:
    _ensure_mirror(args.offline)
    plan = load_plan(args.plan)
    week = args.week_start or date.today().isoformat()
    around = _dinners_only(plan, week)
    structural, hard, soft = run_checks(plan, None, around, week)
    if structural or hard:
        print("Refusing to save — fix these first (cli.py check shows them):")
        for s in structural:
            print(f"  {s}")
        for v in hard:
            print(_fmt(v))
        return 1

    if around:
        # Dinners only: the saved lunch week goes back in, with the reviewer
        # notes it was saved with (rule findings were just recomputed in `soft`).
        plan = with_saved_lunches(plan, around)
        seen = {(v["code"], v["meal_id"], v["message"]) for v in soft}
        soft += [n for n in around["notes"] if (n.get("code"), n.get("meal_id"), n.get("message")) not in seen]
        print(f"Keeping week {week}'s lunch week: "
              + ", ".join(f"{l.get('id')} {l.get('name')}" for l in around["lunches"]))
    plan["_soft_violations"] = soft + _session_notes(plan, args.notes)
    names = {l.get("name") for l in plan.get("lunches") or []}
    lost = [l for l in (_saved_plan(week) or {}).get("lunches") or []
            if l.get("days") and l.get("name") not in names]
    if lost and not args.replace_lunches:
        print(f"Refusing to save — week {week} has lunch-week recipes this plan would drop:")
        for l in lost:
            print(f"  {l.get('id')} {l.get('name')} ({', '.join(l['days'])})")
        print("Pass --replace-lunches to drop them, then re-run `cli.py lunch-save` on the "
              "lunch plan to put them back.")
        return 1
    item = {"plan": plan, "week_start": week}

    sent = False
    if not args.offline:
        try:
            result = _pi_op("import_week", item)
            sent = True
            print(f"Saved to the Pi: week {week} {result['action']} "
                  f"({result['weeks']} weeks in history).")
        except CLIError as e:
            print(f"Could not reach the Pi ({e}).")
    if sent:
        (OUTBOX / f"{week}.json").unlink(missing_ok=True)
        pull(quiet=True)
    else:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        (OUTBOX / f"{week}.json").write_text(json.dumps(item))
        _local_op("import_week", item)
        print(f"Queued in {OUTBOX}/ — run `cli.py push` when the Pi is back.")

    if args.pdf or args.drive:
        _write_pdf(plan, week, args.drive)
    _sync_after_save(week, args)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Days and the Skylight calendar
#
# Runs on the Mac only: the Skylight session (.skylight/, gitignored) lives here.
# A failed sync never fails the save that triggered it; `skylight-sync` retries.
# ─────────────────────────────────────────────────────────────────────────────

SKYLIGHT_DIR = Path(os.environ.get("DIET_SKYLIGHT_DIR") or ROOT / ".skylight")


def _skylight():
    import skylight
    return skylight.Skylight(skylight.TokenStore(SKYLIGHT_DIR / "token.json"))


def _sync_week(week: str, dry_run: bool = False, frame: str | None = None) -> int:
    import skylight
    import skylight_sync
    entry = _find_week(week)
    state = skylight_sync.State(SKYLIGHT_DIR / "state.json")
    try:
        skylight_sync.sync(_skylight(), state, entry["plan"], entry["week_start"],
                           dry_run=dry_run, frame_id=frame)
    except (skylight.SkylightError, RuntimeError) as e:
        raise CLIError(str(e))
    return 0


def _sync_after_save(week: str, args) -> None:
    if getattr(args, "no_skylight", False) or not (SKYLIGHT_DIR / "token.json").exists():
        return
    print("Skylight:")
    try:
        _sync_week(week)
    except CLIError as e:
        print(f"  Skylight not updated ({e}). Retry with: cli.py skylight-sync {week}")


def _parse_days(pairs: list[str]) -> dict:
    import pi_ops
    days = {}
    for pair in pairs:
        meal, sep, day = pair.partition("=")
        if not sep:
            raise CLIError(f"{pair!r}: write it as d1=Mon (or d1= to take it off the calendar)")
        day = day.strip()[:3].title()
        if day and day not in pi_ops.DAYS:
            raise CLIError(f"{pair!r}: day must be one of {', '.join(pi_ops.DAYS)}")
        days[meal.strip()] = day or None
    return days


def cmd_set_days(args) -> int:
    """Put a saved week's dinners on days, or move them, then update Skylight."""
    import pi_ops
    import skylight_sync
    _ensure_mirror(args.offline)
    entry = _find_week(args.week_start)
    week = entry["week_start"]
    payload = {"week_start": week, "days": _parse_days(args.days)}
    try:
        pi_ops.assign_days(entry["plan"], payload["days"])   # fail here, not on the Pi
    except ValueError as e:
        raise CLIError(str(e))
    if args.offline:
        result = _local_op("set_dinner_days", payload)
        print("(local mirror only; re-run without --offline to save it on the Pi)")
    else:
        result = _pi_op("set_dinner_days", payload)
        pull(quiet=True)
    names = {str(d.get("id")): d.get("name") for d in entry["plan"].get("dinners") or []}
    order = {d: n for n, d in enumerate(pi_ops.DAYS)}
    for mid, day in sorted(result["days"].items(), key=lambda kv: order.get(kv[1], 99)):
        when = f"{day} {skylight_sync.day_date(week, day)}" if day else "no day"
        print(f"  {when:<15} {mid} {names.get(mid, '')}")
    if not args.offline:
        _sync_after_save(week, args)
    return 0


def cmd_skylight_login(args) -> int:
    """Interactive: run it yourself (`.venv/bin/python cli.py skylight-login` in a separate Terminal window (the `!` prompt has no typed input)).
    The password is used once and not stored; only the token pair is kept."""
    import getpass
    import skylight
    if not sys.stdin.isatty():
        raise CLIError("skylight-login asks for a password, so it needs a real terminal "
                       "(Claude Code's `!` prompt can't take typed input). Open Terminal and run:\n"
                       f"  cd {str(ROOT)!r} && .venv/bin/python cli.py skylight-login")
    email = args.email or input("Skylight email: ").strip()
    password = getpass.getpass("Skylight password (not saved): ")
    try:
        tokens = skylight.login(email, password, lambda: input(
            "Skylight emailed you a 6-digit code. Code: ").strip())
    except skylight.SkylightError as e:
        raise CLIError(str(e))
    skylight.TokenStore(SKYLIGHT_DIR / "token.json").save(tokens)
    client = _skylight()
    frames = client.frames()
    print(f"Signed in. Session saved in {SKYLIGHT_DIR.relative_to(ROOT) if SKYLIGHT_DIR.is_relative_to(ROOT) else SKYLIGHT_DIR}/token.json.")
    for f in frames:
        print(f"  frame {f['id']}: {f.get('name') or '(unnamed)'}")
    if frames:
        import skylight_sync
        state = skylight_sync.State(SKYLIGHT_DIR / "state.json")
        try:
            frame = skylight_sync.choose_frame(client, state)
            cats = client.meal_categories(frame)
            print("  meal categories: " + ", ".join(skylight.category_name(c) or c["id"] for c in cats))
            state.save()
        except RuntimeError as e:
            print(f"  {e}")
    return 0


def cmd_skylight_sync(args) -> int:
    _ensure_mirror(args.offline)
    return _sync_week(_find_week(args.week_start)["week_start"], args.dry_run, args.frame)


def cmd_push(args) -> int:
    queued = sorted(OUTBOX.glob("*.json")) if OUTBOX.exists() else []
    if not queued:
        print("Nothing queued.")
        return 0
    for path in queued:
        item = json.loads(path.read_text())
        result = _pi_op(item.get("op", "import_week"), item)
        path.unlink()
        print(f"Sent week {item['week_start']} ({result['action']}).")
    pull(quiet=True)
    return 0


AROUND_HELP = ("ISO date of the week being planned. If it already has a lunch week, "
               "the plan is dinners only and is built and checked around those lunches")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def offline(sp):
        sp.add_argument("--offline", action="store_true",
                        help="use the last pulled mirror without contacting the Pi")

    sub.add_parser("pull", help="refresh the local mirror of the Pi's data").set_defaults(fn=cmd_pull)

    sp = sub.add_parser("context", help="print the generation prompt the app would send")
    sp.add_argument("--notes", help="this week's notes, e.g. 'busy Thursday'")
    sp.add_argument("--cuisine", help="cuisine mood for the week")
    sp.add_argument("--use-up", help="ingredients on hand to use up")
    sp.add_argument("--days", type=int, default=5)
    sp.add_argument("--anchors", help="comma-separated anchor ids to use instead of the random sample")
    sp.add_argument("--week-start", help=AROUND_HELP)
    offline(sp)
    sp.set_defaults(fn=cmd_context)

    sp = sub.add_parser("anchors", help="the whole anchor library, one line each")
    sp.add_argument("--role", choices=["main", "side"])
    sp.add_argument("--cuisine")
    sp.add_argument("--search", help="match name, summary or key ingredients")
    sp.add_argument("--show", help="comma-separated ids: print the full entries")
    offline(sp)
    sp.set_defaults(fn=cmd_anchors)

    sp = sub.add_parser("add-anchor", help="screen a recipe, then add it to the Pi's anchor library")
    sp.add_argument("file", help="JSON: name, cuisine, meal_type, role, key_ingredients, summary, "
                                 "technique_notes, source_url, nutrition (optional)")
    offline(sp)
    sp.set_defaults(fn=cmd_add_anchor)

    sp = sub.add_parser("check", help="run the app's structural and constraint checks")
    sp.add_argument("plan")
    sp.add_argument("--days", type=int)
    sp.add_argument("--week-start", help=AROUND_HELP)
    offline(sp)
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("summary", help="one line per meal")
    sp.add_argument("plan")
    sp.set_defaults(fn=cmd_summary)

    sp = sub.add_parser("review-packets", help="write per-meal review instructions for reviewers")
    sp.add_argument("plan")
    sp.add_argument("--out", required=True)
    sp.add_argument("--only", help="comma-separated meal ids (re-proofing after a fix)")
    sp.add_argument("--week-start", help=AROUND_HELP)
    sp.set_defaults(fn=cmd_review_packets)

    sp = sub.add_parser("save", help="check, then write the week to the Pi (and optionally PDF/Drive)")
    sp.add_argument("plan")
    sp.add_argument("--week-start", help="ISO date; defaults to today, as the app does")
    sp.add_argument("--notes", help="JSON file of reviewer findings left unfixed")
    sp.add_argument("--pdf", action="store_true", help="write PDFs/meal_plan_<week>.pdf")
    sp.add_argument("--drive", action="store_true", help="also upload the PDF to Google Drive")
    sp.add_argument("--replace-lunches", action="store_true",
                    help="allow this save to drop lunch-week recipes already saved for the week")
    offline(sp)
    sp.set_defaults(fn=cmd_save)

    def lunch_week_args(sp, plan=True):
        if plan:
            sp.add_argument("plan", help='JSON: {"week_plan": {"lunches": [...], "sunday_prep_list": [...]}}')
        sp.add_argument("--week-start", required=True, help="ISO date of the week the lunches belong to")
        sp.add_argument("--keep", help="comma-separated ids of that week's existing lunches to keep")
        offline(sp)

    sp = sub.add_parser("lunch-context", help="print the prompt for a lunch week (Sunday batch recipes)")
    lunch_week_args(sp, plan=False)
    sp.add_argument("--days", help="days the new recipes cover, e.g. Mon,Tue,Wed,Thu,Fri (the default)")
    sp.add_argument("--recipes", type=int, default=2, help="how many batch recipes share those days")
    sp.add_argument("--anchors", help="comma-separated anchor ids to build the lunches from")
    sp.add_argument("--notes")
    sp.add_argument("--cuisine")
    sp.add_argument("--use-up")
    sp.set_defaults(fn=cmd_lunch_context)

    sp = sub.add_parser("lunch-check", help="check a lunch week against the week it will be saved into")
    lunch_week_args(sp)
    sp.add_argument("--days", help="days the recipes must cover; omit to skip the coverage check")
    sp.set_defaults(fn=cmd_lunch_check)

    sp = sub.add_parser("lunch-packets", help="write review packets for a lunch week")
    lunch_week_args(sp)
    sp.add_argument("--out", required=True)
    sp.add_argument("--only", help="comma-separated lunch ids (re-proofing after a fix)")
    sp.set_defaults(fn=cmd_lunch_packets)

    sp = sub.add_parser("lunch-save", help="check, then save a lunch week into its week on the Pi")
    lunch_week_args(sp)
    sp.add_argument("--days", help="days the recipes must cover; omit to skip the coverage check")
    sp.add_argument("--notes", help="JSON file of reviewer findings left unfixed")
    sp.add_argument("--pdf", action="store_true", help="write PDFs/meal_plan_<week>.pdf for the whole week")
    sp.add_argument("--drive", action="store_true", help="also upload the PDF to Google Drive")
    sp.set_defaults(fn=cmd_lunch_save)

    sp = sub.add_parser("week", help="show a saved week with its ratings; --export to edit it mid-week")
    sp.add_argument("week_start", nargs="?", help="defaults to the newest week")
    sp.add_argument("--export", help="write the plan here for editing, e.g. sessions/<week>/plan.json")
    offline(sp)
    sp.set_defaults(fn=cmd_week)

    sp = sub.add_parser("feedback", help="record how a week went (ratings, notes, dislikes) on the Pi")
    sp.add_argument("file", help='JSON: {"week_start", "meals": [{"id","rating","tags","feedback"}], "constraints": [...]}')
    offline(sp)
    sp.set_defaults(fn=cmd_feedback)

    sp = sub.add_parser("intake", help="read-only: planned vs logged and trend from the Weight tracker")
    sp.add_argument("--apply-ratings", action="store_true", help="also apply ratings queued in the app")
    sp.set_defaults(fn=cmd_intake)

    sp = sub.add_parser("set-target", help="change the lunch and dinner calorie target on the Pi")
    sp.add_argument("calories", type=int)
    offline(sp)
    sp.set_defaults(fn=cmd_set_target)

    sp = sub.add_parser("set-snacks", help="set when/what you snack (plans one snack per snack time)")
    sp.add_argument("notes", help='e.g. "Salty before lunch at work; sweet at 10pm. Like pistachios." ("" clears)')
    offline(sp)
    sp.set_defaults(fn=cmd_set_snacks)

    sub.add_parser("push", help="send weeks queued while the Pi was offline").set_defaults(fn=cmd_push)

    def no_skylight(sp):
        sp.add_argument("--no-skylight", action="store_true",
                        help="don't update the Skylight calendar afterwards")

    for name in ("save", "lunch-save"):
        no_skylight(sub.choices[name])

    sp = sub.add_parser("set-days", help="put a saved week's dinners on days (or move them); updates Skylight")
    sp.add_argument("week_start", help="the saved week, e.g. 2026-11-16")
    sp.add_argument("days", nargs="+", help="d1=Mon d2=Wed ... (d3= takes d3 off the calendar)")
    no_skylight(sp)
    offline(sp)
    sp.set_defaults(fn=cmd_set_days)

    sp = sub.add_parser("skylight-login", help="sign in to Skylight once (interactive; run it yourself)")
    sp.add_argument("--email")
    sp.set_defaults(fn=cmd_skylight_login)

    sp = sub.add_parser("skylight-sync", help="make the Skylight meal planner match a saved week")
    sp.add_argument("week_start", nargs="?", help="defaults to the newest week")
    sp.add_argument("--dry-run", action="store_true", help="show the changes without making them")
    sp.add_argument("--frame", help="Skylight frame id, if the account has several")
    offline(sp)
    sp.set_defaults(fn=cmd_skylight_sync)

    args = p.parse_args(argv)
    os.environ.setdefault("DIET_DATA_DIR", str(MIRROR))
    try:
        return args.fn(args)
    except CLIError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

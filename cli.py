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

_IMPORT_SCRIPT = """
import base64, json, os, sys
os.chdir(os.path.expanduser("{app}"))
sys.path.insert(0, ".")
import data_store
plan = json.loads(base64.b64decode("{payload}"))
week = "{week}"
if any(e["week_start"] == week for e in data_store.load_history()):
    data_store.update_history_plan(plan, week)
    action = "replaced"
else:
    data_store.append_to_history(plan, week)
    action = "added"
data_store.add_recipes_from_plan(plan)
print(json.dumps({{"action": action, "weeks": len(data_store.load_history())}}))
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
        _apply_locally(json.loads(queued.read_text()))
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


def _apply_locally(item: dict) -> None:
    import data_store
    plan, week = item["plan"], item["week_start"]
    if any(e["week_start"] == week for e in data_store.load_history()):
        data_store.update_history_plan(plan, week)
    else:
        data_store.append_to_history(plan, week)
    data_store.add_recipes_from_plan(plan)


def _send(plan: dict, week: str) -> dict:
    payload = base64.b64encode(json.dumps(plan).encode()).decode()
    raw = _run_on_pi(_IMPORT_SCRIPT.format(app=PI_APP, payload=payload, week=week))
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


def run_checks(plan: dict, days: int | None = None) -> tuple[list[str], list[dict], list[dict]]:
    """Structural problems, hard violations, soft violations — the same gates
    meal_planner applies to an API-generated plan."""
    import constraint_check
    import data_store
    from meal_planner import MealPlanError, _validate_plan

    prefs = data_store.load_preferences()
    days = days or len(plan.get("dinners") or [])
    structural = []
    try:
        _validate_plan(plan, days, require_kid_adaptation=bool(prefs.get("children")))
    except MealPlanError as e:
        structural.append(str(e))

    violations = constraint_check.check_week_plan(
        plan,
        user_constraints=data_store.active_constraints_for_prompt(),
        target_sodium_mg=prefs.get("target_sodium_mg", constraint_check.SODIUM_TARGET_DEFAULT_MG),
        history=data_store.load_history()[:6],
    )
    hard = constraint_check.hard_violations(violations)
    soft = [v for v in violations if v["severity"] == "soft"]
    return structural, hard, soft


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
    system_prompt, user_message, require_kid = meal_planner.build_generation_prompts(
        week_notes=args.notes, cuisine_notes=args.cuisine,
        use_up_ingredients=args.use_up, days=args.days,
    )
    print(system_prompt)
    print("\n" + "=" * 79)
    print("USER MESSAGE (what the app would send):")
    print(user_message)
    print(f"\n[{args.days} dinners + {args.days} lunches; kid_adaptation required: {require_kid}]")
    return 0


def cmd_check(args) -> int:
    _ensure_mirror(args.offline)
    plan = load_plan(args.plan)
    structural, hard, soft = run_checks(plan, args.days)
    if structural:
        print("STRUCTURE — the app would reject this plan:")
        for s in structural:
            print(f"  {s}")
    if hard:
        print("HARD — the app would reject this plan:")
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


def cmd_summary(args) -> int:
    plan = load_plan(args.plan)
    print("DINNERS")
    for d in plan.get("dinners") or []:
        n = d.get("nutrition_estimate") or {}
        lunch = " +lunch" if d.get("generates_lunch") else ""
        print(f"  {d.get('id')}  {d.get('name')}")
        print(f"      {d.get('composition')}, {d.get('cook_time_minutes')} min, "
              f"{d.get('primary_equipment')}{lunch} | {n.get('calories_per_adult_serving')} kcal, "
              f"{n.get('protein_g')}g protein, {n.get('fiber_g')}g fiber, {n.get('sodium_mg')}mg Na")
    print("LUNCHES")
    for l in plan.get("lunches") or []:
        src = f"leftover of {l.get('leftover_from_dinner_id')}" if l.get("source") == "leftover" else "standalone"
        print(f"  {l.get('id')}  {l.get('name')}  ({src}, {l.get('reheat')})")
    s = plan.get("week_summary") or {}
    print(f"WEEK  fish {s.get('fish_meal_count')}, red meat {s.get('red_meat_meal_count')}, "
          f"vegetarian {s.get('vegetarian_meal_count')}, est. ${s.get('estimated_weekly_grocery_cost_usd')}")
    if s.get("special_ingredients"):
        print(f"      specials: {', '.join(s['special_ingredients'])}")
    for snack in plan.get("snacks") or []:
        print(f"SNACK {snack.get('name')}")
    return 0


_PACKET_FORMAT = """

Reply with ONLY a JSON object, no prose around it:
{"findings": [{"meal_id": "...", "meal_name": "...", "category": "...", "message": "..."}]}
Allowed categories: %s. An empty list is the common, correct answer."""


def cmd_review_packets(args) -> int:
    """Write the app's own review instructions, one file per meal plus one for
    the week, so reviewers in a session apply exactly the app's rubric."""
    import plan_critique as pc
    plan = load_plan(args.plan)
    only = set(args.only.split(",")) if args.only else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.md"):
        f.unlink()

    dinners = plan.get("dinners") or []
    by_id = {str(d.get("id")): d for d in dinners}
    jobs = [(d, "dinner", None) for d in dinners]
    for l in plan.get("lunches") or []:
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
    print(f"Wrote {len(written)} packets to {out}: {', '.join(written)}")
    return 0


def _session_notes(plan: dict, notes_path: str | None) -> list[dict]:
    """Reviewer findings left unfixed, validated the same way the app
    validates its reviewer's output."""
    if not notes_path:
        return []
    import plan_critique as pc
    raw = json.loads(Path(notes_path).read_text())
    findings = raw.get("findings", raw) if isinstance(raw, dict) else raw
    notes = pc._to_violations(findings, pc._meal_ids(plan), pc.CATEGORIES, cap=100)
    for n in notes:
        if n["code"].removeprefix("critique_") in pc.DEFECT_CATEGORIES:
            n["repair_outcome"] = "left_in_session"
    return notes


def cmd_save(args) -> int:
    _ensure_mirror(args.offline)
    plan = load_plan(args.plan)
    structural, hard, soft = run_checks(plan)
    if structural or hard:
        print("Refusing to save — fix these first (cli.py check shows them):")
        for s in structural:
            print(f"  {s}")
        for v in hard:
            print(_fmt(v))
        return 1

    plan["_soft_violations"] = soft + _session_notes(plan, args.notes)
    week = args.week_start or date.today().isoformat()
    item = {"plan": plan, "week_start": week}

    sent = False
    if not args.offline:
        try:
            result = _send(plan, week)
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
        _apply_locally(item)
        print(f"Queued in {OUTBOX}/ — run `cli.py push` when the Pi is back.")

    if args.pdf or args.drive:
        import pdf_export
        import shopping
        pdf = pdf_export.build_pdf_bytes(plan, shopping.build_shopping_list(plan), week_start=week)
        name = f"meal_plan_{week}.pdf"
        path = ROOT / "PDFs" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(pdf)
        print(f"PDF: {path.relative_to(ROOT)}")
        if args.drive:
            import drive_upload
            url = drive_upload.upload_pdf_to_drive(pdf, name)
            print(f"Uploaded to Drive{': ' + url if url else '.'}")
    return 0


def cmd_push(args) -> int:
    queued = sorted(OUTBOX.glob("*.json")) if OUTBOX.exists() else []
    if not queued:
        print("Nothing queued.")
        return 0
    for path in queued:
        item = json.loads(path.read_text())
        result = _send(item["plan"], item["week_start"])
        path.unlink()
        print(f"Sent week {item['week_start']} ({result['action']}).")
    pull(quiet=True)
    return 0


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
    offline(sp)
    sp.set_defaults(fn=cmd_context)

    sp = sub.add_parser("check", help="run the app's structural and constraint checks")
    sp.add_argument("plan")
    sp.add_argument("--days", type=int)
    offline(sp)
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("summary", help="one line per meal")
    sp.add_argument("plan")
    sp.set_defaults(fn=cmd_summary)

    sp = sub.add_parser("review-packets", help="write per-meal review instructions for reviewers")
    sp.add_argument("plan")
    sp.add_argument("--out", required=True)
    sp.add_argument("--only", help="comma-separated meal ids (re-proofing after a fix)")
    sp.set_defaults(fn=cmd_review_packets)

    sp = sub.add_parser("save", help="check, then write the week to the Pi (and optionally PDF/Drive)")
    sp.add_argument("plan")
    sp.add_argument("--week-start", help="ISO date; defaults to today, as the app does")
    sp.add_argument("--notes", help="JSON file of reviewer findings left unfixed")
    sp.add_argument("--pdf", action="store_true", help="write PDFs/meal_plan_<week>.pdf")
    sp.add_argument("--drive", action="store_true", help="also upload the PDF to Google Drive")
    offline(sp)
    sp.set_defaults(fn=cmd_save)

    sub.add_parser("push", help="send weeks queued while the Pi was offline").set_defaults(fn=cmd_push)

    args = p.parse_args(argv)
    os.environ.setdefault("DIET_DATA_DIR", str(MIRROR))
    try:
        return args.fn(args)
    except CLIError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

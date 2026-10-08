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
        _local_op("import_week", json.loads(queued.read_text()))
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


def run_checks(plan: dict, days: int | None = None) -> tuple[list[str], list[dict], list[dict]]:
    """Structural problems, hard violations, soft violations — the same gates
    meal_planner applies to an API-generated plan."""
    import constraint_check
    import data_store
    from meal_planner import MealPlanError, _validate_plan

    prefs = data_store.load_preferences()
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
        anchor_ids=[i.strip() for i in args.anchors.split(",") if i.strip()] if args.anchors else None,
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
    _print_summary(load_plan(args.plan))
    return 0


def _print_summary(plan: dict, ratings: dict | None = None) -> None:
    """One line per meal. `ratings` maps a meal name to its favorite entry."""
    ratings = ratings or {}

    def rated(name):
        f = ratings.get(name)
        return f"  [{f['rating']}/5{' — ' + f['feedback'] if f.get('feedback') else ''}]" if f else ""

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
        print(f"  {l.get('id')}  {l.get('name')}  ({src}, {l.get('reheat')}){rated(l.get('name'))}")
    s = plan.get("week_summary") or {}
    print(f"WEEK  fish {s.get('fish_meal_count')}, red meat {s.get('red_meat_meal_count')}, "
          f"vegetarian {s.get('vegetarian_meal_count')}, est. ${s.get('estimated_weekly_grocery_cost_usd')}")
    if s.get("special_ingredients"):
        print(f"      specials: {', '.join(s['special_ingredients'])}")
    for snack in plan.get("snacks") or []:
        print(f"SNACK {snack.get('name')}")


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
        result = _pi_op("import_week", item)
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
    sp.add_argument("--anchors", help="comma-separated anchor ids to use instead of the random sample")
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

    sp = sub.add_parser("week", help="show a saved week with its ratings; --export to edit it mid-week")
    sp.add_argument("week_start", nargs="?", help="defaults to the newest week")
    sp.add_argument("--export", help="write the plan here for editing, e.g. sessions/<week>/plan.json")
    offline(sp)
    sp.set_defaults(fn=cmd_week)

    sp = sub.add_parser("feedback", help="record how a week went (ratings, notes, dislikes) on the Pi")
    sp.add_argument("file", help='JSON: {"week_start", "meals": [{"id","rating","tags","feedback"}], "constraints": [...]}')
    offline(sp)
    sp.set_defaults(fn=cmd_feedback)

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

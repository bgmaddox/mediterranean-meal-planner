"""
pi_ops.py
---------
The operations that write the Pi's data, written once.

cli.py sends this file's source to the Pi and runs one function there against
the Pi's own data_store, so the Pi stays the only writer of its data. Tests and
offline mode import this module and run the same function against a local
data_store.

Every function takes (data_store, payload) and returns a JSON-able result.
Keep this file stdlib-only, compatible with the Pi's Python (3.13), and limited
to data_store functions that already exist on the deployed version — it runs
against whatever version the Pi has.
"""


def import_week(ds, p):
    """Add a week to history, or replace it if that week_start is already there,
    and add its meals to the recipe library."""
    plan, week = p["plan"], p["week_start"]
    if any(e["week_start"] == week for e in ds.load_history()):
        ds.update_history_plan(plan, week)
        action = "replaced"
    else:
        ds.append_to_history(plan, week)
        action = "added"
    ds.add_recipes_from_plan(plan)
    return {"action": action, "weeks": len(ds.load_history())}


def apply_feedback(ds, p):
    """Record how a week went.

    payload: {"week_start": ..., "meals": [{"id", "rating", "tags", "feedback"}],
              "constraints": ["No fennel — disliked", ...]}

    Every rated meal becomes (or updates) a favorite, low ratings included: the
    prompt reads 1-2 stars as "include rarely", so a bad rating is what keeps a
    dish from coming back. Notes are prefixed with the week, so a favorite's
    note accumulates a history instead of being overwritten.
    """
    week = p["week_start"]
    entry = next((e for e in ds.load_history() if e["week_start"] == week), None)
    if entry is None:
        raise ValueError(f"No week {week} in history.")
    plan = entry["plan"]
    meals = {}
    for key, meal_type in (("dinners", "dinner"), ("lunches", "lunch")):
        for m in plan.get(key) or []:
            meals[str(m.get("id"))] = (m, meal_type)

    added, updated = [], []
    for fb in p.get("meals") or []:
        if str(fb.get("id")) not in meals:
            raise ValueError(f"Week {week} has no meal {fb.get('id')!r}.")
        meal, meal_type = meals[str(fb["id"])]
        rating = int(fb["rating"])
        note = (fb.get("feedback") or "").strip()
        dated = f"{week}: {note}" if note else ""
        tags = list(fb.get("tags") or [])

        existing = next((f for f in ds.load_favorites() if f["name"] == meal["name"]), None)
        if existing is None:
            ds.save_favorite(meal, meal_type, rating=rating, tags=tags, feedback=dated)
            added.append(meal["name"])
        else:
            merged_tags = existing.get("tags", []) + [t for t in tags if t not in existing.get("tags", [])]
            old = existing.get("feedback", "")
            ds.update_favorite(existing["id"], rating=rating, tags=merged_tags,
                               feedback=" | ".join(x for x in (old, dated) if x))
            updated.append(meal["name"])

        # save_favorite stamps today; the meal was made the week it was planned.
        favorites = ds.load_favorites()
        for f in favorites:
            if f["name"] == meal["name"]:
                f["last_made"] = week
        ds._write(ds.FAVORITES_FILE, favorites)

    have = {c["text"].strip().lower() for c in ds.load_constraints()}
    constraints_added = []
    for text in p.get("constraints") or []:
        if text.strip() and text.strip().lower() not in have:
            ds.add_constraint(text)
            have.add(text.strip().lower())
            constraints_added.append(text.strip())

    return {"favorites_added": added, "favorites_updated": updated,
            "constraints_added": constraints_added}


def add_anchor(ds, p):
    """Add a recipe to the user anchor library (the gitignored user file).

    Written directly rather than through ds.add_anchor_recipe, which on the
    deployed version can't record `role`, `source_url` or `notes_sourced`.
    A recipe already in the library — same source URL or same name — is not
    added twice.
    """
    name = p["name"].strip()
    for a in ds.load_anchor_recipes():
        same_url = p.get("source_url") and a.get("source_url") == p["source_url"]
        if same_url or a["name"].strip().lower() == name.lower():
            return {"action": "exists", "id": a["id"]}

    existing = {a["id"] for a in ds.load_anchor_recipes()}
    base = ds._slugify(name)
    rid, n = base, 2
    while rid in existing:
        rid, n = f"{base}-{n}", n + 1

    entry = {
        "id": rid,
        "name": name,
        "cuisine": (p.get("cuisine") or "").strip() or "Other",
        "meal_type": p.get("meal_type") if p.get("meal_type") in ("dinner", "lunch", "either") else "either",
        "role": p.get("role") if p.get("role") in ("main", "side") else "main",
        "key_ingredients": [k.strip() for k in p.get("key_ingredients") or [] if k.strip()],
        "summary": (p.get("summary") or "").strip(),
        "technique_notes": (p.get("technique_notes") or "").strip(),
        "source_url": p.get("source_url") or "",
        "notes_sourced": bool(p.get("source_url")),
    }
    if p.get("nutrition"):
        entry["nutrition"] = p["nutrition"]
    user = ds.load_anchor_user_recipes()
    user.append(entry)
    ds._write(ds.ANCHOR_RECIPES_USER_FILE, user)
    return {"action": "added", "id": rid}

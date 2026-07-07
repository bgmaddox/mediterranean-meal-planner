"""
data_store.py
-------------
All reads and writes to the data/ JSON files.

One function per operation. Keeps file I/O out of app.py and other modules.
All functions are synchronous; Streamlit's single-threaded model makes this safe.

Files managed:
  data/history.json     — MealHistoryEntry list, newest first, capped at 12 weeks
  data/favorites.json   — FavoriteMeal list
  data/constraints.json — ConstraintEntry list
  data/preferences.json — UserPreferences dict (single object)
"""

import json
import random
import re
import uuid
from datetime import date
from pathlib import Path

from schemas import (
    AnchorRecipe,
    ConstraintEntry,
    FavoriteMeal,
    MealHistoryEntry,
    RecipeLibraryEntry,
    UserPreferences,
    WeekPlan,
    DinnerMeal,
)

DATA_DIR = Path(__file__).parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"
FAVORITES_FILE = DATA_DIR / "favorites.json"
CONSTRAINTS_FILE = DATA_DIR / "constraints.json"
PREFERENCES_FILE = DATA_DIR / "preferences.json"
LIBRARY_FILE = DATA_DIR / "recipe_library.json"
ANCHOR_RECIPES_FILE = DATA_DIR / "anchor_recipes.json"          # shipped seed (git-tracked)
ANCHOR_RECIPES_USER_FILE = DATA_DIR / "anchor_recipes_user.json"  # user additions (gitignored)

MAX_HISTORY_WEEKS = 12     # stored
HISTORY_WEEKS_FOR_PROMPT = 6  # passed to Claude


def _read(path: Path) -> list | dict:
    with open(path) as f:
        return json.load(f)


def _write(path: Path, data: list | dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# History
# ─────────────────────────────────────────────────────────────────────────────

def load_history() -> list[MealHistoryEntry]:
    """Return all stored history entries, newest first."""
    return _read(HISTORY_FILE)


def history_for_prompt() -> list[MealHistoryEntry]:
    """Return only the last HISTORY_WEEKS_FOR_PROMPT entries for Claude's context."""
    return load_history()[:HISTORY_WEEKS_FOR_PROMPT]


def update_history_plan(week_plan: WeekPlan, week_start: str) -> None:
    """
    Overwrite the saved plan for an existing history entry identified by week_start.
    If no entry with that week_start exists, does nothing.
    """
    history = load_history()
    for entry in history:
        if entry["week_start"] == week_start:
            entry["plan"] = week_plan
            entry["meal_names"] = [d["name"] for d in week_plan["dinners"]]
            _write(HISTORY_FILE, history)
            return


def append_to_history(week_plan: WeekPlan, week_start: str | None = None) -> None:
    """
    Save this week's plan to history.

    Parameters
    ----------
    week_plan : WeekPlan
        The generated plan to record.
    week_start : str, optional
        ISO date string for the Monday of this week. Defaults to today.
    """
    if week_start is None:
        week_start = date.today().isoformat()

    entry: MealHistoryEntry = {
        "week_start": week_start,
        "meal_names": [d["name"] for d in week_plan["dinners"]],
        "plan": week_plan,
    }

    history = load_history()
    history.insert(0, entry)  # newest first
    history = history[:MAX_HISTORY_WEEKS]  # cap
    _write(HISTORY_FILE, history)


# ─────────────────────────────────────────────────────────────────────────────
# Favorites
# ─────────────────────────────────────────────────────────────────────────────

def load_favorites() -> list[FavoriteMeal]:
    return _read(FAVORITES_FILE)


def save_favorite(
    meal: DinnerMeal,
    meal_type: str = "dinner",
    rating: int = 3,
    tags: list[str] | None = None,
    feedback: str = "",
) -> FavoriteMeal:
    """
    Save a meal as a favorite. Returns the saved FavoriteMeal dict.
    Raises ValueError if a favorite with the same name already exists.
    """
    favorites = load_favorites()
    if any(f["name"] == meal["name"] for f in favorites):
        raise ValueError(f"'{meal['name']}' is already in favorites.")

    entry: FavoriteMeal = {
        "id": str(uuid.uuid4()),
        "name": meal["name"],
        "rating": max(1, min(5, rating)),
        "tags": tags or [],
        "feedback": feedback,
        "last_made": date.today().isoformat(),
        "meal_type": meal_type,
        "source_meal": meal,
    }
    favorites.append(entry)
    _write(FAVORITES_FILE, favorites)
    return entry


def update_favorite(
    favorite_id: str,
    rating: int | None = None,
    tags: list[str] | None = None,
    feedback: str | None = None,
) -> FavoriteMeal | None:
    """
    Update rating, tags, or feedback on an existing favorite.
    Returns the updated entry, or None if not found.
    """
    favorites = load_favorites()
    for fav in favorites:
        if fav["id"] == favorite_id:
            if rating is not None:
                fav["rating"] = max(1, min(5, rating))
            if tags is not None:
                fav["tags"] = tags
            if feedback is not None:
                fav["feedback"] = feedback
            _write(FAVORITES_FILE, favorites)
            return fav
    return None


def delete_favorite(favorite_id: str) -> bool:
    """Remove a favorite by id. Returns True if found and deleted."""
    favorites = load_favorites()
    updated = [f for f in favorites if f["id"] != favorite_id]
    if len(updated) == len(favorites):
        return False
    _write(FAVORITES_FILE, updated)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Constraints
# ─────────────────────────────────────────────────────────────────────────────

def load_constraints() -> list[ConstraintEntry]:
    # constraints.json is gitignored (personal data) and not shipped; treat a
    # missing file as "no custom constraints". It is recreated on first add.
    if not CONSTRAINTS_FILE.exists():
        return []
    return _read(CONSTRAINTS_FILE)


def active_constraints_for_prompt() -> list[str]:
    """Return the text of all active user-defined constraints (passed to Claude)."""
    return [c["text"] for c in load_constraints() if c["active"]]


def add_constraint(text: str) -> ConstraintEntry:
    """Add a new active constraint. Returns the new entry."""
    constraints = load_constraints()
    entry: ConstraintEntry = {
        "id": str(uuid.uuid4()),
        "text": text.strip(),
        "active": True,
    }
    constraints.append(entry)
    _write(CONSTRAINTS_FILE, constraints)
    return entry


def toggle_constraint(constraint_id: str) -> bool | None:
    """
    Toggle the active flag on a constraint.
    Returns the new active value, or None if not found.
    """
    constraints = load_constraints()
    for c in constraints:
        if c["id"] == constraint_id:
            c["active"] = not c["active"]
            _write(CONSTRAINTS_FILE, constraints)
            return c["active"]
    return None


def delete_constraint(constraint_id: str) -> bool:
    """Remove a constraint by id. Returns True if found and deleted."""
    constraints = load_constraints()
    updated = [c for c in constraints if c["id"] != constraint_id]
    if len(updated) == len(constraints):
        return False
    _write(CONSTRAINTS_FILE, updated)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Preferences
# ─────────────────────────────────────────────────────────────────────────────

def load_preferences() -> UserPreferences:
    return _read(PREFERENCES_FILE)


def save_preferences(prefs: UserPreferences) -> None:
    _write(PREFERENCES_FILE, prefs)


def update_preferences(**kwargs) -> UserPreferences:
    """
    Update one or more preference keys. Unknown keys are ignored.
    Returns the updated preferences.

    Example:
        update_preferences(lunch_adult_count=2, budget="$200/week")
    """
    prefs = load_preferences()
    valid_keys = {
        "children",
        "lunch_adult_count",
        "budget",
        "portion_scale",
        "use_anchor_recipes",
        "include_snacks",
        "recipient_email",
        "auto_send_email",
        "target_calories_lunch_dinner",
        "target_protein_g",
        "target_fiber_g",
    }
    for key, value in kwargs.items():
        if key in valid_keys:
            prefs[key] = value
    save_preferences(prefs)
    return prefs


# ─────────────────────────────────────────────────────────────────────────────
# Recipe Library
# ─────────────────────────────────────────────────────────────────────────────

def load_recipe_library() -> list[RecipeLibraryEntry]:
    if not LIBRARY_FILE.exists():
        return []
    return _read(LIBRARY_FILE)


def add_recipes_from_plan(week_plan: WeekPlan) -> None:
    """Auto-save all meals from a generated plan. Deduplicates by name."""
    library = load_recipe_library()
    by_name = {r["name"]: r for r in library}
    today = date.today().isoformat()

    for meal in week_plan.get("dinners", []):
        if meal["name"] in by_name:
            by_name[meal["name"]]["last_generated"] = today
            by_name[meal["name"]]["meal"] = meal
        else:
            entry: RecipeLibraryEntry = {
                "id": str(uuid.uuid4()),
                "name": meal["name"],
                "meal_type": "dinner",
                "last_generated": today,
                "meal": meal,
            }
            library.append(entry)
            by_name[meal["name"]] = entry

    for meal in week_plan.get("lunches", []):
        if meal["name"] in by_name:
            by_name[meal["name"]]["last_generated"] = today
            by_name[meal["name"]]["meal"] = meal
        else:
            entry: RecipeLibraryEntry = {
                "id": str(uuid.uuid4()),
                "name": meal["name"],
                "meal_type": "lunch",
                "last_generated": today,
                "meal": meal,
            }
            library.append(entry)
            by_name[meal["name"]] = entry

    _write(LIBRARY_FILE, library)


def delete_from_library(recipe_id: str) -> bool:
    """Remove a recipe from the library by id. Returns True if found and deleted."""
    library = load_recipe_library()
    updated = [r for r in library if r["id"] != recipe_id]
    if len(updated) == len(library):
        return False
    _write(LIBRARY_FILE, updated)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Anchor Recipes (curated real-world seed set used to ground generation)
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Turn a recipe name into a URL-safe id stub, e.g. 'Greek Lemon Chicken' -> 'greek-lemon-chicken'."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "recipe"


def load_anchor_seed_recipes() -> list[AnchorRecipe]:
    """Return only the shipped (git-tracked) anchor seed recipes."""
    if not ANCHOR_RECIPES_FILE.exists():
        return []
    return _read(ANCHOR_RECIPES_FILE)


def load_anchor_user_recipes() -> list[AnchorRecipe]:
    """Return only the user-added anchor recipes (gitignored, never overwritten by deploys)."""
    if not ANCHOR_RECIPES_USER_FILE.exists():
        return []
    return _read(ANCHOR_RECIPES_USER_FILE)


def load_anchor_recipes() -> list[AnchorRecipe]:
    """
    Return all anchor recipes: the shipped seed plus user additions.

    User recipes are kept in a separate gitignored file so they survive deploys
    and never conflict with updates to the shipped seed. A user entry whose id
    matches a seed entry overrides it (keeps the seed's position).

    Missing `role` fields are defaulted to 'main' for backwards compatibility
    with seed entries that predate the role field.
    """
    by_id: dict[str, AnchorRecipe] = {r["id"]: r for r in load_anchor_seed_recipes()}
    for r in load_anchor_user_recipes():
        by_id[r["id"]] = r
    recipes = list(by_id.values())
    for r in recipes:
        if "role" not in r:
            r["role"] = "main"
    return recipes


def add_anchor_recipe(
    name: str,
    cuisine: str = "",
    meal_type: str = "either",
    key_ingredients: list[str] | None = None,
    summary: str = "",
    technique_notes: str = "",
) -> AnchorRecipe:
    """
    Add a user-defined anchor recipe to the gitignored user file.

    Returns the new entry. Raises ValueError if the name is blank.
    """
    name = name.strip()
    if not name:
        raise ValueError("Recipe name is required.")
    if meal_type not in ("dinner", "lunch", "either"):
        meal_type = "either"

    existing_ids = {r["id"] for r in load_anchor_recipes()}
    base = _slugify(name)
    rid = base
    n = 2
    while rid in existing_ids:
        rid = f"{base}-{n}"
        n += 1

    entry: AnchorRecipe = {
        "id": rid,
        "name": name,
        "cuisine": cuisine.strip() or "Other",
        "meal_type": meal_type,
        "key_ingredients": [k.strip() for k in (key_ingredients or []) if k.strip()],
        "summary": summary.strip(),
        "technique_notes": technique_notes.strip(),
    }
    user = load_anchor_user_recipes()
    user.append(entry)
    _write(ANCHOR_RECIPES_USER_FILE, user)
    return entry


def delete_anchor_recipe(recipe_id: str) -> bool:
    """
    Delete a user-added anchor recipe. Returns True if found and deleted.

    Only user-added recipes can be deleted; shipped seed recipes are read-only.
    """
    user = load_anchor_user_recipes()
    updated = [r for r in user if r["id"] != recipe_id]
    if len(updated) == len(user):
        return False
    _write(ANCHOR_RECIPES_USER_FILE, updated)
    return True


def select_anchor_recipes(count: int = 10, days: int = 5) -> list[AnchorRecipe]:
    """
    Return a rotating random sample of anchor recipes to inject as inspiration.

    Excludes lunch-only recipes from crowding out dinner ideas, but keeps
    'either' recipes in the pool. Reserves a dedicated side/mezze pool so
    vegetable-forward and mezze compositions have real-recipe grounding.
    The sample is randomized each call so weekly plans draw on different
    anchors over time.

    Parameters
    ----------
    count : int
        How many anchors to return. Capped at the library size.
    days : int
        Days being planned; used to scale the sample up a little for longer weeks
        and to set how many side slots to reserve.
    """
    anchors = load_anchor_recipes()
    if not anchors:
        return []

    # Split into three pools: dinner mains, lunch-only, and side/mezze.
    sides = [a for a in anchors if a.get("role") == "side"]
    dinner_pool = [a for a in anchors if a.get("role") != "side" and a.get("meal_type") in ("dinner", "either")]
    lunch_only = [a for a in anchors if a.get("role") != "side" and a.get("meal_type") == "lunch"]

    target = min(max(count, days + 3), len(anchors))

    # Reserve 2-3 side slots, scaling with days (max(2, days // 2)), capped at pool size.
    side_slots = min(len(sides), max(2, days // 2))
    remaining = target - side_slots

    # Reserve ~1/4 of remaining slots for lunch-only ideas when available.
    lunch_slots = min(len(lunch_only), max(1, remaining // 4))
    dinner_slots = max(remaining - lunch_slots, 0)

    sample = random.sample(dinner_pool, min(dinner_slots, len(dinner_pool)))
    sample += random.sample(lunch_only, min(lunch_slots, len(lunch_only)))
    sample += random.sample(sides, min(side_slots, len(sides)))
    random.shuffle(sample)
    return sample

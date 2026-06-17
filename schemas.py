"""
schemas.py
----------
TypedDicts for every data structure in the app.

Single source of truth for all data shapes. All modules import from here.
If the Claude output schema changes, update OUTPUT_SCHEMA in system_prompt.py
AND the corresponding TypedDicts here.

Structure mirrors the Claude JSON output (WeekPlan / DinnerMeal / LunchMeal)
plus the app's own data (FavoriteMeal / MealHistoryEntry / ConstraintEntry / UserPreferences).
"""

from typing import TypedDict, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Claude output types — mirror OUTPUT_SCHEMA in system_prompt.py
# ─────────────────────────────────────────────────────────────────────────────

class Ingredient(TypedDict):
    name: str
    quantity: str
    unit: str
    pantry_staple: bool
    special: bool          # True → non-staple purchased this week; must appear in 2+ meals


class DinnerServings(TypedDict):
    adults: int
    kids: int


class NutritionEstimate(TypedDict):
    """Claude's rough estimate per adult serving. Disclaimer: ±15–20% accuracy."""
    calories_per_adult_serving: int
    protein_g: int
    fiber_g: int
    fat_g: int
    saturated_fat_note: Optional[str]   # e.g. "Low — salmon fat is mostly unsaturated"


class ServingSize(TypedDict):
    """Recommended portion size for one component of a meal."""
    component: str          # e.g. "salmon fillet", "quinoa", "roasted vegetables"
    adult_portion: str      # e.g. "4 oz (113g) cooked"
    kid_portion: str        # e.g. "2 oz cooked" — empty string for lunch-only meals


class CostEstimate(TypedDict):
    """Claude's estimate of ingredient cost at a typical US grocery store (e.g. Publix/Kroger).
    Non-pantry ingredients only. Shared ingredients counted per-recipe at prorated cost.
    Accuracy: ±20–30% depending on store, region, and sales."""
    total_ingredient_cost_usd: float    # Total non-pantry ingredient cost for this recipe
    cost_per_serving_usd: float         # total / number of servings


class DinnerMeal(TypedDict):
    id: str                             # 'd1' – 'd5'
    name: str
    cook_time_minutes: int
    primary_equipment: str              # e.g. 'sheet pan / oven', 'Instant Pot', 'Blackstone griddle'
    servings: DinnerServings
    generates_lunch: bool               # True → this dinner is scaled up to provide one adult lunch
    lunch_scaling_instructions: Optional[str]
    ingredients: list[Ingredient]
    instructions: list[str]
    sunday_prep: Optional[str]          # Task string for Sunday prep list, or None
    kid_adaptation: str                 # Always required; never None
    health_highlights: list[str]
    uric_acid_tip: Optional[str]        # Purine-reduction technique when relevant
    nutrition_estimate: NutritionEstimate
    cost_estimate: CostEstimate
    serving_sizes: list[ServingSize]


class LunchMeal(TypedDict):
    id: str                             # 'l1' – 'l5'
    name: str
    source: str                         # 'standalone' or 'leftover'
    leftover_from_dinner_id: Optional[str]
    reheat: str                         # 'microwave' or 'none (cold)'
    prep_at_lunchtime_minutes: int      # Target ≤5
    servings: int
    ingredients: list[Ingredient]
    pack_instructions: str
    health_highlights: list[str]
    nutrition_estimate: NutritionEstimate
    cost_estimate: CostEstimate
    serving_sizes: list[ServingSize]


class SundayPrepTask(TypedDict):
    task: str
    yields_for: list[str]               # Meal names this prep supports
    storage: str                        # How to store and how long it holds


class WeekSummary(TypedDict):
    fish_meal_count: int
    red_meat_meal_count: int
    vegetarian_meal_count: int
    special_ingredients: list[str]
    ingredient_overlap_notes: str
    estimated_weekly_grocery_cost_usd: float  # Sum across all meals, deduped shared ingredients


class WeekPlan(TypedDict):
    dinners: list[DinnerMeal]
    lunches: list[LunchMeal]
    sunday_prep_list: list[SundayPrepTask]
    week_summary: WeekSummary


# ─────────────────────────────────────────────────────────────────────────────
# App data types — stored in data/*.json
# ─────────────────────────────────────────────────────────────────────────────

class FavoriteMeal(TypedDict):
    """A meal saved by the user from a generated week plan."""
    id: str                             # UUID string
    name: str
    rating: int                         # 1–5 stars
    tags: list[str]                     # e.g. ['kids loved it', 'great leftover', 'make again soon']
    feedback: str                       # Free-form note (empty string if none)
    last_made: str                      # ISO date string e.g. '2026-03-24'
    meal_type: str                      # 'dinner' or 'lunch'
    source_meal: Optional[DinnerMeal]   # The full meal dict, for reference


class RecipeLibraryEntry(TypedDict):
    """A meal ever generated, auto-saved to the recipe library."""
    id: str                             # UUID string
    name: str
    meal_type: str                      # 'dinner' or 'lunch'
    last_generated: str                 # ISO date of most recent generation
    meal: DinnerMeal                    # Full meal dict (DinnerMeal or LunchMeal)


class AnchorRecipe(TypedDict):
    """A real-world recipe used to anchor Claude's generation (curated seed set).

    These are compact descriptions of existing dishes — not full meal plans. A
    rotating sample is injected into the system prompt as inspiration; Claude
    adapts them freely to satisfy the health constraints, household, and season.
    Stored in data/anchor_recipes.json and hand-editable.
    """
    id: str                             # Readable slug, e.g. 'greek-lemon-chicken'
    name: str                           # Dish name, e.g. 'Greek Lemon Chicken with Roasted Potatoes'
    cuisine: str                        # e.g. 'Greek', 'Thai', 'Mexican'
    meal_type: str                      # 'dinner', 'lunch', or 'either'
    key_ingredients: list[str]          # The defining (mostly non-pantry) ingredients
    summary: str                        # One-line method, e.g. 'Marinate chicken in lemon-oregano-garlic, roast with potatoes.'
    technique_notes: str                # Tested, non-obvious procedural tips Claude should follow (not a full recipe), e.g. 'Separate bok choy stems from leaves; sear stems 2 min before adding leaves.' May be empty.


class MealHistoryEntry(TypedDict):
    """A single week's worth of meals, recorded after generation."""
    week_start: str                     # ISO date string e.g. '2026-03-24' (Monday of that week)
    meal_names: list[str]               # Dinner names only (used for Claude's history context)
    plan: WeekPlan                      # Full plan stored for reference


class ConstraintEntry(TypedDict):
    """A user-defined food constraint, shown and toggled in Settings."""
    id: str                             # UUID string
    text: str                           # Plain-English constraint e.g. 'No cilantro'
    active: bool                        # When False, constraint is saved but not sent to Claude


class Child(TypedDict):
    """A child in the household, for portion sizing and kid adaptations."""
    name: str   # Display name — empty string if unnamed
    age: int    # Age in years


class UserPreferences(TypedDict):
    """Top-level user preferences stored in data/preferences.json."""
    # Household
    children: list[Child]              # e.g. [{"name": "Emma", "age": 5}, {"name": "", "age": 2}]

    # Meal planning
    lunch_adult_count: int              # How many adults bring lunch (currently 1; configurable)
    budget: Optional[str]              # e.g. '$150/week' or None
    portion_scale: float               # Default 1.0; 0.9 = ~10% smaller, 1.1 = ~10% larger
    use_anchor_recipes: bool           # Default True; inject curated real recipes as inspiration

    # Email
    recipient_email: Optional[str]     # Where to send the weekly email report
    auto_send_email: bool              # Auto-send on generation? (currently always False)

    # Nutrition targets — cover lunch + dinner only (not breakfast/snacks)
    target_calories_lunch_dinner: int  # Default: 1450 (supports ~2100 kcal/day total)
    target_protein_g: int              # Default: 100g (from L+D; supports weight loss at goal weight)
    target_fiber_g: int                # Default: 18g  (from L+D; supports ≥25g/day total)

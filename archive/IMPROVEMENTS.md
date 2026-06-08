# Meal Planner — Planned Improvements

## Feature 1: Portion Reduction (~10% fewer leftovers)

**The problem:** Claude generates ingredient quantities for exact servings (2 adults + 2 kids). Portions are slightly too large, leaving leftovers.

**Approach:** Add a `portion_scale` setting to `preferences.json` (default `1.0`, user can set `0.9` for ~10% reduction). Pass it into `system_prompt.py` → `build_system_prompt()`, which injects a single instruction into the Meal Planning Rules section: *"Scale all ingredient quantities down by 10% from a standard serving to reduce leftovers."*

**Files to touch:**
- `schemas.py` — add `portion_scale: float` field to `UserPreferences`
- `data_store.py` — include `portion_scale` in `update_preferences()`
- `system_prompt.py` — add `portion_scale` param to `build_system_prompt()`, inject scaling instruction when ≠ 1.0
- `meal_planner.py` — pass preference through to prompt builder
- `app.py` (Settings tab) — add a slider or select: "Portion size: Normal / Slightly less (−10%) / Slightly more (+10%)"

**Why this approach:** Keeping it in the prompt (not post-processing the quantities) is the right call — Claude handles the math contextually (e.g., it knows "3/4 lb salmon" shouldn't become "0.675 lb"). Trying to multiply ingredient quantities after the fact is fragile because they're strings like `"1 large"` or `"handful"`.

---

## Feature 2: Use-up Ingredients (use what's on hand)

**The problem:** The family has leftover produce/ingredients and wants next week's recipes to incorporate them.

**Approach:** Add a "Ingredients to use up" text area on the **Generate tab** (alongside the existing week notes and cuisine notes fields). This is ephemeral — entered at generation time, not stored. Pass it to `build_system_prompt()` as a new `use_up_ingredients` parameter, which adds a dedicated section to the prompt:

```
## Ingredients to Use Up
The following items are already on hand and should be prioritized in this week's recipes.
Work them into at least 1–2 meals. Do not add them to the shopping list.
  - Carrots (approx. 1 lb)
  - Baby spinach (one bag, starting to wilt)
```

**Files to touch:**
- `system_prompt.py` — add `use_up_ingredients: str | None` param, add section to prompt
- `meal_planner.py` — thread the new param through `build_generation_prompts()`
- `app.py` (Generate tab) — add a `st.text_area()` for this input below the existing notes fields

**Why this approach:** Ephemeral input (not stored) is right here — leftover ingredients change week to week. The user already has a pattern for free-text week-specific input (week notes, cuisine notes), so this follows the same UX. No new data file needed.

---

## Feature 3: Variable Number of Meal Days (3–7)

**The problem:** Some weeks need fewer than 5 dinners and lunches (travel, holidays, etc.).

**Approach:** Add a numeric input on the **Generate tab** for "Days of meals this week" (default 5, range 3–7). Pass it through to `build_system_prompt()` where it:
1. Changes `## Dinners — 5 per week` → `## Dinners — N per week`
2. Changes `## Lunches — 5 per week` → `## Lunches — N per week`
3. Adjusts the OUTPUT_SCHEMA IDs to go `d1`–`dN` and `l1`–`lN`
4. Adjusts the fish/protein balance targets proportionally (e.g., 3 days = "1–2 fish meals, ≤1 red meat")

**Files to touch:**
- `system_prompt.py` — add `days: int` param (default 5), adjust planning rules and schema IDs dynamically
- `meal_planner.py` — thread `days` through `build_generation_prompts()`
- `app.py` (Generate tab) — add `st.number_input()` for days (3–7)

**Why this approach:** This is purely a prompt-level change — the schema and parsing code already handles variable-length lists from Claude. No schema migration needed. Scaling protein targets proportionally (rather than just saying "fewer meals") produces better-balanced short weeks.

---

## Summary

| Feature | New UI element | New prompt param | Data change |
|---|---|---|---|
| Portion scale | Settings slider (persisted) | `portion_scale: float` | `preferences.json` |
| Use-up ingredients | Generate tab text area (ephemeral) | `use_up_ingredients: str` | None |
| Days of meals | Generate tab number input (ephemeral) | `days: int` | None |

All three are independent and can be implemented in any order. The portion scale is the only one that persists across sessions; the other two are entered fresh each generation.

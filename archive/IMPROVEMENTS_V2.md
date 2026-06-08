# Meal Planner — Improvements V2

Batch following the Gemini code review (`gemini_review.md`). Three verified fixes
plus one new feature (ingredient substitution). Optimized for execution by an AI
coding agent: each phase is self-contained with explicit file anchors, exact
changes, acceptance criteria, a recommended model, and a verification step.

## Execution Order & Model Assignment

Phases are independent except Phase 4, which benefits from Phases 1–2 being done
first (the substitution call reuses the same schema-coherence patterns). Do them
in order.

| Phase | Work | Complexity | Recommended model | Why |
|---|---|---|---|---|
| 1 | Bug B — 0-children `kid_adaptation` contradiction | Mechanical, multi-point | **Sonnet 4.6** | Careful conditional edits across an f-string; no design judgment |
| 2 | Bug A — swap lunch/dinner consistency | Contained logic | **Sonnet 4.6** | Algorithm fully specified below; just implement it |
| 3 | `@st.fragment` on Settings (and Favorites edits) | Known Streamlit pattern | **Sonnet 4.6** | Standard refactor; verify rerun scope |
| 4 | Ingredient substitution feature | Net-new, multi-file | **Opus 4.8** | New API function, new dialog, shopping rebuild, design judgment |

**Runtime model (the app's own Claude calls):** keep `claude-sonnet-4-5` (the
existing `MODEL` constant in `meal_planner.py`). The substitution call in Phase 4
is a focused single-recipe rewrite — Sonnet handles it well; no upgrade needed.

**Credit-optimal workflow:** run `/model` → Sonnet for Phases 1–3, then `/model`
→ Opus for Phase 4. Don't burn Opus credits on the mechanical fixes.

**Branch:** `git checkout -b feature/substitute-and-fixes` off `main`.

---

## Phase 1 — Fix Bug B: 0-children `kid_adaptation` contradiction

**Model: Sonnet 4.6**

### Problem
A user can delete every child in Settings (`app.py:832`), leaving
`children == []`. Then `kids_count == 0`, but the prompt still mandates
`kid_adaptation` ("always required", Meal Planning Rules, output rule #5) **and**
`_validate_plan` (`meal_planner.py:174–178`) hard-requires `kid_adaptation` on
every dinner regardless of kid count — so a valid 0-kid plan can throw
`MealPlanError`. The schema/prompt also describe kid servings and kid portions
that don't apply.

### Changes

**`system_prompt.py` → `build_system_prompt()`** — gate all kid content on
`kids_count > 0`. Introduce a single local `has_kids = kids_count > 0` and use it
to conditionally:
1. **Schema (`schema_with_kids`, ~line 482):** when `not has_kids`, delete the
   `kid_adaptation` key from `dinners[0]`, and either drop `kid_portion` from
   `serving_sizes[0]` or set its hint to `""`. Keep `servings.kids = 0` (already
   correct).
2. **Household profile (~lines 526–528):** when `not has_kids`, drop the
   "Children: …" bullet and the "Need milder versions…" sentence (the
   `_describe_children` "no children" string already exists as a fallback).
3. **Meal Planning Rules (~line 628):** drop the "**Kid adaptation is mandatory
   on every dinner.**" bullet when `not has_kids`.
4. **Output rules (~line 702):** drop rule #5 ("`kid_adaptation` is required…")
   when `not has_kids`; renumber or leave a gap (gap is fine).
5. **`serving_sizes` instruction (~line 710):** the "Kid portions should be
   roughly half…" sentence — drop when `not has_kids`.

**`meal_planner.py`** — make kid validation conditional:
1. `_validate_plan(plan, days, require_kid_adaptation=True)` — gate the
   `kid_adaptation` loop (lines 174–178) on `require_kid_adaptation`.
2. Thread the flag through: in `build_generation_prompts` / `generate_week_plan`,
   compute `require_kid = bool(prefs.get("children"))` and pass it into
   `generate_week_plan_from_prompts(..., require_kid_adaptation=require_kid)`,
   which forwards it to `_validate_plan`.
3. **Swap schema (`swap_meal`, ~line 277):** when the household has no kids, the
   dinner schema's `kid_adaptation: "string — required"` should read
   `"string or null"`. Read `prefs.get("children")` (already loaded at line 235)
   to decide.

### Acceptance criteria
- With `children: []` in `preferences.json`, generation succeeds and the prompt
  contains no `kid_adaptation` requirement, no kid servings, no kid portions.
- With ≥1 child (the default), behavior is **unchanged** — diff the generated
  prompt string before/after for a 2-kid household; it should be identical.

### Verify
`streamlit run app.py` → Settings → delete all children → Generate. Then
re-add a child → Generate. Both succeed.

---

## Phase 2 — Fix Bug A: swap lunch/dinner consistency

**Model: Sonnet 4.6**

### Problem
`swap_meal` (`meal_planner.py:248`) decides whether to handle a paired lunch from
the **replaced** dinner's `generates_lunch` flag, not the replacement's. Result:
a swapped dinner's `generates_lunch` flag and the lunch list can drift out of
sync (orphaned leftover lunch, or a `generates_lunch: true` dinner with no
corresponding lunch). No data is lost, but the plan becomes internally
inconsistent and the shopping list can mis-aggregate.

### Fix algorithm (dinner swaps only; lunch swaps are already correct)
Replace the "old flag" logic with paired-slot detection:

1. **Detect the paired slot by data, not flag:**
   `has_paired_lunch = any(l.get("leftover_from_dinner_id") == meal_id for l in week_plan["lunches"])`.
2. **Schema:** include `replacement_lunch` in the request **iff
   `has_paired_lunch`**. Update the swap system/user prompt to tell Claude: *"If
   your replacement dinner naturally yields a leftover lunch, return
   `replacement_lunch` with `source: 'leftover'` and
   `leftover_from_dinner_id` = this dinner's id. If it does not, return a
   standalone lunch (`source: 'standalone'`, `leftover_from_dinner_id: null`) to
   fill the slot. Always return `replacement_lunch` when asked."*
3. **After parsing `replacement_dinner`:**
   - If `has_paired_lunch` and `replacement_lunch` present: preserve the existing
     lunch slot's `id`, write `replacement_lunch` into that slot, and set
     `new_dinner["generates_lunch"] = (replacement_lunch.get("source") == "leftover")`.
     Clear `lunch_scaling_instructions` to `null` if not a leftover.
   - If `has_paired_lunch` but `replacement_lunch` missing: keep the old lunch
     slot but reset its `leftover_from_dinner_id` to the new dinner's id (fallback).
   - If **not** `has_paired_lunch`: force `new_dinner["generates_lunch"] = False`
     and `new_dinner["lunch_scaling_instructions"] = None` — there is no slot to
     attach a leftover to, and we must not grow the lunch list beyond `days`.

### Acceptance criteria
- Swap a dinner that **had** a leftover lunch → the paired lunch slot is
  refilled, its `id` is unchanged, and `generates_lunch` matches the new lunch's
  `source`.
- Swap a dinner that **had no** leftover lunch → lunch list length is unchanged
  and the new dinner's `generates_lunch` is `False`.
- After any dinner swap, `_rebuild_shopping()` produces a list with no orphaned
  leftover references.

### Verify
`streamlit run app.py` → generate a week → swap a dinner that the plan marked
`generates_lunch: true`, then swap one marked `false`. Confirm the lunch list
count stays at `days` and shopping list rebuilds cleanly.

---

## Phase 3 — `@st.fragment` to cut the clunk

**Model: Sonnet 4.6**

### Goal
Stop the full ~1000-line `app.py` from re-running on every Settings toggle. Wrap
self-contained, local-state sections in `@st.fragment` functions and use
`st.rerun(scope="fragment")` for in-fragment state changes.

### Changes
1. **Settings tab (primary win):** extract the Settings body — constraints
   toggles (`toggle_constraint`), children editor, preferences inputs — into a
   `@st.fragment def _settings_fragment():` called from `with tab_settings:`.
   Replace the `st.rerun()` calls that follow `toggle_constraint` /
   `add_constraint` / `delete_constraint` / `update_preferences` with
   `st.rerun(scope="fragment")`. These all read/write `data/*.json` directly and
   are re-read from disk at generation time, so isolating them is safe — no
   cross-tab session_state dependency.
2. **Favorites tab (careful, secondary):** wrap **only** the rating/tags/feedback
   edit controls (`update_favorite`) in a fragment with fragment-scope rerun.
   **Do NOT** fragment the "Add to current plan" button — it mutates
   `st.session_state.week_plan` and calls `_rebuild_shopping()`, which the
   Generate tab renders. That action must use a full `st.rerun()` (app scope).

### Acceptance criteria
- Toggling a constraint or editing a child's age updates instantly without the
  Generate tab's spinner/re-render flashing.
- Generating a plan still picks up the latest settings (they're read from disk,
  not session_state).
- "Add favorite to current plan" still updates the Generate tab and shopping list.

### Verify
`streamlit run app.py`. In Settings, toggle several constraints rapidly — should
feel instant. Then generate and confirm the toggled constraints applied.

### Note for the agent
If unsure about current `@st.fragment` semantics or `rerun(scope=...)`, consult
context7 / the claude-code docs before refactoring — Streamlit fragment rules
have changed across versions and a wrong scope silently breaks reactivity.

---

## Phase 4 — Ingredient Substitution (new feature)

**Model: Opus 4.8**

### User story
"I like this recipe but dislike one of its ingredients. Let me flag the
ingredient and have the tool find an appropriate substitute, then update the
recipe and shopping list — optionally remembering to avoid that ingredient in
future plans."

### Decisions (locked)
- **Persistence:** one-off substitution now, with an optional "also avoid this
  going forward" checkbox that adds a constraint via
  `data_store.add_constraint(...)`.
- **UI:** a per-recipe "Substitute an ingredient" dropdown (the recipe's
  non-pantry ingredients) + button, mirroring the existing swap dialog.
- **Scope:** available on **both dinners and lunches**.
- **Regeneration scope:** Claude returns the **full updated meal object** in the
  same schema, keeping everything else as close to the original as possible
  (not just swapping the ingredient line) so instructions, quantities,
  nutrition, cost, and serving sizes stay coherent.

### New code

**`meal_planner.py` — new function:**
```
def substitute_ingredient(
    week_plan: WeekPlan,
    meal_id: str,
    meal_type: str,            # "dinner" or "lunch"
    ingredient_name: str,
    reason: str = "",
) -> WeekPlan:
```
- Locate the meal in `week_plan["dinners"]` / `["lunches"]` by `id` (reuse the
  `next(... enumerate ...)` pattern from `swap_meal`; raise `MealPlanError` if not
  found).
- New system constant `_SUBSTITUTE_SYSTEM`: "You are revising a single recipe to
  remove one disliked ingredient. Replace `<ingredient>` with the most
  appropriate substitute that preserves the dish's character, cuisine, and ALL
  health constraints (Mediterranean, uric-acid, cholesterol, weight-loss — same
  rules as generation). Adjust only what the substitution requires: update the
  `ingredients` array, affected `instructions`, `health_highlights`,
  `nutrition_estimate`, `cost_estimate`, and `serving_sizes`. Keep the meal `id`,
  `name` (unless the name references the removed ingredient), and unrelated
  fields unchanged. Honor existing food constraints (no mushrooms, no oranges,
  no fish in lunches). Return ONLY the full updated meal object as JSON — no
  markdown."
- User message: the disliked `ingredient_name`, optional `reason`, the household
  constraints summary (reuse `data_store.active_constraints_for_prompt()` +
  defaults), and the **current meal object** as JSON. Ask for the full updated
  meal back under a top-level key (e.g. `{"updated_meal": {...}}`).
- Reuse `MODEL` (`claude-sonnet-4-5`), `max_tokens≈3000`, `_strip_fences`, and
  the same JSON-parse error handling as `swap_meal`.
- Write the returned meal back into the same index; return `week_plan`.
- **Lunch coherence:** if a dinner substitution changes a dinner that has a
  paired leftover lunch, run the same reconciliation as Phase 2 (the leftover
  lunch's ingredients may now be stale). Minimal v1: if the substituted dinner
  has a paired leftover lunch, also pass that lunch and ask Claude to update it;
  or, simpler, flag it in the UI ("paired lunch may need review"). Pick the
  pass-and-update path if it fits one call cleanly.

**`app.py` — UI wiring:**
1. Add `st.session_state.substituting: tuple | None = None` to the init block
   (near `swapping`, ~line 63).
2. Add a `@st.dialog("Substitute Ingredient", width="small") def
   _substitute_dialog():` modeled on `_swap_dialog` (lines 143–182). It reads
   `(meal_id, meal_type, meal_name, ingredient_name)` from
   `st.session_state.substituting`, shows an optional reason `text_input`, a
   **"Also avoid this ingredient in future plans"** checkbox, then on confirm:
   - call `meal_planner.substitute_ingredient(...)`,
   - if checkbox: `data_store.add_constraint(f"No {ingredient_name} — disliked, avoid in future plans.")`,
   - `st.session_state.week_plan = updated`, `_rebuild_shopping()`,
     `data_store.update_history_plan(updated, st.session_state.week_start)`,
     clear `substituting`, `st.rerun()`.
3. On each **dinner** card (near the swap/remove buttons, ~line 383) and each
   **lunch** card, add a small row: a `st.selectbox` of that meal's non-pantry
   ingredient names + a "Substitute" button that sets
   `st.session_state.substituting = (id, type, name, chosen_ingredient)`.
   Use the `pantry_staple` flag to filter the dropdown to real (purchasable)
   ingredients.
4. Trigger the dialog where `_swap_dialog` is triggered (~line 238):
   `if st.session_state.substituting: _substitute_dialog()`.

**`schemas.py`:** no new shape required — substitution reuses `DinnerMeal` /
`LunchMeal`. (Only add a TypedDict if you introduce a new persisted structure,
which this feature does not.)

### Acceptance criteria
- Selecting an ingredient and confirming returns a coherent updated recipe: the
  disliked ingredient is gone, instructions still make sense, and nutrition/cost/
  serving sizes are refreshed.
- The shopping list rebuilds and no longer lists the removed ingredient (unless
  another meal uses it).
- With the checkbox ticked, a new active constraint appears in Settings and is
  reflected in the next generation's prompt.
- Works on both a dinner and a lunch card.
- Health constraints respected (e.g. a substitute is not fish in a lunch).

### Verify
`streamlit run app.py` → generate a week → on a dinner, substitute a main
protein/produce item → confirm recipe + shopping list update. Repeat on a lunch.
Tick "avoid in future," regenerate, and confirm the ingredient is absent.

---

## Out of scope (deferred from the Gemini review)
- **Bug C (deepcopy state):** not a real bug in single-threaded Streamlit; skip.
- **SQLite migration:** loses the human-editable JSON design goal; skip.
- **`pint` unit summing in the shopping list:** nice-to-have; defer (chokes on
  `"handful"`, `"1 large"`, `"to taste"` — needs careful fallbacks).
- **FastAPI + Next.js migration:** reassess after Phase 3 lands and we see how
  much `@st.fragment` reduces the clunk.

## Status checklist (agent updates as it goes)
- [x] Phase 1 — Bug B (0-children)
- [x] Phase 2 — Bug A (swap consistency)
- [x] Phase 3 — `@st.fragment`
- [x] Phase 4 — Ingredient substitution

---

## Implementation Notes (Phase 1–3, 2026-06-08)

**Branch:** `feature/substitute-and-fixes`
**Commit:** `ec43ae2`

### Phase 1

- Added `has_kids = kids_count > 0` after `kids_count = len(children)` in `build_system_prompt()`.
- Pre-computed four conditional string variables just before the `import copy` / schema block: `_children_profile_line`, `_kid_adaptation_rule`, `_kid_output_rule_5`, `_kid_portion_sentence`. These are inserted into the f-string at the four gating points, keeping the f-string readable.
- Schema modification: `schema_with_kids["week_plan"]["dinners"][0].pop("kid_adaptation", None)` when `not has_kids`; also zeroes out `kid_portion` in all `serving_sizes` entries.
- `_validate_plan` now takes `require_kid_adaptation: bool = True`; the kid loop is gated on that flag.
- `build_generation_prompts` now returns a **3-tuple** `(system_prompt, user_message, require_kid)`. Updated callers: `generate_week_plan` unpacks it and passes `require_kid_adaptation=require_kid` to `generate_week_plan_from_prompts`. `app.py` stores `require_kid` as `st.session_state.pending_require_kid` and passes it to the prompt preview dialog.
- `swap_meal` dinner schema: `"kid_adaptation"` hint is `"string — required"` when kids exist, `"string or null"` when not.

### Phase 2

- Replaced `generates_lunch = ... replaced_meal.get("generates_lunch", False)` with `has_paired_lunch = any(l.get("leftover_from_dinner_id") == meal_id ...)` — detects actual pairing by data, not flag.
- When `has_paired_lunch`, the replacement_lunch schema now uses `source: "leftover or standalone"` with conditional `leftover_from_dinner_id`, and the user_message includes explicit instructions telling Claude to return a standalone lunch if the replacement doesn't naturally yield leftovers.
- Post-parse: if `has_paired_lunch` and Claude returns `replacement_lunch`, `generates_lunch` on the new dinner is set to `source == "leftover"`. If Claude omits `replacement_lunch` (fallback), the existing lunch slot has its `leftover_from_dinner_id` updated to the new dinner's id. If `not has_paired_lunch`, `generates_lunch` and `lunch_scaling_instructions` are forced to `False`/`None` on the new dinner.

### Phase 3

- `_settings_fragment()` decorated with `@st.fragment` and called from `with tab_settings:`. All six `st.rerun()` calls inside it changed to `st.rerun(scope="fragment")` — covers: delete child, add child (form submit), toggle constraint, delete constraint, add constraint.
- `_favorite_edit_fragment(fav)` is a per-item `@st.fragment` that renders tags/rating/feedback/save. "Save changes" uses `scope="fragment"`. "Remove favorite" is outside the fragment and keeps a full `st.rerun()` (it changes the list length, which the outer loop sees).
- **Note:** `st.form` inside a fragment works fine in Streamlit ≥1.37. The add-child form behaves identically to before.
- **Note:** Settings preferences (lunch count, budget, portion scale, nutrition targets) still use `st.success("Saved.")` with no rerun — they don't need one since the values are read from disk at generation time, not from session state.

### Phase 4 — what to know before starting

- The `substitute_ingredient` function will mirror `swap_meal` closely. Key reuse: `_strip_fences`, `MealPlanError`, `MODEL`, `data_store.active_constraints_for_prompt()`.
- The lunch coherence path (paired leftover lunch update) can reuse the same `leftover_from_dinner_id` detection pattern from Phase 2.
- `app.py` already has `st.session_state.swapping` as a model for `st.session_state.substituting`; the dialog pattern is established.
- The selectbox of non-pantry ingredients needs `[ing for ing in meal["ingredients"] if not ing.get("pantry_staple")]` — `pantry_staple` flag is reliable on all Claude-generated meals.
- Recommend Opus 4.8 per the spec — the substitution prompt requires nuanced judgment to keep the dish coherent while swapping a single ingredient.

### Phase 4 — as implemented (2026-06-08)

**`meal_planner.py`**
- New `_SUBSTITUTE_SYSTEM` constant and `substitute_ingredient(week_plan, meal_id, meal_type, ingredient_name, reason="")`.
- Locates the meal by id (same `next(... enumerate ...)` pattern as `swap_meal`); raises `MealPlanError` if not found. `meals` is a live reference into `week_plan`, so `meals[idx] = updated_meal` mutates the plan in place.
- Food constraints sent to Claude = hardcoded defaults (`No mushrooms`, `No oranges or orange juice`, `No fish in lunches`) + `data_store.active_constraints_for_prompt()`.
- **Lunch coherence (pass-and-update path chosen):** when the target is a dinner with a paired leftover lunch (`leftover_from_dinner_id == meal_id`), both the dinner and that lunch are sent in one call and Claude returns `{"updated_meal": ..., "updated_lunch": ...}`. Otherwise just `{"updated_meal": ...}`. The original `id` (and the lunch's `leftover_from_dinner_id`) are re-stamped after parsing so Claude can't drift them.
- Reuses `MODEL`, `max_tokens=3000`, `_strip_fences`, and the same JSON-parse error handling as `swap_meal`.

**`app.py`**
- Added `st.session_state.substituting` (4-tuple: `meal_id, meal_type, meal_name, ingredient_name`) to the init block next to `swapping`.
- Added `@st.dialog("Substitute Ingredient", width="small") _substitute_dialog()` modeled on `_swap_dialog`: optional reason `text_input`, "Also avoid this ingredient in future plans" checkbox, then calls `substitute_ingredient`, optionally `data_store.add_constraint(f"No {ingredient_name} — disliked, avoid in future plans.")`, rebuilds shopping, updates history, full `st.rerun()`.
- Triggered alongside `_swap_dialog` (`if st.session_state.substituting: _substitute_dialog()`).
- Each dinner and lunch card has a `st.selectbox` (placeholder, `index=None`) of non-pantry ingredient names + a "Substitute" button. Button guards on a chosen ingredient (warns if none), sets `substituting`, and `st.rerun()`s to open the dialog.
- **Note:** unlike the swap button (which relies on the implicit button-click rerun), the substitute button calls `st.rerun()` explicitly because the selectbox + button live in the same widget row — the explicit rerun guarantees the dialog opens on the click that has a valid selection.
- No `schemas.py` change — substitution reuses `DinnerMeal` / `LunchMeal`.

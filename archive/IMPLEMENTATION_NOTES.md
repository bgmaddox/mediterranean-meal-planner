# Archive — Implementation Notes

This folder holds completed planning documents for the Mediterranean Meal
Planner. Each was fully implemented; this file records **how** and **where** the
work landed so the archived plans stay useful as a historical record.

Archived on **2026-06-08**. The master design/reference doc (`PLAN.md`) and
`CLAUDE.md` remain at the project root. Open/deferred work lives in the
root-level `FUTURE_WORK.md`.

---

## `IMPROVEMENTS.md` — three generation features

**Status: ✅ Complete.** Implemented in commit `d2fe852`
("Implement all three IMPROVEMENTS.md features").

| Feature | How it was implemented |
|---|---|
| **Portion scale** (persisted) | `portion_scale: float` added to `UserPreferences` (`schemas.py`); threaded through `data_store.update_preferences()` → `build_system_prompt(portion_scale=...)`, which injects a scaling instruction when ≠ 1.0. Settings control in `app.py`. Quantities are scaled by Claude in-prompt (not post-processed), per the plan's rationale. |
| **Use-up ingredients** (ephemeral) | `st.text_area` on the Generate tab → `build_generation_prompts(use_up_ingredients=...)` → dedicated prompt section instructing Claude to work them into 1–2 meals and keep them off the shopping list. Not persisted. |
| **Variable meal days (3–7)** | `st.number_input` "Days of meals this week" on the Generate tab → `days` param threaded through `build_generation_prompts` and `build_system_prompt`; planning rules, schema IDs (`d1..dN` / `l1..lN`), and protein balance scale with `days`. `_validate_plan` enforces the expected count. |

---

## `gemini_review.md` — senior-dev code review

**Status: ✅ Addressed.** This review is the source that produced
`IMPROVEMENTS_V2.md`. Disposition of each item:

| Review item | Disposition |
|---|---|
| **Bug A — "lunch drop" on meal swap** | Fixed in V2 Phase 2 (commit `ec43ae2`). Pairing now detected by data (`leftover_from_dinner_id`), not the replaced meal's flag. |
| **Bug B — 0-children `kid_adaptation` contradiction** | Fixed in V2 Phase 1 (commit `ec43ae2`). All kid content gated on `has_kids`; `_validate_plan` takes `require_kid_adaptation`. |
| **Bug C — in-place state mutation / deepcopy** | **Deferred (won't fix).** Not a real bug in single-threaded Streamlit. See `FUTURE_WORK.md`. |
| **`@st.fragment` optimization** | Done in V2 Phase 3 (commit `ec43ae2`). |
| **SQLite migration** | **Deferred.** Loses the human-editable JSON design goal. See `FUTURE_WORK.md`. |
| **`pint` unit summing for shopping list** | **Deferred.** Chokes on `"handful"`, `"1 large"`, `"to taste"`. See `FUTURE_WORK.md`. |
| **FastAPI + Next.js migration** | **Deferred.** Reassess now that fragments reduced the clunk. See `FUTURE_WORK.md`. |

---

## `IMPROVEMENTS_V2.md` — four-phase batch

**Status: ✅ Complete (all 4 phases).** Detailed per-phase implementation notes
are embedded at the bottom of `IMPROVEMENTS_V2.md` itself ("Implementation
Notes" sections). Summary:

| Phase | Work | Commit |
|---|---|---|
| 1 | Bug B — gate all kid content on `has_kids`; conditional `_validate_plan` | `ec43ae2` |
| 2 | Bug A — data-driven paired-lunch detection on dinner swaps | `ec43ae2` |
| 3 | `@st.fragment` on Settings + Favorites edits (scoped reruns) | `ec43ae2` |
| 4 | Ingredient substitution feature (`substitute_ingredient`, dialog, per-card selectbox) | `819101f` |

**Phase 4 verification:** ran the app through Streamlit's `AppTest` harness — app
loads with zero exceptions; substitute selectboxes/buttons render on both dinner
and lunch cards; clicking sets the correct `substituting` 4-tuple that opens the
dialog. The live Claude API call inside the dialog was not exercised (needs
credits), but wiring is verified up to that call.

---

## `PLAN.md` (kept at root) — master design doc

Not archived — it remains the canonical reference for the app's health
rationale, user profile, pantry staples, equipment, and overall design. Its
"Planned Features (Not Yet Built)" section was mostly delivered:

| Planned feature | Status |
|---|---|
| Swap a Meal | ✅ Built (`swap_meal`) |
| Nutritional Estimates | ✅ Built (`nutrition_estimate` in schema + meal cards + week strip) |
| Season / Weather Awareness | ✅ Built (`current_date` + "Notes for this week" free-text) |
| Kid Meal Notes Export | ✅ Built (`generate_kid_notes`) |
| Weekly Email Report | ⚠️ **Backend built (`email_report.py` + schema fields), UI not wired.** See `FUTURE_WORK.md`. |

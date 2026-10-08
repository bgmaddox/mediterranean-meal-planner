# Meal Composition & Vegetable-Side Variety — Implementation Plan

**Status:** ✅ Complete — archived 2026-09-06. All three phases landed; see
`IMPLEMENTATION_NOTES.md` in this folder for where the work went.
**Created:** 2026-06-24
**Goal:** Break the rigid "1 protein / 1 grain / 1 plain vegetable" plate structure that every generated dinner currently follows, and make vegetable components real, technique-driven dishes that vary across the week — without weakening the macro/health targets.

---

## Problem Statement (why this plan exists)

Generated dinners almost always come out as one protein + one grain + one plain vegetable side. The vegetable side is an afterthought (e.g. "grill peppers, serve") and repeats with little variation. Three root causes, all in `system_prompt.py`:

1. **The prompt hardcodes the plate model.**
   - `system_prompt.py` (Weight Loss section): *"Meals should be filling at normal portions (roughly: half veg, quarter protein, quarter whole grain)"* — single archetype.
   - `system_prompt.py` Output rule 11: *"List every main component (protein, grain/starch, vegetable sides)… typically 4–6 oz protein, 1/2 cup grain, 1–2 cups vegetables."* — reinforces protein+grain+veg on every dinner.
2. **No vegetable-treatment guidance.** Proteins get detailed technique (purine-leaching, searing); vegetables get nothing, so Claude defaults to plain preparations.
3. **The grounding mechanism is mains-only.** All anchor recipes in `data/anchor_recipes.json` are full main dishes (`meal_type` ∈ dinner/lunch/either). There are no side/mezze/salad anchors, so vegetable treatments have no real-recipe grounding the way mains do.

Mediterranean eating is frequently vegetable-forward or a spread of small dishes (mezze/tapas), not always a structured protein-centered plate. The plan adds that flexibility plus tastier, more varied vegetable components.

---

## Phase Overview & Model Assignment

| Phase | Work | Model | Why this model |
|---|---|---|---|
| **1** | Prompt-only: add meal-composition archetypes + vegetable-as-dish rule + protein floor; loosen plate-model language | `claude-opus-4-8` | Prompt engineering is high-judgment writing where wording precision directly changes generation quality. Opus 4.8 is the strongest instruction-follower and is already the generation model. Use `effort: high`. |
| **2** | Add a `role` field to anchor schema; seed ~15–20 sourced vegetable/mezze side anchors via Apify; reserve side slots in selection | `claude-sonnet-4-6` | Mostly mechanical: schema edit, web scraping, distilling technique notes into a fixed format, repetitive seeding. High volume, well-specified, cost-sensitive. Sonnet 4.6 at `effort: medium` is the right cost/quality point. Escalate a single tricky technique-note distillation to Opus only if a source is ambiguous. |
| **3** | Prompt audit: find redundancy, conflicts, and ordering problems introduced by Phases 1–2 and pre-existing bloat; tighten | `claude-opus-4-8` | Whole-prompt analysis and editorial judgment — what to cut without losing meaning, what conflicts, what hurts caching. Opus 4.8 at `effort: high`. |

> Set the model per phase in `meal_planner.py` → `MODEL` **only if** a phase needs to test generation output. The phase *implementation* (editing files) is done by the coding agent; the `MODEL` constant governs runtime meal generation and should stay `claude-opus-4-8` (current value) unless a phase explicitly says to change it. **None of these phases change `MODEL`.**

---

## Phase 1 — Prompt-only composition variety (high leverage, reversible)

**Model:** `claude-opus-4-8`, `effort: high`
**Branch:** `feature/meal-composition-variety`
**Files touched:** `system_prompt.py` only.

### 1.1 Add a "Meal Composition" section

Add a new section inside `build_system_prompt()`'s returned prompt, placed in the `# HEALTH FRAMEWORK` block immediately after the `## Mediterranean Diet Principles` subsection (so it reads as a framing rule, before the individual health constraints). Insert as a static string (no f-string interpolation needed — keep it cache-stable).

Content requirements — the section must:
- Name **three composition archetypes** and instruct Claude to **vary them across the {days} dinners**, not default to one:
  1. **Classic plate** — a protein, a whole grain/starch, and a vegetable (the current default; still valid, just not the only mode).
  2. **Vegetable-forward** — a vegetable dish is the centerpiece; protein and/or grain are secondary or folded in (e.g. a hearty white-bean-and-greens braise, a stuffed-vegetable main, a chickpea-and-vegetable tagine over a small amount of couscous). Legumes/eggs/dairy carry protein here.
  3. **Mezze / small-plate spread** — 3–4 smaller dishes served together (e.g. a dip + a vegetable dish + a legume dish + a small protein), eaten tapas-style rather than as a single plated entrée.
- State that **at least 2 of the week's dinners should be vegetable-forward or mezze-style** (not all classic plates). Scale gently: for a 3-day week, at least 1; for 5+, at least 2.
- Explicitly say a grain is **optional**, not mandatory, on vegetable-forward and mezze nights.

### 1.2 Add a "Vegetables are dishes, not afterthoughts" rule

Add this as a clearly labeled HARD-ish guidance block (same section or immediately after). It must:
- Require every vegetable component to be a **deliberately seasoned, technique-driven dish** — never "plain steamed/grilled/raw with no seasoning."
- Require **acid, fat (EVOO), aromatics, herbs/spices, and a finishing element** (e.g. a sauce, a sprinkle of dukkah/za'atar/feta/toasted nuts, a yogurt-tahini drizzle, a quick pickle, charring) as appropriate.
- Require the **treatment to vary across the week** — list concrete contrasting techniques the model should rotate among: charring/blistering, roasting with spice rub, braising, raw salad with bright dressing, quick-pickling, grilling then dressing, a vegetable purée. The same vegetable should not get the same treatment twice in one week.
- Note that these treatments should still respect the health rules (EVOO as fat, no deep-frying, etc.).

### 1.3 Add a protein/macro floor (skeptic guardrail)

Because vegetable-forward and mezze nights can undershoot protein/satiety, add one sentence requiring that **every dinner still meets the household's per-adult protein and satiety needs** — on vegetable-forward/mezze nights this is achieved via legumes, eggs, Greek yogurt/dairy, nuts/seeds, and modest protein portions rather than a large central protein. Reference the existing nutrition targets so macros are not sacrificed for structure.

### 1.4 Loosen the existing plate-model language

- In the `# HEALTH CONSTRAINT: WEIGHT LOSS` section, change the line *"Meals should be filling at normal portions (roughly: half veg, quarter protein, quarter whole grain)"* to present that ratio as **one example of a filling plate, not a rule for every meal** — e.g. note it applies to classic-plate dinners, while vegetable-forward and mezze nights distribute differently but should hit the same calorie/fiber/protein targets.
- In **Output rule 11** (the `serving_sizes` rule), change *"List every main component (protein, grain/starch, vegetable sides)…"* so it lists **whatever components the chosen composition actually has** (which may be several vegetable dishes and no grain, or a mezze of small plates), and keep the per-component portion guidance generic rather than presuming a protein+grain+veg triplet. Do **not** remove the portion-size requirement — every component still needs `serving_sizes`.

### 1.5 Constraints / non-goals for Phase 1
- Do **not** touch `schemas.py`, `data_store.py`, `shopping.py`, or `app.py`. The output JSON shape is unchanged — `serving_sizes` already supports an arbitrary list of components, so mezze/vegetable-forward meals fit the existing schema.
- Do **not** add new equipment-spreading or cuisine rules.
- Keep additions concise — the prompt is already long. Prefer 3–5 tight sentences per block over paragraphs. (Phase 3 will tighten further.)

### 1.6 Acceptance criteria / verification
1. App still boots: `source .venv/bin/activate && streamlit run app.py` with no import/render error.
2. Generate one week (5 dinners) and inspect:
   - At least 2 dinners are vegetable-forward or mezze-style (not all classic plates).
   - At least one dinner has **no grain** or a clearly secondary grain.
   - Vegetable components show real seasoning/technique and the technique **differs** across dinners (no two "plain grilled X").
   - `nutrition_estimate.protein_g` on vegetable-forward nights is still reasonable for an adult dinner (sanity check against `target_protein_g`).
3. Shopping list still builds without error (Shopping List tab).
4. Commit on the feature branch; do **not** merge to `main` or deploy to the Pi until the user has reviewed a generated week.

---

## Phase 2 — Side/mezze anchor recipes (durable quality)

**Model:** `claude-sonnet-4-6`, `effort: medium` (escalate individual ambiguous technique-note distillations to `claude-opus-4-8`)
**Branch:** `feature/side-anchor-recipes`
**Files touched:** `schemas.py`, `data_store.py`, `data/anchor_recipes.json`, `CLAUDE.md`. Possibly `system_prompt.py` (anchor-block intro copy).
**Precondition:** None — Phase 2 runs unconditionally after Phase 1. Side anchors are additive and low-risk (they expand the sampled library without constraining anything), the `role` field is backwards-compatible, and more real-recipe grounding reliably improves output quality the same way it did for the mains. Running it always avoids a stop-and-evaluate cycle and ships the complete improvement in one pass.

### 2.1 Add a `role` field to the anchor schema
- In `schemas.py` → `AnchorRecipe` TypedDict, add `role: str` with allowed values `"main"` and `"side"`. Document it: a `"side"` is a vegetable/mezze/salad/dip dish meant to accompany or compose a meal, not a full entrée.
- Treat the field as **optional/backwards-compatible at load time**: existing seed entries lack `role`. In `data_store.load_anchor_recipes()` (or `load_anchor_seed_recipes`), default a missing `role` to `"main"` so the 106 existing recipes need no migration.

### 2.2 Reserve side slots in selection
- In `data_store.py` → `select_anchor_recipes(count, days)`, add a third pool: `sides = [a for a in anchors if a.get("role") == "side"]`.
- Reserve **2–3 side slots** per selection (scale with `days`: ~`max(2, days // 2)`, capped at the side-pool size), drawn in addition to the existing dinner/lunch split. Shuffle into the returned sample so they appear interleaved.
- Keep the existing dinner/lunch reservation logic intact; sides are a new, additive reservation. Ensure total returned count still respects `len(anchors)`.

### 2.3 Update the anchor-block prompt copy (small)
- In `system_prompt.py`, the "Anchor Recipes" block intro currently frames anchors as full-meal starting points. Add one sentence noting that some anchors are **side/mezze dishes** — use them to build the vegetable-forward and mezze compositions from Phase 1, and to give vegetable components real technique. Keep it short (Phase 3 will audit).

### 2.4 Seed ~15–20 sourced side anchors
Follow the existing enrichment workflow documented in `CLAUDE.md` → "Adding Anchor Recipes" and `archive/ANCHOR_ENRICHMENT_PLAN.md`:
1. Pull each recipe from the **source allowlist** (`themediterraneandish.com`, `seriouseats.com`, `eatingwell.com`, `thekitchn.com`, `simplyrecipes.com`, etc.) via the Apify RAG browser (`mcp__apify__apify--rag-web-browser`). Respect the denylist (paywalled NYT/ATK/etc.).
2. Extract **real** technique details (timing, temp, order-of-operations, finishing) — do not invent. Set `notes_sourced: true` and `source_url`.
3. Distill into the anchor format: `key_ingredients`, `summary`, `technique_notes` (**≤60 words** — they inject into every generation), `cuisine`, `meal_type` (usually `"either"` or `"dinner"`), and `role: "side"`.
4. Write only to the shipped seed file `data/anchor_recipes.json` (these are curated, not personal — they should survive deploys). Do **not** write to the gitignored user file.

Target a spread of side types so the model has variety to draw on:
- Charred/blistered vegetables with a sauce (e.g. charred broccolini, blistered green beans with tahini).
- Braised/stewed vegetable dishes (e.g. gigantes plaki, braised greens, ratatouille-style).
- Stuffed/roasted vegetables (e.g. stuffed peppers, roasted eggplant).
- Bright raw/chopped salads (e.g. shirazi/Greek/fattoush-style, lemony lentil salad).
- Dips/mezze (e.g. melitzanosalata, muhammara, whipped feta, tzatziki, hummus variations) — respecting **no-mushroom** and other hard constraints.
- Quick pickles / marinated vegetables.

Avoid duplicating mains already present. Keep mezze dips compatible with the uric-acid/cholesterol rules (EVOO, yogurt, legumes — all encouraged).

### 2.5 Update documentation
- In `CLAUDE.md`, update the anchor-recipe table/description to mention the new `role` field and that side anchors feed the vegetable-forward/mezze compositions.

### 2.6 Acceptance criteria / verification
1. `python3 -c "import json; d=json.load(open('data/anchor_recipes.json')); from collections import Counter; print(Counter(r.get('role','main') for r in d))"` shows ~15–20 `side` entries and the rest `main`.
2. App boots; `data_store.select_anchor_recipes()` returns a sample that includes at least 1–2 `role=="side"` entries across several calls (it's randomized — run it a few times).
3. Integration sanity: build the system prompt and confirm at least one side anchor with its `Technique:` note appears in the Anchor Recipes block.
4. Generate a week and confirm vegetable-forward/mezze dinners now visibly borrow technique from the side anchors.
5. Commit on the feature branch; user reviews before merge/deploy.

---

## Phase 3 — Prompt optimization audit (always runs last)

**Model:** `claude-opus-4-8`, `effort: high`
**Branch:** continue on the last feature branch (or a dedicated `feature/prompt-audit`).
**Files touched:** `system_prompt.py` (primarily).

After Phases 1–2 the prompt is longer. This phase makes a deliberate pass to ensure the prompt is as effective and efficient as possible. The agent should:

### 3.1 Audit for redundancy and conflict
- Read the **entire** assembled prompt (call `build_system_prompt(...)` with representative inputs and read the output, not just the source) so the audit reflects what Claude actually receives.
- Find and resolve:
  - **Duplicated instructions** (e.g. the pantry-staple "include every staple in ingredients" rule appears in both the INGREDIENT EFFICIENCY section and Output rule 1 — consolidate or cross-reference rather than restate in full).
  - **Conflicts** between the new composition rules and existing language (e.g. the weight-loss plate ratio vs. vegetable-forward nights — confirm Phase 1 resolved this cleanly).
  - **Stale or contradictory portion guidance** now that grain is optional.

### 3.2 Tighten without losing meaning
- Compress verbose passages while preserving every health rule, hard constraint, and output requirement. Do not drop any of: the three health constraints (uric acid / cholesterol / weight loss), the hard food constraints, the corrected-guidance nuances (legumes/greens safe, low-fat dairy lowers uric acid), pantry-staple handling, special-ingredient rule, or any output-rule semantics.
- Prefer turning repeated prose into tight rules. Keep the JSON schema description intact.

### 3.3 Protect prompt caching (important — see `claude-api` skill, `shared/prompt-caching.md`)
- Caching is a **prefix match**: anything volatile early in the prompt invalidates the cache for everything after it. Audit for silent invalidators:
  - `current_date` / season are interpolated near the top (`Today's date: {current_date}`). This already changes daily — note it, and ensure no *new* per-request volatility (UUIDs, timestamps) was introduced by Phases 1–2.
  - Keep the new static composition/vegetable blocks **static** (no f-string interpolation of per-request values) so they don't add invalidation points.
  - Confirm the large static health/framework content stays ahead of the volatile per-week context (history, week_notes, use_up_ingredients) — volatile content should remain late in the prompt.
- This is an audit/recommendation step: if the date-at-top placement is costing cache hits, **report it to the user with a concrete recommendation** (e.g. move `current_date` into the late per-week context block) rather than silently restructuring — moving it is a behavior-affecting change the user should approve.

### 3.4 Deliverable
- Apply safe tightening edits directly.
- Produce a short written summary (in the PR/commit body or a note to the user) of: what was consolidated, any conflicts found and how resolved, and any caching recommendation that needs user sign-off.

### 3.5 Acceptance criteria
1. App boots; a generated week is still correct and now reflects Phases 1–2.
2. Assembled prompt is meaningfully shorter or no longer than before, with **zero** lost rules (spot-check each health constraint and output rule is still present).
3. Token check (optional, via `claude-api` skill's `count_tokens`): the assembled prompt's token count is reported before/after so the user sees the savings.
4. User reviews before merge/deploy.

---

## Sequencing & Workflow Notes

- Follow the project Git workflow (`CLAUDE.md`): branch off `main`, test locally with `streamlit run app.py`, never commit experimental work directly to `main`, merge only after the user approves a reviewed generated week.
- **Sequence:** Run Phase 1 → Phase 2 → Phase 3 in order. All three are planned; Phase 2 is not gated on a Phase 1 evaluation. Phase 3 always runs last, as the final tightening pass over whatever Phases 1–2 added.
- Deployment to the Pi (`ssh rachett 'bash ~/deploy.sh diet'`) happens only after the user approves, per global instructions.
- When each phase completes and is merged, move this plan to `archive/` per the project convention (completed plans live in `archive/`, see `archive/IMPLEMENTATION_NOTES.md`).

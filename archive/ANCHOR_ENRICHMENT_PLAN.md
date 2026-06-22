# Anchor Recipe Enrichment Plan

> **✅ COMPLETE (2026-06-21).** All 10 batches run. Final tally below.
>
> | Outcome | Count |
> |---|---|
> | Total anchor recipes | 98 |
> | **Sourced** (`notes_sourced: true`) | **57** (4 pre-existing EatingWell + 53 enriched) |
> | Attempted & skipped (kept self-generated notes) | 41 |
>
> **Enriched per wave:** batches 1–4 → 26, batches 5–8 → 27, batches 9–10 → 0.
>
> **Why the 41 skips:** their best matches were behind the NYT Cooking paywall or on
> off-allowlist blogs (no allowlisted, non-paywalled source matched). Notably all 14
> recipes in batches 9–10 were originally added *from* NYT Cooking, so NYT is their only
> real source. These keep their reasonable self-generated notes and can be retried later
> with manual URLs. Re-running is idempotent (picks up any `notes_sourced != true`).
>
> Deployed to the Pi after batch 8 (batches 9–10 added no data changes).

**Status:** Complete · **Owner:** AI agent (orchestrator + per-batch subagents)
**Goal:** Replace self-generated `technique_notes` in `data/anchor_recipes.json` with notes
distilled from *real* recipe pages, where a trustworthy non-paywalled source can be found.

This is a **one-time backfill** of the legacy recipes. The convention for *new* anchors
(scrape the source when adding) already lives in `CLAUDE.md` → "Adding Anchor Recipes".

> **Why:** Real recipe pages add concrete specifics that general knowledge misses —
> carry-over temperatures, exact times/ratios, order-of-operations, and pitfalls
> (e.g. "don't wipe the pan between steps", "salmon is done at 130–135°F, carries to 145°F").

---

## Current state

- File: `data/anchor_recipes.json` (the git-tracked shipped seed).
- **98 recipes total.**
- **4 already sourced** from EatingWell (mark these `notes_sourced: true`, do NOT redo):

  | id | source_url |
  |---|---|
  | `italian-wedding-soup` | https://www.eatingwell.com/recipe/269824/minestra-maritata-italian-wedding-soup/ |
  | `caprese-chickpea-salad` | https://www.eatingwell.com/caprese-chickpea-salad-11753314 |
  | `chickpea-quinoa-red-pepper-bowl` | https://www.eatingwell.com/recipe/258195/chickpea-quinoa-bowl-with-roasted-red-pepper-sauce/ |
  | `one-skillet-garlicky-salmon-broccoli` | https://www.eatingwell.com/one-skillet-garlicky-salmon-broccoli-8778821 |

- **~94 remaining** with self-generated notes → the work queue.

---

## Architecture: orchestrator + per-batch subagents

**Token isolation is the whole point.** Each scrape is 10–15k tokens of ad-laden markdown.
Subagents absorb that cost and return only compact patches, so the orchestrator's context
stays small and the job survives long runs.

- **Orchestrator (main context)** — owns the file. Builds the work queue, chunks it into
  batches, spawns one subagent per batch, applies the returned patches to the JSON,
  validates, commits. **Subagents never write to the repo** (avoids write conflicts).
- **Subagent (per batch)** — read-only on the repo. For each recipe in its batch:
  searches, picks a source, match-checks, distills, and returns a compact JSON array of
  patches. Raw scrape markdown never leaves the subagent.

Use `subagent_type: "general-purpose"` (needs Apify tools + read tools).

---

## Phase 0 — Schema & provenance setup (orchestrator, once)

1. In `schemas.py`, add two optional fields to the `AnchorRecipe` TypedDict:
   ```python
   source_url: Optional[str]   # URL notes were derived from; absent/"" = self-generated
   notes_sourced: bool         # True once enriched from a real recipe
   ```
   *(Safe: `system_prompt.py:410-422` reads anchor fields via `.get()`, so extra JSON keys
   are silently ignored by the prompt builder.)*
2. In `data/anchor_recipes.json`, add `source_url` + `notes_sourced: true` to the 4 recipes
   in the table above.
3. Treat any recipe without `notes_sourced: true` as the work queue.

---

## Phase 1 — Per-recipe procedure (runs inside the subagent)

For each recipe in the batch:

1. **Search** — Apify RAG browser:
   - `query`: `"{name} recipe"`
   - `maxResults`: `3`
   - `scrapingTool`: `"browser-playwright"`
   - `htmlTransformer`: `"readable text"` ← strips ads/nav; major token saver
   - Poll `get-actor-run`, then `get-dataset-items` with `fields=markdown,metadata.url`.
2. **Pick a source** from the allowlist; reject paywalled domains (see lists below).
3. **Match check (anti-wrong-recipe)** — accept a result only if the scraped dish matches
   the anchor: name intent matches OR **≥2 of `key_ingredients`** appear. If no result
   matches → **skip** this recipe (leave it unsourced, report it as skipped). Never force.
4. **Distill — AUGMENT, don't replace, don't invent:**
   - Start from the existing `technique_notes` as the base.
   - Add/correct only concrete specifics **actually present in the scraped source**:
     temperatures, times, ratios, order-of-operations, pitfalls.
   - **Hard cap: ≤60 words.** These inject into every generation — bloat costs tokens
     forever and can overflow the one-page recipe PDF.
   - No verbatim copying (copyright + bloat). No details not in the source.
   - Keep `cuisine`, `meal_type`, `key_ingredients`, `summary` unchanged unless the real
     recipe reveals a clear factual error (report it; don't silently change).
5. **Emit a patch** `{id, technique_notes, source_url}` (or `{id, skipped: true, reason}`).

### Allowlist (prefer, scrapeable, free)
`eatingwell.com`, `seriouseats.com`, `thekitchn.com`, `simplyrecipes.com`,
`allrecipes.com`, `foodnetwork.com`, `budgetbytes.com`, `themediterraneandish.com`,
`bbcgoodfood.com`, `loveandlemons.com`

### Denylist (paywalled → usually only yields the name)
`cooking.nytimes.com`, `americastestkitchen.com`, `177milkstreet.com`,
`washingtonpost.com`, `bonappetit.com` (often gated)

---

## Subagent prompt template

> The orchestrator fills in `{BATCH_JSON}` with that batch's recipes
> (`id`, `name`, `cuisine`, `key_ingredients`, current `technique_notes`).

```
You are enriching Mediterranean-diet anchor recipes with technique notes from REAL recipe pages.

For each recipe below, follow this procedure exactly:

1. SEARCH with the Apify RAG web browser:
   - query: "{name} recipe"
   - maxResults: 3, scrapingTool: "browser-playwright", htmlTransformer: "readable text"
   - Start the run, poll get-actor-run until SUCCEEDED, then get-dataset-items
     (fields="markdown,metadata.url").
2. PICK a result from this allowlist only:
   eatingwell.com, seriouseats.com, thekitchn.com, simplyrecipes.com, allrecipes.com,
   foodnetwork.com, budgetbytes.com, themediterraneandish.com, bbcgoodfood.com, loveandlemons.com
   Reject paywalled sites (nytimes, americastestkitchen, milkstreet, washingtonpost, bonappetit).
3. MATCH CHECK: only use a result if the dish matches — name intent matches OR >=2 of the
   recipe's key_ingredients appear in it. If none of the 3 results match, SKIP the recipe.
4. DISTILL: start from the existing technique_notes; ADD or CORRECT only concrete specifics
   ACTUALLY PRESENT in the scraped page (temps, times, ratios, order-of-operations, pitfalls).
   Max 60 words. No invented details. No verbatim copying. Do not change cuisine/meal_type/
   key_ingredients/summary (flag clear errors in your reason instead).

Return ONLY a JSON array, one object per input recipe:
  - enriched: {"id": "...", "technique_notes": "...", "source_url": "https://..."}
  - skipped:  {"id": "...", "skipped": true, "reason": "no non-paywalled match found"}
Do not write any files. Return only the JSON array.

RECIPES:
{BATCH_JSON}
```

---

## Phase 2 — Batching (orchestrator)

1. Load `data/anchor_recipes.json`; build queue = recipes where `notes_sourced != true`.
2. Chunk into **batches of 10**, in file order (deterministic + resumable).
3. For each batch:
   a. Spawn a general-purpose subagent with the template above.
   b. Parse the returned JSON array.
   c. Apply enriched patches to the JSON: set `technique_notes`, `source_url`,
      `notes_sourced: true`. Leave skipped recipes untouched (stay unsourced).
   d. Run Phase 3 validation.
   e. `git commit` the batch (message: `Enrich anchor recipes batch N (sourced from real recipes)`).
4. After the final batch: `git push` and `ssh rachett 'bash ~/deploy.sh diet'`.

---

## Phase 3 — Validation (after every batch)

- `python3 -c "import json; d=json.load(open('data/anchor_recipes.json')); print(len(d))"`
  → must still print **98**.
- All `id`s unique; JSON parses.
- No `technique_notes` exceeds ~60 words.
- Spot-check 2 enriched notes against their `source_url`.

---

## Resumability & cost

- **Restartable:** re-running picks up wherever `notes_sourced != true` remains. Safe across
  context clears, interruptions, and partial batches.
- **Skipped recipes** keep self-generated notes (they're still reasonable) and can be retried
  later with different sources or manual URLs.
- **Cost:** Apify free tier; ~0.03–0.12 CU per scrape → roughly 15–30 CU total for the backfill.

---

## Definition of done

- Every recipe is either `notes_sourced: true` (with a `source_url`) or has been attempted
  and intentionally skipped (logged).
- Count still 98; JSON valid; deployed to the Pi.
- A short summary of how many were enriched vs. skipped, and why.

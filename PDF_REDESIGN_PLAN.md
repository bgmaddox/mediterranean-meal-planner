# PDF Redesign Plan — WeasyPrint + Card Design

**Goal:** Replace the hand-drawn `fpdf2` weekly PDF with an HTML/CSS → PDF pipeline
(WeasyPrint) that reuses the Mediterranean card design from `card_html.py`, so the
printed kitchen sheet looks like the app instead of a plain document.

**Branch:** `feature/pdf-weasyprint-redesign` (already created)
**Model:** fine for Sonnet — scoped CSS-and-iterate work with a clear reference design.

---

## Status of dependencies (DONE)

- `weasyprint>=69.0`, `pymupdf>=1.27`, `pypdf>=6.0` installed in `.venv` and added to `requirements.txt`.
- macOS system libs installed: `brew install pango gdk-pixbuf libffi`.
- Verified: WeasyPrint renders a test PDF; pymupdf + pypdf import.
- **Pi deploy step (not yet done):** before deploying, run on the Pi
  `sudo apt install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev`
  then `pip install -r requirements.txt` in the shared venv. (Documented in `requirements.txt`.)

---

## Key constraints discovered (read before coding)

1. **`card_html.py` renderers are summary-only.** `render_dinner_card()` etc. emit
   title + chips + a 4-stat strip. They do **not** render ingredients, instructions,
   serving sizes, or callouts — which the PDF must include. So we reuse the
   **CSS + palette + card chrome**, but write **new print partials** for recipe bodies.
2. **Fonts load from a Google Fonts CDN** (`@import url(fonts.googleapis.com...)` at the
   top of `CARD_STYLES`). WeasyPrint *can* fetch remote fonts, but that needs network at
   render time — fragile on the Pi and slow. **Bundle the fonts locally** (Fraunces + Inter
   `.woff2` in `assets/fonts/`, referenced via `@font-face` with `file://` or relative URLs)
   OR fall back to system serif/sans. Decide in Phase 1.
3. **Screen CSS ≠ print CSS.** Drop `box-shadow` (renders as gray smears on paper), remove
   `max-width:560px` (let cards fill the page width), and add `@page` margins + page-break
   rules. Keep borders/hairlines — they read well in print.
4. **Same entry point must be preserved.** `app.py` calls
   `pdf_export.build_pdf_bytes(week_plan, shopping_sections, week_start) -> bytes` in two
   places (lines ~472 and ~506: download + Drive upload). Keep this signature so neither
   call site changes.
5. **Unicode is now free.** The whole `_UNICODE_MAP` / `_s()` Latin-1 sanitizer in the old
   `fpdf2` file can be deleted — WeasyPrint is full UTF-8. Use `html.escape()` instead, for
   safety, not transliteration.

---

## Data shapes (from `schemas.py` / `shopping.py`)

- `week_plan["dinners"]` → list of `DinnerMeal`: `name`, `cook_time_minutes`,
  `primary_equipment`, `health_highlights[]`, `ingredients[]` (`name/quantity/unit/pantry_staple`),
  `instructions[]`, `serving_sizes[]` (`component/adult_portion/kid_portion`),
  `kid_adaptation`, `uric_acid_tip`, `sunday_prep`, `nutrition_estimate`, `cost_estimate`.
- `week_plan["lunches"]` → `LunchMeal`: as above + `source` (leftover/standalone), `reheat`,
  `pack_instructions`.
- `week_plan["sunday_prep_list"]` → `SundayPrepTask`: `task`, `yields_for[]`, `storage`.
- `week_plan["week_summary"]` → `fish_meal_count`, `red_meat_meal_count`,
  `vegetarian_meal_count`, `special_ingredients[]`, `ingredient_overlap_notes`.
- `shopping_sections` → `dict[str, list[ShoppingItem]]`; each item has `.name` and
  `.display_quantity()`.

---

## Phased build

### Phase 1 — Scaffolding + print stylesheet
1. Create `assets/` (and `assets/fonts/` if bundling fonts). Download Fraunces + Inter
   `.woff2`, or decide on a system-font fallback and skip the download.
2. New module `pdf_render.py` (keep `pdf_export.py` as the public entry point — see Phase 5).
   It holds:
   - `PRINT_STYLES`: a print-tuned copy of the card CSS — strip shadows, drop `max-width`,
     add `@page { size: Letter; margin: 14mm 14mm 16mm; @bottom-center { content: "Page " counter(page); } }`,
     add `.page-break { break-before: page; }`, and a top "running header" with the week.
   - `@font-face` blocks pointing at the bundled fonts (or omit if using system fonts).
3. Write a throwaway `scripts/preview_pdf.py` that loads a sample `week_plan` (reuse the
   existing `data/history.json` or a saved plan), builds the HTML, and writes
   `PDFs/preview.pdf`. This is the iteration loop for the rest of the work.

### Phase 2 — Recipe partials (the new HTML)
Write pure functions returning HTML strings (mirror the `card_html.py` style — `html.escape`
everything, no f-string injection):
- `_cover(week_start, summary)` — title band + week summary stat strip (reuse `.med-summary`).
- `_dinner_section(dinner)` — full card: title, meta chips (time/equipment), health-highlight
  tags, **Ingredients** list, **Instructions** ol, serving sizes, and callout boxes
  (kids / uric-acid tip / sunday prep) styled as colored left-accent blocks, nutrition + cost
  footer line.
- `_lunch_section(lunch)` — same minus instructions, plus pack instructions + source/reheat.
- `_prep_section(tasks)` — reuse `.med-prep` left-olive-accent card per task.
- `_shopping_section(sections)` — two-column layout via CSS columns
  (`column-count:2; column-gap:14mm`) so it stays one tidy block; reuse `.med-shop` chrome.
- Add print-only CSS for **callouts** (kids = warm cream, tip = sage, prep = slate-blue)
  and the **ingredients/instructions** typography — these classes don't exist in
  `card_html.py` yet.

### Phase 3 — Assemble the document
- `build_html(week_plan, shopping_sections, week_start) -> str`: concatenate
  `<style>PRINT_STYLES</style>` + cover + dinners + lunches + prep + shopping, inserting
  `<div class="page-break"></div>` between major sections (one dinner per page is fine; or
  flow naturally and let WeasyPrint break — try natural flow first, it usually looks better
  and wastes less paper).
- `render_pdf(html) -> bytes`: `weasyprint.HTML(string=html, base_url=<assets dir>).write_pdf()`.
  `base_url` must point at the project root so `file://` font/asset URLs resolve.

### Phase 4 — Visual iteration (the real work)
Loop: run `scripts/preview_pdf.py` → open `PDFs/preview.pdf` → screenshot first 1–2 pages
with pymupdf (`page.get_pixmap()`) → eyeball → tweak CSS → repeat. Targets:
- Kitchen-glanceable: generous type (≥11pt body), clear meal titles, easy-scan ingredient lists.
- No orphaned headers at page bottoms (`break-after: avoid` on titles/labels).
- Cover page that looks intentional, not like a form.
- Shopping list legible at arm's length on a counter.

### Phase 5 — Swap in, keep the contract
- Rewrite `pdf_export.build_pdf_bytes(...)` to delegate to the new pipeline:
  `return pdf_render.render_pdf(pdf_render.build_html(week_plan, shopping_sections, week_start))`.
  Signature unchanged → `app.py` untouched.
- Delete the old `fpdf2` `MealPDF` class + `_UNICODE_MAP`/`_s()` once the new path is verified.
- Decide whether to drop `fpdf2` from `requirements.txt` (only if nothing else imports it —
  grep first).
- Smoke test: build a PDF for a real saved week, confirm bytes > 0 and it opens.

### Phase 6 — Ship
- `streamlit run app.py`, generate/load a week, click **Build & Download Weekly PDF**, verify.
- Merge to `main`, push.
- Deploy: install the Pi `apt` libs (above), `pip install -r requirements.txt` in the shared
  venv, `ssh rachett 'bash ~/deploy.sh diet'`, then load `rachett.local/diet` and download a
  PDF to confirm WeasyPrint works on the Pi.

---

## Risks / watch-items
- **Pi system libs** — the one true deploy risk. If WeasyPrint won't import on the Pi, it's a
  missing `libpango`/`libgdk-pixbuf`. Test the import on the Pi *before* trusting the deploy.
- **Fonts** — if bundling is a hassle, system `Georgia`/`-apple-system` fallback still looks
  far better than the current PDF. Don't let font polish block the redesign.
- **Page breaks** — WeasyPrint's break control is good but not Chrome-perfect; budget a little
  iteration time in Phase 4 for awkward breaks.
- **Keep a rollback** — don't delete `pdf_export.py`'s old code until Phase 5 is verified end to end.

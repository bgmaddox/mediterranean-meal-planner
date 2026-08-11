"""
pdf_render.py
-------------
HTML/CSS → PDF pipeline using WeasyPrint.

Palette (matches card_html.py):
  surface  #FFFDF8   page bg  #F7F1E3   olive    #5B7553
  terracotta #C56A3D navy txt #233044   muted    #5A6472
  hairline #E7DFCD   gold     #D8A24A

Public API:
    build_html(week_plan, shopping_sections, week_start) -> str
    render_pdf(html, base_url=None) -> bytes
"""

from datetime import datetime
from html import escape
from pathlib import Path

import weasyprint

from schemas import WeekPlan
from shopping import ShoppingItem

_PROJECT_ROOT = Path(__file__).parent

# ── Print stylesheet ──────────────────────────────────────────────────────────
# Derived from card_html.py's CARD_STYLES, adapted for print:
#   - System fonts (no CDN; Pi-safe)
#   - box-shadow removed (smears on paper)
#   - max-width removed (fill page)
#   - @page margins, running header/footer
#   - break-inside:avoid on cards so they don't split mid-recipe
#   - New classes: .pr-* for ingredients, instructions, callouts
PRINT_STYLES = """
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

@page {
  size: Letter;
  margin: 14mm 15mm 16mm;
  @top-center {
    content: "Mediterranean Meal Plan";
    font-family: Georgia, serif;
    font-size: 8pt;
    color: #5B7553;
    letter-spacing: .06em;
  }
  @bottom-center {
    content: "Page " counter(page) " of " counter(pages);
    font-family: -apple-system, sans-serif;
    font-size: 7.5pt;
    color: #9aa1ab;
  }
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 10.5pt;
  color: #233044;
  background: #FFFDF8;
  line-height: 1.5;
}

/* ── Page-break helpers ──────────────────────────────────────────────────── */
.page-break        { break-before: page; }
.keep-with-next    { break-after: avoid; }

/* ── Cover ───────────────────────────────────────────────────────────────── */
.pr-cover {
  text-align: center;
  padding: 28pt 0 20pt;
  border-bottom: 1.5pt solid #5B7553;
  margin-bottom: 18pt;
}
.pr-cover__title {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 30pt;
  font-weight: bold;
  color: #5B7553;
  line-height: 1.1;
  margin-bottom: 6pt;
}
.pr-cover__sub {
  font-size: 12pt;
  color: #5A6472;
  margin-bottom: 16pt;
}
.pr-cover__dinners {
  display: inline-block;
  text-align: left;
  margin: 0 auto;
}
.pr-cover__dinner-row {
  font-size: 10.5pt;
  color: #233044;
  padding: 2pt 0;
}
.pr-cover__dinner-row::before {
  content: "▸ ";
  color: #C56A3D;
}

/* ── Summary strip (cover) ───────────────────────────────────────────────── */
.pr-summary {
  display: flex;
  gap: 0;
  border: 1pt solid #E7DFCD;
  border-radius: 10pt;
  overflow: hidden;
  margin: 16pt 0 0;
  background: #F7F1E3;
}
.pr-summary__stat {
  flex: 1;
  text-align: center;
  padding: 10pt 6pt;
  border-right: 1pt solid #E7DFCD;
}
.pr-summary__stat:last-child { border-right: none; }
.pr-summary__val {
  font-family: Georgia, serif;
  font-size: 20pt;
  font-weight: bold;
  color: #233044;
  line-height: 1.1;
  display: block;
}
.pr-summary__val--cost { color: #C56A3D; }
.pr-summary__label {
  font-size: 7.5pt;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: #5A6472;
  margin-top: 3pt;
  display: block;
}

/* ── Section banner (DINNERS / LUNCHES / etc.) ───────────────────────────── */
.pr-section {
  background: #5B7553;
  color: #FFFDF8;
  font-family: Georgia, serif;
  font-size: 13pt;
  font-weight: bold;
  letter-spacing: .08em;
  padding: 7pt 14pt;
  border-radius: 6pt;
  margin: 0 0 16pt;
  break-after: avoid;
}

/* ── Meal card wrapper ───────────────────────────────────────────────────── */
.pr-meal {
  border: 1pt solid #E7DFCD;
  border-radius: 8pt;
  padding: 10pt 12pt;
  margin-bottom: 10pt;
  background: #FFFDF8;
}
/* Dinners after the first get their own page */
.pr-meal--dinner + .pr-meal--dinner { break-before: page; }

/* ── Dinner-only compaction (keep recipes to one page each) ───────────────── */
.pr-meal--dinner { padding: 8pt 10pt; line-height: 1.4; }
.pr-meal--dinner .pr-meal__title { font-size: 14pt; }
.pr-meal--dinner .pr-columns { gap: 12pt; }
.pr-meal--dinner .pr-ingredients li,
.pr-meal--dinner .pr-steps li { font-size: 8pt; padding-top: 1pt; padding-bottom: 1pt; }
.pr-meal--dinner .pr-ing__pantry { font-size: 7.5pt; }
.pr-meal--dinner .pr-servings { font-size: 8pt; }
.pr-meal--dinner .pr-callout { font-size: 7.5pt; }
.pr-meal__title {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 16pt;
  font-weight: bold;
  color: #233044;
  line-height: 1.2;
  margin-bottom: 3pt;
  break-after: avoid;
}

/* Keeps counter+title+meta+tags+divider together; prevents split before content */
.pr-meal-header { break-after: avoid; }

/* Dinner counter badge (e.g. "DINNER 2 OF 5") */
.pr-dinner-counter {
  font-size: 7pt;
  font-weight: 700;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: #5B7553;
  margin-bottom: 2pt;
}
.pr-meal__meta {
  font-size: 8pt;
  color: #5A6472;
  margin-bottom: 3pt;
}
.pr-meal__meta span + span::before { content: " · "; color: #C56A3D; }

/* Health highlight tags */
.pr-tags { display: flex; flex-wrap: wrap; gap: 3pt; margin-bottom: 5pt; }
.pr-tag {
  background: rgba(91,117,83,.12);
  color: #5B7553;
  font-size: 7pt;
  font-weight: 600;
  padding: 1pt 5pt;
  border-radius: 99pt;
}

/* Divider */
.pr-divider {
  border: none;
  border-top: 1pt solid #E7DFCD;
  margin: 5pt 0;
}

/* ── Two-column ingredient + instruction layout ───────────────────────────── */
.pr-columns {
  display: flex;
  gap: 14pt;
  margin-bottom: 2pt;
}
.pr-col-left  { flex: 0 0 42%; }
.pr-col-right { flex: 1; }

/* ── Labels (Ingredients / Instructions / etc.) ──────────────────────────── */
.pr-label {
  font-size: 7.5pt;
  font-weight: 700;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: #5A6472;
  margin-bottom: 3pt;
  break-after: avoid;
}

/* ── Ingredient list ─────────────────────────────────────────────────────── */
.pr-ingredients { list-style: none; }
.pr-ingredients li {
  font-size: 9pt;
  color: #233044;
  padding: 1.5pt 0;
  border-bottom: .5pt solid #F1EBDC;
  display: flex;
  gap: 5pt;
}
.pr-ingredients li:last-child { border-bottom: none; }
.pr-ingredients li::before { content: "·"; color: #C56A3D; flex-shrink: 0; }
.pr-ing__pantry { color: #9aa1ab; font-size: 8.5pt; }

/* ── Instruction steps ───────────────────────────────────────────────────── */
.pr-steps { list-style: none; counter-reset: steps; }
.pr-steps li {
  counter-increment: steps;
  font-size: 9pt;
  color: #233044;
  padding: 2pt 0 2pt 18pt;
  position: relative;
  border-bottom: .5pt solid #F1EBDC;
}
.pr-steps li:last-child { border-bottom: none; }
.pr-steps li::before {
  content: counter(steps);
  position: absolute;
  left: 0;
  top: 2pt;
  background: #5B7553;
  color: #FFFDF8;
  font-size: 7pt;
  font-weight: 700;
  width: 14pt;
  height: 14pt;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 14pt;
  text-align: center;
}

/* ── Serving sizes table ─────────────────────────────────────────────────── */
.pr-servings { width: 100%; border-collapse: collapse; margin-top: 6pt; font-size: 9pt; }
.pr-servings th {
  text-align: left;
  font-size: 7.5pt;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: #5A6472;
  padding: 3pt 6pt;
  border-bottom: 1pt solid #E7DFCD;
}
.pr-servings td {
  padding: 3pt 6pt;
  border-bottom: .5pt solid #F1EBDC;
  color: #233044;
}
.pr-servings tr:last-child td { border-bottom: none; }

/* ── Callout boxes ───────────────────────────────────────────────────────── */
.pr-callout {
  border-radius: 4pt;
  padding: 4pt 8pt;
  margin-top: 4pt;
  font-size: 8pt;
  break-inside: avoid;
}
.pr-callout__label {
  font-size: 7pt;
  font-weight: 700;
  letter-spacing: .07em;
  text-transform: uppercase;
  margin-bottom: 2pt;
}
.pr-callout--kids   { background: #FFF8DC; border-left: 3pt solid #D8A24A; }
.pr-callout--tip    { background: #EAF4EA; border-left: 3pt solid #5B7553; }
.pr-callout--prep   { background: #E8EEF6; border-left: 3pt solid #4A6DA7; }
.pr-callout--kids   .pr-callout__label { color: #A07828; }
.pr-callout--tip    .pr-callout__label { color: #3D6B3D; }
.pr-callout--prep   .pr-callout__label { color: #2E5090; }

/* ── Nutrition + cost footnote ───────────────────────────────────────────── */
.pr-footer-note {
  font-size: 7.5pt;
  color: #9aa1ab;
  margin-top: 4pt;
  padding-top: 3pt;
  border-top: .5pt solid #E7DFCD;
}
.pr-footer-note span + span::before { content: " · "; }

/* ── Sunday prep card ────────────────────────────────────────────────────── */
.pr-prep {
  border: 1pt solid #E7DFCD;
  border-left: 3pt solid #5B7553;
  border-radius: 8pt;
  padding: 10pt 14pt;
  margin-bottom: 10pt;
  background: #FFFDF8;
  break-inside: avoid;
}
.pr-prep__task { font-size: 11pt; font-weight: 700; color: #233044; margin-bottom: 5pt; }
.pr-prep__yields { font-size: 8.5pt; color: #5A6472; margin-bottom: 4pt; }
.pr-prep__storage { font-size: 8.5pt; color: #5A6472; }
.pr-prep__storage b { color: #5B7553; }

/* ── Shopping list ───────────────────────────────────────────────────────── */
.pr-shop-grid {
  columns: 2;
  column-gap: 16pt;
}
.pr-shop-section {
  break-inside: avoid;
  margin-bottom: 12pt;
}
.pr-shop__head {
  background: #F2ECDD;
  border: 1pt solid #E7DFCD;
  border-radius: 6pt 6pt 0 0;
  padding: 6pt 10pt;
  font-size: 9.5pt;
  font-weight: 700;
  color: #233044;
  display: flex;
  justify-content: space-between;
}
.pr-shop__count {
  font-size: 8pt;
  font-weight: 600;
  color: #5A6472;
}
.pr-shop__row {
  display: flex;
  justify-content: space-between;
  gap: 8pt;
  padding: 4pt 10pt;
  border: 1pt solid #E7DFCD;
  border-top: none;
  font-size: 9pt;
}
.pr-shop__row:last-child { border-radius: 0 0 6pt 6pt; }
.pr-shop__item { color: #233044; }
.pr-shop__qty  { color: #5A6472; text-align: right; white-space: nowrap; }

/* ── Cover extras (lower 2/3 of page 1) ──────────────────────────────────── */
.pr-extras__heading {
  font-family: Georgia, serif;
  font-size: 11pt;
  font-weight: bold;
  color: #5B7553;
  letter-spacing: .04em;
  margin: 16pt 0 6pt;
  break-after: avoid;
}

/* Menu-at-a-glance table */
.pr-menu { width: 100%; border-collapse: collapse; font-size: 9.5pt; }
.pr-menu th {
  text-align: left;
  font-size: 7.5pt;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: #5A6472;
  padding: 4pt 8pt;
  border-bottom: 1pt solid #5B7553;
}
.pr-menu td {
  padding: 4pt 8pt;
  border-bottom: .5pt solid #F1EBDC;
  color: #233044;
  vertical-align: top;
}
.pr-menu tr:last-child td { border-bottom: none; }
.pr-menu__num {
  width: 18pt;
  color: #C56A3D;
  font-weight: 700;
  font-family: Georgia, serif;
}
.pr-menu__leftover {
  color: #9aa1ab;
  font-size: 7.5pt;
  font-style: italic;
}

/* Weekend prep checklist */
.pr-prep-list { list-style: none; }
.pr-prep-list li {
  font-size: 9pt;
  color: #233044;
  padding: 3pt 0 3pt 16pt;
  position: relative;
  border-bottom: .5pt solid #F1EBDC;
}
.pr-prep-list li:last-child { border-bottom: none; }
.pr-prep-list li::before {
  content: "";
  position: absolute;
  left: 0;
  top: 4pt;
  width: 8pt;
  height: 8pt;
  border: 1pt solid #5B7553;
  border-radius: 2pt;
}

/* Ingredient overlap notes */
.pr-overlap {
  font-size: 8.5pt;
  color: #5A6472;
  line-height: 1.45;
  background: #F7F1E3;
  border-left: 3pt solid #C56A3D;
  border-radius: 0 4pt 4pt 0;
  padding: 7pt 10pt;
  margin-top: 4pt;
}
</style>
"""


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _e(text) -> str:
    return escape(str(text)) if text is not None else ""


def _fmt_date(iso: str) -> str:
    """'2026-03-30' → 'March 30, 2026'"""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")
    except (ValueError, AttributeError):
        return iso


def _callout(cls: str, label: str, text: str) -> str:
    if not text:
        return ""
    return (
        f'<div class="pr-callout pr-callout--{cls}">'
        f'<div class="pr-callout__label">{label}</div>'
        f'{_e(text)}'
        '</div>'
    )


def _footer_note(nut: dict, cost: dict) -> str:
    parts = []
    cal = nut.get("calories_per_adult_serving")
    pro = nut.get("protein_g")
    fib = nut.get("fiber_g")
    fat = nut.get("fat_g")
    cps = cost.get("cost_per_serving_usd")
    if cal is not None:
        parts.append(f"<span>{cal} kcal</span>")
    if pro is not None:
        parts.append(f"<span>{pro}g protein</span>")
    if fib is not None:
        parts.append(f"<span>{fib}g fiber</span>")
    if fat is not None:
        parts.append(f"<span>{fat}g fat</span>")
    sod = nut.get("sodium_mg")
    if sod is not None:
        parts.append(f"<span>{sod}mg sodium</span>")
    if cps is not None:
        parts.append(f"<span>~${cps:.2f}/serving</span>")
    if not parts:
        return ""
    return f'<div class="pr-footer-note">{"".join(parts)}</div>'


# ── Section builders ──────────────────────────────────────────────────────────

def _cover(week_start: str, summary: dict, dinners: list) -> str:
    fish  = summary.get("fish_meal_count", 0)
    meat  = summary.get("red_meat_meal_count", 0)
    veg   = summary.get("vegetarian_meal_count", 0)
    cost  = summary.get("estimated_weekly_grocery_cost_usd")

    # Computed from the dinners themselves (not Claude's summary): weekly
    # scorecard for the Mediterranean-pattern goals — fiber and plate mix.
    fibers = [
        d.get("nutrition_estimate", {}).get("fiber_g")
        for d in dinners
    ]
    fibers = [f for f in fibers if isinstance(f, (int, float))]
    avg_fiber = round(sum(fibers) / len(fibers)) if fibers else None
    veg_fwd = sum(
        1 for d in dinners
        if d.get("composition") in ("vegetable_forward", "mezze")
    )
    has_composition = any(d.get("composition") for d in dinners)

    stats_html = (
        f'<div class="pr-summary__stat">'
        f'<span class="pr-summary__val">{fish}</span>'
        f'<span class="pr-summary__label">Fish</span></div>'
        f'<div class="pr-summary__stat">'
        f'<span class="pr-summary__val">{meat}</span>'
        f'<span class="pr-summary__label">Red meat</span></div>'
        f'<div class="pr-summary__stat">'
        f'<span class="pr-summary__val">{veg}</span>'
        f'<span class="pr-summary__label">Vegetarian</span></div>'
    )
    if has_composition:
        stats_html += (
            f'<div class="pr-summary__stat">'
            f'<span class="pr-summary__val">{veg_fwd}</span>'
            f'<span class="pr-summary__label">Veg-forward</span></div>'
        )
    if avg_fiber is not None:
        stats_html += (
            f'<div class="pr-summary__stat">'
            f'<span class="pr-summary__val">{avg_fiber}g</span>'
            f'<span class="pr-summary__label">Avg fiber</span></div>'
        )
    if cost:
        stats_html += (
            f'<div class="pr-summary__stat">'
            f'<span class="pr-summary__val pr-summary__val--cost">${cost:.0f}</span>'
            f'<span class="pr-summary__label">Est. groceries</span></div>'
        )

    special = summary.get("special_ingredients", [])
    special_html = ""
    if special:
        special_html = (
            f'<p style="font-size:8.5pt;color:#5A6472;margin-top:10pt;">'
            f'Special ingredients this week: {_e(", ".join(special))}</p>'
        )

    return f"""
<div class="pr-cover">
  <div class="pr-cover__title">Mediterranean Meal Plan</div>
  <div class="pr-cover__sub">Week of {_e(_fmt_date(week_start))}</div>
  {special_html}
  <div class="pr-summary">{stats_html}</div>
</div>
"""


def _menu_table(dinners: list, lunches: list) -> str:
    """Side-by-side dinner + lunch menu for the week, row per slot."""
    rows = ""
    n = max(len(dinners), len(lunches))
    for i in range(n):
        d = dinners[i] if i < len(dinners) else None
        l = lunches[i] if i < len(lunches) else None
        dinner_cell = _e(d.get("name", "")) if d else "—"
        if l:
            leftover = (
                ' <span class="pr-menu__leftover">↩ leftover</span>'
                if l.get("source") == "leftover" else ""
            )
            lunch_cell = f'{_e(l.get("name", ""))}{leftover}'
        else:
            lunch_cell = "—"
        rows += (
            f'<tr><td class="pr-menu__num">{i + 1}</td>'
            f'<td>{dinner_cell}</td><td>{lunch_cell}</td></tr>'
        )
    return (
        '<div class="pr-extras__heading">This Week\'s Menu</div>'
        '<table class="pr-menu">'
        '<thead><tr><th></th><th>Dinner</th><th>Lunch</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


def _prep_checklist(prep: list) -> str:
    """Compact weekend-prep preview for the cover."""
    if not prep:
        return ""
    # Show only the action (first sentence); full storage notes live in the
    # dedicated SUNDAY PREP section later in the document.
    def _action(text: str) -> str:
        return text.split(". ", 1)[0].rstrip(".")

    items = "".join(
        f'<li>{_e(_action(t["task"]))}</li>' for t in prep if t.get("task")
    )
    if not items:
        return ""
    return (
        '<div class="pr-extras__heading">This Weekend\'s Prep</div>'
        f'<ul class="pr-prep-list">{items}</ul>'
    )


def _overlap_notes(summary: dict) -> str:
    notes = summary.get("ingredient_overlap_notes")
    if not notes:
        return ""
    return (
        '<div class="pr-extras__heading">How the Shopping Works</div>'
        f'<div class="pr-overlap">{_e(notes)}</div>'
    )


def _cover_extras(dinners: list, lunches: list, prep: list,
                  summary: dict) -> str:
    """Fill the lower portion of page 1 with at-a-glance planning info."""
    return (
        _menu_table(dinners, lunches)
        + _prep_checklist(prep)
        + _overlap_notes(summary)
    )


def _dinner(d: dict, n: int = 0, total: int = 0) -> str:
    name    = d.get("name", "Untitled")
    cook    = d.get("cook_time_minutes")
    equip   = d.get("primary_equipment", "")
    highlights = d.get("health_highlights") or []
    ingredients = d.get("ingredients") or []
    instructions = d.get("instructions") or []
    serving_sizes = d.get("serving_sizes") or []
    nut  = d.get("nutrition_estimate") or {}
    cost = d.get("cost_estimate") or {}

    # meta chips
    meta_parts = []
    if cook:
        meta_parts.append(f'<span>{cook} min</span>')
    if equip:
        meta_parts.append(f'<span>{_e(equip)}</span>')
    meta_html = f'<div class="pr-meal__meta">{"".join(meta_parts)}</div>' if meta_parts else ""

    # health tags
    tags_html = ""
    if highlights:
        tags = "".join(f'<span class="pr-tag">{_e(t)}</span>' for t in highlights)
        tags_html = f'<div class="pr-tags">{tags}</div>'

    # ingredients
    ing_items = ""
    for ing in ingredients:
        qty  = ing.get("quantity", "")
        unit = ing.get("unit", "")
        ing_name = ing.get("name", "")
        pantry = ing.get("pantry_staple", False)
        qty_str = f"{qty} {unit}".strip()
        pantry_html = ' <span class="pr-ing__pantry">(pantry)</span>' if pantry else ""
        ing_items += (
            f"<li><span>{_e(qty_str)} {_e(ing_name)}{pantry_html}</span></li>"
        )
    ing_html = f'<div class="pr-label">Ingredients</div><ul class="pr-ingredients">{ing_items}</ul>'

    # instructions
    step_items = "".join(f"<li>{_e(step)}</li>" for step in instructions)
    steps_html = f'<div class="pr-label">Instructions</div><ol class="pr-steps">{step_items}</ol>'

    # two-column layout
    columns_html = (
        '<div class="pr-columns">'
        f'<div class="pr-col-left">{ing_html}</div>'
        f'<div class="pr-col-right">{steps_html}</div>'
        '</div>'
    )

    # serving sizes
    servings_html = ""
    if serving_sizes:
        rows = ""
        for s in serving_sizes:
            kid = s.get("kid_portion", "")
            kid_td = f"<td>{_e(kid)}</td>" if kid else "<td>—</td>"
            rows += (
                f"<tr><td>{_e(s.get('component',''))}</td>"
                f"<td>{_e(s.get('adult_portion',''))}</td>"
                f"{kid_td}</tr>"
            )
        servings_html = (
            '<div class="pr-label" style="margin-top:10pt;">Serving Sizes</div>'
            '<table class="pr-servings">'
            '<thead><tr><th>Component</th><th>Adult</th><th>Kids</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )

    # callouts
    callouts = (
        _callout("kids", "Kids", d.get("kid_adaptation", ""))
        + _callout("tip", "Health tip", d.get("uric_acid_tip") or "")
        + _callout("prep", "Sunday prep", d.get("sunday_prep") or "")
    )

    counter_html = (
        f'<div class="pr-dinner-counter">Dinner {n} of {total}</div>'
        if n and total else ""
    )
    return (
        '<div class="pr-meal pr-meal--dinner">'
        '<div class="pr-meal-header">'
        f'{counter_html}'
        f'<div class="pr-meal__title">{_e(name)}</div>'
        f'{meta_html}{tags_html}'
        '<hr class="pr-divider">'
        '</div>'
        f'{columns_html}'
        f'{servings_html}'
        f'{callouts}'
        f'{_footer_note(nut, cost)}'
        '</div>'
    )


def _lunch(l: dict) -> str:
    name   = l.get("name", "Untitled")
    source = "Leftover from dinner" if l.get("source") == "leftover" else "Standalone"
    reheat = l.get("reheat", "")
    highlights = l.get("health_highlights") or []
    ingredients = l.get("ingredients") or []
    serving_sizes = l.get("serving_sizes") or []
    pack = l.get("pack_instructions", "")
    nut  = l.get("nutrition_estimate") or {}
    cost = l.get("cost_estimate") or {}

    meta_parts = [f'<span>{_e(source)}</span>']
    if reheat:
        meta_parts.append(f'<span>{_e(reheat)}</span>')
    meta_html = f'<div class="pr-meal__meta">{"".join(meta_parts)}</div>'

    tags_html = ""
    if highlights:
        tags = "".join(f'<span class="pr-tag">{_e(t)}</span>' for t in highlights)
        tags_html = f'<div class="pr-tags">{tags}</div>'

    ing_items = ""
    for ing in ingredients:
        qty  = ing.get("quantity", "")
        unit = ing.get("unit", "")
        qty_str = f"{qty} {unit}".strip()
        pantry = ing.get("pantry_staple", False)
        pantry_html = ' <span class="pr-ing__pantry">(pantry)</span>' if pantry else ""
        ing_items += f"<li><span>{_e(qty_str)} {_e(ing.get('name',''))}{pantry_html}</span></li>"
    ing_html = f'<div class="pr-label">Ingredients</div><ul class="pr-ingredients">{ing_items}</ul>' if ing_items else ""

    servings_html = ""
    if serving_sizes:
        rows = "".join(
            f'<tr><td>{_e(s.get("component",""))}</td><td>{_e(s.get("adult_portion",""))}</td></tr>'
            for s in serving_sizes
        )
        servings_html = (
            '<div class="pr-label" style="margin-top:10pt;">Serving Sizes</div>'
            '<table class="pr-servings">'
            '<thead><tr><th>Component</th><th>Amount</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )

    pack_html = _callout("prep", "Pack instructions", pack) if pack else ""

    return (
        '<div class="pr-meal avoid-break">'
        f'<div class="pr-meal__title">{_e(name)}</div>'
        f'{meta_html}{tags_html}'
        '<hr class="pr-divider">'
        f'{ing_html}'
        f'{servings_html}'
        f'{pack_html}'
        f'{_footer_note(nut, cost)}'
        '</div>'
    )


def _prep_section(tasks: list) -> str:
    if not tasks:
        return ""
    cards = ""
    for task in tasks:
        yields = task.get("yields_for") or []
        yields_html = ""
        if yields:
            tags = "".join(f'<span class="pr-tag">{_e(y)}</span>' for y in yields)
            yields_html = f'<div class="pr-prep__yields" style="margin-bottom:5pt;"><div class="pr-tags">{tags}</div></div>'
        storage = task.get("storage", "")
        storage_html = (
            f'<div class="pr-prep__storage"><b>Storage:</b> {_e(storage)}</div>'
            if storage else ""
        )
        cards += (
            '<div class="pr-prep">'
            f'<div class="pr-prep__task">{_e(task.get("task",""))}</div>'
            f'{yields_html}{storage_html}'
            '</div>'
        )
    return cards


def _snacks_section(snacks: list) -> str:
    """Snack suggestions appended below the Sunday Prep cards (same page)."""
    if not snacks:
        return ""
    cards = '<div class="pr-section" style="margin-top:14pt;">SNACKS</div>'
    for snack in snacks:
        cards += (
            '<div class="pr-prep">'
            f'<div class="pr-prep__task">{_e(snack.get("name", ""))}</div>'
            f'<div class="pr-prep__storage">{_e(snack.get("description", ""))}</div>'
            '</div>'
        )
    return cards


def _shopping(sections: dict[str, list]) -> str:
    if not sections:
        return ""
    section_blocks = ""
    for section_name, items in sections.items():
        if not items:
            continue
        rows = "".join(
            f'<div class="pr-shop__row">'
            f'<span class="pr-shop__item">{_e(item.name)}</span>'
            f'<span class="pr-shop__qty">{_e(item.display_quantity())}</span>'
            f'</div>'
            for item in items
        )
        section_blocks += (
            '<div class="pr-shop-section">'
            f'<div class="pr-shop__head">'
            f'<span>{_e(section_name)}</span>'
            f'<span class="pr-shop__count">{len(items)}</span>'
            f'</div>'
            f'{rows}'
            '</div>'
        )
    return f'<div class="pr-shop-grid">{section_blocks}</div>'


# ── Document assembly ─────────────────────────────────────────────────────────

def build_html(
    week_plan: WeekPlan,
    shopping_sections: dict[str, list[ShoppingItem]],
    week_start: str | None = None,
) -> str:
    from datetime import date
    if week_start is None:
        week_start = date.today().isoformat()

    dinners  = week_plan.get("dinners") or []
    lunches  = week_plan.get("lunches") or []
    prep     = week_plan.get("sunday_prep_list") or []
    snacks   = week_plan.get("snacks") or []
    summary  = week_plan.get("week_summary") or {}

    # Dinners — each gets its own page via .pr-meal--dinner CSS; section banner
    # sits at the top of the first dinner page (no standalone page).
    total_dinners = len(dinners)
    dinners_html = "".join(_dinner(d, i + 1, total_dinners) for i, d in enumerate(dinners))

    # Lunches — flow naturally on one section page
    lunches_html = "".join(_lunch(l) for l in lunches)

    body = (
        _cover(week_start, summary, dinners)
        + _cover_extras(dinners, lunches, prep, summary)
        # DINNERS: section banner + first dinner share a page; subsequent dinners
        # each get a new page via break-before:page on .pr-meal--dinner.
        + '<div class="pr-section page-break">DINNERS</div>'
        + dinners_html
        + '<div class="pr-section page-break">LUNCHES</div>'
        + lunches_html
        + '<div class="pr-section page-break">SUNDAY PREP</div>'
        + _prep_section(prep)
        + _snacks_section(snacks)
        + '<div class="pr-section page-break">SHOPPING LIST</div>'
        + _shopping(shopping_sections)
    )

    return f"<!DOCTYPE html><html><head><meta charset='utf-8'>{PRINT_STYLES}</head><body>{body}</body></html>"


# ── Renderer ──────────────────────────────────────────────────────────────────

def render_pdf(html: str, base_url: str | None = None) -> bytes:
    if base_url is None:
        base_url = str(_PROJECT_ROOT)
    return weasyprint.HTML(string=html, base_url=base_url).write_pdf()

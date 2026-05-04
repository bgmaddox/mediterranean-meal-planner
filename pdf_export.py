"""
pdf_export.py
-------------
Generates a downloadable weekly meal plan PDF containing:
  - All dinners (ingredients, instructions, kid adaptation, health highlights)
  - All lunches (ingredients, pack instructions)
  - Sunday prep list
  - Shopping list grouped by section

Entry point: build_pdf_bytes(week_plan, shopping_sections, week_start) -> bytes
"""

from datetime import date
from io import BytesIO

from fpdf import FPDF

from schemas import WeekPlan
from shopping import ShoppingItem

# ── Text sanitiser (Helvetica is Latin-1 only) ────────────────────────────────
_UNICODE_MAP = str.maketrans({
    "\u2014": "-",   # em dash
    "\u2013": "-",   # en dash
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2026": "...", # ellipsis
    "\u2022": "*",   # bullet
    "\u00b7": "|",   # middle dot
    "\u2019": "'",   # right single quote (duplicate alias)
})


def _s(text: str | None) -> str:
    """Sanitise text for Helvetica: map known unicode, drop the rest."""
    if not text:
        return ""
    text = text.translate(_UNICODE_MAP)
    return text.encode("latin-1", errors="ignore").decode("latin-1")


# ── Colours ───────────────────────────────────────────────────────────────────
_OLIVE   = (91, 110, 68)    # section headers
_SLATE   = (60, 70, 80)     # sub-headers
_LIGHT   = (245, 245, 240)  # shaded row backgrounds
_WHITE   = (255, 255, 255)
_DARK    = (30, 30, 30)     # body text


class MealPDF(FPDF):
    def __init__(self, week_start: str):
        super().__init__()
        self.week_start = week_start
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 18, 18)

    # ── Repeated page elements ─────────────────────────────────────────────────
    def header(self):
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*_OLIVE)
        self.cell(0, 6, _s(f"Mediterranean Meal Plan - Week of {self.week_start}"), align="L")
        self.set_text_color(*_DARK)
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 6, f"Page {self.page_no()}", align="C")
        self.set_text_color(*_DARK)

    def _reset_x(self):
        """Always call before rendering — fpdf2 leaves x at right edge after multi_cell."""
        self.set_x(self.l_margin)

    # ── Section title (big olive banner) ──────────────────────────────────────
    def section_title(self, text: str):
        self.ln(2)
        self._reset_x()
        self.set_fill_color(*_OLIVE)
        self.set_text_color(*_WHITE)
        self.set_font("Helvetica", "B", 13)
        self.multi_cell(self.epw, 9, _s(f"  {text}"), fill=True)
        self.set_text_color(*_DARK)
        self._reset_x()
        self.ln(3)

    # ── Meal name bar ─────────────────────────────────────────────────────────
    def meal_title(self, name: str, meta: str):
        self._reset_x()
        self.set_fill_color(*_LIGHT)
        self.set_font("Helvetica", "B", 11)
        self.multi_cell(self.epw, 7, _s(name), fill=True)
        if meta:
            self._reset_x()
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(90, 90, 90)
            self.multi_cell(self.epw, 5, _s(meta), fill=True)
            self.set_text_color(*_DARK)
        self._reset_x()
        self.ln(1)

    # ── Small bold label ──────────────────────────────────────────────────────
    def label(self, text: str):
        self._reset_x()
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*_SLATE)
        self.multi_cell(self.epw, 5, _s(text))
        self.set_text_color(*_DARK)
        self._reset_x()

    # ── Bullet item ───────────────────────────────────────────────────────────
    def bullet(self, text: str):
        self._reset_x()
        self.set_font("Helvetica", "", 9)
        self.multi_cell(self.epw, 5, _s(f"  *  {text}"))
        self._reset_x()

    # ── Numbered step ─────────────────────────────────────────────────────────
    def numbered(self, n: int, text: str):
        self._reset_x()
        self.set_font("Helvetica", "", 9)
        self.multi_cell(self.epw, 5, _s(f"  {n}.  {text}"))
        self._reset_x()

    # ── Callout box (kids / tip / prep) ───────────────────────────────────────
    def callout(self, prefix: str, text: str, r: int, g: int, b: int):
        self._reset_x()
        self.set_fill_color(r, g, b)
        self.set_font("Helvetica", "B", 8)
        self.multi_cell(self.epw, 5, _s(f"  {prefix}  {text}"), fill=True)
        self._reset_x()
        self.ln(1)


# ── Main builder ──────────────────────────────────────────────────────────────

def build_pdf_bytes(
    week_plan: WeekPlan,
    shopping_sections: dict[str, list[ShoppingItem]],
    week_start: str | None = None,
) -> bytes:
    """
    Build and return the weekly meal plan PDF as raw bytes.

    Parameters
    ----------
    week_plan : WeekPlan
    shopping_sections : dict from shopping.build_shopping_list()
    week_start : ISO date string (defaults to today)

    Returns
    -------
    bytes - PDF file content ready for st.download_button()
    """
    if week_start is None:
        week_start = date.today().isoformat()

    pdf = MealPDF(week_start)
    pdf.add_page()

    # ── Cover strip ───────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*_OLIVE)
    pdf._reset_x()
    pdf.multi_cell(pdf.epw, 12, "Mediterranean Meal Plan", align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 90, 90)
    pdf._reset_x()
    pdf.multi_cell(pdf.epw, 7, f"Week of {week_start}", align="C")
    pdf.set_text_color(*_DARK)
    pdf._reset_x()
    pdf.ln(6)

    # ── Week summary strip ────────────────────────────────────────────────────
    summary = week_plan.get("week_summary", {})
    if summary:
        pdf.set_font("Helvetica", "", 9)
        parts = []
        if summary.get("fish_meal_count") is not None:
            parts.append(f"Fish meals: {summary['fish_meal_count']}")
        if summary.get("red_meat_meal_count") is not None:
            parts.append(f"Red meat: {summary['red_meat_meal_count']}")
        if summary.get("vegetarian_meal_count") is not None:
            parts.append(f"Vegetarian: {summary['vegetarian_meal_count']}")
        if parts:
            pdf._reset_x()
            pdf.multi_cell(pdf.epw, 6, _s("  |  ".join(parts)), align="C")
        special = summary.get("special_ingredients", [])
        if special:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(90, 90, 90)
            pdf._reset_x()
            pdf.multi_cell(pdf.epw, 5, _s(f"Special ingredients: {', '.join(special)}"), align="C")
            pdf.set_text_color(*_DARK)
    pdf._reset_x()
    pdf.ln(4)
    pdf.set_draw_color(*_OLIVE)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + pdf.epw, pdf.get_y())
    pdf._reset_x()
    pdf.ln(4)

    # ── Dinners ───────────────────────────────────────────────────────────────
    pdf.section_title("DINNERS")

    dinners = week_plan.get("dinners", [])
    for i, dinner in enumerate(dinners):
        meta_parts = [f"{dinner['cook_time_minutes']} min", dinner.get("primary_equipment", "")]
        meta_parts = [p for p in meta_parts if p]
        pdf.meal_title(dinner["name"], "  |  ".join(meta_parts))

        highlights = dinner.get("health_highlights", [])
        if highlights:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(80, 110, 70)
            pdf._reset_x()
            pdf.multi_cell(pdf.epw, 4, _s("  ".join(highlights)))
            pdf.set_text_color(*_DARK)
            pdf._reset_x()
            pdf.ln(1)

        # Ingredients
        ingredients = dinner.get("ingredients", [])
        if ingredients:
            pdf.label("Ingredients")
            for ing in ingredients:
                pantry_tag = " (pantry)" if ing.get("pantry_staple") else ""
                pdf.bullet(f"{ing.get('quantity', '')} {ing.get('unit', '')} {ing['name']}{pantry_tag}".strip())

        # Instructions
        instructions = dinner.get("instructions", [])
        if instructions:
            pdf.ln(1)
            pdf.label("Instructions")
            for n, step in enumerate(instructions, 1):
                pdf.numbered(n, step)

        # Serving sizes
        serving_sizes = dinner.get("serving_sizes", [])
        if serving_sizes:
            pdf.ln(1)
            pdf.label("Serving Sizes (per person)")
            for s in serving_sizes:
                adult = s.get("adult_portion", "")
                kid = s.get("kid_portion", "")
                kid_str = f"  |  Kids: {kid}" if kid else ""
                pdf.bullet(f"{s.get('component', '')}: Adult: {adult}{kid_str}")

        # Callouts
        if dinner.get("kid_adaptation"):
            pdf.ln(1)
            pdf.callout("KIDS:", dinner["kid_adaptation"], 255, 248, 220)
        if dinner.get("uric_acid_tip"):
            pdf.callout("TIP:", dinner["uric_acid_tip"], 220, 240, 220)
        if dinner.get("sunday_prep"):
            pdf.callout("SUNDAY PREP:", dinner["sunday_prep"], 220, 230, 245)

        # Nutrition + cost
        nut = dinner.get("nutrition_estimate")
        cost = dinner.get("cost_estimate")
        if nut or cost:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 100, 100)
            pdf._reset_x()
            if nut:
                pdf.multi_cell(pdf.epw, 5, _s(
                    f"Nutrition (per adult): "
                    f"{nut.get('calories_per_adult_serving', '-')} kcal  "
                    f"{nut.get('protein_g', '-')}g protein  "
                    f"{nut.get('fiber_g', '-')}g fiber  "
                    f"{nut.get('fat_g', '-')}g fat"
                ))
                pdf._reset_x()
            if cost:
                pdf.multi_cell(pdf.epw, 5, _s(
                    f"Est. cost: ${cost.get('total_ingredient_cost_usd', 0):.2f} total  "
                    f"(${cost.get('cost_per_serving_usd', 0):.2f}/serving)  non-pantry only, +/-20-30%"
                ))
                pdf._reset_x()
            pdf.set_text_color(*_DARK)

        pdf.ln(4)
        if i < len(dinners) - 1:  # page break after every dinner except the last
            pdf.add_page()

    # ── Lunches ───────────────────────────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("LUNCHES")

    for i, lunch in enumerate(week_plan.get("lunches", [])):
        source = "Leftover" if lunch.get("source") == "leftover" else "Standalone"
        reheat = lunch.get("reheat", "")
        meta = f"{source}  |  {reheat}" if reheat else source
        pdf.meal_title(lunch["name"], meta)

        highlights = lunch.get("health_highlights", [])
        if highlights:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(80, 110, 70)
            pdf._reset_x()
            pdf.multi_cell(pdf.epw, 4, _s("  ".join(highlights)))
            pdf.set_text_color(*_DARK)
            pdf._reset_x()
            pdf.ln(1)

        ingredients = lunch.get("ingredients", [])
        if ingredients:
            pdf.label("Ingredients")
            for ing in ingredients:
                pantry_tag = " (pantry)" if ing.get("pantry_staple") else ""
                pdf.bullet(f"{ing.get('quantity', '')} {ing.get('unit', '')} {ing['name']}{pantry_tag}".strip())

        serving_sizes = lunch.get("serving_sizes", [])
        if serving_sizes:
            pdf.ln(1)
            pdf.label("Serving Sizes")
            for s in serving_sizes:
                pdf.bullet(f"{s.get('component', '')}: {s.get('adult_portion', '')}")

        if lunch.get("pack_instructions"):
            pdf.ln(1)
            pdf.callout("PACK:", lunch["pack_instructions"], 255, 248, 220)

        nut = lunch.get("nutrition_estimate")
        cost = lunch.get("cost_estimate")
        if nut or cost:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 100, 100)
            pdf._reset_x()
            if nut:
                pdf.multi_cell(pdf.epw, 5, _s(
                    f"Nutrition (per adult): "
                    f"{nut.get('calories_per_adult_serving', '-')} kcal  "
                    f"{nut.get('protein_g', '-')}g protein  "
                    f"{nut.get('fiber_g', '-')}g fiber  "
                    f"{nut.get('fat_g', '-')}g fat"
                ))
                pdf._reset_x()
            if cost:
                pdf.multi_cell(pdf.epw, 5, _s(
                    f"Est. cost: ${cost.get('total_ingredient_cost_usd', 0):.2f}  non-pantry only, +/-20-30%"
                ))
                pdf._reset_x()
            pdf.set_text_color(*_DARK)

        pdf.ln(4)
        if i % 3 == 2:  # page break after every 3rd lunch
            pdf.add_page()

    # ── Sunday Prep ───────────────────────────────────────────────────────────
    pdf.add_page()
    sunday_tasks = week_plan.get("sunday_prep_list", [])
    if sunday_tasks:
        pdf.section_title("SUNDAY PREP")
        for task in sunday_tasks:
            pdf.set_font("Helvetica", "B", 10)
            pdf._reset_x()
            pdf.multi_cell(pdf.epw, 6, _s(task.get("task", "")))
            if task.get("yields_for"):
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(90, 90, 90)
                pdf._reset_x()
                pdf.multi_cell(pdf.epw, 5, _s("Used in: " + ", ".join(task["yields_for"])))
                pdf.set_text_color(*_DARK)
            if task.get("storage"):
                pdf.set_font("Helvetica", "", 8)
                pdf._reset_x()
                pdf.multi_cell(pdf.epw, 5, _s(f"Storage: {task['storage']}"))
            pdf._reset_x()
            pdf.ln(2)

    # ── Shopping List ─────────────────────────────────────────────────────────
    pdf.add_page()
    if shopping_sections:
        pdf.section_title("SHOPPING LIST")

        col_w = (pdf.epw - 8) / 2   # 8 mm gutter between columns
        col2_x = pdf.l_margin + col_w + 8

        section_list = [(s, itms) for s, itms in shopping_sections.items() if itms]
        mid = (len(section_list) + 1) // 2
        columns = [section_list[:mid], section_list[mid:]]
        x_offsets = [pdf.l_margin, col2_x]

        start_y = pdf.get_y()
        for col_sections, x_off in zip(columns, x_offsets):
            pdf.set_xy(x_off, start_y)
            for section, items in col_sections:
                pdf.set_x(x_off)
                pdf.set_font("Helvetica", "B", 10)
                pdf.set_text_color(*_SLATE)
                pdf.multi_cell(col_w, 6, _s(section))
                pdf.set_text_color(*_DARK)
                for item in items:
                    pdf.set_x(x_off)
                    pdf.set_font("Helvetica", "", 9)
                    pdf.multi_cell(col_w, 5, _s(f"  *  {item.name} - {item.display_quantity()}"))
                pdf.set_x(x_off)
                pdf.ln(2)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()

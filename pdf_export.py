"""
pdf_export.py
-------------
Public entry point for PDF generation.
Delegates to pdf_render.py (WeasyPrint HTML/CSS pipeline).

Entry point: build_pdf_bytes(week_plan, shopping_sections, week_start) -> bytes
"""

import pdf_render
from schemas import WeekPlan
from shopping import ShoppingItem


def build_pdf_bytes(
    week_plan: WeekPlan,
    shopping_sections: dict[str, list[ShoppingItem]],
    week_start: str | None = None,
) -> bytes:
    """Return the weekly meal plan PDF as raw bytes for st.download_button()."""
    html = pdf_render.build_html(week_plan, shopping_sections, week_start)
    return pdf_render.render_pdf(html)

"""
scripts/preview_pdf.py
----------------------
Render a preview PDF from the most recent saved week plan.
Output: PDFs/preview.pdf

Usage:
    source .venv/bin/activate
    python scripts/preview_pdf.py
"""

import json
import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import pdf_render
from shopping import build_shopping_list

DATA = Path(__file__).parent.parent / "data"
OUT  = Path(__file__).parent.parent / "PDFs" / "preview.pdf"


def main():
    history = json.loads((DATA / "history.json").read_text())
    if not history:
        print("No saved weeks in history.json — generate a week plan first.")
        sys.exit(1)

    entry     = history[0]          # most recent week
    week_plan = entry["plan"]
    week_start = entry["week_start"]

    shopping_sections = build_shopping_list(week_plan)

    print(f"Building PDF for week of {week_start}...")
    html  = pdf_render.build_html(week_plan, shopping_sections, week_start)
    pdf   = pdf_render.render_pdf(html)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(pdf)
    print(f"Written {len(pdf):,} bytes → {OUT}")


if __name__ == "__main__":
    main()

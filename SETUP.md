# Mediterranean Meal Planner — Setup Guide

A weekly meal planning app powered by Claude AI. Generates personalized dinner + lunch plans, shopping lists, and printable PDFs.

## Prerequisites

- Python 3.13
- An [Anthropic API key](https://console.anthropic.com/) (Claude API — pay-per-use, roughly $0.10–0.30 per meal plan generation)

## Installation

```bash
git clone https://github.com/bgmaddox/mediterranean-meal-planner
cd mediterranean-meal-planner
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the App

```bash
source .venv/bin/activate
ANTHROPIC_API_KEY=your-key-here streamlit run app.py
```

Or set the key in your shell profile so you don't have to pass it every time:

```bash
export ANTHROPIC_API_KEY=your-key-here   # add this to ~/.zshrc or ~/.bashrc
streamlit run app.py
```

---

## Customizing for Your Family

This app was built for a specific household. Before using it, you should tailor three things in `system_prompt.py`:

### 1. Household food constraints (`DEFAULT_CONSTRAINTS`)

Around line 221, find:

```python
DEFAULT_CONSTRAINTS = [
    "No mushrooms — avoid entirely in all forms: whole, sliced, dried, or as part of sauces, broths, or umami pastes.",
    "No oranges or orange juice — wife's constraint. Lemons and limes are fine and encouraged.",
    "No fish in lunches — fish should not be reheated at the office (smell). Avoid fish-based lunches entirely.",
]
```

Replace these with your family's actual constraints — allergies, dislikes, dietary restrictions, or anything that should never appear. Write them in plain English; Claude interprets them as hard rules. Remove any that don't apply.

### 2. Health goals (the system prompt header)

Around line 564, the prompt opens with:

```
Your job is to generate a complete, practical weekly meal plan for a specific family,
optimized for three simultaneous health goals: weight loss, improving cholesterol
(lower LDL, raise HDL), and managing uric acid levels to prevent gout.
```

Rewrite this sentence with your actual goals. Examples:
- "optimized for heart health and blood sugar management"
- "focused on high-protein, low-carb meals for weight loss"
- "designed for a family eating mostly plant-based with occasional fish"

The detailed constraint sections below that line (`# HEALTH CONSTRAINT: URIC ACID MANAGEMENT`, `# HEALTH CONSTRAINT: CHOLESTEROL`) are specific to the original owner's medical situation. Edit or replace them to match your own health context. If you don't have specific medical constraints, you can simplify or remove those sections entirely.

### 3. Household profile

Around line 583:

```python
- **Adults:** 2. Both are adventurous eaters who welcome bold flavors from any cuisine...
```

Update the adults description to match your household's tastes and comfort level with different cuisines. The children section (just below) is already configurable through the app's Settings tab — you can add/remove kids and their ages there without editing code.

---

## What's Already Configurable in the App

The **Settings tab** in the app handles these without code changes:

- Number of adults bringing lunch to work
- Weekly grocery budget
- Kids' names and ages
- Number of dinner days per week
- Ingredient portion scale
- Whether to use the curated "anchor recipes" library as inspiration
- Custom food constraints (add/remove without editing code)

---

## Pantry Staples

`PANTRY_STAPLES` (top of `system_prompt.py`) is the list of items Claude assumes you always have on hand — they won't appear on your shopping list. Edit this list to match your actual pantry. Add items you reliably keep stocked; remove anything you'd actually need to buy.

---

## Data Files

Your history, favorites, and preferences are stored in `data/` as plain JSON files. Safe to inspect or edit manually. These files are gitignored — they won't be overwritten if you pull updates.

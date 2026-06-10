"""
pages/kitchen.py
----------------
Kitchen Mode — full-screen recipe card view designed for casting to a
Google Nest Hub or any display. Navigate through the week's meals one at a time.

Access via the Streamlit sidebar (appears automatically as a page).
Cast the Chrome tab to your Nest Hub while cooking.
"""

import html as _html
import streamlit as st

import icons

st.set_page_config(
    page_title="Kitchen Mode",
    page_icon=":material/skillet:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────────────────
# CSS — Mediterranean palette, large readable text, hidden sidebar
# ─────────────────────────────────────────────────────────────────────────────
PALETTE = dict(
    olive_dark="#3d5733",
    olive="#4a6741",
    olive_light="#5a7a3a",
    terracotta="#c0553a",
    terra_light="#d4673a",
    cream="#faf8f2",
    cream_warm="#f5f2ea",
    sage_light="#e8f0e0",
    sage_border="#b8d4a8",
    gold="#d4943c",
    gold_pale="#f0e8cc",
    slate_dark="#2c3e2d",
    slate_text="#3a4a3b",
    muted="#8a8878",
    rule="#e0ddd4",
    lunch_dark="#445570",
    lunch_mid="#5a6a8a",
    lunch_light="#6a7a9a",
)

st.markdown(f"""
<style>
/* ── Layout ── */
.block-container {{
    padding-top: 1rem !important;
    padding-bottom: 0.5rem !important;
    max-width: 1180px !important;
}}

/* ── Card shell ── */
.recipe-card {{
    background: {PALETTE['cream']};
    border-radius: 18px;
    box-shadow: 0 6px 28px rgba(0,0,0,0.14);
    overflow: hidden;
    font-family: Georgia, 'Times New Roman', serif;
    margin: 0 auto;
}}

/* ── Header — dinner (olive) ── */
.card-header {{
    background: linear-gradient(130deg, {PALETTE['olive_dark']} 0%, {PALETTE['olive_light']} 100%);
    color: white;
    padding: 26px 38px 22px;
}}

/* ── Header — lunch (slate-blue) ── */
.card-header.lunch {{
    background: linear-gradient(130deg, {PALETTE['lunch_dark']} 0%, {PALETTE['lunch_light']} 100%);
}}

.card-badge {{
    font-family: Arial, sans-serif;
    font-size: 0.82rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    opacity: 0.80;
    margin-bottom: 10px;
}}

.card-meal-name {{
    font-size: 2.5rem;
    font-weight: bold;
    line-height: 1.15;
    margin-bottom: 10px;
    text-shadow: 0 1px 3px rgba(0,0,0,0.2);
}}

.card-meta {{
    font-family: Arial, sans-serif;
    font-size: 1.05rem;
    opacity: 0.88;
}}

/* ── Health highlights strip ── */
.card-highlights {{
    background: {PALETTE['sage_light']};
    padding: 11px 38px;
    font-family: Arial, sans-serif;
    font-size: 0.92rem;
    color: {PALETTE['olive_dark']};
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
    min-height: 44px;
}}

.highlight-pill {{
    background: white;
    border-radius: 20px;
    padding: 3px 14px;
    border: 1px solid {PALETTE['sage_border']};
    color: {PALETTE['olive_dark']};
    white-space: nowrap;
}}

/* ── Body — two-column grid ── */
.card-body {{
    display: grid;
    grid-template-columns: 1fr 1.5fr;
    gap: 0;
}}

.ingredients-col {{
    background: {PALETTE['cream_warm']};
    padding: 26px 30px 26px 38px;
    border-right: 2px solid {PALETTE['rule']};
}}

.steps-col {{
    background: {PALETTE['cream']};
    padding: 26px 38px 26px 32px;
}}

.col-label {{
    font-family: Arial, sans-serif;
    font-size: 0.78rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: {PALETTE['muted']};
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 2.5px solid {PALETTE['gold']};
}}

.ingredient-row {{
    font-size: 1.1rem;
    color: {PALETTE['slate_dark']};
    padding: 6px 0;
    border-bottom: 1px solid {PALETTE['rule']};
    line-height: 1.4;
    display: flex;
    gap: 10px;
}}

.ingredient-row:last-child {{ border-bottom: none; }}

.ing-qty {{
    color: {PALETTE['olive']};
    font-weight: bold;
    min-width: 60px;
    font-size: 1rem;
    font-family: Arial, sans-serif;
}}

.step-row {{
    font-size: 1.05rem;
    color: {PALETTE['slate_dark']};
    padding: 7px 0;
    border-bottom: 1px solid {PALETTE['rule']};
    line-height: 1.55;
    display: flex;
    gap: 14px;
}}

.step-row:last-child {{ border-bottom: none; }}

.step-num {{
    font-family: Arial, sans-serif;
    font-weight: bold;
    color: {PALETTE['olive']};
    min-width: 22px;
    font-size: 0.95rem;
    margin-top: 2px;
    flex-shrink: 0;
}}

/* ── Kid callout — terracotta ── */
.kid-callout {{
    background: linear-gradient(90deg, {PALETTE['terracotta']} 0%, {PALETTE['terra_light']} 100%);
    color: white;
    padding: 13px 38px;
    font-family: Arial, sans-serif;
    font-size: 1.02rem;
    line-height: 1.45;
}}

.kid-callout strong {{ color: {PALETTE['gold_pale']}; }}

/* ── Uric acid tip — sage ── */
.tip-callout {{
    background: {PALETTE['sage_light']};
    color: {PALETTE['olive_dark']};
    padding: 10px 38px;
    font-family: Arial, sans-serif;
    font-size: 0.95rem;
    border-left: 4px solid {PALETTE['olive']};
}}

/* ── Nutrition bar ── */
.nutrition-bar {{
    background: {PALETTE['slate_dark']};
    color: {PALETTE['gold_pale']};
    padding: 14px 38px;
    font-family: Arial, sans-serif;
    display: flex;
    gap: 48px;
    align-items: center;
}}

.nut-item {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1px;
}}

.nut-value {{
    font-size: 1.3rem;
    font-weight: bold;
    color: {PALETTE['gold']};
}}

.nut-label {{
    font-size: 0.72rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    opacity: 0.70;
}}

/* ── Pack instruction (lunch) ── */
.pack-callout {{
    background: #e8edf5;
    color: {PALETTE['lunch_dark']};
    padding: 13px 38px;
    font-family: Arial, sans-serif;
    font-size: 1.02rem;
    border-left: 4px solid {PALETTE['lunch_mid']};
}}

/* ── Navigation buttons ── */
div[data-testid="stColumns"] .stButton > button {{
    border-radius: 50px !important;
    font-size: 1.15rem !important;
    padding: 0.55rem 2.2rem !important;
    font-weight: bold !important;
    width: 100% !important;
}}
</style>
""", unsafe_allow_html=True)

# Icon mask classes (Streamlit strips inline <svg>; masked spans survive).
st.markdown(icons.ICON_CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _e(text) -> str:
    """HTML-escape a value; return empty string for None/falsy."""
    return _html.escape(str(text)) if text else ""


def _render_dinner_card(meal: dict, badge: str) -> str:
    nut = meal.get("nutrition_estimate") or {}
    highlights = meal.get("health_highlights") or []
    ingredients = meal.get("ingredients") or []
    instructions = meal.get("instructions") or []
    kid = meal.get("kid_adaptation", "")
    tip = meal.get("uric_acid_tip", "")
    equip = meal.get("primary_equipment", "")
    time_min = meal.get("cook_time_minutes", "")

    meta_parts = []
    if time_min:
        meta_parts.append(f'{icons.icon("clock", size=15)} {_e(time_min)} min')
    if equip:
        meta_parts.append(_e(equip))
    meta = "  ·  ".join(meta_parts)

    pills = "".join(
        f'<span class="highlight-pill">{_e(h)}</span>' for h in highlights
    )

    rows_ing = "".join(
        f"""<div class="ingredient-row">
              <span class="ing-qty">{_e(ing.get("quantity",""))} {_e(ing.get("unit",""))}</span>
              <span>{_e(ing.get("name",""))}</span>
            </div>"""
        for ing in ingredients
    )

    rows_step = "".join(
        f"""<div class="step-row">
              <span class="step-num">{i}.</span>
              <span>{_e(step)}</span>
            </div>"""
        for i, step in enumerate(instructions, 1)
    )

    kid_block = (
        f'<div class="kid-callout"><strong>{icons.icon("child", size=17)} Kids:</strong>  {_e(kid)}</div>'
        if kid else ""
    )
    tip_block = (
        f'<div class="tip-callout">{icons.icon("bulb", size=16)} {_e(tip)}</div>'
        if tip else ""
    )

    cal = nut.get("calories_per_adult_serving", "—")
    prot = nut.get("protein_g", "—")
    fib = nut.get("fiber_g", "—")
    fat = nut.get("fat_g", "—")

    return f"""
<div class="recipe-card">
  <div class="card-header">
    <div class="card-badge">{badge}</div>
    <div class="card-meal-name">{_e(meal.get("name",""))}</div>
    <div class="card-meta">{meta}</div>
  </div>

  <div class="card-highlights">{pills if pills else "&nbsp;"}</div>

  <div class="card-body">
    <div class="ingredients-col">
      <div class="col-label">Ingredients</div>
      {rows_ing}
    </div>
    <div class="steps-col">
      <div class="col-label">Steps</div>
      {rows_step}
    </div>
  </div>

  {kid_block}
  {tip_block}

  <div class="nutrition-bar">
    <div class="nut-item"><span class="nut-value">{_e(cal)}</span><span class="nut-label">Cal</span></div>
    <div class="nut-item"><span class="nut-value">{_e(prot)}g</span><span class="nut-label">Protein</span></div>
    <div class="nut-item"><span class="nut-value">{_e(fib)}g</span><span class="nut-label">Fiber</span></div>
    <div class="nut-item"><span class="nut-value">{_e(fat)}g</span><span class="nut-label">Fat</span></div>
  </div>
</div>
"""


def _render_lunch_card(meal: dict, badge: str) -> str:
    nut = meal.get("nutrition_estimate") or {}
    highlights = meal.get("health_highlights") or []
    ingredients = meal.get("ingredients") or []
    pack = meal.get("pack_instructions", "")
    source = meal.get("source", "standalone")
    reheat = meal.get("reheat", "")

    source_label = "Leftover from dinner" if source == "leftover" else "Standalone lunch"
    reheat_label = "· Microwave" if "microwave" in (reheat or "").lower() else "· Cold (no reheat)"
    _meta_icon = "box" if source == "leftover" else "bowl"
    meta = f'{icons.icon(_meta_icon, size=15)} {_e(source_label)}  {_e(reheat_label)}'

    pills = "".join(
        f'<span class="highlight-pill">{_e(h)}</span>' for h in highlights
    )

    rows_ing = "".join(
        f"""<div class="ingredient-row">
              <span class="ing-qty">{_e(ing.get("quantity",""))} {_e(ing.get("unit",""))}</span>
              <span>{_e(ing.get("name",""))}</span>
            </div>"""
        for ing in ingredients
    )

    pack_block = (
        f'<div class="pack-callout">{icons.icon("backpack", size=16)} <strong>Pack:</strong>  {_e(pack)}</div>'
        if pack else ""
    )

    cal = nut.get("calories_per_adult_serving", "—")
    prot = nut.get("protein_g", "—")
    fib = nut.get("fiber_g", "—")
    fat = nut.get("fat_g", "—")

    return f"""
<div class="recipe-card">
  <div class="card-header lunch">
    <div class="card-badge">{badge}</div>
    <div class="card-meal-name">{_e(meal.get("name",""))}</div>
    <div class="card-meta">{meta}</div>
  </div>

  <div class="card-highlights">{pills if pills else "&nbsp;"}</div>

  <div class="card-body">
    <div class="ingredients-col">
      <div class="col-label">Ingredients</div>
      {rows_ing}
    </div>
    <div class="steps-col">
      <div class="col-label">How to Pack</div>
      <div style="font-size:1.05rem; color:#2c3e2d; line-height:1.6; padding-top:4px;">
        {_e(pack) if pack else "<em>See pack instructions below.</em>"}
      </div>
    </div>
  </div>

  {pack_block}

  <div class="nutrition-bar">
    <div class="nut-item"><span class="nut-value">{_e(cal)}</span><span class="nut-label">Cal</span></div>
    <div class="nut-item"><span class="nut-value">{_e(prot)}g</span><span class="nut-label">Protein</span></div>
    <div class="nut-item"><span class="nut-value">{_e(fib)}g</span><span class="nut-label">Fiber</span></div>
    <div class="nut-item"><span class="nut-value">{_e(fat)}g</span><span class="nut-label">Fat</span></div>
  </div>
</div>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────
if "kitchen_idx" not in st.session_state:
    st.session_state.kitchen_idx = 0

plan = st.session_state.get("week_plan")

if plan is None:
    st.markdown(f"""
    <div style="text-align:center; margin-top:80px; font-family:Georgia,serif;">
      <div style="color:#4a6741; margin-bottom:16px;">{icons.icon("skillet", size=52)}</div>
      <div style="font-size:1.5rem; color:#4a6741; margin-bottom:10px;">Kitchen Mode</div>
      <div style="font-size:1.1rem; color:#666;">No meal plan loaded yet.<br>
           Go to the main app and generate a week first.</div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# Build flat list: dinners first, then lunches
dinners = plan.get("dinners", [])
lunches = plan.get("lunches", [])
all_meals: list[tuple[dict, str]] = (
    [(m, "dinner") for m in dinners]
    + [(m, "lunch") for m in lunches]
)
total = len(all_meals)

if total == 0:
    st.warning("The current plan has no meals.")
    st.stop()

# Clamp index
idx = max(0, min(st.session_state.kitchen_idx, total - 1))
st.session_state.kitchen_idx = idx

meal, meal_type = all_meals[idx]

# Count dinners / lunches for badge
dinner_count = sum(1 for _, t in all_meals[:idx + 1] if t == "dinner")
lunch_count = sum(1 for _, t in all_meals[:idx + 1] if t == "lunch")

if meal_type == "dinner":
    d_num = dinner_count
    badge = f'{icons.icon("plate", size=15)}  Dinner {d_num} of {len(dinners)}'
    card_html = _render_dinner_card(meal, badge)
else:
    l_num = lunch_count
    badge = f'{icons.icon("bowl", size=15)}  Lunch {l_num} of {len(lunches)}'
    card_html = _render_lunch_card(meal, badge)

# ─────────────────────────────────────────────────────────────────────────────
# Render
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(card_html, unsafe_allow_html=True)

# ── Navigation ────────────────────────────────────────────────────────────────
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

col_prev, col_pos, col_next = st.columns([1, 2, 1])

with col_prev:
    if st.button("Prev", icon=":material/chevron_left:", type="secondary", use_container_width=True, disabled=(idx == 0)):
        st.session_state.kitchen_idx = idx - 1
        st.rerun()

with col_pos:
    meal_name = meal.get("name", "")
    label = f"**{idx + 1} of {total}** — {meal_name}"
    st.markdown(
        f"<div style='text-align:center; padding:10px 0; font-family:Arial,sans-serif;"
        f"font-size:0.95rem; color:#5a6a5b;'>{idx + 1} / {total} &nbsp;·&nbsp; "
        f"<strong style='color:#3d5733'>{_html.escape(meal_name)}</strong></div>",
        unsafe_allow_html=True,
    )

with col_next:
    if st.button("Next", icon=":material/chevron_right:", type="primary", use_container_width=True, disabled=(idx == total - 1)):
        st.session_state.kitchen_idx = idx + 1
        st.rerun()

"""
card_html.py
------------
Self-contained HTML rendering for the app's *display-only* surfaces.

The visual design direction was generated with Google Stitch (Mediterranean
palette) and hand-translated here into dependency-free, scoped CSS so it renders
reliably inside Streamlit via ``st.html()`` — no Tailwind CDN, no JS.

Usage in app.py:
    import card_html
    st.html(card_html.CARD_STYLES)                 # once, near the top of the app
    st.html(card_html.render_dinner_card(dinner))  # per surface

Only display-only data goes through here. Interactive controls (buttons,
selectboxes, sliders, text areas, the recipe expanders) stay native Streamlit,
rendered around these cards.

Public renderers
----------------
    render_dinner_card(dinner)
    render_lunch_card(lunch)
    render_week_summary_card(...)
    render_prep_card(task)
    render_shopping_section(section_name, rows)
    render_favorite_card(fav)
"""

from html import escape

import icons
from schemas import DinnerMeal, LunchMeal, SundayPrepTask

# ─────────────────────────────────────────────────────────────────────────────
# Palette (kept here as the single source of truth for the card CSS)
#   surface #FFFDF8 · page #F7F1E3 · olive #5B7553 · terracotta #C56A3D
#   navy text #233044 · muted #5A6472 · hairline #E7DFCD · gold ★ #D8A24A
# ─────────────────────────────────────────────────────────────────────────────

# One consolidated stylesheet, injected once. Every selector is scoped under a
# `.med-*` class so the styles can't leak into Streamlit's own widgets.
CARD_STYLES = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap');

.med-card,.med-card *,.med-summary,.med-summary *,.med-prep,.med-prep *,
.med-shop,.med-shop *,.med-fav,.med-fav *{box-sizing:border-box;}

/* Inline line-drawing icons inherit text colour and sit like type. */
.med-ico{display:inline-block;vertical-align:-0.16em;flex-shrink:0;}

/* ── Base meal card (dinner / lunch) ─────────────────────────────────────── */
.med-card{
  max-width:560px;background:#FFFDF8;border:1px solid #E7DFCD;border-radius:16px;
  padding:20px 24px;box-shadow:0 4px 20px rgba(35,48,68,.06);
  font-family:'Inter',-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
  margin:6px 0 2px;
}
.med-card__title{
  font-family:'Fraunces','Source Serif 4',Georgia,serif;font-size:24px;
  line-height:1.2;font-weight:600;color:#233044;margin:0 0 12px;
}
.med-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px;}
.med-chip{
  display:inline-flex;align-items:center;gap:6px;background:#EFEADC;
  color:#5A6472;font-size:14.5px;font-weight:500;padding:5px 13px;border-radius:999px;
}
.med-tags{display:flex;flex-wrap:wrap;gap:8px;}
.med-tag{
  display:inline-flex;background:rgba(91,117,83,.12);color:#5B7553;
  font-size:13px;font-weight:500;padding:4px 11px;border-radius:999px;
}
.med-divider{border:none;border-top:1px solid #E7DFCD;margin:16px 0;}

/* ── Stat strip (shared by meal cards) ───────────────────────────────────── */
.med-stats{display:flex;flex-wrap:wrap;}
.med-stat{
  flex:1 1 25%;min-width:78px;display:flex;flex-direction:column;
  align-items:center;text-align:center;padding:2px 8px;
}
.med-stat + .med-stat{border-left:1px solid #E7DFCD;}
.med-stat__val{
  font-family:'Fraunces','Source Serif 4',Georgia,serif;font-size:24px;
  font-weight:600;color:#233044;line-height:1.1;
}
.med-stat__val--cost{color:#C56A3D;}
.med-stat__val--ok{color:#5B7553;}
.med-stat__val--warn{color:#B5722E;}
.med-stat__label{
  font-size:12.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
  color:#5A6472;margin-top:4px;
}
.med-stat__sub{font-size:11.5px;color:#9aa1ab;margin-top:2px;}
@media (max-width:520px){
  .med-stat{flex:1 1 50%;}
  .med-stat:nth-child(3){border-left:none;}
  .med-stat:nth-child(3),.med-stat:nth-child(4){
    border-top:1px solid #E7DFCD;margin-top:8px;padding-top:12px;
  }
}

/* ── Week-summary banner (full width, 6 stats, no separators) ────────────── */
.med-summary{
  background:#FFFDF8;border:1px solid #E7DFCD;border-radius:16px;
  padding:18px 24px;box-shadow:0 4px 20px rgba(35,48,68,.06);
  font-family:'Inter',system-ui,sans-serif;margin:2px 0 10px;
}
.med-summary__title{
  font-family:'Fraunces',Georgia,serif;font-size:15px;font-weight:600;
  letter-spacing:.04em;text-transform:uppercase;color:#5A6472;margin:0 0 14px;
}
.med-summary__stats{display:flex;flex-wrap:wrap;gap:8px 4px;}
.med-summary__stats .med-stat{flex:1 1 0;min-width:120px;border:none!important;}

/* ── Sunday-prep card (left olive accent) ────────────────────────────────── */
.med-prep{
  background:#FFFDF8;border:1px solid #E7DFCD;border-left:4px solid #5B7553;
  border-radius:12px;padding:14px 18px;margin:8px 0;
  font-family:'Inter',system-ui,sans-serif;box-shadow:0 2px 10px rgba(35,48,68,.04);
}
.med-prep__task{font-size:16px;font-weight:600;color:#233044;margin:0 0 8px;}
.med-prep__storage{font-size:13px;color:#5A6472;margin-top:8px;}
.med-prep__storage b{color:#5B7553;font-weight:600;}

/* ── Shopping-list section card ──────────────────────────────────────────── */
.med-shop{
  background:#FFFDF8;border:1px solid #E7DFCD;border-radius:14px;
  padding:0;margin:0 0 16px;overflow:hidden;
  font-family:'Inter',system-ui,sans-serif;box-shadow:0 2px 12px rgba(35,48,68,.05);
}
.med-shop__head{
  display:flex;align-items:center;justify-content:space-between;
  background:#F2ECDD;padding:10px 16px;border-bottom:1px solid #E7DFCD;
}
.med-shop__name{font-size:15.5px;font-weight:600;color:#233044;
  display:inline-flex;align-items:center;gap:8px;}
.med-shop__count{
  font-size:12px;font-weight:600;color:#5A6472;background:#FFFDF8;
  border:1px solid #E7DFCD;border-radius:999px;padding:1px 9px;
}
.med-shop__row{
  display:flex;align-items:baseline;justify-content:space-between;gap:12px;
  padding:8px 16px;border-bottom:1px solid #F1EBDC;
}
.med-shop__row:last-child{border-bottom:none;}
.med-shop__item{font-size:15.5px;color:#233044;}
.med-shop__qty{font-size:14.5px;color:#5A6472;text-align:right;white-space:nowrap;}

/* ── Favorite card ───────────────────────────────────────────────────────── */
.med-fav{
  background:#FFFDF8;border:1px solid #E7DFCD;border-radius:14px;
  padding:16px 20px;margin:6px 0 2px;
  font-family:'Inter',system-ui,sans-serif;box-shadow:0 3px 14px rgba(35,48,68,.05);
}
.med-fav__top{display:flex;align-items:baseline;justify-content:space-between;gap:12px;}
.med-fav__name{font-family:'Fraunces',Georgia,serif;font-size:20px;font-weight:600;
  color:#233044;line-height:1.2;}
.med-fav__stars{color:#D8A24A;white-space:nowrap;line-height:1;display:inline-flex;gap:1px;}
.med-fav__stars .dim{color:#E0D7C2;}
.med-fav__meta{font-size:13px;color:#5A6472;margin-top:6px;}
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Small builders
# ─────────────────────────────────────────────────────────────────────────────
def _chip(text: str) -> str:
    return f'<span class="med-chip">{text}</span>'


def _tag(text: str) -> str:
    return f'<span class="med-tag">{escape(text)}</span>'


def _stat(value: str, label: str, *, variant: str = "", sub: str = "") -> str:
    """One stat block. `variant` ∈ {'', 'cost', 'ok', 'warn'}."""
    vcls = "med-stat__val" + (f" med-stat__val--{variant}" if variant else "")
    sub_html = f'<span class="med-stat__sub">{escape(sub)}</span>' if sub else ""
    return (
        '<div class="med-stat">'
        f'<span class="{vcls}">{escape(value)}</span>'
        f'<span class="med-stat__label">{escape(label)}</span>'
        f"{sub_html}"
        "</div>"
    )


def _meal_card(title: str, chips: list[str], tags: list[str], stats_html: str) -> str:
    chips_html = f'<div class="med-chips">{"".join(chips)}</div>' if chips else ""
    tags_html = (
        f'<div class="med-tags">{"".join(_tag(t) for t in tags[:3])}</div>'
        if tags else ""
    )
    return (
        '<div class="med-card">'
        f'<h2 class="med-card__title">{escape(title)}</h2>'
        f"{chips_html}{tags_html}"
        '<hr class="med-divider">'
        f'<div class="med-stats">{stats_html}</div>'
        "</div>"
    )


def _nutrition_stats(nut: dict, cost: dict) -> str:
    cal = nut.get("calories_per_adult_serving")
    pro = nut.get("protein_g")
    fib = nut.get("fiber_g")
    cps = cost.get("cost_per_serving_usd")
    return (
        _stat(str(cal) if cal is not None else "—", "Calories")
        + _stat(f"{pro}g" if pro is not None else "—", "Protein")
        + _stat(f"{fib}g" if fib is not None else "—", "Fiber")
        + _stat(f"${cps:.2f}" if cps is not None else "—", "Per serving", variant="cost")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public renderers
# ─────────────────────────────────────────────────────────────────────────────
def render_dinner_card(dinner: DinnerMeal) -> str:
    """Display-only summary card for one dinner."""
    chips = []
    cook = dinner.get("cook_time_minutes")
    if cook:
        chips.append(_chip(f"{icons.icon('clock')} {int(cook)} min"))
    equip = dinner.get("primary_equipment")
    if equip:
        chips.append(_chip(f"{icons.icon('flame')} {escape(equip)}"))
    return _meal_card(
        dinner.get("name") or "Untitled",
        chips,
        dinner.get("health_highlights") or [],
        _nutrition_stats(dinner.get("nutrition_estimate") or {}, dinner.get("cost_estimate") or {}),
    )


def render_lunch_card(lunch: LunchMeal) -> str:
    """Display-only summary card for one lunch."""
    chips = []
    if lunch.get("source") == "leftover":
        chips.append(_chip(f"{icons.icon('box')} Leftover from dinner"))
    else:
        chips.append(_chip(f"{icons.icon('bowl')} Standalone"))
    if lunch.get("reheat") == "microwave":
        chips.append(_chip(f"{icons.icon('microwave')} Microwave"))
    else:
        chips.append(_chip(f"{icons.icon('snowflake')} Cold (no reheat)"))
    prep = lunch.get("prep_at_lunchtime_minutes")
    if prep:
        chips.append(_chip(f"{icons.icon('clock')} {int(prep)} min prep"))
    return _meal_card(
        lunch.get("name") or "Untitled",
        chips,
        lunch.get("health_highlights") or [],
        _nutrition_stats(lunch.get("nutrition_estimate") or {}, lunch.get("cost_estimate") or {}),
    )


def render_week_summary_card(
    *,
    fish: int, red_meat: int, veg: int, weekly_cost: float | None,
    avg_cal: int, t_cal: int,
    avg_protein: int, t_prot: int,
    avg_fiber: int, t_fib: int,
    avg_sodium: int = 0, t_sodium: int = 700,
) -> str:
    """Full-width 'this week at a glance' banner."""
    def tracked(value: int, target: int, label: str, suffix: str) -> str:
        variant = "ok" if value >= target else "warn"
        return _stat(f"{value}{suffix}", label, variant=variant,
                     sub=f"target {target}{suffix}")

    def tracked_ceiling(value: int, target: int, label: str, suffix: str) -> str:
        """Like `tracked`, but for metrics where lower is better (sodium)."""
        variant = "ok" if value <= target else "warn"
        return _stat(f"{value}{suffix}", label, variant=variant,
                     sub=f"max {target}{suffix}")

    stats = (
        _stat(str(fish), "Fish meals")
        + _stat(str(red_meat), "Red meat")
        + _stat(str(veg), "Vegetarian")
    )
    if weekly_cost:
        stats += _stat(f"${weekly_cost:.0f}", "Est. grocery", variant="cost")
    stats += (
        tracked(avg_cal, t_cal, "Cal/day", "")
        + tracked(avg_protein, t_prot, "Protein/day", "g")
        + tracked(avg_fiber, t_fib, "Fiber/day", "g")
    )
    if avg_sodium:
        stats += tracked_ceiling(avg_sodium, t_sodium, "Sodium/meal", "mg")
    return (
        '<div class="med-summary">'
        '<div class="med-summary__title">This week at a glance</div>'
        f'<div class="med-stats med-summary__stats">{stats}</div>'
        "</div>"
    )


def render_prep_card(task: SundayPrepTask) -> str:
    """One Sunday-prep task as a compact accented card."""
    yields = task.get("yields_for") or []
    tags_html = (
        f'<div class="med-tags">{"".join(_tag(y) for y in yields)}</div>'
        if yields else ""
    )
    storage = task.get("storage")
    storage_html = (
        f'<div class="med-prep__storage"><b>Storage:</b> {escape(storage)}</div>'
        if storage else ""
    )
    return (
        '<div class="med-prep">'
        f'<div class="med-prep__task">{escape(task.get("task") or "")}</div>'
        f"{tags_html}{storage_html}"
        "</div>"
    )


_SECTION_ICONS = {
    "Produce": "leaf", "Proteins": "fish", "Dairy & Eggs": "cheese",
    "Pantry": "jar", "Frozen": "snowflake", "Other": "cart",
    "Pantry Staples (check stock)": "salt",
}


def render_shopping_section(section_name: str, rows: list[tuple[str, str]]) -> str:
    """A grouped shopping-list section: header + item rows. `rows` = (name, qty)."""
    icon = icons.icon(_SECTION_ICONS.get(section_name, "cart"), size=17)
    row_html = "".join(
        '<div class="med-shop__row">'
        f'<span class="med-shop__item">{escape(name)}</span>'
        f'<span class="med-shop__qty">{escape(qty)}</span>'
        "</div>"
        for name, qty in rows
    )
    return (
        '<div class="med-shop">'
        '<div class="med-shop__head">'
        f'<span class="med-shop__name">{icon} {escape(section_name)}</span>'
        f'<span class="med-shop__count">{len(rows)}</span>'
        "</div>"
        f"{row_html}"
        "</div>"
    )


def render_favorite_card(fav: dict) -> str:
    """Header card for a saved favorite (rating + tags). Edit controls live outside."""
    rating = int(fav.get("rating", 0) or 0)
    stars = (
        '<span class="med-fav__stars">'
        + icons.star(filled=True) * rating
        + f'<span class="dim">{icons.star(filled=False) * (5 - rating)}</span>'
        + "</span>"
    )
    tags = fav.get("tags") or []
    tags_html = (
        f'<div class="med-tags" style="margin-top:10px">{"".join(_tag(t) for t in tags)}</div>'
        if tags else ""
    )
    last = fav.get("last_made")
    meta = f'<div class="med-fav__meta">Last made: {escape(last)}</div>' if last else ""
    return (
        '<div class="med-fav">'
        '<div class="med-fav__top">'
        f'<span class="med-fav__name">{escape(fav.get("name") or "Untitled")}</span>'
        f"{stars}"
        "</div>"
        f"{meta}{tags_html}"
        "</div>"
    )

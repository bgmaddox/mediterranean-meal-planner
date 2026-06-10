"""
icons.py
--------
Minimalist line-drawing icons, hand-drawn for this app to give it a distinct,
non-emoji look.

Why not inline ``<svg>``? Streamlit's HTML sanitizer strips ``<svg>`` elements
from ``st.html`` / ``st.markdown`` output, so injected SVG silently vanishes.
Instead each icon is rendered as an empty ``<span>`` whose shape comes from a
CSS ``mask-image`` (an SVG baked into a data-URI). ``<span>`` and ``<style>``
both survive sanitisation, and because the span is painted with
``background-color: currentColor`` the icon still inherits the surrounding text
colour — olive in the header, muted grey in chips, gold for rating stars, etc.

Usage::

    import icons
    st.html(icons.ICON_CSS)          # inject once per page, before any icon()
    icons.icon("clock")              # -> <span class="med-ico med-ico--clock" ...>
    icons.star(filled=True)

For *native* Streamlit widgets (buttons, page/nav icons) we use Streamlit's own
Material Symbols (``:material/…:``) — same thin-line monochrome style.
"""

from urllib.parse import quote

# Inner markup of a 24×24 viewBox for each icon. Strokes are added at the root
# (see _svg). Solid fills use #000 so the mask's alpha channel is opaque there.
_ICONS: dict[str, str] = {
    # time / heat / cold
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "flame": '<path d="M12 3c1 3-2 4-2 6.8a2.1 2.1 0 0 0 4.2.3c1.4 1 2.3 2.6 2.3 4.4a4.4 4.4 0 1 1-8.8 0c0-3.4 2.8-4.6 4.3-11.5z"/>',
    "snowflake": '<path d="M12 2.5v19M3.7 7.2l16.6 9.6M20.3 7.2L3.7 16.8"/><path d="M12 6l-2-2M12 6l2-2M12 18l-2 2M12 18l2 2"/>',
    "microwave": '<rect x="3" y="6" width="18" height="12" rx="1.6"/><path d="M15 6v12"/><circle cx="9" cy="12" r="2.2"/>',
    # meal-type marks
    "box": '<rect x="4" y="7.5" width="16" height="12.5" rx="2"/><path d="M4 11.5h16M9 7.5V5.5h6v2"/>',
    "bowl": '<path d="M3.5 11h17a8.5 8.5 0 0 1-17 0z"/><path d="M9.5 11c0-3 1.5-5 3-5.5"/>',
    # shopping sections
    "leaf": '<path d="M5 19c0-8 6-14 14-14 0 8-6 14-14 14z"/><path d="M5 19C8 14.5 12 11.5 16.5 9.5"/>',
    "fish": '<path d="M3 12c4-5 11-5 15 0-4 5-11 5-15 0z"/><path d="M18 8.8c2 1 3 2.1 3 3.2s-1 2.2-3 3.2"/><circle cx="8" cy="11.4" r=".8" fill="#000" stroke="none"/>',
    "cheese": '<path d="M3 17l11-8 7 3v5H3z"/><circle cx="9" cy="14.4" r="1" fill="#000" stroke="none"/><circle cx="14" cy="14.9" r=".8" fill="#000" stroke="none"/>',
    "jar": '<rect x="6" y="8.5" width="12" height="11.5" rx="2"/><path d="M8 8.5V6.5h8v2M9 4.5h6"/>',
    "cart": '<circle cx="9.5" cy="20" r="1.3"/><circle cx="17" cy="20" r="1.3"/><path d="M3 4.5h2l2.4 11.5h10l1.8-7.5H6"/>',
    "salt": '<path d="M8 9.5h8V19a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2z"/><path d="M8 9.5c0-2 1.6-3.2 4-3.2s4 1.2 4 3.2"/><circle cx="10.6" cy="5.4" r=".6" fill="#000" stroke="none"/><circle cx="12" cy="4.9" r=".6" fill="#000" stroke="none"/><circle cx="13.4" cy="5.4" r=".6" fill="#000" stroke="none"/>',
    # kitchen-mode + misc
    "child": '<circle cx="12" cy="7" r="3"/><path d="M6 21c0-4 2.6-6.8 6-6.8s6 2.8 6 6.8"/>',
    "bulb": '<path d="M9.5 18h5M10.5 21h3"/><path d="M12 3a6 6 0 0 0-3.8 10.6c.7.6 1.1 1.4 1.3 2.4h5c.2-1 .6-1.8 1.3-2.4A6 6 0 0 0 12 3z"/>',
    "backpack": '<rect x="6" y="7" width="12" height="14" rx="3"/><path d="M9 7V5.4a3 3 0 0 1 6 0V7M9 13.5h6"/>',
    "plate": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/>',
    "skillet": '<circle cx="10" cy="13" r="6"/><path d="M16 12.4h6"/>',
    # logo
    "olive": '<path d="M4 20.5c6-1 11.5-5.5 16.5-15.5"/><ellipse cx="14.5" cy="9" rx="2" ry="3" transform="rotate(38 14.5 9)"/><ellipse cx="9.5" cy="14" rx="2" ry="3" transform="rotate(38 9.5 14)"/>',
}

_STAR_PATH = "M12 3l2.7 5.7 6.3.7-4.7 4.3 1.3 6.1L12 16.9 6.1 19.8l1.3-6.1L2.7 9.4l6.3-.7z"


def _svg(body: str, *, stroke: float = 1.6) -> str:
    """Wrap inner markup in a full stroked SVG (black, opaque — for masking)."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="#000" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def _mask_uri(svg: str) -> str:
    return "data:image/svg+xml," + quote(svg)


def _mask_rule(name: str, svg: str) -> str:
    uri = _mask_uri(svg)
    return (
        f".med-ico--{name}{{-webkit-mask-image:url('{uri}');mask-image:url('{uri}');}}"
    )


def _build_css() -> str:
    rules = [
        ".med-ico{display:inline-block;background-color:currentColor;"
        "vertical-align:-0.16em;flex-shrink:0;"
        "-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;"
        "-webkit-mask-position:center;mask-position:center;"
        "-webkit-mask-size:contain;mask-size:contain;}"
    ]
    for name, body in _ICONS.items():
        rules.append(_mask_rule(name, _svg(body)))
    # rating stars: filled (solid) + outline
    star_filled = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="#000" stroke="none"><path d="{_STAR_PATH}"/></svg>'
    )
    star_outline = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="#000" stroke-width="1.4" stroke-linejoin="round">'
        f'<path d="{_STAR_PATH}"/></svg>'
    )
    rules.append(_mask_rule("star", star_filled))
    rules.append(_mask_rule("star-o", star_outline))
    return "<style>" + "".join(rules) + "</style>"


# Inject this once per page (st.html) before rendering any icons.
ICON_CSS = _build_css()


def icon(name: str, size: int = 16, *, cls: str = "") -> str:
    """An inline icon span. Inherits text colour via currentColor."""
    classes = f"med-ico med-ico--{name} {cls}".strip()
    return f'<span class="{classes}" style="width:{size}px;height:{size}px"></span>'


def star(*, filled: bool, size: int = 16) -> str:
    """Rating star span. ``filled`` → solid; otherwise a thin outline."""
    name = "star" if filled else "star-o"
    return f'<span class="med-ico med-ico--{name}" style="width:{size}px;height:{size}px"></span>'

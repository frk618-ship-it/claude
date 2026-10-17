"""Inline SVG icon set for the static site (24x24, stroke icons drawn with currentColor).

The page embeds SPRITE once and references icons with <use href="#i-name">, so the site
needs no external icon font or CDN.
"""

from __future__ import annotations

from urllib.parse import quote

ICONS = {
    # brand: a sputtering target seen from above, with its erosion racetrack
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.5" stroke-dasharray="2.2 1.6"/><circle cx="12" cy="12" r="1.6"/>',
    # news kinds
    "news": '<rect x="3" y="5" width="15" height="14" rx="2"/><path d="M18 9h2a1 1 0 0 1 1 1v7a2 2 0 0 1-2 2"/><path d="M7 9h7M7 13h7M7 16h4"/>',
    "filing": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    "market": '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    "forum": '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12z"/>',
    "research": '<path d="M4 20V10M10 20V4M16 20v-7M21 20H3"/>',
    "profile": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/><path d="M6 16c.6-1.6 1.8-2.4 3-2.4s2.4.8 3 2.4M14 10h4M14 14h3"/>',
    # page furniture
    "building": '<path d="M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16"/><path d="M15 9h4a1 1 0 0 1 1 1v11"/><path d="M3 21h18M8 8h3M8 12h3M8 16h3"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
    "book": '<path d="M3 5a2 2 0 0 1 2-2h5a2 2 0 0 1 2 2v16a2 2 0 0 0-2-2H3z"/><path d="M21 5a2 2 0 0 0-2-2h-5a2 2 0 0 0-2 2v16a2 2 0 0 1 2-2h7z"/>',
    "sparkle": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z"/>',
    "star": '<path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>',
    "list": '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow-left": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "filter": '<path d="M4 5h16l-6 7.5V19l-4 2v-8.5z"/>',
    "store": '<path d="M3 9l1.5-5h15L21 9"/><path d="M4 9v11h16V9"/><path d="M3 9h18M10 20v-6h4v6"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.3-3.6 4.3-5.5 8-5.5s6.7 1.9 8 5.5"/>',
    # news thumbnails (what the story is about)
    "factory": '<path d="M3 21V11l5 3V11l5 3V7l4-3v17"/><path d="M17 4h3v17"/><path d="M3 21h18M7 18h2M12 18h2"/>',
    "bars": '<path d="M3 21h18"/><rect x="5" y="12" width="3" height="7" rx=".5"/><rect x="10.5" y="8" width="3" height="11" rx=".5"/><rect x="16" y="4" width="3" height="15" rx=".5"/>',
    "handshake": '<path d="M2 11l4-4 4 2 3-2 4 1 5 4"/><path d="M6 7l-3 7 6 6 2-1 2 1 4-4"/><path d="M9 13l3 3M11 11l3 3M13 9.5l3 3"/>',
    "tag": '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "flask": '<path d="M9 3h6M10 3v6l-5.5 9.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/><path d="M7.5 15h9"/>',
    "truck": '<path d="M2 6h12v10H2z"/><path d="M14 9h4l3 3.5V16h-7"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
    "wafer": '<circle cx="12" cy="12" r="9"/><path d="M8 4.5v15M12 3v18M16 4.5v15M4.5 8h15M3 12h18M4.5 16h15"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="1.5"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    "chamber": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M7 6h10M8 6v2h8V6"/><path d="M9 11l-1 4M12 11v4M15 11l1 4" stroke-dasharray="1.5 1.5"/><path d="M7 17h10v2H7z"/>',
    "coin": '<ellipse cx="12" cy="7" rx="7" ry="3"/><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7"/><path d="M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/>',
    # research topics (manufacturing steps)
    "flame": '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3.4 2.6-5.6 4-8.8.6 2 1.8 3.1 3 3.6.2-2.2 1.3-4.3 3-5.6.6 3.4 3 5.4 3 9.3 0 4.1-2.8 7.7-6.5 7.7z"/>',
    "droplet": '<path d="M12 3c3.5 4.6 6 8 6 11a6 6 0 0 1-12 0c0-3 2.5-6.4 6-11z"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "gem": '<path d="M6 4h12l3 5-9 11L3 9z"/><path d="M3 9h18M9.5 4L8 9l4 11 4-11-1.5-5"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "cube": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    "alloy": '<circle cx="9" cy="9" r="5"/><circle cx="15" cy="9" r="5"/><circle cx="12" cy="15" r="5"/>',
    "cylinder": '<ellipse cx="12" cy="5.5" rx="6" ry="2.5"/><path d="M6 5.5v13c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5v-13"/>',
    "gauge": '<path d="M4 17a8 8 0 1 1 16 0"/><path d="M12 17l4-5"/><path d="M4 17h2M18 17h2M12 9V7"/>',
    "recycle": '<path d="M4 12a8 8 0 0 1 13.7-5.7L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M20 12a8 8 0 0 1-13.7 5.7L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
}

# Paper topic (from config research.topics) -> icon
TOPIC_ICONS = {
    "粉末冶金與燒結": "flame",
    "熔煉與鑄造": "droplet",
    "塑性加工與晶粒織構": "layers",
    "高純化與雜質控制": "gem",
    "綁定與背板": "link",
    "陶瓷與氧化物靶": "cube",
    "合金與複合靶": "alloy",
    "旋轉靶": "cylinder",
    "濺鍍行為與靶材利用率": "gauge",
    "回收與再利用": "recycle",
}

SPRITE = (
    '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">'
    + "".join(f'<symbol id="i-{name}" viewBox="0 0 24 24">{body}</symbol>' for name, body in ICONS.items())
    + "</svg>"
)


def icon(name: str, cls: str = "ic") -> str:
    if name not in ICONS:
        name = "book"
    return f'<svg class="{cls}" aria-hidden="true" focusable="false"><use href="#i-{name}"/></svg>'


def topic_icon(topic: str) -> str:
    return TOPIC_ICONS.get(topic, "book")


def favicon_href(color: str = "#8a4f24") -> str:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="2" stroke-linecap="round">{ICONS["target"]}</svg>'
    )
    return "data:image/svg+xml," + quote(svg)

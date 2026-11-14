"""Floating page switcher shared by every page of the site (news pages, the game, the dashboard).

A round button floats in the bottom-right corner; tapping it springs open a stack of page
buttons (staggered), with the current page highlighted. Esc, a tap outside or picking a page
closes it. Self-contained: its own inline icons, CSS and script, so the financial dashboard
(a separate template) can use it too. Colours follow the host page's --accent / --surface when
they exist.
"""

from __future__ import annotations

import base64
import html
from functools import cache
from pathlib import Path

# key, label, path relative to the site root (the folder that holds news/ and financials/), icon
PAGES = [
    ("news", "新聞摘要", "news/index.html", "news"),
    ("papers", "技術文獻庫", "news/papers.html", "book"),
    ("forecast", "商情預測", "news/forecast.html", "chart"),
    ("quiz", "靶材產業島", "news/quiz.html", "island"),
    ("financials", "財務儀表板", "financials/", "bars"),
    ("metals", "貴金屬預警", "metals/", "bell"),
    ("leads", "商機雷達", "leads/", "radar"),
    ("companies", "公司追蹤", "news/companies.html", "building"),
    ("search", "全站搜尋", "news/search.html", "search"),
]

ICONS = {
    "news": '<path d="M4 5h13v14H6a2 2 0 0 1-2-2z"/><path d="M17 9h3v8a2 2 0 0 1-2 2"/><path d="M7 9h7M7 13h7M7 16h4"/>',
    "book": '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5M8 7h7"/>',
    "chart": '<path d="M4 19h16"/><path d="M5 15l4-5 4 3 6-7"/><path d="M15 6h4v4"/>',
    "island": '<circle cx="12" cy="10" r="6"/><circle cx="12" cy="10" r="2.5"/><path d="M7 15l5 6 5-6"/>',
    "building": '<path d="M4 21V5l8-2v18M12 7h8v14"/><path d="M7 8h2M7 12h2M7 16h2M15 11h2M15 15h2M2 21h20"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    "bars": '<path d="M4 20h16"/><rect x="6" y="11" width="3" height="7"/><rect x="11" y="7" width="3" height="11"/><rect x="16" y="4" width="3" height="14"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "radar": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="M12 12l5-5"/><circle cx="16.5" cy="9.5" r="1" fill="currentColor"/>',
    "menu": '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
}

STYLE = """
:root{--fab-accent:var(--accent,#173c66);--fab-accent-2:var(--accent-2,#005a7f);--fab-surface:var(--surface,#fbfcfd);--fab-ink:var(--fg,var(--ink-1,var(--ink,#131415)));
  --fab-on:#fbfcfd;--fab-on-soft:rgba(251,252,253,.2);--fab-scrim:rgba(19,20,21,.18);--fab-font:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;
  --fab-ease:cubic-bezier(.16,1,.3,1);--fab-micro:120ms;--fab-short:220ms}
.fabnav{position:fixed;right:max(18px,env(safe-area-inset-right));bottom:max(18px,env(safe-area-inset-bottom));z-index:300;display:grid;justify-items:end;gap:8px;
  font-family:var(--fab-font)}
.fabnav svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none}
.fab-main{position:relative;width:56px;height:56px;border-radius:50%;border:0;padding:0;cursor:pointer;background:var(--fab-accent);color:var(--fab-on);display:grid;place-items:center;
  animation:fab-float 3.2s var(--fab-ease) infinite;transition:background-color var(--fab-short) var(--fab-ease)}
.fab-main:hover{background:var(--fab-accent-2)}
.fab-main:active{transform:translateY(1px)}
.fab-main:focus-visible{outline:3px solid var(--fab-accent-2);outline-offset:3px}
.fab-main .ic-x{position:absolute;opacity:0;transform:rotate(-90deg) scale(.5);transition:opacity var(--fab-short) var(--fab-ease),transform var(--fab-short) var(--fab-ease)}
.fab-main .ic-m{transition:opacity var(--fab-short) var(--fab-ease),transform var(--fab-short) var(--fab-ease)}
.fabnav.open .fab-main{animation:none;background:var(--fab-ink)}
.fabnav.open .fab-main .ic-m{opacity:0;transform:rotate(90deg) scale(.5)}
.fabnav.open .fab-main .ic-x{opacity:1;transform:none}
.fab-hint{position:absolute;right:68px;top:50%;transform:translate(6px,-50%);opacity:0;pointer-events:none;white-space:nowrap;background:var(--fab-ink);color:var(--fab-surface);
  font-size:13px;font-weight:600;padding:4px 12px;border-radius:6px;transition:opacity var(--fab-short) var(--fab-ease),transform var(--fab-short) var(--fab-ease)}
.fab-main:hover .fab-hint{opacity:1;transform:translate(0,-50%);transition-delay:800ms}
.fab-main:focus-visible .fab-hint{opacity:1;transform:translate(0,-50%);transition-delay:0ms}
.fabnav.open .fab-hint{opacity:0}
.fab-list{display:grid;justify-items:end;gap:8px;margin:0;padding:0;list-style:none}
.fab-list a{display:inline-flex;align-items:center;gap:12px;text-decoration:none;color:var(--fab-ink);background:var(--fab-surface);border:2px solid color-mix(in srgb,var(--fab-accent) 25%,transparent);
  border-radius:999px;padding:4px 8px 4px 16px;font-size:14px;font-weight:600;white-space:nowrap;
  opacity:0;transform:translateY(12px);pointer-events:none;
  transition:opacity var(--fab-short) var(--fab-ease),transform var(--fab-short) var(--fab-ease),border-color var(--fab-micro) var(--fab-ease);transition-delay:calc((var(--n) - var(--i)) * 30ms)}
.fab-list a .fi{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,var(--fab-accent) 14%,var(--fab-surface));color:var(--fab-accent)}
.fab-list a:hover{border-color:var(--fab-accent)}
.fab-list a:focus-visible{border-color:var(--fab-accent);outline:2px solid var(--fab-accent-2);outline-offset:2px}
.fab-list a[aria-current=page]{background:var(--fab-accent);color:var(--fab-on);border-color:var(--fab-accent)}
.fab-list a[aria-current=page] .fi{background:var(--fab-on-soft);color:var(--fab-on)}
.fabnav.open .fab-list a{opacity:1;transform:none;pointer-events:auto;transition-delay:calc(var(--i) * 40ms)}
.fab-scrim{position:fixed;inset:0;z-index:299;background:var(--fab-scrim);opacity:0;pointer-events:none;transition:opacity var(--fab-short) var(--fab-ease)}
.fab-scrim.on{opacity:1;pointer-events:auto}
@keyframes fab-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
@media (prefers-reduced-motion:reduce){.fab-main{animation:none}.fab-list a,.fab-main,.fab-main .ic-m,.fab-main .ic-x,.fab-hint,.fab-scrim{transition:none}}
@media print{.fabnav,.fab-scrim{display:none}}
"""

SCRIPT = """
(function(){
  const nav = document.getElementById('fabnav'), main = nav.querySelector('.fab-main'), scrim = document.getElementById('fab-scrim');
  const links = [...nav.querySelectorAll('.fab-list a')];
  const set = open => { nav.classList.toggle('open', open); scrim.classList.toggle('on', open); main.setAttribute('aria-expanded', String(open));
    main.setAttribute('aria-label', open ? '關閉頁面選單' : '切換頁面'); links.forEach(a => a.tabIndex = open ? 0 : -1);
    if (open) (links.find(a => !a.hasAttribute('aria-current')) || links[0]).focus({preventScroll: true}); };
  main.addEventListener('click', () => set(!nav.classList.contains('open')));
  scrim.addEventListener('click', () => set(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && nav.classList.contains('open')) { set(false); main.focus(); } });
  links.forEach(a => a.addEventListener('click', () => set(false)));
  set(false);
})();
"""


def _svg(name: str, cls: str = "") -> str:
    return f'<svg viewBox="0 0 24 24" aria-hidden="true"{f" class={cls!r}" if cls else ""}>{ICONS[name]}</svg>'


BRAND_STYLE = """
:root{--logo-wash:rgba(255,255,255,.2);--logo-ink:var(--fg,var(--ink-1,var(--ink,#131415)));--logo-font:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif}
.corner-logo{position:absolute;top:10px;right:max(14px,env(safe-area-inset-right));z-index:30;display:flex;align-items:center;gap:10px;
  padding:4px 12px 4px 4px;background:var(--logo-wash);border-radius:6px}  /* 80% transparent, no border or shadow */
.corner-logo img{display:block;height:auto}.corner-logo .cl-mark{width:46px}
.corner-logo .cl-name{font:700 15px/1.2 var(--logo-font);letter-spacing:.04em;white-space:nowrap;color:var(--logo-ink)}  /* the wordmark: tracking allowed */
@media (max-width:720px){.corner-logo{padding:4px;top:8px}.corner-logo .cl-name{display:none}.corner-logo .cl-mark{width:38px}}
/* keep page headers clear of the logo: the dashboard's title row, the alert page's header, the news pages' brand row */
@media (min-width:721px){header.top,header>.topbar,header>.sub,header.mast{padding-right:280px}}
@media (max-width:720px){header.top,header>.topbar,header>.sub,header>.brand,header.mast{padding-right:52px}}
@media print{.corner-logo{position:static;width:max-content;margin:0 0 0 auto}}
"""


@cache
def _png(name: str) -> str:
    data = (Path(__file__).with_name("assets") / name).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def corner_logo() -> str:
    """The company logo (TTMC mark + 鑫科材料科技股份有限公司) pinned to the page's top-right corner.
    The mark is inlined so encrypted pages and the intranet copy need no extra files; the name is text
    in the page's ink, so it stays readable in dark mode."""
    return (
        f"<style>{BRAND_STYLE}</style>"
        '<div class="corner-logo">'
        f'<img class="cl-mark" src="{_png("ttmc-mark.png")}" alt="TTMC">'
        '<span class="cl-name">鑫科材料科技股份有限公司</span></div>'
    )


def fab_nav(current: str, root: str, skip: set[str] | frozenset[str] = frozenset()) -> str:
    """HTML for the floating switcher. `root` is the site root as seen from this page
    (e.g. "../" from news/, or an absolute URL); `skip` drops pages the site doesn't build."""
    pages = [p for p in PAGES if p[0] not in skip]
    n = len(pages)
    items = "".join(
        f'<li><a href="{html.escape(root + path)}" style="--i:{n - 1 - k};--n:{n - 1}"'
        f'{" aria-current=page" if key == current else ""}>{html.escape(label)}<span class="fi">{_svg(icon)}</span></a></li>'
        for k, (key, label, path, icon) in enumerate(pages)
    )
    from .taste import layer

    return (
        corner_logo()
        + layer()
        + f"<style>{STYLE}</style>"
        '<div class="fab-scrim" id="fab-scrim"></div>'
        f'<nav class="fabnav" id="fabnav" aria-label="切換頁面"><ul class="fab-list">{items}</ul>'
        f'<button type="button" class="fab-main" aria-expanded="false" aria-label="切換頁面">{_svg("menu", "ic-m")}{_svg("close", "ic-x")}'
        '<span class="fab-hint">切換頁面</span></button></nav>'
        f"<script>{SCRIPT}</script>"
    )

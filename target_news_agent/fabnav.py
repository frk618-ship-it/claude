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
]

ICONS = {
    "news": '<path d="M4 5h13v14H6a2 2 0 0 1-2-2z"/><path d="M17 9h3v8a2 2 0 0 1-2 2"/><path d="M7 9h7M7 13h7M7 16h4"/>',
    "book": '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5M8 7h7"/>',
    "chart": '<path d="M4 19h16"/><path d="M5 15l4-5 4 3 6-7"/><path d="M15 6h4v4"/>',
    "island": '<circle cx="12" cy="10" r="6"/><circle cx="12" cy="10" r="2.5"/><path d="M7 15l5 6 5-6"/>',
    "bars": '<path d="M4 20h16"/><rect x="6" y="11" width="3" height="7"/><rect x="11" y="7" width="3" height="11"/><rect x="16" y="4" width="3" height="14"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "menu": '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
}

STYLE = """
.fabnav{--fa:var(--accent,#173c66);--fa2:var(--accent-2,#005a7f);--fs:var(--surface,#fff);--ff:var(--fg,var(--ink-1,#162033));
  position:fixed;right:max(18px,env(safe-area-inset-right));bottom:max(18px,env(safe-area-inset-bottom));z-index:40;display:grid;justify-items:end;gap:10px;
  font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif}
.fabnav svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none}
.fab-main{position:relative;width:58px;height:58px;border-radius:50%;border:0;padding:0;cursor:pointer;background:var(--fa);color:#fff;display:grid;place-items:center;
  animation:fab-float 3.2s ease-in-out infinite;transition:background .2s,transform .2s}
.fab-main:hover{background:var(--fa2)}
.fab-main:focus-visible{outline:3px solid var(--fa2);outline-offset:3px}
.fab-main::before{content:"";position:absolute;inset:0;border-radius:50%;border:2px solid var(--fa);animation:fab-ring 2.6s ease-out infinite;pointer-events:none}
.fab-main .ic-x{position:absolute;opacity:0;transform:rotate(-90deg) scale(.5);transition:.25s}
.fab-main .ic-m{transition:.25s}
.fabnav.open .fab-main{animation:none;background:var(--ff)}
.fabnav.open .fab-main::before{animation:none;opacity:0}
.fabnav.open .fab-main .ic-m{opacity:0;transform:rotate(90deg) scale(.5)}
.fabnav.open .fab-main .ic-x{opacity:1;transform:none}
.fab-hint{position:absolute;right:68px;top:50%;transform:translate(6px,-50%);opacity:0;pointer-events:none;white-space:nowrap;background:var(--ff);color:var(--fs);
  font-size:12.5px;font-weight:600;padding:5px 10px;border-radius:6px;transition:.2s}
.fab-main:hover .fab-hint,.fab-main:focus-visible .fab-hint{opacity:1;transform:translate(0,-50%)}
.fabnav.open .fab-hint{opacity:0}
.fab-list{display:grid;justify-items:end;gap:10px;margin:0;padding:0;list-style:none}
.fab-list a{display:inline-flex;align-items:center;gap:10px;text-decoration:none;color:var(--ff);background:var(--fs);border:2px solid color-mix(in srgb,var(--fa) 25%,transparent);
  border-radius:999px;padding:6px 8px 6px 16px;font-size:14px;font-weight:600;
  opacity:0;transform:translateY(16px) scale(.7);transform-origin:100% 50%;pointer-events:none;
  transition:opacity .2s,transform .35s cubic-bezier(.34,1.56,.64,1),background .15s,border-color .15s;transition-delay:calc((var(--n) - var(--i)) * 35ms)}
.fab-list a .fi{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,var(--fa) 14%,var(--fs));color:var(--fa);transition:transform .2s}
.fab-list a:hover,.fab-list a:focus-visible{border-color:var(--fa);outline:none}
.fab-list a:hover .fi,.fab-list a:focus-visible .fi{transform:rotate(-12deg) scale(1.08)}
.fab-list a[aria-current=page]{background:var(--fa);color:#fff;border-color:var(--fa)}
.fab-list a[aria-current=page] .fi{background:rgba(255,255,255,.2);color:#fff}
.fabnav.open .fab-list a{opacity:1;transform:none;pointer-events:auto;transition-delay:calc(var(--i) * 45ms)}
.fab-scrim{position:fixed;inset:0;z-index:39;background:rgba(15,20,36,.18);opacity:0;pointer-events:none;transition:opacity .2s}
.fab-scrim.on{opacity:1;pointer-events:auto}
@keyframes fab-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
@keyframes fab-ring{0%{transform:scale(1);opacity:.55}80%,100%{transform:scale(1.55);opacity:0}}
@media (prefers-reduced-motion:reduce){.fab-main,.fab-main::before{animation:none}.fab-list a,.fab-main .ic-m,.fab-main .ic-x,.fab-hint{transition:none}}
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
.corner-logo{position:absolute;top:10px;right:max(14px,env(safe-area-inset-right));z-index:30;display:flex;align-items:center;gap:10px;
  padding:5px 12px 5px 6px;background:rgba(255,255,255,.2);border-radius:6px}  /* 80% transparent, no border or shadow */
.corner-logo img{display:block;height:auto}.corner-logo .cl-mark{width:46px}
.corner-logo .cl-name{font:700 15px/1.2 "Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;letter-spacing:.04em;white-space:nowrap;
  color:var(--fg,var(--ink-1,var(--ink,#131415)))}
@media (max-width:720px){.corner-logo{padding:4px;top:8px}.corner-logo .cl-name{display:none}.corner-logo .cl-mark{width:38px}}
/* keep page headers clear of the logo: the dashboard's title row, the alert page's header, the news pages' brand row */
@media (min-width:721px){header.top,header>.topbar,header>.sub{padding-right:280px}}
@media (max-width:720px){header.top,header>.topbar,header>.sub,header>.brand{padding-right:52px}}
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
    return (
        corner_logo()
        + f"<style>{STYLE}</style>"
        '<div class="fab-scrim" id="fab-scrim"></div>'
        f'<nav class="fabnav" id="fabnav" aria-label="切換頁面"><ul class="fab-list">{items}</ul>'
        f'<button type="button" class="fab-main" aria-expanded="false" aria-label="切換頁面">{_svg("menu", "ic-m")}{_svg("close", "ic-x")}'
        '<span class="fab-hint">切換頁面</span></button></nav>'
        f"<script>{SCRIPT}</script>"
    )

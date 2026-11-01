"""Build the single-file card scanner page: web/template.html with the CSS and JS inlined.

One self-contained file because the published site encrypts every HTML page and decrypts it with
document.write (see target_news_agent.protect); relative script files would be fetched unencrypted
anyway, but a single page also works offline from a saved copy and on the intranet mirror.
"""

from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).with_name("web")
# load order matters: app.js uses the globals the others define
SCRIPTS = ["parser.js", "search.js", "store.js", "ocr.js", "app.js"]


def render_page() -> str:
    html = (WEB / "template.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    js = "\n".join(f"/* ---- {name} ---- */\n" + (WEB / name).read_text(encoding="utf-8") for name in SCRIPTS)
    if "</script" in js.lower():
        raise ValueError("inline script must not contain '</script'")
    html = html.replace("/*__CSS__*/", css, 1)
    return html.replace("/*__JS__*/", js, 1)


def write_site(out_dir: str | Path, nav: bool = True) -> Path:
    """Write ``index.html`` to `out_dir` (``site/cards/`` on the published site). `nav` adds the
    site's page switcher and corner logo, which link to the sibling sections."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html = render_page()
    if nav:
        from target_news_agent.fabnav import fab_nav

        html = html.replace("</body>", fab_nav("cards", "../") + "\n</body>", 1)
    page = out_dir / "index.html"
    page.write_text(html, encoding="utf-8")
    return page

"""Render the lead-radar page (report embedded, so it works offline, encrypted and on the intranet)."""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")


def render_page(report: dict | None) -> str:
    data = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data, 1)


def write_site(report: dict | None, out_dir: str | Path, nav: bool = True, root: str = "../") -> Path:
    """Static page for the published site (``site/leads/``), with the site's page switcher."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html = render_page(report)
    if nav:
        from target_news_agent.fabnav import fab_nav

        html = html.replace("</body>", fab_nav("leads", root) + "\n</body>", 1)
    page = out / "index.html"
    page.write_text(html, encoding="utf-8")
    return page

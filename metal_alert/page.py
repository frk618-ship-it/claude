"""Render the dashboard HTML (data embedded, so the static page works offline and behind protect)."""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")


def render_page(snapshot: dict | None, live: bool = False) -> str:
    data = json.dumps(snapshot, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    return html.replace("/*__DATA__*/null", data, 1).replace("/*__LIVE__*/false", "true" if live else "false", 1)


def write_site(snapshot: dict | None, out_dir: Path) -> Path:
    """Static page for the published site (``site/metals/``), with the site's page switcher."""
    from target_news_agent.fabnav import fab_nav

    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / "index.html"
    html = render_page(snapshot).replace("</body>", fab_nav("metals", "../") + "\n</body>", 1)
    page.write_text(html, encoding="utf-8")
    return page

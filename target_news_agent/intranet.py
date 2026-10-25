"""Package the built site for a company intranet.

The GitHub Pages build loads two things from the internet (Google Fonts, three.js from jsdelivr)
and hides behind a secret path + page encryption (protect.py). Behind an intranet web server with
SSO none of that is wanted, so this step takes the plain built site and:

- drops the Google Fonts links (pages fall back to the system CJK fonts already in their stacks);
- serves three.js from the package itself (news/vendor/), MIT licence included;
- rewrites absolute links to the public site (e.g. the dashboard's links to the news pages) into
  relative ones, so the site works under any host name;
- adds robots.txt and the deployment kit (Nginx example, daily update script, collector env
  template, README) next to the web root.

Afterwards every page is scanned for scripts, stylesheets, images or fonts still loaded from
another host; links people click (news articles, papers) are fine and are not reported.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

THREE_CDN = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.min.js"
VENDOR = Path(__file__).with_name("assets") / "vendor"
KIT = Path(__file__).resolve().parents[1] / "deploy" / "intranet"
FONT_LINKS = re.compile(r'<link rel="preconnect" href="https://fonts\.g[a-z]+\.com"(?: crossorigin)?>'
                        r'|<link rel="stylesheet" href="https://fonts\.googleapis\.com/[^"]*">')
# resources the browser fetches by itself (not <a href> links people click)
EXTERNAL_LOADS = re.compile(r'<(?:script|img|iframe|source|video|audio)\b[^>]*\bsrc="(https?://[^"]+)"'
                            r'|<link\b[^>]*\bhref="(https?://[^"]+)"'
                            r"|import\(['\"](https?://[^'\"]+)['\"]\)"
                            r"|url\((https?://[^)]+)\)"
                            r"|'(https?://[^']+\.(?:m?js|css|woff2?))'")


def rewrite(page: str, public_base: str, depth: int) -> str:
    page = FONT_LINKS.sub("", page)
    page = page.replace(THREE_CDN, "./vendor/three.module.min.js")  # module specifiers need ./
    if public_base:
        page = page.replace(public_base.rstrip("/") + "/", "../" * depth or "./")
    return page


def external_loads(page: str) -> list[str]:
    return sorted({next(g for g in m.groups() if g) for m in EXTERNAL_LOADS.finditer(page)})


def package(src: str | Path, out: str | Path, public_base: str = "") -> dict:
    """Write <out>/site (the web root) and the deployment kit. Returns {pages, external}."""
    src, out = Path(src), Path(out)
    if out.exists():
        shutil.rmtree(out)
    site = out / "site"
    shutil.copytree(src, site)
    pages, external = 0, {}
    for f in sorted(site.rglob("*.html")):
        rel = f.relative_to(site)
        page = rewrite(f.read_text(encoding="utf-8"), public_base, len(rel.parts) - 1)
        f.write_text(page, encoding="utf-8")
        pages += 1
        found = [u for u in external_loads(page) if not (public_base and u.startswith(public_base))]
        if found:
            external[str(rel)] = found
    vendor = site / "news" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    for name in ("three.module.min.js", "three.LICENSE"):
        shutil.copyfile(VENDOR / name, vendor / name)
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    if KIT.exists():
        for f in KIT.rglob("*"):
            if f.is_file():
                dest = out / f.relative_to(KIT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dest)
                dest.chmod(f.stat().st_mode)
    return {"pages": pages, "external": external}

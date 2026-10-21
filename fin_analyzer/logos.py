"""Company logos for the dashboard's profit x growth chart.

`fetch_logos` downloads each company's site icon once (icons declared on the homepage,
/apple-touch-icon.png, then Google's favicon service; the www. host and the bare domain are
both tried) into fin_analyzer/assets/logos/<ticker>.<ext>; files already there
are kept, so a hand-picked logo dropped in that folder (PNG / JPG / SVG) always wins.
The dashboard embeds them as data URIs, so the page stays self-contained.
"""

from __future__ import annotations

import base64
import html
import logging
import re
import struct
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .config import Company

log = logging.getLogger(__name__)

LOGO_DIR = Path(__file__).with_name("assets") / "logos"
MIN_SIZE = 32  # px; smaller icons look blurry inside a bubble
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp"}

Fetcher = Callable[[str], bytes]


def http_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (fin-analyzer logo fetch)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a PNG, or None when the bytes are not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def hosts(domain: str) -> list[str]:
    bare = domain.split("/")[0].removeprefix("www.")
    return [f"www.{bare}", bare]


def homepages(website: str) -> list[str]:
    """`website` is a domain or a page URL (scheme optional), e.g. www.e-ttmc.com.tw/chinese/index.html."""
    site = re.sub(r"^https?://", "", website.strip()).rstrip("/")
    path = site.split("/", 1)[1] if "/" in site else ""
    return [f"https://{h}/{path}" for h in hosts(site)]


_LINK = re.compile(r"<link\b[^>]*>", re.I)
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z-]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)')


def page_icons(page: str, base: str) -> list[str]:
    """Icon URLs declared in a homepage, best first: apple-touch-icon, then larger / SVG icons."""
    found = []
    for tag in _LINK.findall(page):
        attrs = {k.lower(): html.unescape(v.strip("\"'")) for k, v in _ATTR.findall(tag)}
        rel, href = attrs.get("rel", "").lower(), attrs.get("href", "")
        if not href or "icon" not in rel or "mask-icon" in rel:
            continue
        sizes = [int(n) for n in re.findall(r"(\d+)x\d+", attrs.get("sizes", ""))]
        rank = (2 if "apple-touch" in rel else 1, 999 if href.lower().endswith(".svg") else max(sizes, default=0))
        found.append((rank, urllib.parse.urljoin(base, href)))
    return [u for _, u in sorted(found, key=lambda x: x[0], reverse=True)]


def page_logo_images(page: str, base: str) -> list[str]:
    """<img> tags that look like the site logo (src / alt / class / id mention "logo"), in page order."""
    out = []
    for tag in _IMG.findall(page):
        attrs = {k.lower(): html.unescape(v.strip("\"'")) for k, v in _ATTR.findall(tag)}
        src = attrs.get("src", "")
        if src and not src.startswith("data:") and "logo" in " ".join(attrs.get(k, "") for k in ("src", "alt", "class", "id")).lower():
            out.append(urllib.parse.urljoin(base, src))
    return out


def image_kind(data: bytes) -> str | None:
    """File extension for usable logo bytes (PNG at least MIN_SIZE px, SVG, JPEG), else None."""
    size = png_size(data)
    if size:
        return ".png" if min(size) >= MIN_SIZE else None
    head = data[:600].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return ".svg"
    if data[:3] == b"\xff\xd8\xff" and len(data) > 2000:
        return ".jpg"
    return None


def candidate_urls(website: str, fetch: Fetcher) -> list[str]:
    """Square site icons first (they fit a round bubble), then the header logo image, then Google's favicon."""
    urls, logos = [], []
    for home in homepages(website):
        try:
            page = fetch(home).decode("utf-8", "replace")
        except Exception as exc:
            log.info("%s: homepage failed: %s", home, exc)
            continue
        root = home.split("/", 3)
        urls += page_icons(page, home) + [f"{root[0]}//{root[2]}/apple-touch-icon.png"]
        logos = page_logo_images(page, home)
        break
    google = [f"https://www.google.com/s2/favicons?domain={urllib.parse.quote(h)}&sz=128" for h in hosts(website)]
    return list(dict.fromkeys(urls + logos + google))


def logo_file(ticker: str, logo_dir: Path = LOGO_DIR) -> Path | None:
    for ext in MIME:
        path = logo_dir / f"{ticker}{ext}"
        if path.exists():
            return path
    return None


def fetch_logos(companies: list[Company], fetch: Fetcher = http_fetch, logo_dir: Path = LOGO_DIR, force: bool = False) -> dict[str, str]:
    """Download missing logos; returns {ticker: status} for the log."""
    logo_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    for c in companies:
        if not c.website:
            status[c.ticker] = "no website"
            continue
        if logo_file(c.ticker, logo_dir) and not force:
            status[c.ticker] = "kept"
            continue
        status[c.ticker] = "not found"
        for url in candidate_urls(c.website, fetch):
            try:
                data = fetch(url)
            except Exception as exc:  # try the next source
                log.info("%s: %s failed: %s", c.ticker, url, exc)
                continue
            ext = image_kind(data)
            if not ext:
                log.info("%s: %s not a usable image (png size %s)", c.ticker, url, png_size(data))
                continue
            old = logo_file(c.ticker, logo_dir)
            if old:
                old.unlink()
            (logo_dir / f"{c.ticker}{ext}").write_bytes(data)
            size = png_size(data)
            status[c.ticker] = f"{ext[1:]} {'%dx%d ' % size if size else ''}from {url}"
            break
        log.warning("logo %-10s %s", c.ticker, status[c.ticker])
    return status


def logo_data_uri(ticker: str, logo_dir: Path = LOGO_DIR) -> str:
    path = logo_file(ticker, logo_dir)
    if not path:
        return ""
    return f"data:{MIME[path.suffix.lower()]};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

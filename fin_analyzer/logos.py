"""Company logos for the dashboard's profit x growth chart.

`fetch_logos` downloads each company's site icon once (the site's apple-touch-icon, then
Google's favicon service) into fin_analyzer/assets/logos/<ticker>.png; files already there
are kept, so a hand-picked logo dropped in that folder (PNG / JPG / SVG) always wins.
The dashboard embeds them as data URIs, so the page stays self-contained.
"""

from __future__ import annotations

import base64
import logging
import struct
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .config import Company

log = logging.getLogger(__name__)

LOGO_DIR = Path(__file__).with_name("assets") / "logos"
MIN_SIZE = 48  # px; smaller icons look blurry inside a bubble
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


def candidate_urls(domain: str) -> list[str]:
    q = urllib.parse.quote(domain)
    return [
        f"https://{domain}/apple-touch-icon.png",
        f"https://www.google.com/s2/favicons?domain={q}&sz=128",
    ]


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
        for url in candidate_urls(c.website):
            try:
                data = fetch(url)
            except Exception as exc:  # try the next source
                log.info("%s: %s failed: %s", c.ticker, url, exc)
                continue
            size = png_size(data)
            if not size or min(size) < MIN_SIZE:
                log.info("%s: %s not a usable PNG (%s)", c.ticker, url, size)
                continue
            (logo_dir / f"{c.ticker}.png").write_bytes(data)
            status[c.ticker] = f"{size[0]}x{size[1]} from {url}"
            break
        log.warning("logo %-10s %s", c.ticker, status[c.ticker])
    return status


def logo_data_uri(ticker: str, logo_dir: Path = LOGO_DIR) -> str:
    path = logo_file(ticker, logo_dir)
    if not path:
        return ""
    return f"data:{MIME[path.suffix.lower()]};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

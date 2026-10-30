"""OHLCV bars from the Yahoo Finance chart API (free, no key)."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# Yahoo answers 429 to a full browser UA that arrives without its cookies; a bare one is served.
USER_AGENT = "Mozilla/5.0"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")

Fetcher = Callable[[str], bytes]


@dataclass
class Bar:
    ts: int          # bar start, unix seconds (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def chart_url(symbol: str, interval: str, range_: str, host: str = HOSTS[0]) -> str:
    return f"https://{host}/v8/finance/chart/{symbol}?interval={interval}&range={range_}&includePrePost=false"


def http_fetch(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_chart(data: bytes) -> tuple[list[Bar], dict]:
    """Chart JSON -> (bars oldest first, meta). Bars with a missing price are skipped."""
    chart = json.loads(data)["chart"]
    if chart.get("error"):
        raise ValueError(chart["error"].get("description") or str(chart["error"]))
    result = chart["result"][0]
    q = result["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(result.get("timestamp") or []):
        o, h, lo, c = (q[k][i] for k in ("open", "high", "low", "close"))
        if None in (o, h, lo, c):
            continue
        bars.append(Bar(int(ts), float(o), float(h), float(lo), float(c), float((q.get("volume") or [0] * (i + 1))[i] or 0)))
    return bars, result.get("meta") or {}


def fetch_bars(symbol: str, interval: str, range_: str, fetch: Fetcher = http_fetch,
               retries: int = 2) -> tuple[list[Bar], dict]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        host = HOSTS[attempt % len(HOSTS)]
        try:
            bars, meta = parse_chart(fetch(chart_url(symbol, interval, range_, host)))
            if bars:
                return bars, meta
            last = ValueError("no bars")
        except Exception as exc:  # retry on the other host
            last = exc
        log.warning("yahoo %s %s attempt %d failed: %s", symbol, interval, attempt + 1, last)
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Yahoo Finance {symbol} {interval}: {last}")

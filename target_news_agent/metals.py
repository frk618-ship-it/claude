"""Daily precious-metal quotes (gold, silver, platinum, palladium) for the report.

Free sources, no API key: Stooq daily history CSV first, Yahoo Finance chart API as
fallback. Each quote is the latest daily close and its change from the close before.
A failure only leaves that metal out; it never stops the news run.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger(__name__)

Fetcher = Callable[[str], bytes]


@dataclass
class MetalSpec:
    name: str            # 顯示名稱,例如 黃金
    code: str            # 元素符號,例如 Au
    stooq: str = ""      # Stooq 代號,例如 xauusd
    yahoo: str = ""      # Yahoo Finance 代號,例如 GC=F
    unit: str = "USD/oz"


@dataclass
class Quote:
    name: str
    code: str
    price: float
    prev: float | None
    date: str           # YYYY-MM-DD of the latest close
    unit: str
    source: str

    @property
    def change_pct(self) -> float | None:
        return (self.price / self.prev - 1) * 100 if self.prev else None

    def to_dict(self) -> dict:
        return {**asdict(self), "change_pct": None if self.change_pct is None else round(self.change_pct, 2)}


def stooq_url(symbol: str) -> str:
    return f"https://stooq.com/q/d/l/?s={symbol}&i=d"


def yahoo_url(symbol: str) -> str:
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10d&interval=1d"


def parse_stooq(data: bytes) -> list[tuple[str, float]]:
    """Daily CSV (Date,Open,High,Low,Close,...) -> [(date, close)] oldest first."""
    rows = []
    for row in csv.DictReader(io.StringIO(data.decode("utf-8", "replace"))):
        try:
            rows.append((row["Date"], float(row["Close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def parse_yahoo(data: bytes) -> list[tuple[str, float]]:
    result = json.loads(data)["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    out = []
    for ts, close in zip(result["timestamp"], closes):
        if close is not None:
            out.append((datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(), float(close)))
    return out


def fetch_quote(spec: MetalSpec, fetch: Fetcher) -> Quote | None:
    sources = []
    if spec.stooq:
        sources.append(("Stooq", stooq_url(spec.stooq), parse_stooq))
    if spec.yahoo:
        sources.append(("Yahoo Finance", yahoo_url(spec.yahoo), parse_yahoo))
    for label, url, parse in sources:
        try:
            series = parse(fetch(url))
        except Exception as exc:  # try the next source
            log.warning("metal quote %s from %s failed: %s", spec.name, label, exc)
            continue
        if not series:
            log.warning("metal quote %s from %s: no data", spec.name, label)
            continue
        date, price = series[-1]
        prev = series[-2][1] if len(series) > 1 else None
        return Quote(spec.name, spec.code, round(price, 2), prev and round(prev, 2), date, spec.unit, label)
    return None


def fetch_quotes(specs: list[MetalSpec], fetch: Fetcher) -> list[Quote]:
    quotes = []
    for spec in specs:
        q = fetch_quote(spec, fetch)
        if q:
            log.info("metal %-4s %10.2f %s (%s, %s)", spec.code, q.price, spec.unit, q.date, q.source)
            quotes.append(q)
    return quotes

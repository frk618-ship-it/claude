"""Normalise quotes (currency, pricing unit), score them against the requested spec and summarise."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from .config import Settings
from .fx import FxConverter
from .models import MaterialSpec, Quote
from .spec import match_level, match_score, parse_spec

# unit -> (basis, quantity of the basis unit per 1 unit)
UNITS = {
    "pcs": ("每片/個", 1.0), "pc": ("每片/個", 1.0), "piece": ("每片/個", 1.0), "ea": ("每片/個", 1.0),
    "片": ("每片/個", 1.0), "個": ("每片/個", 1.0), "set": ("每組", 1.0), "組": ("每組", 1.0),
    "kg": ("每公斤", 1.0), "公斤": ("每公斤", 1.0), "g": ("每公斤", 0.001), "克": ("每公斤", 0.001),
    "lb": ("每公斤", 0.45359237), "oz": ("每公斤", 0.028349523), "ozt": ("每公斤", 0.0311034768),
    "t": ("每公斤", 1000.0), "ton": ("每公斤", 1000.0), "噸": ("每公斤", 1000.0),
}
USABLE_LEVELS = ("符合", "近似")


@dataclass
class BasisStats:
    basis: str
    count: int
    low: float
    median: float
    high: float
    best: Quote


@dataclass
class Comparison:
    spec: MaterialSpec
    quotes: list[Quote]
    stats: list[BasisStats] = field(default_factory=list)
    excluded: int = 0
    market_summary: str = ""
    fx_rates: dict[str, float] = field(default_factory=dict)
    fx_live: bool = False
    base_currency: str = "TWD"

    @property
    def usable(self) -> list[Quote]:
        return [q for q in self.quotes if _usable(q)]


def _usable(q: Quote) -> bool:
    return q.match_level in USABLE_LEVELS and q.unit_price_twd is not None and q.price_type != "reference" and not q.outlier


def _age_days(q: Quote, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(q.quote_date[:10])).days
    except ValueError:
        return None


def analyse(spec: MaterialSpec, quotes: list[Quote], settings: Settings, fx: FxConverter,
            today: date | None = None, market_summary: str = "") -> Comparison:
    today = today or date.today()
    kept: list[Quote] = []
    excluded = 0
    seen: set[tuple] = set()
    for q in quotes:
        key = (q.vendor.lower(), q.url.lower(), round(q.price, 4), q.currency.upper(), q.unit.lower())
        if key in seen:
            continue
        seen.add(key)

        q.spec = parse_spec(f"{q.title} {q.spec_text}")
        q.match_score, q.match_notes = match_score(spec, q.spec, settings.dim_tolerance)
        q.match_level = match_level(q.match_score)
        if q.match_score < settings.min_match_score:
            excluded += 1
            continue

        age = _age_days(q, today)
        if age is not None and age > settings.max_quote_age_days:
            q.match_notes.append(f"報價已 {age} 天")
            q.outlier = "過舊"

        q.price_twd = fx.convert(q.price, q.currency)
        basis, factor = UNITS.get(q.unit.strip().lower(), ("", 0.0))
        if q.price_twd is None:
            q.match_notes.append(f"無 {q.currency} 匯率")
        elif factor:
            q.basis = basis
            q.unit_price_twd = q.price_twd / (q.pack_qty * factor)
        else:
            q.match_notes.append(f"無法換算單位 {q.unit}")
        kept.append(q)

    _flag_outliers(kept, settings.outlier_ratio)
    kept.sort(key=lambda q: (USABLE_LEVELS.index(q.match_level) if q.match_level in USABLE_LEVELS else 9,
                             q.basis, q.unit_price_twd if q.unit_price_twd is not None else float("inf")))
    comp = Comparison(spec=spec, quotes=kept, excluded=excluded, market_summary=market_summary,
                      fx_rates=dict(fx.rates), fx_live=fx.live, base_currency=fx.base)
    comp.stats = basis_stats(comp.usable)
    return comp


def _flag_outliers(quotes: list[Quote], ratio: float) -> None:
    by_basis: dict[str, list[Quote]] = {}
    for q in quotes:
        if q.match_level in USABLE_LEVELS and q.unit_price_twd and q.price_type != "reference" and not q.outlier:
            by_basis.setdefault(q.basis, []).append(q)
    for group in by_basis.values():
        if len(group) < 3:
            continue
        med = statistics.median(q.unit_price_twd for q in group)
        for q in group:
            if q.unit_price_twd > med * ratio:
                q.outlier = "偏高"
            elif q.unit_price_twd < med / ratio:
                q.outlier = "偏低"


def basis_stats(quotes: list[Quote]) -> list[BasisStats]:
    groups: dict[str, list[Quote]] = {}
    for q in quotes:
        groups.setdefault(q.basis, []).append(q)
    out = []
    for basis, group in groups.items():
        prices = sorted(q.unit_price_twd for q in group)
        # Prefer the cheapest exact match; fall back to the cheapest close match.
        best = min(group, key=lambda q: (q.match_level != "符合", q.unit_price_twd))
        out.append(BasisStats(basis, len(group), prices[0], statistics.median(prices), prices[-1], best))
    return sorted(out, key=lambda s: -s.count)

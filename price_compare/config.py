"""Load config/price_compare.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Settings:
    base_currency: str = "TWD"
    fx_rates: dict[str, float] = field(default_factory=dict)  # 1 unit of currency = x base currency
    live_fx: bool = True
    quote_book: str = "data/quotes/quotes.csv"
    min_match_score: int = 40
    dim_tolerance: float = 0.03
    outlier_ratio: float = 3.0
    max_quote_age_days: int = 365
    # Claude web search
    web_search: bool = True
    max_searches: int = 12
    user_location: dict[str, str] = field(default_factory=dict)
    blocked_domains: list[str] = field(default_factory=list)
    search_hints: list[str] = field(default_factory=list)
    # PChome 24h 購物(一般消費性商品才有用)
    pchome: bool = False
    request_delay: float = 0.5


def load_config(path: str | Path) -> Settings:
    p = Path(path)
    raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    src = raw.get("sources", {}) or {}
    web = src.get("claude_web_search", {}) or {}
    return Settings(
        base_currency=str(raw.get("base_currency", "TWD")).upper(),
        fx_rates={str(k).upper(): float(v) for k, v in (raw.get("fx_rates", {}) or {}).items()},
        live_fx=bool(raw.get("live_fx", True)),
        quote_book=str(src.get("quote_book", "data/quotes/quotes.csv")),
        min_match_score=int(raw.get("min_match_score", 40)),
        dim_tolerance=float(raw.get("dim_tolerance", 0.03)),
        outlier_ratio=float(raw.get("outlier_ratio", 3.0)),
        max_quote_age_days=int(raw.get("max_quote_age_days", 365)),
        web_search=bool(web.get("enabled", True)),
        max_searches=int(web.get("max_searches", 12)),
        user_location=dict(web.get("user_location", {}) or {}),
        blocked_domains=[str(d) for d in web.get("blocked_domains", []) or []],
        search_hints=[str(h) for h in web.get("search_hints", []) or []],
        pchome=bool((src.get("pchome", {}) or {}).get("enabled", False)),
        request_delay=float(raw.get("request_delay", 0.5)),
    )

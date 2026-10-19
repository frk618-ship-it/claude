"""Load the analysis config: companies to compare (or target + peers), thresholds, FX, industry notes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_THRESHOLDS = {
    "gross_margin_drop_pp": 0.03,  # 毛利率較去年同季下滑超過 3 個百分點
    "gross_margin_decline_quarters": 3,  # 毛利率連續下滑季數
    "revenue_yoy_strong": 0.20,
    "monthly_momentum_up": 1.10,  # 近 3 月平均 / 近 12 月平均
    "monthly_momentum_down": 0.90,
    "dio_increase": 0.20,  # 存貨天數年增 20%
    "dso_increase": 0.20,
    "ocf_to_ni_min": 0.80,
    "debt_ratio_max": 0.60,
    "current_ratio_min": 1.20,
    "interest_coverage_min": 3.0,
    "non_operating_share_max": 0.30,
    "roe_good": 0.15,
    "fscore_good": 7,
    "fscore_bad": 3,
}


MARKET_CURRENCY = {"TW": "TWD", "JP": "JPY", "US": "USD", "CN": "CNY", "HK": "HKD", "KR": "KRW"}
MARKET_REGION = {"TW": "台灣", "JP": "日本", "US": "美國", "CN": "中國大陸", "HK": "香港", "KR": "韓國"}
FOCUS_LABELS = {"pure": "靶材專業廠", "major": "靶材為主要事業之一", "diversified": "多角化(靶材占比較低)"}


def guess_market(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".T"):
        return "JP"
    if t.endswith((".SS", ".SZ")):
        return "CN"
    if t.endswith(".HK"):
        return "HK"
    if t.endswith((".KS", ".KQ")):
        return "KR"
    if t.isdigit() or t.endswith((".TW", ".TWO")):
        return "TW"
    return "US"


@dataclass
class Company:
    ticker: str
    name: str = ""
    segment: str = ""
    market: str = ""
    currency: str = ""
    target_focus: str = ""  # pure / major / diversified
    short: str = ""  # short label for charts

    def __post_init__(self) -> None:
        self.market = (self.market or guess_market(self.ticker)).upper()
        self.currency = (self.currency or MARKET_CURRENCY.get(self.market, "")).upper()

    @property
    def region(self) -> str:
        return MARKET_REGION.get(self.market, self.market)

    @property
    def focus_label(self) -> str:
        return FOCUS_LABELS.get(self.target_focus, self.target_focus)

    @property
    def par_value(self) -> float | None:
        return 10.0 if self.market == "TW" else None


@dataclass
class FinConfig:
    target: Company
    peers: list[Company] = field(default_factory=list)
    data_dir: str = "data/financials"
    report_dir: str = "reports/financials"
    start_date: str = "2019-01-01"
    par_value: float = 10.0
    amount_unit: float = 1e8
    amount_unit_label: str = "億元"
    quarters_in_table: int = 8
    months_in_table: int = 12
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    industry_name: str = ""
    industry_notes: list[str] = field(default_factory=list)
    finmind_field_map: dict = field(default_factory=dict)
    finmind_ytd: dict = field(default_factory=dict)
    display_currency: str = "TWD"
    fx_rates: dict[str, float] = field(default_factory=dict)  # 1 unit of currency -> display currency
    dashboard_dir: str = "reports/dashboard"
    title: str = ""
    news_url: str = ""  # link to the industry-news site shown in the dashboard header
    site_url: str = ""  # public URL of the dashboard; used for absolute og:image / og:url links

    @property
    def companies(self) -> list[Company]:
        return [self.target, *self.peers]


def _company(raw) -> Company:
    if isinstance(raw, (str, int)):
        return Company(ticker=str(raw))
    return Company(
        ticker=str(raw["ticker"]),
        name=str(raw.get("name", "")),
        segment=str(raw.get("segment", "")),
        market=str(raw.get("market", "")),
        currency=str(raw.get("currency", "")),
        target_focus=str(raw.get("target_focus", "")),
        short=str(raw.get("short", "")),
    )


def load_fin_config(path: str | Path) -> FinConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    s = raw.get("settings", {}) or {}
    industry = raw.get("industry", {}) or {}
    finmind = s.get("finmind", {}) or {}
    if "companies" in raw:
        # Dashboard style: a flat list of companies; `focus` picks the highlighted / report target.
        companies = [_company(c) for c in raw["companies"] or []]
        if not companies:
            raise ValueError(f"{path}: `companies` is empty")
        focus = str(raw.get("focus", companies[0].ticker))
        target = next((c for c in companies if c.ticker == focus), None)
        if target is None:
            raise ValueError(f"{path}: focus {focus!r} is not in `companies`")
        raw = {**raw, "target": target, "peers": [c for c in companies if c is not target]}
    elif "target" not in raw:
        raise ValueError(f"{path}: needs `companies` or `target`")
    field_map = {
        k: (str(v["statement"]), [str(c) for c in v["candidates"]], int(v.get("sign", 1)))
        for k, v in (finmind.get("field_map", {}) or {}).items()
    }
    return FinConfig(
        target=raw["target"] if isinstance(raw["target"], Company) else _company(raw["target"]),
        peers=[p if isinstance(p, Company) else _company(p) for p in raw.get("peers", []) or []],
        data_dir=str(s.get("data_dir", "data/financials")),
        report_dir=str(s.get("report_dir", "reports/financials")),
        start_date=str(s.get("start_date", "2019-01-01")),
        par_value=float(s.get("par_value", 10)),
        amount_unit=float(s.get("amount_unit", 1e8)),
        amount_unit_label=str(s.get("amount_unit_label", "億元")),
        quarters_in_table=int(s.get("quarters_in_table", 8)),
        months_in_table=int(s.get("months_in_table", 12)),
        thresholds={**DEFAULT_THRESHOLDS, **{k: float(v) for k, v in (s.get("thresholds", {}) or {}).items()}},
        industry_name=str(industry.get("name", "")),
        industry_notes=[str(n) for n in industry.get("notes", []) or []],
        finmind_field_map=field_map,
        finmind_ytd={k: bool(v) for k, v in (finmind.get("ytd", {}) or {}).items()},
        display_currency=str(s.get("display_currency", "TWD")).upper(),
        fx_rates={str(k).upper(): float(v) for k, v in (s.get("fx_rates", {}) or {}).items()},
        dashboard_dir=str(s.get("dashboard_dir", "reports/dashboard")),
        title=str(raw.get("title", "")),
        news_url=str(s.get("news_url", "") or ""),
        site_url=str(s.get("site_url", "") or ""),
    )

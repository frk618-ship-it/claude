"""Load config/fin_targets.yaml (target company, peers, thresholds, industry notes)."""

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


@dataclass
class Company:
    ticker: str
    name: str = ""
    segment: str = ""


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

    @property
    def companies(self) -> list[Company]:
        return [self.target, *self.peers]


def _company(raw) -> Company:
    if isinstance(raw, (str, int)):
        return Company(ticker=str(raw))
    return Company(ticker=str(raw["ticker"]), name=str(raw.get("name", "")), segment=str(raw.get("segment", "")))


def load_fin_config(path: str | Path) -> FinConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    s = raw.get("settings", {}) or {}
    industry = raw.get("industry", {}) or {}
    finmind = s.get("finmind", {}) or {}
    if "target" not in raw:
        raise ValueError(f"{path}: missing `target`")
    field_map = {
        k: (str(v["statement"]), [str(c) for c in v["candidates"]], int(v.get("sign", 1)))
        for k, v in (finmind.get("field_map", {}) or {}).items()
    }
    return FinConfig(
        target=_company(raw["target"]),
        peers=[_company(p) for p in raw.get("peers", []) or []],
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
    )

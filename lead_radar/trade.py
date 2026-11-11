"""Import records (海關 / 貿易資料) as a localization score.

Taiwan customs statistics are public only as totals by HS code and country; who imported what is
not released (關稅法第 12 條). Company-level records therefore come from files a person puts in
`data/lead_radar/trade/` — e.g. the 國際貿易署 出進口廠商登記系統 (which lists the companies that
import a given CCC / HS code, with their import-amount bracket), a paid customs-data service, or
what sales learns from customers.

A listed company that imports a material we make is a localization (進口替代) opportunity:
it already buys the material, abroad. It gets a 在地化分數 (0–100) that adds to the overall score.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .config import Account, Config
from .signals import match_accounts

log = logging.getLogger(__name__)

COLUMNS = ["date", "company", "tax_id", "hs_code", "product", "origin", "value_ntd", "weight_kg", "source", "note"]
BASE, RECURRING, LARGE = 60.0, 20.0, 20.0          # 在地化分數: imports at all / in 2+ periods / a large volume
LARGE_VALUE_NTD, LARGE_WEIGHT_KG = 10_000_000, 10_000


@dataclass
class TradeRecord:
    date: str               # YYYY-MM or YYYY-MM-DD
    company: str
    hs_code: str = ""
    product: str = ""       # product key; empty = from hs_code
    origin: str = ""
    value_ntd: float = 0.0
    weight_kg: float = 0.0
    tax_id: str = ""
    source: str = ""
    note: str = ""

    @property
    def period(self) -> str:
        return self.date[:7]


def _num(v: str) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def load_trade(path: str | Path) -> list[TradeRecord]:
    """Every *.csv in the folder (or one file). Lines starting with "#" are comments."""
    p = Path(path)
    files = sorted(p.glob("*.csv")) if p.is_dir() else ([p] if p.exists() else [])
    out: list[TradeRecord] = []
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        for row in csv.DictReader(lines):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("company") or not row.get("date"):
                continue
            out.append(TradeRecord(
                date=row["date"], company=row["company"], hs_code=row.get("hs_code", "").replace(".", ""),
                product=row.get("product", ""), origin=row.get("origin", ""), value_ntd=_num(row.get("value_ntd", "")),
                weight_kg=_num(row.get("weight_kg", "")), tax_id=row.get("tax_id", ""), source=row.get("source", "") or f.name,
                note=row.get("note", ""),
            ))
    return out


def product_of(rec: TradeRecord, config: Config) -> str:
    if rec.product and config.product(rec.product):
        return rec.product
    hs_map = config.settings.trade.get("hs", {})
    for key, prefixes in hs_map.items():
        if any(rec.hs_code.startswith(str(pre)) for pre in prefixes):
            return key
    return ""


def records_for(account: Account, records: list[TradeRecord], config: Config) -> list[TradeRecord]:
    out = []
    for r in records:
        if account.tax_id and r.tax_id:
            if r.tax_id == account.tax_id:
                out.append(r)
        elif account in match_accounts(r.company.lower(), [account]):
            out.append(r)
    return out


def localization(account: Account, records: list[TradeRecord], config: Config, asof: date) -> dict:
    """在地化分數 and the import records behind it (only materials the account has a fit for)."""
    months = int(config.settings.trade.get("lookback_months", 12))
    since = (asof - timedelta(days=round(months * 30.5))).isoformat()[:7]
    rows = []
    for r in records_for(account, records, config):
        prod = product_of(r, config)
        if prod and account.fit.get(prod, 0) >= 1 and since <= r.period <= asof.isoformat()[:7]:
            rows.append((r, prod))
    if not rows:
        return {"score": 0.0, "products": [], "records": [], "summary": ""}
    periods = {r.period for r, _ in rows}
    value = sum(r.value_ntd for r, _ in rows)
    weight = sum(r.weight_kg for r, _ in rows)
    score = BASE + (RECURRING if len(periods) >= 2 else 0) + (LARGE if value >= LARGE_VALUE_NTD or weight >= LARGE_WEIGHT_KG else 0)
    products = sorted({p for _, p in rows}, key=lambda p: -sum(1 for _, q in rows if q == p))
    names = {p.key: p.name for p in config.products}
    origins = sorted({r.origin for r, _ in rows if r.origin})
    parts = [f"進口{'、'.join(names[p] for p in products)} {len(rows)} 筆({min(periods)}–{max(periods)})"]
    if origins:
        parts.append("來源:" + "、".join(origins))
    if value:
        parts.append(f"金額約 NT${value / 1e6:,.1f} 百萬")
    if weight:
        parts.append(f"重量約 {weight / 1000:,.1f} 公噸")
    return {
        "score": min(100.0, score),
        "products": products,
        "records": [{"date": r.date, "hs_code": r.hs_code, "product": p, "origin": r.origin, "value_ntd": r.value_ntd,
                     "weight_kg": r.weight_kg, "source": r.source, "note": r.note} for r, p in rows][:10],
        "summary": ";".join(parts),
    }

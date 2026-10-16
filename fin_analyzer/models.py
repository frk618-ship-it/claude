"""Financial data containers, field definitions and CSV storage.

Storage layout (one folder per company):

    data/financials/<ticker>/quarterly.csv         period,revenue,cogs,...   (one row per quarter)
    data/financials/<ticker>/monthly_revenue.csv   month,revenue             (2025-09,...)
    data/financials/<ticker>/prices.csv            date,close
    data/financials/<ticker>/annual.csv            period,revenue,...        (fiscal-year totals, optional)
    data/financials/<ticker>/meta.json             {"ticker": ..., "name": ..., "currency": "TWD"}

Amounts are in the company's reporting currency (``meta.json`` → ``currency``).
``annual.csv`` holds fiscal-year flow totals keyed by the quarter the fiscal year ends in
(a March year-end FY2025 → ``2026Q1``); metrics fall back to them when a company's
quarterly statements are incomplete (e.g. Japanese filers on Yahoo Finance).
Quarterly conventions: income-statement and cash-flow fields are *single-quarter*
amounts; balance-sheet fields are period-end balances. Blank cells mean "unknown".
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# field -> 中文名稱
INCOME_FIELDS = {
    "revenue": "營業收入",
    "cogs": "營業成本",
    "gross_profit": "營業毛利",
    "operating_expenses": "營業費用",
    "operating_income": "營業利益",
    "non_operating": "營業外收支",
    "pretax_income": "稅前淨利",
    "income_tax": "所得稅費用",
    "net_income": "本期淨利",
    "net_income_parent": "歸屬母公司淨利",
    "eps": "每股盈餘(元)",
}
BALANCE_FIELDS = {
    "cash": "現金及約當現金",
    "receivables": "應收帳款及票據",
    "inventory": "存貨",
    "current_assets": "流動資產",
    "ppe": "不動產、廠房及設備",
    "total_assets": "資產總額",
    "payables": "應付帳款及票據",
    "short_term_debt": "短期借款",
    "long_term_debt": "長期借款",
    "current_liabilities": "流動負債",
    "total_liabilities": "負債總額",
    "share_capital": "普通股股本",
    "shares": "流通在外股數(股)",
    "retained_earnings": "保留盈餘",
    "equity": "權益總額",
    "equity_parent": "歸屬母公司權益",
}
CASHFLOW_FIELDS = {
    "operating_cf": "營業活動現金流量",
    "capex": "購置不動產、廠房及設備(支出,正數)",
    "depreciation": "折舊費用",
    "interest_expense": "利息費用",
    "dividends_paid": "發放現金股利(支出,正數)",
}
FIELDS = {**INCOME_FIELDS, **BALANCE_FIELDS, **CASHFLOW_FIELDS}
FLOW_FIELDS = set(INCOME_FIELDS) | set(CASHFLOW_FIELDS)

_PERIOD_RE = re.compile(r"^(\d{4})\s*-?\s*Q([1-4])$", re.I)


def parse_period(period: str) -> tuple[int, int]:
    m = _PERIOD_RE.match(period.strip())
    if not m:
        raise ValueError(f"invalid quarter {period!r}, expected e.g. 2025Q2")
    return int(m.group(1)), int(m.group(2))


def fmt_period(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def shift_period(period: str, quarters: int) -> str:
    y, q = parse_period(period)
    idx = y * 4 + (q - 1) + quarters
    return fmt_period(idx // 4, idx % 4 + 1)


def period_from_date(date: str) -> str:
    """'2025-06-30' -> '2025Q2' (FinMind reports quarter-end dates)."""
    y, m = int(date[:4]), int(date[5:7])
    return fmt_period(y, (m - 1) // 3 + 1)


def shift_month(month: str, months: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "NA", "N/A", "nan", "None"):
        return None
    if text.startswith("(") and text.endswith(")"):  # accounting negative
        text = "-" + text[1:-1]
    return float(text)


@dataclass
class CompanyData:
    ticker: str
    name: str = ""
    quarters: dict[str, dict[str, float | None]] = field(default_factory=dict)
    monthly_revenue: dict[str, float] = field(default_factory=dict)  # "2025-09" -> 元
    prices: list[tuple[str, float]] = field(default_factory=list)  # (date, close), ascending
    note: str = ""
    currency: str = "TWD"
    annual: dict[str, dict[str, float | None]] = field(default_factory=dict)  # FY-end quarter -> flow totals

    @property
    def periods(self) -> list[str]:
        return sorted(self.quarters, key=parse_period)

    @property
    def months(self) -> list[str]:
        return sorted(self.monthly_revenue)

    @property
    def label(self) -> str:
        return f"{self.name}({self.ticker})" if self.name else self.ticker

    def get(self, period: str, name: str) -> float | None:
        return self.quarters.get(period, {}).get(name)

    @property
    def last_close(self) -> tuple[str, float] | None:
        return self.prices[-1] if self.prices else None

    def fill_derived(self, par_value: float | None = None) -> None:
        """Fill fields that can be derived from others when the source omitted them.

        ``par_value``: derive share count from share capital (Taiwan: NT$10 per share).
        """
        for row in [*self.quarters.values(), *self.annual.values()]:
            rev, cogs, gp = row.get("revenue"), row.get("cogs"), row.get("gross_profit")
            if gp is None and rev is not None and cogs is not None:
                row["gross_profit"] = rev - cogs
            elif cogs is None and rev is not None and gp is not None:
                row["cogs"] = rev - gp
            if row.get("net_income_parent") is None and row.get("net_income") is not None:
                row["net_income_parent"] = row["net_income"]
            if row.get("equity_parent") is None and row.get("equity") is not None:
                row["equity_parent"] = row["equity"]
            if row.get("equity") is None and row.get("total_assets") is not None and row.get("total_liabilities") is not None:
                row["equity"] = row["total_assets"] - row["total_liabilities"]
            if row.get("non_operating") is None and row.get("pretax_income") is not None and row.get("operating_income") is not None:
                row["non_operating"] = row["pretax_income"] - row["operating_income"]
            if row.get("shares") is None and par_value and row.get("share_capital"):
                row["shares"] = row["share_capital"] / par_value
            if row.get("eps") is None and row.get("net_income_parent") is not None and row.get("shares"):
                row["eps"] = row["net_income_parent"] / row["shares"]


def _write_periods(path: Path, rows: dict[str, dict[str, float | None]], fields) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["period", *fields])
        for p in sorted(rows, key=parse_period):
            row = rows[p]
            w.writerow([p, *("" if row.get(c) is None else _fmt_num(row[c]) for c in fields)])


def _read_periods(path: Path, fields) -> dict[str, dict[str, float | None]]:
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            y, q = parse_period(row["period"])
            out[fmt_period(y, q)] = {k: _num(row.get(k)) for k in fields}
    return out


def save_company(data: CompanyData, folder: str | Path) -> Path:
    folder = Path(folder) / data.ticker
    folder.mkdir(parents=True, exist_ok=True)
    _write_periods(folder / "quarterly.csv", data.quarters, FIELDS)
    if data.annual:
        _write_periods(folder / "annual.csv", data.annual, [*INCOME_FIELDS, *CASHFLOW_FIELDS])
    if data.monthly_revenue:
        with open(folder / "monthly_revenue.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["month", "revenue"])
            for m in data.months:
                w.writerow([m, _fmt_num(data.monthly_revenue[m])])
    if data.prices:
        with open(folder / "prices.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            w.writerows((d, _fmt_num(c)) for d, c in data.prices)
    meta = {"ticker": data.ticker, "name": data.name, "currency": data.currency}
    if data.note:
        meta["note"] = data.note
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.6g}"


def load_company(ticker: str, folder: str | Path, name: str = "", par_value: float | None = None) -> CompanyData:
    folder = Path(folder) / ticker
    if not (folder / "quarterly.csv").exists():
        raise FileNotFoundError(f"no financial data for {ticker} in {folder}; run `fetch` first")
    meta = {}
    if (folder / "meta.json").exists():
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    data = CompanyData(
        ticker=ticker, name=name or meta.get("name", ""), note=meta.get("note", ""), currency=meta.get("currency", "TWD")
    )

    data.quarters = _read_periods(folder / "quarterly.csv", FIELDS)
    if (folder / "annual.csv").exists():
        data.annual = _read_periods(folder / "annual.csv", [*INCOME_FIELDS, *CASHFLOW_FIELDS])
    if (folder / "monthly_revenue.csv").exists():
        with open(folder / "monthly_revenue.csv", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                v = _num(row.get("revenue"))
                if v is not None:
                    data.monthly_revenue[row["month"].strip()[:7]] = v
    if (folder / "prices.csv").exists():
        with open(folder / "prices.csv", newline="", encoding="utf-8-sig") as f:
            data.prices = sorted(
                (row["date"].strip(), c) for row in csv.DictReader(f) if (c := _num(row.get("close"))) is not None
            )
    data.fill_derived(par_value)
    return data

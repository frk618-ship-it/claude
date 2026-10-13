"""Download Taiwan-listed company financials from the FinMind open-data API.

FinMind (https://finmindtrade.com) republishes MOPS financial statements, monthly
revenue and daily prices for TWSE / TPEx stocks. A free token (env FINMIND_TOKEN)
raises the rate limit but is not required.

FinMind returns long-format rows: {"date": "2025-06-30", "type": "Revenue",
"value": ..., "origin_name": "營業收入合計"}. ``FIELD_MAP`` lists, per field, the
``type`` codes and Chinese ``origin_name`` labels we accept (first match wins);
extend it in config (``finmind_field_map``) if a company files under other labels.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Callable

from .models import CompanyData, parse_period, period_from_date, shift_period

log = logging.getLogger(__name__)

API_URL = "https://api.finmindtrade.com/api/v4/data"

DATASETS = {
    "income": "TaiwanStockFinancialStatements",
    "balance": "TaiwanStockBalanceSheet",
    "cashflow": "TaiwanStockCashFlowsStatement",
    "monthly": "TaiwanStockMonthRevenue",
    "price": "TaiwanStockPrice",
    "info": "TaiwanStockInfo",
}

# field -> (statement, [type codes / origin_name labels], sign)
# sign = -1 flips cash outflows (reported negative) into positive spend amounts.
FIELD_MAP: dict[str, tuple[str, list[str], int]] = {
    "revenue": ("income", ["Revenue", "營業收入合計", "營業收入"], 1),
    "cogs": ("income", ["CostOfGoodsSold", "營業成本合計", "營業成本"], 1),
    "gross_profit": ("income", ["GrossProfit", "營業毛利（毛損）", "營業毛利(毛損)", "營業毛利"], 1),
    "operating_expenses": ("income", ["OperatingExpenses", "營業費用合計", "營業費用"], 1),
    "operating_income": ("income", ["OperatingIncome", "營業利益（損失）", "營業利益(損失)", "營業利益"], 1),
    "non_operating": ("income", ["TotalNonoperatingIncomeAndExpense", "營業外收入及支出合計"], 1),
    "pretax_income": ("income", ["PreTaxIncome", "稅前淨利（淨損）", "繼續營業單位稅前淨利（淨損）"], 1),
    "income_tax": ("income", ["TAX", "IncomeTaxExpense", "所得稅費用（利益）合計", "所得稅費用（利益）"], 1),
    "net_income": ("income", ["IncomeAfterTaxes", "NetIncome", "本期淨利（淨損）"], 1),
    "net_income_parent": ("income", ["EquityAttributableToOwnersOfParent", "母公司業主（淨利／損）", "淨利（淨損）歸屬於母公司業主"], 1),
    "eps": ("income", ["EPS", "基本每股盈餘（元）", "基本每股盈餘"], 1),
    "cash": ("balance", ["CashAndCashEquivalents", "現金及約當現金"], 1),
    "receivables": ("balance", ["AccountsReceivableNet", "應收帳款淨額"], 1),
    "inventory": ("balance", ["Inventories", "存貨"], 1),
    "current_assets": ("balance", ["CurrentAssets", "流動資產合計"], 1),
    "ppe": ("balance", ["PropertyPlantAndEquipment", "不動產、廠房及設備"], 1),
    "total_assets": ("balance", ["TotalAssets", "資產總額", "資產總計"], 1),
    "payables": ("balance", ["AccountsPayable", "應付帳款"], 1),
    "short_term_debt": ("balance", ["ShorttermBorrowings", "短期借款"], 1),
    "long_term_debt": ("balance", ["LongtermBorrowings", "長期借款"], 1),
    "current_liabilities": ("balance", ["CurrentLiabilities", "流動負債合計"], 1),
    "total_liabilities": ("balance", ["Liabilities", "負債總額", "負債總計"], 1),
    "share_capital": ("balance", ["OrdinaryShare", "CapitalStock", "普通股股本", "股本合計"], 1),
    "retained_earnings": ("balance", ["RetainedEarnings", "保留盈餘合計"], 1),
    "equity": ("balance", ["Equity", "權益總額", "權益總計"], 1),
    "equity_parent": ("balance", ["EquityAttributableToOwnersOfParent", "歸屬於母公司業主之權益合計"], 1),
    "operating_cf": ("cashflow", ["CashFlowsFromOperatingActivities", "NetCashInflowFromOperatingActivities", "營業活動之淨現金流入（流出）"], 1),
    "capex": ("cashflow", ["PropertyAndPlantAndEquipment", "AcquisitionOfPropertyPlantAndEquipment", "取得不動產、廠房及設備"], -1),
    "depreciation": ("cashflow", ["Depreciation", "DepreciationExpense", "折舊費用"], 1),
    "interest_expense": ("cashflow", ["InterestExpense", "利息費用"], 1),
    "dividends_paid": ("cashflow", ["CashDividendsPaid", "發放現金股利"], -1),
}

# Taiwan cash-flow statements are cumulative year-to-date; FinMind's income statement
# rows are already single-quarter. Override in config if a dataset behaves differently.
DEFAULT_YTD = {"income": False, "balance": False, "cashflow": True}

Fetcher = Callable[[str, dict], list[dict]]


def http_fetch(dataset: str, params: dict) -> list[dict]:
    query = urllib.parse.urlencode({"dataset": dataset, **params})
    req = urllib.request.Request(f"{API_URL}?{query}", headers={"User-Agent": "fin-analyzer/0.1"})
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") not in (200, None):
        raise RuntimeError(f"FinMind {dataset}: {payload.get('msg')}")
    return payload.get("data", [])


def _pick(rows_by_key: dict[str, float], candidates: list[str]) -> float | None:
    for c in candidates:
        if c in rows_by_key:
            return rows_by_key[c]
    return None


def statements_to_quarters(
    rows: dict[str, list[dict]],
    field_map: dict[str, tuple[str, list[str], int]] = FIELD_MAP,
    ytd: dict[str, bool] = DEFAULT_YTD,
) -> dict[str, dict[str, float | None]]:
    """Map FinMind long-format statement rows into {period: {field: value}}."""
    # statement -> period -> {type or origin_name: value}
    by_stmt: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for stmt, data in rows.items():
        for r in data:
            key_type = str(r.get("type", ""))
            if key_type.endswith("_per"):  # common-size percentage rows
                continue
            try:
                value = float(r["value"])
            except (KeyError, TypeError, ValueError):
                continue
            bucket = by_stmt[stmt][period_from_date(r["date"])]
            bucket.setdefault(key_type, value)
            if r.get("origin_name"):
                bucket.setdefault(str(r["origin_name"]), value)

    periods = sorted({p for s in by_stmt.values() for p in s}, key=parse_period)
    quarters: dict[str, dict[str, float | None]] = {p: {} for p in periods}
    for fld, (stmt, candidates, sign) in field_map.items():
        raw = {p: _pick(by_stmt[stmt].get(p, {}), candidates) for p in periods}
        for p in periods:
            v = raw[p]
            if v is not None and ytd.get(stmt, False):
                y, q = parse_period(p)
                if q > 1:
                    prev = raw.get(shift_period(p, -1))
                    v = v - prev if prev is not None else None  # can't de-cumulate without prior quarter
            quarters[p][fld] = None if v is None else v * sign
    return quarters


def fetch_company(
    ticker: str,
    start_date: str,
    fetch: Fetcher = http_fetch,
    delay: float = 0.5,
    field_map: dict | None = None,
    ytd: dict | None = None,
    name: str = "",
) -> CompanyData:
    params = {"data_id": ticker, "start_date": start_date}
    statements = {}
    for stmt in ("income", "balance", "cashflow"):
        statements[stmt] = fetch(DATASETS[stmt], params)
        time.sleep(delay)
    if not any(statements.values()):
        raise RuntimeError(f"FinMind returned no statements for {ticker}")

    fmap = {**FIELD_MAP, **(field_map or {})}
    data = CompanyData(ticker=ticker, name=name)
    data.quarters = statements_to_quarters(statements, fmap, {**DEFAULT_YTD, **(ytd or {})})

    try:
        for r in fetch(DATASETS["monthly"], params):
            y, m = int(r.get("revenue_year") or 0), int(r.get("revenue_month") or 0)
            if y and m:
                data.monthly_revenue[f"{y:04d}-{m:02d}"] = float(r["revenue"])
        time.sleep(delay)
        # Prices: last ~400 days are enough for valuation.
        y = int(max(data.periods)[:4]) if data.periods else int(start_date[:4])
        price_rows = fetch(DATASETS["price"], {"data_id": ticker, "start_date": f"{y - 1}-01-01"})
        data.prices = sorted((r["date"], float(r["close"])) for r in price_rows if r.get("close"))
        if not name:
            info = fetch(DATASETS["info"], {"data_id": ticker})
            data.name = next((r.get("stock_name", "") for r in info if r.get("stock_id") == ticker), "")
    except Exception as exc:  # monthly revenue / prices are optional extras
        log.warning("%s: optional dataset failed: %s", ticker, exc)

    data.fill_derived()
    return data

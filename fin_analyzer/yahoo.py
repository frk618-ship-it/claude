"""Download Japanese / US / Chinese-listed company financials via Yahoo Finance (yfinance).

Used for markets FinMind does not cover, e.g. JX金屬 (5016.T), 東曹 (4042.T),
Solstice (SOLS), 江豐電子 (300666.SZ), 有研新材 (600206.SS). Yahoo's quarterly
statements are single-quarter amounts and usually cover the last 4-6 quarters.

Yahoo's quarterly data has gaps: Chinese A-shares often lack one quarter (e.g. Q3),
and Japanese filers may have no quarterly income / cash-flow statements at all. The
annual statements are therefore fetched too: a single missing quarter is recovered as
fiscal-year total minus the other three quarters, and the fiscal-year totals are kept
(``CompanyData.annual``) so TTM metrics can fall back to them.

``yfinance`` is imported lazily so the rest of the tool works without it.
"""

from __future__ import annotations

import logging
import math

from .models import CompanyData, parse_period, period_from_date, shift_period

log = logging.getLogger(__name__)

# field -> (statement, [Yahoo row labels], sign)
YAHOO_FIELD_MAP: dict[str, tuple[str, list[str], int]] = {
    "revenue": ("income", ["Total Revenue", "Operating Revenue"], 1),
    "cogs": ("income", ["Cost Of Revenue", "Reconciled Cost Of Revenue"], 1),
    "gross_profit": ("income", ["Gross Profit"], 1),
    "operating_expenses": ("income", ["Operating Expense"], 1),
    "operating_income": ("income", ["Operating Income", "Total Operating Income As Reported"], 1),
    "pretax_income": ("income", ["Pretax Income"], 1),
    "income_tax": ("income", ["Tax Provision"], 1),
    "net_income": ("income", ["Net Income Including Noncontrolling Interests", "Net Income"], 1),
    "net_income_parent": ("income", ["Net Income Common Stockholders", "Net Income"], 1),
    "eps": ("income", ["Basic EPS", "Diluted EPS"], 1),
    "interest_expense": ("income", ["Interest Expense", "Interest Expense Non Operating"], 1),
    "depreciation": ("cashflow", ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Depreciation"], 1),
    "cash": ("balance", ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"], 1),
    "receivables": ("balance", ["Accounts Receivable", "Receivables"], 1),
    "inventory": ("balance", ["Inventory"], 1),
    "current_assets": ("balance", ["Current Assets"], 1),
    "ppe": ("balance", ["Net PPE"], 1),
    "total_assets": ("balance", ["Total Assets"], 1),
    "payables": ("balance", ["Accounts Payable", "Payables"], 1),
    "short_term_debt": ("balance", ["Current Debt", "Current Debt And Capital Lease Obligation"], 1),
    "long_term_debt": ("balance", ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 1),
    "current_liabilities": ("balance", ["Current Liabilities"], 1),
    "total_liabilities": ("balance", ["Total Liabilities Net Minority Interest"], 1),
    "share_capital": ("balance", ["Common Stock", "Capital Stock"], 1),
    "shares": ("balance", ["Ordinary Shares Number", "Share Issued"], 1),
    "retained_earnings": ("balance", ["Retained Earnings"], 1),
    "equity": ("balance", ["Total Equity Gross Minority Interest"], 1),
    "equity_parent": ("balance", ["Stockholders Equity", "Common Stock Equity"], 1),
    # Chinese A-shares report the direct method; Yahoo labels it "...Operating Activities Direct".
    "operating_cf": ("cashflow", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
                                  "Cash Flowsfromusedin Operating Activities Direct"], 1),
    "capex": ("cashflow", ["Capital Expenditure", "Purchase Of PPE"], -1),
    "dividends_paid": ("cashflow", ["Cash Dividends Paid", "Common Stock Dividend Paid"], -1),
}


def _clean(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def frames_to_quarters(
    frames: dict[str, dict], field_map: dict[str, tuple[str, list[str], int]] = YAHOO_FIELD_MAP
) -> dict[str, dict[str, float | None]]:
    """``frames``: statement -> {column date: {row label: value}} (``DataFrame.to_dict()``)."""
    by_stmt: dict[str, dict[str, dict[str, float]]] = {}
    for stmt, cols in frames.items():
        for date, rows in (cols or {}).items():
            p = period_from_date(str(date)[:10])
            bucket = by_stmt.setdefault(stmt, {}).setdefault(p, {})
            for label, v in rows.items():
                if (f := _clean(v)) is not None:
                    bucket.setdefault(str(label), f)
    periods = sorted({p for s in by_stmt.values() for p in s})
    quarters: dict[str, dict[str, float | None]] = {}
    for p in periods:
        row: dict[str, float | None] = {}
        for fld, (stmt, labels, sign) in field_map.items():
            bucket = by_stmt.get(stmt, {}).get(p, {})
            v = next((bucket[l] for l in labels if l in bucket), None)
            row[fld] = None if v is None else v * sign
        quarters[p] = row
    return quarters


# Fiscal-year flow totals kept from the annual statements.
ANNUAL_FIELDS = {k: v for k, v in YAHOO_FIELD_MAP.items() if v[0] in ("income", "cashflow")}


def fill_quarter_from_annual(
    quarters: dict[str, dict[str, float | None]], annual: dict[str, dict[str, float | None]]
) -> list[str]:
    """Fill a quarter's missing flow field when the other three quarters of that fiscal year
    are known: Q = FY - (other three). Returns the periods that received derived values."""
    filled: set[str] = set()
    for fy_end, totals in annual.items():
        qs = [shift_period(fy_end, -i) for i in range(4)]
        for fld, total in totals.items():
            if fld == "eps" or fld not in ANNUAL_FIELDS or total is None:  # EPS is not additive
                continue
            vals = {q: quarters.get(q, {}).get(fld) for q in qs}
            missing = [q for q, v in vals.items() if v is None]
            if len(missing) != 1:
                continue
            q = missing[0]
            row = quarters.setdefault(q, {f: None for f in YAHOO_FIELD_MAP})
            row[fld] = total - sum(v for v in vals.values() if v is not None)
            filled.add(q)
    return sorted(filled, key=parse_period)


def adjust_for_splits(rows: dict[str, dict[str, float | None]], splits: dict[str, float]) -> list[str]:
    """Restate share counts and EPS to today's share basis. Yahoo's price history is already
    split-adjusted, but the statements keep the share count of their period, so a split
    after the period end would distort P/E, P/B and market cap. ``splits``: date -> ratio."""
    adjusted = []
    for p, row in rows.items():
        y, q = parse_period(p)
        period_end = f"{y:04d}-{q * 3:02d}-31"  # string compare: later than any day of the quarter's last month
        factor = 1.0
        for date, ratio in splits.items():
            if str(date)[:10] > period_end and ratio and ratio > 0:
                factor *= ratio
        if factor != 1.0:
            if row.get("shares") is not None:
                row["shares"] *= factor
            if row.get("eps") is not None:
                row["eps"] /= factor
            adjusted.append(p)
    return adjusted


def _frame(t, attr: str) -> dict:
    df = getattr(t, attr, None)
    return df.to_dict() if df is not None and hasattr(df, "to_dict") else {}


def fetch_yahoo(ticker: str, name: str = "", currency: str = "", ticker_factory=None) -> CompanyData:
    """Fetch quarterly statements and ~1 year of prices for a Yahoo Finance symbol."""
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker
    t = ticker_factory(ticker)
    frames = {
        "income": t.quarterly_income_stmt.to_dict(),
        "balance": t.quarterly_balance_sheet.to_dict(),
        "cashflow": t.quarterly_cashflow.to_dict(),
    }
    quarters = frames_to_quarters(frames)
    if not quarters:
        raise RuntimeError(f"Yahoo Finance returned no statements for {ticker}")
    annual = {
        p: row
        for p, row in frames_to_quarters({"income": _frame(t, "income_stmt"), "cashflow": _frame(t, "cashflow")}, ANNUAL_FIELDS).items()
        if any(v is not None for v in row.values())
    }
    filled = fill_quarter_from_annual(quarters, annual)
    splits = {}
    try:
        raw_splits = getattr(t, "splits", None)
        splits = {str(k)[:10]: float(v) for k, v in (raw_splits.to_dict() if raw_splits is not None else {}).items()}
    except Exception as exc:  # split history is a best-effort extra
        log.warning("%s: split history unavailable: %s", ticker, exc)
    if adjust_for_splits(quarters, splits) + adjust_for_splits(annual, splits):
        log.info("%s: shares / EPS restated for stock splits %s", ticker, splits)
    info: dict = {}
    try:
        info = t.info or {}
    except Exception as exc:  # info is a best-effort extra
        log.warning("%s: info unavailable: %s", ticker, exc)
    data = CompanyData(
        ticker=ticker,
        name=name or info.get("shortName", ""),
        currency=currency or info.get("financialCurrency") or info.get("currency") or "",
        quarters=quarters,
        annual=annual,
    )
    notes = []
    if filled:
        notes.append(f"{'、'.join(filled)} 部分損益/現金流量為年度合計減其餘三季推得")
    last_fy = max(annual, key=parse_period) if annual else None
    if last_fy and any(quarters.get(shift_period(last_fy, -i), {}).get("revenue") is None for i in range(4)):
        notes.append(f"Yahoo Finance 季度損益不完整,近四季指標改用截至 {last_fy} 的年度財報")
    if notes:
        data.note = f"{data.label}:" + ";".join(notes)
    hist = t.history(period="1y", auto_adjust=False)
    data.prices = sorted((str(idx)[:10], float(c)) for idx, c in hist["Close"].items() if _clean(c) is not None)
    data.fill_derived()
    return data


def fetch_fx(currencies: list[str], base: str = "TWD", ticker_factory=None) -> dict[str, float]:
    """Latest close of <CUR><BASE>=X for each currency (1 unit of CUR in BASE)."""
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker
    rates = {}
    for cur in sorted(set(currencies) - {base}):
        hist = ticker_factory(f"{cur}{base}=X").history(period="5d")
        closes = [float(c) for _, c in hist["Close"].items() if _clean(c) is not None]
        if closes:
            rates[cur] = closes[-1]
    return rates

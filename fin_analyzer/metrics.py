"""Financial ratios: per-quarter margins & growth, TTM returns, efficiency, cash quality,
DuPont decomposition, Piotroski F-score, Altman Z''-score and valuation."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CompanyData, shift_period

DAYS_PER_QUARTER = 365 / 4


def div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def growth(cur: float | None, prev: float | None) -> float | None:
    """Growth rate; undefined when the base is zero or negative (e.g. loss -> profit)."""
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1


def avg(*values: float | None) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if len(vals) == len(values) and vals else None


def _sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _sum(*values: float | None) -> float | None:
    return None if any(v is None for v in values) else sum(values)  # type: ignore[arg-type]


# Metrics expressed in NT$ (scaled to the report unit); everything else is a ratio / days / per-share.
AMOUNT_KEYS = {
    "revenue", "revenue_ttm", "net_income_ttm", "operating_cf_ttm", "capex_ttm", "fcf_ttm",
    "ebit_ttm", "ebitda_ttm", "net_debt", "market_cap", "ytd",
}

# metric key -> (中文名稱, format) ; format: pct / x / days / num / amt
METRIC_LABELS: dict[str, tuple[str, str]] = {
    "revenue": ("營收", "amt"),
    "revenue_yoy": ("營收年增率", "pct"),
    "revenue_qoq": ("營收季增率", "pct"),
    "gross_margin": ("毛利率", "pct"),
    "operating_margin": ("營業利益率", "pct"),
    "net_margin": ("稅後淨利率(母)", "pct"),
    "opex_ratio": ("營業費用率", "pct"),
    "non_operating_share": ("業外占稅前比重", "pct"),
    "tax_rate": ("有效稅率", "pct"),
    "eps": ("EPS(元)", "num"),
    "eps_yoy": ("EPS 年增率", "pct"),
    "eps_ttm": ("近四季 EPS(元)", "num"),
    "roe_ttm": ("ROE(近四季)", "pct"),
    "roa_ttm": ("ROA(近四季)", "pct"),
    "asset_turnover_ttm": ("總資產週轉率(次)", "x"),
    "equity_multiplier": ("權益乘數(倍)", "x"),
    "current_ratio": ("流動比率", "x"),
    "quick_ratio": ("速動比率", "x"),
    "debt_ratio": ("負債比率", "pct"),
    "net_debt_to_equity": ("淨負債/權益", "pct"),
    "interest_coverage_ttm": ("利息保障倍數(近四季)", "x"),
    "dso": ("應收帳款天數", "days"),
    "dio": ("存貨天數", "days"),
    "dpo": ("應付帳款天數", "days"),
    "ccc": ("現金轉換循環", "days"),
    "revenue_ttm": ("近四季營收", "amt"),
    "revenue_ttm_yoy": ("近四季營收年增", "pct"),
    "gross_margin_ttm": ("毛利率(近四季)", "pct"),
    "operating_margin_ttm": ("營業利益率(近四季)", "pct"),
    "net_margin_ttm": ("淨利率(近四季)", "pct"),
    "fcf_margin_ttm": ("自由現金流/營收", "pct"),
    "operating_cf_ttm": ("營業現金流(近四季)", "amt"),
    "fcf_ttm": ("自由現金流(近四季)", "amt"),
    "ocf_to_net_income_ttm": ("營業現金流/淨利", "x"),
    "capex_to_revenue_ttm": ("資本支出/營收", "pct"),
    "capex_to_depreciation_ttm": ("資本支出/折舊", "x"),
    "bvps": ("每股淨值(元)", "num"),
}


def quarter_metrics(data: CompanyData) -> dict[str, dict[str, float | None]]:
    """All per-quarter metrics, keyed by period."""
    out: dict[str, dict[str, float | None]] = {}
    g = data.get
    for p in data.periods:
        prev_q, prev_y = shift_period(p, -1), shift_period(p, -4)
        ttm = [shift_period(p, -i) for i in range(4)]
        ttm_prev = [shift_period(p, -4 - i) for i in range(4)]

        def ttm_sum(name: str, periods=ttm) -> float | None:
            v = _sum(*(g(q, name) for q in periods))
            if v is None:  # incomplete quarters: use the fiscal year ending in periods[0], if any
                v = data.annual.get(periods[0], {}).get(name)
            return v

        def turnover_days(balance: str, flow: str) -> float | None:
            d = _days(avg(g(p, balance), g(prev_q, balance)), g(p, flow))
            fy = data.annual.get(p, {}).get(flow)
            if d is None and g(p, flow) is None and fy is not None:
                bal = avg(g(p, balance), g(prev_y, balance)) or g(p, balance)
                d = _days(bal, fy)
                d = None if d is None else d * 4  # annual flow: 365 days instead of a quarter
            return d

        rev, cogs = g(p, "revenue"), g(p, "cogs")
        pretax = g(p, "pretax_income")
        ni_parent = g(p, "net_income_parent")
        m: dict[str, float | None] = {
            "revenue": rev,
            "revenue_yoy": growth(rev, g(prev_y, "revenue")),
            "revenue_qoq": growth(rev, g(prev_q, "revenue")),
            "gross_margin": div(g(p, "gross_profit"), rev),
            "operating_margin": div(g(p, "operating_income"), rev),
            "net_margin": div(ni_parent, rev),
            "opex_ratio": div(g(p, "operating_expenses"), rev),
            "non_operating_share": div(g(p, "non_operating"), abs(pretax)) if pretax else None,
            "tax_rate": div(g(p, "income_tax"), pretax) if pretax and pretax > 0 else None,
            "eps": g(p, "eps"),
            "eps_yoy": growth(g(p, "eps"), g(prev_y, "eps")),
            "eps_ttm": ttm_sum("eps"),
        }
        if m["eps_ttm"] is None:  # a quarter without EPS: TTM parent net income / period-end shares
            m["eps_ttm"] = div(ttm_sum("net_income_parent"), g(p, "shares"))

        # TTM returns & DuPont (average of balances 4 quarters apart).
        rev_ttm, ni_ttm = ttm_sum("revenue"), ttm_sum("net_income_parent")
        avg_assets = avg(g(p, "total_assets"), g(prev_y, "total_assets"))
        avg_equity = avg(g(p, "equity_parent"), g(prev_y, "equity_parent"))
        m["roe_ttm"] = div(ni_ttm, avg_equity) if avg_equity and avg_equity > 0 else None
        m["roa_ttm"] = div(ni_ttm, avg_assets)
        m["asset_turnover_ttm"] = div(rev_ttm, avg_assets)
        m["equity_multiplier"] = div(avg_assets, avg_equity)
        m["net_margin_ttm"] = div(ni_ttm, rev_ttm)
        m["gross_margin_ttm"] = div(ttm_sum("gross_profit"), rev_ttm)
        m["operating_margin_ttm"] = div(ttm_sum("operating_income"), rev_ttm)

        # Liquidity & leverage (period-end).
        ca, cl, inv = g(p, "current_assets"), g(p, "current_liabilities"), g(p, "inventory")
        debt = (g(p, "short_term_debt") or 0.0) + (g(p, "long_term_debt") or 0.0)
        m["current_ratio"] = div(ca, cl)
        m["quick_ratio"] = div(_sub(ca, inv), cl)
        m["debt_ratio"] = div(g(p, "total_liabilities"), g(p, "total_assets"))
        m["net_debt"] = _sub(debt, g(p, "cash"))
        m["net_debt_to_equity"] = div(m["net_debt"], g(p, "equity"))
        ebit_ttm = _sum(ttm_sum("pretax_income"), ttm_sum("interest_expense"))
        m["ebit_ttm"] = ebit_ttm
        int_ttm = ttm_sum("interest_expense")
        m["interest_coverage_ttm"] = div(ebit_ttm, int_ttm) if int_ttm and int_ttm > 0 else None

        # Working-capital efficiency (quarter flows, average balances).
        m["dso"] = turnover_days("receivables", "revenue")
        m["dio"] = turnover_days("inventory", "cogs")
        m["dpo"] = turnover_days("payables", "cogs")
        m["ccc"] = _sum(m["dso"], m["dio"], -m["dpo"] if m["dpo"] is not None else None)
        if m["ccc"] is None and m["dso"] is not None and m["dio"] is not None and g(p, "payables") is None:
            m["ccc"] = m["dso"] + m["dio"]  # no payables data: DSO + DIO

        # Cash-flow quality.
        ocf_ttm, capex_ttm = ttm_sum("operating_cf"), ttm_sum("capex")
        m["operating_cf_ttm"] = ocf_ttm
        m["capex_ttm"] = capex_ttm
        m["fcf_ttm"] = _sub(ocf_ttm, capex_ttm)
        m["ocf_to_net_income_ttm"] = div(ocf_ttm, ni_ttm) if ni_ttm and ni_ttm > 0 else None
        m["capex_to_revenue_ttm"] = div(capex_ttm, rev_ttm)
        m["fcf_margin_ttm"] = div(m["fcf_ttm"], rev_ttm)
        m["capex_to_depreciation_ttm"] = div(capex_ttm, ttm_sum("depreciation"))
        m["ebitda_ttm"] = _sum(ttm_sum("operating_income"), ttm_sum("depreciation"))
        m["revenue_ttm"] = rev_ttm
        m["net_income_ttm"] = ni_ttm
        m["revenue_ttm_yoy"] = growth(rev_ttm, ttm_sum("revenue", ttm_prev))
        m["revenue_yoy_quarters"] = 4.0 if m["revenue_ttm_yoy"] is not None else None
        if m["revenue_ttm_yoy"] is None:
            # Short history (e.g. Yahoo's 5-6 quarters): compare the recent quarters that have a
            # same-quarter-last-year figure, if at least two do. The dashboard labels it "近 N 季".
            pairs = []
            for q in ttm:  # most recent first; stop at the first gap
                cur, ago = g(q, "revenue"), g(shift_period(q, -4), "revenue")
                if cur is None or ago is None:
                    break
                pairs.append((cur, ago))
            if len(pairs) >= 2:
                m["revenue_ttm_yoy"] = growth(sum(a for a, _ in pairs), sum(b for _, b in pairs))
                m["revenue_yoy_quarters"] = float(len(pairs))
        out[p] = m
    return out


def _days(balance: float | None, flow: float | None) -> float | None:
    v = div(balance, flow)
    return None if v is None or flow is None or flow <= 0 else v * DAYS_PER_QUARTER


@dataclass
class ScoreResult:
    score: int | None
    max_score: int
    details: list[tuple[str, bool | None]] = field(default_factory=list)  # (criterion, passed)
    zone: str = ""
    value: float | None = None


def piotroski(data: CompanyData, qm: dict[str, dict[str, float | None]], period: str) -> ScoreResult:
    """Piotroski F-score on TTM figures vs the TTM ending 4 quarters earlier."""
    g = data.get
    prev = shift_period(period, -4)
    cur_m, prev_m = qm.get(period, {}), qm.get(prev, {})

    def gm_ttm(m_period: str) -> float | None:
        qs = [shift_period(m_period, -i) for i in range(4)]
        return div(_sum(*(g(q, "gross_profit") for q in qs)), _sum(*(g(q, "revenue") for q in qs)))

    def lev(p: str) -> float | None:
        return div(g(p, "long_term_debt") or 0.0, g(p, "total_assets"))

    roa, roa_prev = cur_m.get("roa_ttm"), prev_m.get("roa_ttm")
    ocf, ni = cur_m.get("operating_cf_ttm"), cur_m.get("net_income_ttm")

    def cmp(a, b, fn):
        return None if a is None or b is None else bool(fn(a, b))

    checks = [
        ("ROA 為正", None if roa is None else roa > 0),
        ("營業現金流為正", None if ocf is None else ocf > 0),
        ("ROA 較去年提升", cmp(roa, roa_prev, lambda a, b: a > b)),
        ("營業現金流 > 淨利(盈餘品質)", cmp(ocf, ni, lambda a, b: a > b)),
        ("長期負債比重下降", cmp(lev(period), lev(prev), lambda a, b: a <= b)),
        ("流動比率提升", cmp(cur_m.get("current_ratio"), prev_m.get("current_ratio"), lambda a, b: a > b)),
        ("未增發股本", cmp(g(period, "share_capital"), g(prev, "share_capital"), lambda a, b: a <= b * 1.001)),
        ("毛利率提升", cmp(gm_ttm(period), gm_ttm(prev), lambda a, b: a > b)),
        ("資產週轉率提升", cmp(cur_m.get("asset_turnover_ttm"), prev_m.get("asset_turnover_ttm"), lambda a, b: a > b)),
    ]
    known = [c for _, c in checks if c is not None]
    score = sum(known) if len(known) >= 7 else None  # too many gaps -> no score
    return ScoreResult(score=score, max_score=9, details=checks)


def altman_z2(data: CompanyData, qm: dict[str, dict[str, float | None]], period: str) -> ScoreResult:
    """Altman Z''-score (emerging-market / book-value version, no market cap needed)."""
    g = data.get
    ta = g(period, "total_assets")
    x1 = div(_sub(g(period, "current_assets"), g(period, "current_liabilities")), ta)
    x2 = div(g(period, "retained_earnings"), ta)
    x3 = div(qm.get(period, {}).get("ebit_ttm"), ta)
    x4 = div(g(period, "equity"), g(period, "total_liabilities"))
    parts = [("X1 營運資金/總資產", x1), ("X2 保留盈餘/總資產", x2), ("X3 EBIT/總資產", x3), ("X4 權益/負債", x4)]
    if any(v is None for _, v in parts):
        return ScoreResult(score=None, max_score=0, details=[(n, None) for n, _ in parts])
    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4  # type: ignore[operator]
    zone = "安全區" if z > 2.6 else "灰色地帶" if z >= 1.1 else "財務危機區"
    return ScoreResult(score=None, max_score=0, details=[(f"{n} = {v:.3f}", True) for n, v in parts], zone=zone, value=z)


def valuation(
    data: CompanyData, qm: dict[str, dict[str, float | None]], period: str, par_value: float | None = 10.0
) -> dict[str, float | None]:
    """Price-based multiples using the latest close. Share count comes from the ``shares``
    field, else share capital / par value (Taiwan: NT$10 per share)."""
    close = data.last_close
    m = qm.get(period, {})
    shares = data.get(period, "shares") or (div(data.get(period, "share_capital"), par_value) if par_value else None)
    bvps = div(data.get(period, "equity_parent"), shares)
    out: dict[str, float | None] = {"shares": shares, "bvps": bvps, "close": None, "close_date": None}
    if not close or shares is None:
        return out
    date, price = close
    mcap = price * shares
    eps_ttm = m.get("eps_ttm")
    debt = (data.get(period, "short_term_debt") or 0.0) + (data.get(period, "long_term_debt") or 0.0)
    ev = _sub(mcap + debt, data.get(period, "cash"))
    ebitda = m.get("ebitda_ttm")
    out.update(
        close=price,
        close_date=date,  # type: ignore[dict-item]
        market_cap=mcap,
        pe_ttm=div(price, eps_ttm) if eps_ttm and eps_ttm > 0 else None,
        pb=div(price, bvps) if bvps and bvps > 0 else None,
        ps_ttm=div(mcap, m.get("revenue_ttm")),
        ev_ebitda_ttm=div(ev, ebitda) if ebitda and ebitda > 0 else None,
        fcf_yield=div(m.get("fcf_ttm"), mcap),
    )
    return out

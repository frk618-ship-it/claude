"""Generate fictional demo financials (deterministic) for examples/demo_financials."""
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fin_analyzer.models import CompanyData, fmt_period, save_company  # noqa: E402

NOTE = "示意資料:虛構公司與數字,僅供展示工具功能,非任何真實公司財報。"

def build(ticker, name, base_q_rev, growth, gm, opex, inv_days, capex_ratio, seed, shares_cap, start_equity, price,
          currency="TWD", monthly=True):
    rnd = random.Random(seed)
    d = CompanyData(ticker=ticker, name=name, note=NOTE, currency=currency)
    periods = [(y, q) for y in range(2021, 2027) for q in range(1, 5)][:22]  # 2021Q1..2026Q2
    months = {}
    equity, re, cash, ltd = start_equity, start_equity * 0.35, start_equity * 0.25, start_equity * 0.15
    level = base_q_rev
    for i, (y, q) in enumerate(periods):
        g = growth[min(i * len(growth) // len(periods), len(growth) - 1)]
        level *= (1 + g) ** 0.25
        rev_q = level * (1 + 0.04 * math.sin(i * 1.3)) * rnd.uniform(0.97, 1.03)
        # monthly split
        w = [rnd.uniform(0.9, 1.1) for _ in range(3)]
        for k in range(3):
            months[f"{y:04d}-{(q - 1) * 3 + k + 1:02d}"] = round(rev_q * w[k] / sum(w))
        rev_q = sum(months[f"{y:04d}-{(q - 1) * 3 + k + 1:02d}"] for k in range(3))
        gm_q = gm[min(i * len(gm) // len(periods), len(gm) - 1)] + rnd.uniform(-0.01, 0.01)
        gp = rev_q * gm_q
        ox = rev_q * opex * rnd.uniform(0.95, 1.05)
        oi = gp - ox
        nonop = oi * rnd.uniform(-0.05, 0.25)
        interest = ltd * 0.02 / 4
        pretax = oi + nonop
        tax = max(pretax, 0) * 0.2
        ni = pretax - tax
        dep = rev_q * capex_ratio * 0.7
        capex = rev_q * capex_ratio * rnd.uniform(0.8, 1.3)
        cogs = rev_q - gp
        inv = cogs * inv_days[min(i * len(inv_days) // len(periods), len(inv_days) - 1)] / 91.25
        ar = rev_q * rnd.uniform(55, 70) / 91.25
        ap = cogs * 35 / 91.25
        ocf = ni + dep - (inv * 0.08 if i > 15 else 0) + rnd.uniform(-0.1, 0.1) * ni
        div = ni * 2.5 if q == 3 else 0
        equity += ni - div
        re += ni - div
        cash += ocf - capex - div
        ppe = start_equity * 0.6 + sum(1 for _ in range(i)) * 0
        ca = cash + ar + inv
        cl = ca * rnd.uniform(0.38, 0.55)
        tl = cl + ltd
        ta = equity + tl
        ppe = ta - ca
        d.quarters[fmt_period(y, q)] = dict(
            revenue=round(rev_q), cogs=round(cogs), gross_profit=round(gp), operating_expenses=round(ox),
            operating_income=round(oi), non_operating=round(nonop), pretax_income=round(pretax), income_tax=round(tax),
            net_income=round(ni), net_income_parent=round(ni * 0.98), eps=round(ni * 0.98 / (shares_cap / 10), 2),
            cash=round(cash), receivables=round(ar), inventory=round(inv), current_assets=round(ca), ppe=round(ppe),
            total_assets=round(ta), payables=round(ap), short_term_debt=round(ca * 0.15), long_term_debt=round(ltd),
            current_liabilities=round(cl), total_liabilities=round(tl), share_capital=shares_cap,
            shares=None if monthly else shares_cap / 10, retained_earnings=round(re), equity=round(equity), equity_parent=round(equity * 0.97),
            operating_cf=round(ocf), capex=round(capex), depreciation=round(dep), interest_expense=round(interest),
            dividends_paid=round(div),
        )
    # July–Sept 2026 monthly revenue beyond last quarter
    last = months["2026-06"]
    for k, m in enumerate(["2026-07", "2026-08", "2026-09"]):
        months[m] = round(last * (1.04 + 0.03 * k))
    d.monthly_revenue = months if monthly else {}
    d.prices = [("2026-10-08", price[0]), ("2026-10-09", price[1]), ("2026-10-12", price[2])]
    return d

OUT = ROOT / "examples" / "demo_financials"
COMPANIES = [
    # ticker, name, quarterly revenue, growth path, GM path, opex, inventory-days path, capex/rev, seed, share capital, equity, prices
    build("DEMO-A", "示範靶材(虛構)", 6.0e9, [0.05, 0.02, 0.18], [0.11, 0.095, 0.085, 0.10], 0.04, [55, 60, 70, 85], 0.02, 1, 5.6e9, 1.4e10, (88.0, 89.5, 91.2)),
    build("DEMO-B", "示範合金靶(虛構)", 4.0e8, [0.10, 0.06], [0.24, 0.22, 0.20], 0.12, [90, 110], 0.05, 2, 1.0e9, 2.0e9, (45.0, 44.2, 43.8)),
    build("DEMO-J", "示範金屬(虛構,日圓)", 1.6e11, [0.04, 0.09], [0.20, 0.23], 0.09, [100, 105], 0.06, 3, 9.0e10, 6.0e11, (160.0, 162.5, 165.0), "JPY", False),
    build("DEMO-U", "示範電子材料(虛構,美元)", 9.0e8, [0.06, 0.04], [0.30, 0.31], 0.15, [60, 62], 0.05, 4, 1.6e9, 3.5e9, (51.0, 52.2, 52.0), "USD", False),
    build("DEMO-C", "示範濺射靶材(虛構,人民幣)", 9.0e8, [0.25, 0.20], [0.29, 0.27, 0.26], 0.13, [150, 170], 0.18, 5, 2.6e9, 6.0e9, (68.0, 70.5, 69.9), "CNY", False),
]

if __name__ == "__main__":
    for c in COMPANIES:
        print(save_company(c, OUT))

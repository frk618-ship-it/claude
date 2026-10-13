"""Monthly revenue analysis — Taiwan-listed companies publish revenue by the 10th of
each month, the most timely operating signal between quarterly reports."""

from __future__ import annotations

from .metrics import div, growth
from .models import CompanyData, shift_month


def monthly_metrics(data: CompanyData) -> dict[str, dict[str, float | None]]:
    rev = data.monthly_revenue
    out: dict[str, dict[str, float | None]] = {}
    seen_max = 0.0
    for m in data.months:
        v = rev[m]
        year_months = [f"{m[:4]}-{i:02d}" for i in range(1, int(m[5:7]) + 1)]
        prev_year_months = [shift_month(x, -12) for x in year_months]
        ytd = sum(rev[x] for x in year_months) if all(x in rev for x in year_months) else None
        ytd_prev = sum(rev[x] for x in prev_year_months) if all(x in rev for x in prev_year_months) else None
        last3 = [shift_month(m, -i) for i in range(3)]
        last12 = [shift_month(m, -i) for i in range(12)]
        avg3 = sum(rev[x] for x in last3) / 3 if all(x in rev for x in last3) else None
        avg12 = sum(rev[x] for x in last12) / 12 if all(x in rev for x in last12) else None
        prev12 = [shift_month(m, -i) for i in range(1, 13)]
        out[m] = {
            "revenue": v,
            "mom": growth(v, rev.get(shift_month(m, -1))),
            "yoy": growth(v, rev.get(shift_month(m, -12))),
            "ytd": ytd,
            "ytd_yoy": growth(ytd, ytd_prev),
            "avg3_vs_avg12": div(avg3, avg12),  # >1 = short-term momentum above trend
            "high_12m": 1.0 if all(x in rev for x in prev12) and v > max(rev[x] for x in prev12) else 0.0,
            "record_high": 1.0 if seen_max and v > seen_max else 0.0,
        }
        seen_max = max(seen_max, v)
    return out

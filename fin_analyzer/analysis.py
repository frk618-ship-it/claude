"""Run every analysis for a company and derive rule-based signals (優勢 / 注意 / 風險)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Company, FinConfig
from .metrics import ScoreResult, altman_z2, piotroski, quarter_metrics, valuation
from .models import CompanyData, shift_period
from .monthly import monthly_metrics

POSITIVE, WATCH, RISK = "優勢", "注意", "風險"


@dataclass
class Signal:
    level: str  # 優勢 / 注意 / 風險
    topic: str
    message: str


@dataclass
class Analysis:
    data: CompanyData
    quarterly: dict[str, dict[str, float | None]]
    monthly: dict[str, dict[str, float | None]]
    latest: str
    fscore: ScoreResult
    zscore: ScoreResult
    valuation: dict[str, float | None]
    signals: list[Signal] = field(default_factory=list)
    company: Company | None = None

    @property
    def segment(self) -> str:
        return self.company.segment if self.company else ""

    @property
    def latest_metrics(self) -> dict[str, float | None]:
        return self.quarterly.get(self.latest, {})


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def analyze(data: CompanyData, cfg: FinConfig, company: Company | None = None) -> Analysis:
    if not data.periods:
        raise ValueError(f"{data.label}: no quarterly data")
    qm = quarter_metrics(data)
    mm = monthly_metrics(data)
    latest = data.periods[-1]
    a = Analysis(
        data=data,
        quarterly=qm,
        monthly=mm,
        latest=latest,
        fscore=piotroski(data, qm, latest),
        zscore=altman_z2(data, qm, latest),
        valuation=valuation(data, qm, latest, company.par_value if company else cfg.par_value),
        company=company,
    )
    a.signals = signals(a, cfg.thresholds)
    return a


def signals(a: Analysis, t: dict[str, float]) -> list[Signal]:
    out: list[Signal] = []
    p, qm = a.latest, a.quarterly
    m = qm[p]
    yago = qm.get(shift_period(p, -4), {})

    def add(level: str, topic: str, msg: str) -> None:
        out.append(Signal(level, topic, msg))

    # --- 成長 ---
    yoy = m.get("revenue_yoy")
    if yoy is not None:
        if yoy >= t["revenue_yoy_strong"]:
            add(POSITIVE, "成長", f"{p} 營收年增 {_pct(yoy)},成長動能強")
        elif yoy < 0:
            add(WATCH, "成長", f"{p} 營收年減 {_pct(-yoy)}")

    # --- 獲利能力 ---
    gm, gm_y = m.get("gross_margin"), yago.get("gross_margin")
    if gm is not None and gm_y is not None:
        diff = gm - gm_y
        if diff <= -t["gross_margin_drop_pp"]:
            add(RISK, "獲利", f"毛利率 {_pct(gm)},較去年同季下滑 {diff * -100:.1f} 個百分點")
        elif diff >= t["gross_margin_drop_pp"]:
            add(POSITIVE, "獲利", f"毛利率 {_pct(gm)},較去年同季提升 {diff * 100:.1f} 個百分點")
    n = int(t["gross_margin_decline_quarters"])
    seq = [qm.get(shift_period(p, -i), {}).get("gross_margin") for i in range(n + 1)]
    if all(v is not None for v in seq) and all(seq[i] < seq[i + 1] for i in range(n)):
        add(RISK, "獲利", f"毛利率已連續 {n} 季下滑")
    roe = m.get("roe_ttm")
    if roe is not None:
        if roe >= t["roe_good"]:
            add(POSITIVE, "獲利", f"近四季 ROE {_pct(roe)},高於 {_pct(t['roe_good'])} 門檻")
        elif roe < 0:
            add(RISK, "獲利", f"近四季 ROE 為負({_pct(roe)})")
    nos = m.get("non_operating_share")
    if nos is not None and abs(nos) >= t["non_operating_share_max"]:
        direction = "挹注" if nos > 0 else "侵蝕"
        add(WATCH, "獲利品質", f"業外收支{direction}稅前淨利比重 {_pct(abs(nos))},本業獲利需另行評估")

    # --- 營運效率 ---
    for key, label, th in (("dio", "存貨天數", "dio_increase"), ("dso", "應收帳款天數", "dso_increase")):
        cur, prev = m.get(key), yago.get(key)
        if cur is not None and prev:
            change = cur / prev - 1
            if change >= t[th]:
                add(WATCH, "營運效率", f"{label} {cur:.0f} 天,較去年同季增加 {_pct(change)}")
            elif change <= -t[th]:
                add(POSITIVE, "營運效率", f"{label} {cur:.0f} 天,較去年同季縮短 {_pct(-change)}")

    # --- 現金流 ---
    q = m.get("ocf_to_net_income_ttm")
    if q is not None and q < t["ocf_to_ni_min"]:
        add(RISK, "現金流", f"近四季營業現金流僅為淨利的 {q:.2f} 倍,盈餘含金量偏低")
    fcf = m.get("fcf_ttm")
    if fcf is not None and fcf < 0:
        add(WATCH, "現金流", "近四季自由現金流為負(營業現金流不足以支應資本支出)")
    ocf = m.get("operating_cf_ttm")
    if ocf is not None and ocf < 0:
        add(RISK, "現金流", "近四季營業現金流為負")

    # --- 財務結構 ---
    dr = m.get("debt_ratio")
    if dr is not None and dr > t["debt_ratio_max"]:
        add(RISK, "財務結構", f"負債比率 {_pct(dr)},高於 {_pct(t['debt_ratio_max'])}")
    cr = m.get("current_ratio")
    if cr is not None and cr < t["current_ratio_min"]:
        add(WATCH if cr >= 1 else RISK, "財務結構", f"流動比率 {cr:.2f} 倍,短期償債能力偏弱")
    ic = m.get("interest_coverage_ttm")
    if ic is not None and ic < t["interest_coverage_min"]:
        add(RISK, "財務結構", f"利息保障倍數 {ic:.1f} 倍,低於 {t['interest_coverage_min']:.0f} 倍")
    nde = m.get("net_debt_to_equity")
    if nde is not None and nde < 0:
        add(POSITIVE, "財務結構", "淨現金部位(現金大於有息負債)")

    # --- 月營收 ---
    if a.monthly:
        lm = max(a.monthly)
        mmv = a.monthly[lm]
        mom = mmv.get("avg3_vs_avg12")
        if mom is not None:
            if mom >= t["monthly_momentum_up"]:
                add(POSITIVE, "月營收", f"{lm} 近 3 月平均營收為近 12 月平均的 {mom:.2f} 倍,短期動能向上")
            elif mom <= t["monthly_momentum_down"]:
                add(WATCH, "月營收", f"{lm} 近 3 月平均營收為近 12 月平均的 {mom:.2f} 倍,短期動能轉弱")
        if mmv.get("record_high"):
            add(POSITIVE, "月營收", f"{lm} 單月營收創資料期間新高")
        ytd = mmv.get("ytd_yoy")
        if ytd is not None and ytd < 0:
            add(WATCH, "月營收", f"{lm[:4]} 年累計營收年減 {_pct(-ytd)}")

    # --- 綜合評分 ---
    fs = a.fscore.score
    if fs is not None:
        if fs >= t["fscore_good"]:
            add(POSITIVE, "綜合評分", f"Piotroski F-score {fs}/9,基本面改善")
        elif fs <= t["fscore_bad"]:
            add(RISK, "綜合評分", f"Piotroski F-score {fs}/9,基本面轉弱")
    if a.zscore.value is not None and a.zscore.zone != "安全區":
        add(RISK if a.zscore.zone == "財務危機區" else WATCH, "綜合評分", f"Altman Z'' = {a.zscore.value:.2f}({a.zscore.zone})")
    return out


def peer_ranking(analyses: list[Analysis], keys: list[str]) -> dict[str, dict[str, int]]:
    """Rank (1 = best) each company on each metric; lower-is-better metrics are inverted."""
    lower_better = {"dso", "dio", "ccc", "debt_ratio", "net_debt_to_equity", "pe_ttm", "pb", "ev_ebitda_ttm"}
    ranks: dict[str, dict[str, int]] = {a.data.ticker: {} for a in analyses}
    for k in keys:
        vals = [(a.data.ticker, _metric(a, k)) for a in analyses]
        known = sorted((v, tk) for tk, v in vals if v is not None)
        if k not in lower_better:
            known.reverse()
        for i, (_, tk) in enumerate(known, 1):
            ranks[tk][k] = i
    return ranks


def _metric(a: Analysis, key: str) -> float | None:
    if key in a.valuation:
        return a.valuation[key]
    return a.latest_metrics.get(key)

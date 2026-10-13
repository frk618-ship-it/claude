"""Render the analysis as Markdown, JSON and a quarterly-ratio CSV."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .analysis import POSITIVE, RISK, WATCH, Analysis, peer_ranking
from .commentary import Commentary
from .config import FinConfig
from .metrics import METRIC_LABELS
from .models import shift_period

NA = "—"


def fmt(value: float | None, kind: str, unit: float = 1e8) -> str:
    if value is None:
        return NA
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "x":
        return f"{value:.2f}"
    if kind == "days":
        return f"{value:.0f}"
    if kind == "amt":
        return f"{value / unit:,.2f}"
    return f"{value:,.2f}"


def _change(cur: float | None, prev: float | None, kind: str) -> str:
    if cur is None or prev is None:
        return NA
    if kind == "pct":
        return f"{(cur - prev) * 100:+.1f} pp"
    if kind == "days":
        return f"{cur - prev:+.0f} 天"
    if kind == "amt":
        if prev > 0 and cur < 0:
            return "轉負"
        if prev < 0 and cur > 0:
            return "轉正"
        return f"{(cur / prev - 1) * 100:+.1f}%" if prev > 0 else NA
    return f"{cur - prev:+.2f}"


def _table(header: list[str], rows: list[list[str]], text_cols: int = 1) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * text_cols + ["---:"] * (len(header) - text_cols)) + "|",
        *("| " + " | ".join(r) + " |" for r in rows),
        "",
    ]


def _metric_rows(a: Analysis, periods: list[str], keys: list[str], unit: float) -> list[str]:
    header = ["指標", *periods]
    rows = []
    for k in keys:
        label, kind = METRIC_LABELS[k]
        rows.append([label, *(fmt(a.quarterly.get(p, {}).get(k), kind, unit) for p in periods)])
    return _table(header, rows)


SNAPSHOT_KEYS = [
    "revenue", "revenue_yoy", "gross_margin", "operating_margin", "net_margin", "eps", "eps_ttm",
    "roe_ttm", "debt_ratio", "current_ratio", "dio", "ccc", "operating_cf_ttm", "fcf_ttm",
]
PEER_KEYS = [
    "revenue_ttm_yoy", "gross_margin", "operating_margin", "roe_ttm", "debt_ratio",
    "current_ratio", "dio", "ccc", "ocf_to_net_income_ttm", "pe_ttm", "pb",
]
PEER_LABELS = {
    **METRIC_LABELS,
    "revenue_ttm_yoy": ("近四季營收年增", "pct"),
    "pe_ttm": ("本益比", "x"),
    "pb": ("股價淨值比", "x"),
}


def render_markdown(
    target: Analysis, peers: list[Analysis], cfg: FinConfig, commentary: Commentary, generated: datetime
) -> str:
    d, unit, ul = target.data, cfg.amount_unit, cfg.amount_unit_label
    periods = d.periods[-cfg.quarters_in_table :]
    lines = [
        f"# {d.label} 財報分析報告",
        "",
        f"產業:{cfg.industry_name or '—'} · 業務:{target.segment or '—'}  ",
        f"最新財報季度:**{target.latest}** · 資料期間 {d.periods[0]}–{d.periods[-1]}"
        + (f" · 月營收至 {max(d.monthly_revenue)}" if d.monthly_revenue else "")
        + f" · 金額單位:{ul} · 產生時間 {generated.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if d.note:
        lines += [f"> ⚠️ {d.note}", ""]

    if commentary.summary:
        lines += ["## 分析摘要(Claude)", "", commentary.summary, ""]
        for title, items in (("優勢", commentary.strengths), ("風險", commentary.risks), ("後續追蹤", commentary.watch_items)):
            if items:
                lines += [f"**{title}**", ""] + [f"- {x}" for x in items] + [""]
        if commentary.peer_view:
            lines += ["**同業相對位置**", "", commentary.peer_view, ""]

    # Signals
    lines += ["## 重點訊號", ""]
    if target.signals:
        icon = {POSITIVE: "🟢", WATCH: "🟡", RISK: "🔴"}
        for level in (RISK, WATCH, POSITIVE):
            for s in (s for s in target.signals if s.level == level):
                lines.append(f"- {icon[level]} **{s.level}|{s.topic}**:{s.message}")
        lines.append("")
    else:
        lines += ["未觸發任何規則門檻。", ""]

    # Snapshot vs one year ago
    m, prev = target.latest_metrics, target.quarterly.get(shift_period(target.latest, -4), {})
    rows = []
    for k in SNAPSHOT_KEYS:
        label, kind = METRIC_LABELS[k]
        rows.append([label, fmt(m.get(k), kind, unit), fmt(prev.get(k), kind, unit), _change(m.get(k), prev.get(k), kind)])
    lines += ["## 關鍵指標速覽", ""]
    lines += _table(["指標", target.latest, shift_period(target.latest, -4), "年變化"], rows)

    lines += ["## 損益與獲利能力(單季)", ""]
    lines += _metric_rows(target, periods, ["revenue", "revenue_yoy", "revenue_qoq", "gross_margin", "operating_margin",
                                             "net_margin", "opex_ratio", "non_operating_share", "eps", "eps_yoy"], unit)
    lines += ["## 杜邦分析(近四季)", "", "ROE = 稅後淨利率 × 總資產週轉率 × 權益乘數", ""]
    dupont = []
    for p in periods:
        q = target.quarterly[p]
        dupont.append([p, fmt(q.get("net_margin_ttm"), "pct"), fmt(q.get("asset_turnover_ttm"), "x"),
                       fmt(q.get("equity_multiplier"), "x"), fmt(q.get("roe_ttm"), "pct"), fmt(q.get("roa_ttm"), "pct")])
    lines += _table(["季度", "淨利率", "資產週轉率", "權益乘數", "ROE", "ROA"], dupont)
    lines += ["## 營運效率(天)", ""]
    lines += _metric_rows(target, periods, ["dso", "dio", "dpo", "ccc"], unit)
    lines += ["## 財務結構與償債能力", ""]
    lines += _metric_rows(target, periods, ["current_ratio", "quick_ratio", "debt_ratio", "net_debt_to_equity", "interest_coverage_ttm"], unit)
    lines += ["## 現金流量(近四季)", ""]
    lines += _metric_rows(target, periods, ["operating_cf_ttm", "fcf_ttm", "ocf_to_net_income_ttm",
                                             "capex_to_revenue_ttm", "capex_to_depreciation_ttm"], unit)

    if target.monthly:
        months = sorted(target.monthly)[-cfg.months_in_table :]
        mrows = []
        for mo in reversed(months):
            x = target.monthly[mo]
            flag = "🔺新高" if x.get("record_high") else ("12月高" if x.get("high_12m") else "")
            mrows.append([mo, fmt(x["revenue"], "amt", unit), fmt(x.get("mom"), "pct"), fmt(x.get("yoy"), "pct"),
                          fmt(x.get("ytd_yoy"), "pct"), fmt(x.get("avg3_vs_avg12"), "x"), flag])
        lines += ["## 月營收", ""]
        lines += _table(["月份", f"營收({ul})", "月增", "年增", "累計年增", "3M/12M 均值比", "備註"], mrows)

    lines += ["## 綜合評分", ""]
    fs = target.fscore
    lines.append(f"**Piotroski F-score:{fs.score if fs.score is not None else NA} / 9**(近四季 vs 前一年同期)")
    lines.append("")
    lines += [f"- {'✅' if ok else '❌' if ok is False else '❔'} {name}" for name, ok in fs.details]
    lines.append("")
    z = target.zscore
    if z.value is not None:
        lines += [f"**Altman Z''-score:{z.value:.2f}({z.zone})** — >2.6 安全、1.1–2.6 灰色、<1.1 危機", ""]
        lines += [f"- {name}" for name, _ in z.details] + [""]
    else:
        lines += ["**Altman Z''-score:資料不足**", ""]

    v = target.valuation
    if v.get("close") is not None:
        lines += ["## 評價", ""]
        lines += _table(
            ["收盤價", "市值", "本益比(近四季)", "股價淨值比", "股價營收比", "EV/EBITDA", "自由現金流殖利率"],
            [[f"{v['close']:,.2f}({v['close_date']})", fmt(v.get("market_cap"), "amt", unit), fmt(v.get("pe_ttm"), "x"),
              fmt(v.get("pb"), "x"), fmt(v.get("ps_ttm"), "x"), fmt(v.get("ev_ebitda_ttm"), "x"), fmt(v.get("fcf_yield"), "pct")]],
        )

    if peers:
        everyone = [target, *peers]
        ranks = peer_ranking(everyone, PEER_KEYS)
        header = ["公司", "業務", "最新季", *(PEER_LABELS[k][0] for k in PEER_KEYS)]
        rows = []
        for a in everyone:
            cells = []
            for k in PEER_KEYS:
                val = a.valuation.get(k) if k in a.valuation else a.latest_metrics.get(k)
                cell = fmt(val, PEER_LABELS[k][1], unit)
                r = ranks[a.data.ticker].get(k)
                cells.append(f"{cell} ({r})" if r and val is not None else cell)
            name = f"**{a.data.label}**" if a is target else a.data.label
            rows.append([name, a.segment or NA, a.latest, *cells])
        lines += ["## 同業比較", "", f"括號內為 {len(everyone)} 家公司中的排名(1 = 最佳;天數、負債比、本益比、股價淨值比以低者為佳)。", ""]
        lines += _table(header, rows, text_cols=3)

    if cfg.industry_notes:
        lines += ["## 產業分析重點", ""] + [f"- {n}" for n in cfg.industry_notes] + [""]

    lines += [
        "## 計算說明",
        "",
        "- 損益與現金流為單季金額,資產負債為季末餘額;近四季(TTM)為最近四季加總,ROE/ROA 以期初(去年同季)與期末平均計算。",
        "- 營運效率天數 = 平均餘額 ÷ 單季營收(或營業成本)× 91.25;資料缺少應付帳款時,現金轉換循環 = 應收天數 + 存貨天數。",
        "- Piotroski F-score 有效項目少於 7 項時不計分;Altman Z'' 採新興市場/帳面價值版本(6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4)。",
        "- 本報告僅為財務資料的量化整理,不構成投資建議。",
        "",
    ]
    return "\n".join(lines)


def _json_ready(a: Analysis) -> dict:
    return {
        "ticker": a.data.ticker,
        "name": a.data.name,
        "segment": a.segment,
        "latest_quarter": a.latest,
        "note": a.data.note,
        "quarterly_metrics": a.quarterly,
        "monthly_metrics": a.monthly,
        "piotroski": {"score": a.fscore.score, "details": a.fscore.details},
        "altman_z2": {"value": a.zscore.value, "zone": a.zscore.zone},
        "valuation": a.valuation,
        "signals": [s.__dict__ for s in a.signals],
    }


def write_reports(
    target: Analysis, peers: list[Analysis], cfg: FinConfig, commentary: Commentary, out_dir: str | Path, generated: datetime
) -> tuple[Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{target.data.ticker}-{generated.strftime('%Y-%m-%d')}"
    md_path, json_path, csv_path = out / f"{stem}.md", out / f"{stem}.json", out / f"{stem}-quarterly.csv"
    md_path.write_text(render_markdown(target, peers, cfg, commentary, generated), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "generated": generated.isoformat(),
                "amount_unit": "TWD",
                "commentary": commentary.model_dump(),
                "target": _json_ready(target),
                "peers": [_json_ready(p) for p in peers],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    keys = list(next(iter(target.quarterly.values())).keys())
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows 中文 correctly
        w = csv.writer(f)
        w.writerow(["period", *keys])
        w.writerow(["季度", *(METRIC_LABELS.get(k, (k,))[0] for k in keys)])
        for p in target.data.periods:
            w.writerow([p, *("" if target.quarterly[p].get(k) is None else round(target.quarterly[p][k], 6) for k in keys)])
    return md_path, json_path, csv_path


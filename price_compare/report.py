"""Render a comparison as Markdown / JSON / CSV."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from .compare import Comparison
from .models import Quote

PRICE_TYPES = {"list": "公開標價", "quote": "報價", "reference": "參考行情"}
CSV_FIELDS = [
    "match_level", "match_score", "vendor", "title", "spec", "spec_text", "price", "currency", "unit", "pack_qty",
    "price_twd", "basis", "unit_price_twd", "price_type", "moq", "lead_time", "region", "quote_date", "source",
    "url", "outlier", "match_notes", "note",
]


def money(v: float | None, cur: str = "") -> str:
    if v is None:
        return "—"
    s = f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"
    return f"{cur} {s}".strip()


def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def quote_row(q: Quote, base: str) -> list[str]:
    pack = f"{q.pack_qty:g} {q.unit}" if q.pack_qty != 1 else q.unit
    vendor = f"[{_md(q.vendor or '?')}]({q.url})" if q.url else _md(q.vendor or "?")
    flags = [*q.match_notes] + ([f"⚠ {q.outlier}"] if q.outlier else []) + ([q.note] if q.note else [])
    return [
        f"{q.match_level}({q.match_score})",
        vendor,
        _md(q.title)[:60],
        _md(q.spec.summary() if q.spec else ""),
        f"{money(q.price, q.currency.upper())} / {pack}",
        money(q.unit_price_twd, base) + (f" {q.basis}" if q.basis else ""),
        PRICE_TYPES.get(q.price_type, q.price_type),
        _md(" ".join(x for x in (q.region, q.quote_date, q.moq and f"MOQ {q.moq}", q.lead_time) if x)),
        _md(";".join(flags)),
    ]


def render_markdown(comp: Comparison, generated: datetime) -> str:
    base = comp.base_currency
    lines = [
        f"# 物料比價:{comp.spec.raw}",
        "",
        f"系統解析規格:**{comp.spec.summary()}** · 產生時間 {generated.strftime('%Y-%m-%d %H:%M')} · "
        f"報價 {len(comp.quotes)} 筆(可比 {len(comp.usable)} 筆,另排除規格不符 {comp.excluded} 筆)",
        "",
    ]
    if comp.stats:
        lines += ["## 比價摘要", "", f"| 計價基準 | 可比筆數 | 最低 | 中位數 | 最高 | 建議優先詢價 |", "|---|---|---|---|---|---|"]
        for s in comp.stats:
            lines.append(
                f"| {s.basis} | {s.count} | {money(s.low, base)} | {money(s.median, base)} | {money(s.high, base)} | "
                f"{_md(s.best.vendor)}({money(s.best.unit_price_twd, base)},{s.best.match_level}) |"
            )
        lines.append("")
    else:
        lines += ["沒有找到規格相符、可換算比較的報價。可放寬規格(例如去掉尺寸)或在報價紀錄 CSV 補上詢價結果。", ""]

    if comp.market_summary:
        lines += ["## 市場概況(Claude 整理)", "", comp.market_summary, ""]

    if comp.quotes:
        lines += [
            "## 報價明細",
            "",
            f"| 相符度 | 供應商 | 品名 | 解析規格 | 原始報價 | 換算單價({base}) | 類型 | 地區/日期/MOQ/交期 | 備註 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        lines += ["| " + " | ".join(quote_row(q, base)) + " |" for q in comp.quotes]
        lines.append("")

    used = sorted({q.currency.upper() for q in comp.quotes} - {base})
    if used:
        rates = ",".join(f"{c} {comp.fx_rates[c]:g}" for c in used if c in comp.fx_rates)
        src = "臺灣銀行即期中價" if comp.fx_live else "設定檔匯率"
        lines += [f"匯率({src},1 外幣 = {base}):{rates}", ""]
    lines += [
        "說明:相符度 ≥85 為「符合」、60–84 為「近似」;「參考行情」、過舊或偏離中位數過多的報價不列入統計。"
        "網路公開標價常未含稅、運費、綁靶/背板與加工費,下單前請向供應商確認正式報價。",
        "",
    ]
    return "\n".join(lines)


def to_records(comp: Comparison) -> list[dict]:
    rows = []
    for q in comp.quotes:
        d = q.to_dict()
        d["match_notes"] = ";".join(q.match_notes)
        rows.append({k: d.get(k) for k in CSV_FIELDS})
    return rows


def render_json(comp: Comparison, generated: datetime) -> str:
    return json.dumps(
        {
            "query": comp.spec.raw,
            "parsed_spec": {**{k: v for k, v in vars(comp.spec).items()}, "summary": comp.spec.summary()},
            "generated": generated.isoformat(timespec="seconds"),
            "base_currency": comp.base_currency,
            "fx_live": comp.fx_live,
            "summary": [
                {"basis": s.basis, "count": s.count, "low": s.low, "median": s.median, "high": s.high,
                 "best_vendor": s.best.vendor, "best_url": s.best.url,
                 "best_unit_price": s.best.unit_price_twd, "best_level": s.best.match_level}
                for s in comp.stats
            ],
            "market_summary": comp.market_summary,
            "excluded": comp.excluded,
            "quotes": to_records(comp),
        },
        ensure_ascii=False,
        indent=1,
    )


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w.\-]+", "-", text, flags=re.UNICODE).strip("-")
    return slug[:60] or "query"


def write_reports(comp: Comparison, out_dir: str | Path, generated: datetime) -> tuple[Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{slugify(comp.spec.raw)}-{generated.strftime('%Y%m%d-%H%M')}"
    md, js, cs = out / f"{stem}.md", out / f"{stem}.json", out / f"{stem}.csv"
    md.write_text(render_markdown(comp, generated), encoding="utf-8")
    js.write_text(render_json(comp, generated), encoding="utf-8")
    with cs.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel opens Chinese correctly
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(to_records(comp))
    return md, js, cs

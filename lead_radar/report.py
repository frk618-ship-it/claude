"""Assemble the analysis into JSON / Markdown / CSV (CRM import) reports."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .hotspot import analyse, hotspots, signal_heat
from .leads import build_leads, labels
from .signals import Signal


def build_report(signals: list[Signal], config: Config, asof: date, *, coverage_start: date | None = None,
                 first_seen: dict[str, str] | None = None, generated: datetime | None = None) -> dict:
    s = config.settings
    window = [sig for sig in signals if signal_heat(sig, config, asof) > 0]
    grid = analyse(signals, config, asof, coverage_start)
    cells = grid["cells"]
    leads = build_leads(window, cells, config, asof, first_seen, grid["factors"])
    hot = hotspots(cells)
    used = {sid for c in cells if c["count"] for sid in c["signal_ids"]}
    rows = []
    for sig in sorted(window, key=lambda x: (x.published, x.id), reverse=True):
        d = sig.to_dict()
        d["heat"] = round(signal_heat(sig, config, asof), 3)
        rows.append(d)
    grades = Counter(l["grade"] for l in leads)
    return {
        "generated": (generated or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "asof": asof.isoformat(),
        "days": s.days,
        "half_life_days": s.half_life_days,
        "coverage_start": coverage_start.isoformat() if coverage_start else None,
        "slices": grid["slices"],
        "labels": labels(config),
        "segment_pages": [{"key": k, "name": v.name} for k, v in config.segments.items()],   # links to 客群分析 pages
        "thresholds": {"z_hot": s.z_hot, "z_warm": s.z_warm, "min_signals": s.min_signals, "grades": s.grades},
        "stats": {
            "signals": len(window),
            "attributed": sum(1 for sig in window if sig.orgs),
            "new_signals": sum(1 for sig in window if sig.published >= (asof - timedelta(days=7)).isoformat()),
            "hot_cells": sum(1 for c in cells if c["level"] == "hot"),
            "warm_cells": sum(1 for c in cells if c["level"] == "warm"),
            "leads": len(leads),
            "grades": {g: grades.get(g, 0) for g in "ABCD"},
            "enriched": sum(1 for sig in window if sig.enriched),
            "by_product": {p.key: sum(1 for sig in window if p.key in sig.products) for p in config.products},
        },
        "cells": [c for c in cells if c["count"] or any(c["history"])],
        "hotspots": [{k: v for k, v in c.items() if k != "signal_ids"} | {"signal_ids": c["signal_ids"][:12]} for c in hot],
        "leads": leads,
        "signals": [r for r in rows if r["id"] in used or r["orgs"]],
    }


def _name(lab: dict, kind: str, key: str) -> str:
    return lab[kind].get(key, key)


def render_markdown(rep: dict) -> str:
    lab = rep["labels"]
    st = rep["stats"]
    sig_by_id = {s["id"]: s for s in rep["signals"]}
    out = [
        f"# 商機雷達 {rep['asof']}",
        "",
        f"最近 {rep['days']} 天 {st['signals']} 則需求訊號(本週新增 {st['new_signals']} 則),"
        f"熱點 {st['hot_cells']} 個、溫點 {st['warm_cells']} 個;潛在客戶 {st['leads']} 家"
        f"(A {st['grades']['A']} / B {st['grades']['B']} / C {st['grades']['C']} / D {st['grades']['D']})。",
        "",
        "## 需求熱點",
        "",
        "| 產品線 | 應用產業 | 地區 | 等級 | 趨勢 | Gi* z | 熱度 | 訊號數 |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for c in rep["hotspots"][:15]:
        out.append(
            f"| {_name(lab, 'products', c['product'])} | {_name(lab, 'industries', c['industry'])} | {_name(lab, 'regions', c['region'])} "
            f"| {lab['levels'][c['level']]} | {lab['trends'].get(c['trend'], '')} | {c['z']:+.2f} | {c['heat']:.1f} | {c['count']} |"
        )
    if not rep["hotspots"]:
        out.append("| — | 本期沒有達到門檻的熱點 | | | | | | |")
    out += ["", "## 潛在客戶(A / B 級)", "", "| 等級 | 分數 | 公司 | 產品線 | 產業 | 地區 | 主要訊號 | 建議行動 |", "|---|---:|---|---|---|---|---|---|"]
    top = [l for l in rep["leads"] if l["grade"] in ("A", "B")] or rep["leads"][:10]
    for l in top[:40]:
        star = "★ " if l["account"] else ""
        out.append(
            f"| {l['grade']} | {l['score']:.0f} | {star}{l['org']} | {_name(lab, 'products', l['product'])} | {_name(lab, 'industries', l['industry'])} "
            f"| {_name(lab, 'regions', l['region'])} | {_name(lab, 'intents', l['top_intent']) if l['top_intent'] else '—'} | {l['next_action']} |"
        )
    out += ["", "★ = 目標客戶清單中的公司。", ""]
    for l in top[:15]:
        out.append(f"### {l['org']}({l['grade']} 級,{l['score']:.0f} 分)")
        if l["need"]:
            out.append(f"- 需求研判:{l['need']}")
        out.append(f"- 切入重點:{l['pitch']}")
        for sid in l["evidence"][:4]:
            s = sig_by_id.get(sid)
            if s:
                title = s.get("title_zh") or s["title"]
                out.append(f"- {s['published']} [{title}]({s['link']})({s['source']})" if s["link"] else f"- {s['published']} {title}({s['source']})")
        out.append("")
    out.append("> 熱點以 Getis-Ord Gi* 計算(同產業各地區 + 同地區各產業為鄰域);分數僅為公開資訊的量化整理,接觸前請人工確認。")
    return "\n".join(out) + "\n"


CSV_COLUMNS = ["等級", "分數", "公司", "目標客戶", "產品線", "應用產業", "地區", "主要訊號", "訊號數", "最近訊號", "熱點", "需求研判", "建議行動", "切入重點", "佐證連結"]


def render_csv(rep: dict) -> str:
    lab = rep["labels"]
    sig_by_id = {s["id"]: s for s in rep["signals"]}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for l in rep["leads"]:
        links = [sig_by_id[i]["link"] for i in l["evidence"] if i in sig_by_id and sig_by_id[i]["link"]]
        hs = l["hotspot"]
        w.writerow([
            l["grade"], l["score"], l["org"], "是" if l["account"] else "", _name(lab, "products", l["product"]),
            _name(lab, "industries", l["industry"]), _name(lab, "regions", l["region"]),
            _name(lab, "intents", l["top_intent"]) if l["top_intent"] else "", l["signals"], l["last_signal"],
            f"{lab['levels'][hs['level']]} {lab['trends'].get(hs['trend'], '')}".strip(), l["need"], l["next_action"], l["pitch"], " ".join(links[:3]),
        ])
    return "﻿" + buf.getvalue()   # BOM so Excel opens UTF-8 correctly


def write_reports(rep: dict, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = rep["asof"]
    paths = [out / f"{stem}.json", out / f"{stem}.md", out / f"{stem}-leads.csv"]
    paths[0].write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    paths[1].write_text(render_markdown(rep), encoding="utf-8")
    paths[2].write_text(render_csv(rep), encoding="utf-8")
    return paths


def latest_report(reports_dir: str | Path) -> Path | None:
    files = sorted(Path(reports_dir).glob("????-??-??.json"))
    return files[-1] if files else None

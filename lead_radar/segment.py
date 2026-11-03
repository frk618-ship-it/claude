"""Segment analysis: one customer group (e.g. Taiwan semiconductor equipment makers) in depth.

The general radar asks "which markets are heating up"; a segment run asks "inside this group,
whom do we visit first, with which product". Two additions on top of the normal report:

* Account map — every account of the segment is ranked, even with no news yet:
      opportunity = 0.4 × product fit (judged 0–3, as 0–100) + 0.6 × signal score (0–100)
  so a perfect-fit account with no news still shows up, and news moves it up the list.
* Sub-segment hotspots — Gi* on a sub-segment × product grid (e.g. "parts & modules × nickel
  alloys"), with the same neighbour weighting as the market grid.
"""

from __future__ import annotations

import copy
import csv
import io
from datetime import date, datetime

from .config import Config, Segment
from .hotspot import Cell, getis_ord, level_of, signal_heat, story_factors
from .leads import org_weights
from .report import build_report
from .signals import OTHER, Signal

TIERS = [(70, "優先開發"), (45, "積極經營"), (0, "持續觀察")]


def segment_config(config: Config, key: str) -> Config:
    """The config with the segment's own time window / half-life."""
    cfg = copy.deepcopy(config)
    seg = cfg.segments[key]
    if seg.days:
        cfg.settings.days = seg.days
    if seg.half_life_days:
        cfg.settings.half_life_days = seg.half_life_days
    return cfg


def in_segment(sig: Signal, config: Config, seg: Segment) -> bool:
    names = {a.name for a in config.accounts if a.segment == seg.key}
    if names & set(sig.orgs) or names & set(sig.accounts):
        return True
    return sig.region in seg.regions and any(i in seg.industries for i in sig.industries.values())


def _tier(score: float) -> str:
    return next(name for floor, name in TIERS if score >= floor)


def build_segment_report(signals: list[Signal], config: Config, key: str, asof: date, *,
                         coverage_start: date | None = None, first_seen: dict[str, str] | None = None,
                         generated: datetime | None = None) -> dict:
    cfg = segment_config(config, key)
    seg = cfg.segments[key]
    sigs = [s for s in signals if in_segment(s, cfg, seg)]
    rep = build_report(sigs, cfg, asof, coverage_start=coverage_start, first_seen=first_seen, generated=generated)

    window = [s for s in sigs if signal_heat(s, cfg, asof) > 0]
    factors = story_factors(window, cfg)
    members = {a.name: a for a in cfg.accounts if a.segment == key}
    products = [p.key for p in cfg.products]

    # account map
    heat = {n: {p: 0.0 for p in products} for n in members}
    evidence: dict[str, list[tuple[float, str]]] = {n: [] for n in members}
    for s in window:
        h = signal_heat(s, cfg, asof) * factors.get(s.id, 1.0)
        weights = org_weights(s, cfg)
        for org in set(s.orgs) | set(s.accounts):
            if org in members:
                hw = h * weights.get(org, 0.5)
                for p in s.products:
                    heat[org][p] += hw
                evidence[org].append((hw, s.id))
    leads = {l["org"]: l for l in rep["leads"]}
    accounts = []
    for name, a in members.items():
        fit = {p: a.fit.get(p, 0) for p in products}
        fit_pct = max(fit.values(), default=0) / 3 * 100
        lead = leads.get(name)
        sig_score = lead["score"] if lead else 0.0
        opp = round(0.4 * fit_pct + 0.6 * sig_score, 1)
        best = max(products, key=lambda p: (fit[p] * (1 + heat[name][p]), fit[p]))
        if lead:
            action = lead["next_action"]
        else:
            action = f"首次拜訪:以{cfg.product(best).name}的應用案例切入,確認採購與研發窗口"
        accounts.append({
            "org": name,
            "ticker": a.ticker,
            "subsegment": a.subsegment,
            "fit": fit,
            "note": a.note,
            "heat": {p: round(v, 2) for p, v in heat[name].items()},
            "signal_score": sig_score,
            "signals": lead["signals"] if lead else 0,
            "top_intent": lead["top_intent"] if lead else "",
            "last_signal": lead["last_signal"] if lead else "",
            "opportunity": opp,
            "tier": _tier(opp),
            "best_product": best,
            "next_action": action,
            "pitch": cfg.product(best).pitch,
            "pitch_source": cfg.product(best).pitch_source,
            "evidence": [sid for _, sid in sorted(evidence[name], reverse=True)[:6]],
        })
    accounts.sort(key=lambda x: (-x["opportunity"], x["org"]))

    # sub-segment × product hotspots
    rows = list(seg.subsegments) + [OTHER]
    grid = {("seg", r, p): Cell("seg", r, p) for r in rows for p in products}
    for s in window:
        f = factors.get(s.id, 1.0)
        h = signal_heat(s, cfg, asof) * f
        sub = next((members[o].subsegment for o in list(s.accounts) + list(s.orgs) if o in members), OTHER)
        sub = sub if sub in seg.subsegments else OTHER
        for p in s.products:
            c = grid[("seg", sub, p)]
            c.heat += h
            c.count += 1 if f == 1.0 and not s.noise else 0
            c.signal_ids.append(s.id)
    getis_ord(grid, rows, products, "seg", cfg.settings.neighbor_weight)
    seg_cells = [{
        "subsegment": c.industry, "product": c.region, "heat": round(c.heat, 3), "count": c.count,
        "z": round(c.z, 2), "level": level_of(c, cfg), "signal_ids": c.signal_ids[:12],
    } for c in grid.values()]

    rep["segment"] = {
        "key": key, "name": seg.name, "description": seg.description,
        "subsegments": {**seg.subsegments, OTHER: "其他未歸戶業者"},
        "days": cfg.settings.days, "half_life_days": cfg.settings.half_life_days,
    }
    order = cfg.segment_order
    rep["links"] = cfg.settings.links
    rep["segment_pages"] = [{"key": k, "name": cfg.segments[k].name, "primary": i == 0} for i, k in enumerate(order)]
    rep["accounts"] = accounts
    rep["segment_cells"] = seg_cells
    rep["stats"]["accounts"] = len(accounts)
    rep["stats"]["tiers"] = {name: sum(1 for a in accounts if a["tier"] == name) for _, name in TIERS}
    return rep


def render_segment_markdown(rep: dict) -> str:
    seg, lab = rep["segment"], rep["labels"]
    prod = lab["products"]
    out = [
        f"# 客群分析:{seg['name']}({rep['asof']})",
        "",
        seg["description"],
        "",
        f"時間窗最近 {seg['days']} 天(半衰期 {seg['half_life_days']:g} 天);{rep['stats']['signals']} 則訊號,"
        f"{len(rep['accounts'])} 家目標客戶:" + "、".join(f"{k} {v} 家" for k, v in rep["stats"]["tiers"].items()) + "。",
        "",
        "## 次產業 × 產品線熱點",
        "",
        "| 次產業 | " + " | ".join(prod.values()) + " |",
        "|---|" + "---:|" * len(prod),
    ]
    cells = {(c["subsegment"], c["product"]): c for c in rep["segment_cells"]}
    for sub, name in seg["subsegments"].items():
        row = []
        for p in prod:
            c = cells.get((sub, p))
            mark = {"hot": " 🔥", "warm": " ▲"}.get(c["level"], "") if c else ""
            row.append(f"{c['z']:+.1f}({c['count']}){mark}" if c and c["heat"] else "·")
        out.append(f"| {name} | " + " | ".join(row) + " |")
    out += ["", "格內為 Gi* z 值(訊號數);🔥 熱點、▲ 溫點。", "", "## 目標客戶開發順序", "",
            "| 順位 | 公司 | 代號 | 次產業 | 機會分數 | 分級 | 主推產品 | 適配(靶/鈦/鎳) | 近期訊號 | 建議行動 |",
            "|---:|---|---|---|---:|---|---|---|---|---|"]
    for i, a in enumerate(rep["accounts"], 1):
        fit = "/".join(str(a["fit"].get(p, 0)) for p in prod)
        intent = lab["intents"].get(a["top_intent"], "—") if a["signals"] else "—"
        out.append(f"| {i} | {a['org']} | {a['ticker']} | {seg['subsegments'].get(a['subsegment'], a['subsegment'])} | {a['opportunity']:.0f} "
                   f"| {a['tier']} | {prod[a['best_product']]} | {fit} | {intent} | {a['next_action']} |")
    out += ["", "適配度為依公開業務內容推估的初步假設(0–3),請依拜訪結果在 `config/lead_radar.yaml` 修正。", ""]
    return "\n".join(out) + "\n"


def render_accounts_csv(rep: dict) -> str:
    seg, lab = rep["segment"], rep["labels"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["順位", "公司", "代號", "次產業", "機會分數", "分級", "主推產品", "靶材適配", "鈦適配", "鎳基適配", "訊號數", "最近訊號", "主要訊號", "建議行動", "推估理由", "切入重點", "規格依據"])
    for i, a in enumerate(rep["accounts"], 1):
        w.writerow([i, a["org"], a["ticker"], seg["subsegments"].get(a["subsegment"], ""), a["opportunity"], a["tier"],
                    lab["products"][a["best_product"]], a["fit"].get("target", 0), a["fit"].get("titanium", 0), a["fit"].get("nickel", 0),
                    a["signals"], a["last_signal"], lab["intents"].get(a["top_intent"], ""), a["next_action"], a["note"], a["pitch"], a.get("pitch_source", "")])
    return "﻿" + buf.getvalue()

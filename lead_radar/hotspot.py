"""Hotspot analysis on the demand grid.

The "map" is a grid per product line: application industry × region. Each signal drops
heat into one cell:

    heat = intent weight × relevance × noise factor × Claude urgency factor × 0.5^(age / half-life)

Hot spots are found with the Getis-Ord Gi* statistic, as in spatial hot-spot analysis:
a cell's neighbourhood is itself (weight 1) plus every cell in the same industry row (other
regions) and the same region column (other industries), each with weight `neighbor_weight`. Gi* asks whether that neighbourhood holds
more heat than chance would put there, given the whole grid; z ≥ 1.96 is a hot spot at 95 %.
So a cell is hot when demand clusters around it — e.g. aerospace titanium heats up in several
regions at once, or one region shows titanium demand across several industries.

Repeating the analysis at weekly time slices gives an "emerging hot spot" view: new,
persistent, intensifying, sporadic and diminishing hot spots.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import Config
from difflib import SequenceMatcher

from target_news_agent.processing import normalize_title

from .signals import OTHER, UNKNOWN_REGION, Signal

ECHO = 0.25          # weight of further reports of a story already counted …
ECHO_CAP = 0.5       # … but all reprints of one story together add at most this much
STORY_DAYS = 10

LEVELS = {"hot": "熱點", "warm": "溫點", "neutral": "一般", "cold": "冷點"}
TRENDS = {
    "new": "新興熱點",
    "persistent": "持續熱點",
    "intensifying": "升溫熱點",
    "sporadic": "零星熱點",
    "diminishing": "降溫",
    "rising": "升溫中",
    "": "",
}


def industry_keys(config: Config, product_key: str) -> list[str]:
    return config.product(product_key).industries + [OTHER]


def signal_heat(sig: Signal, config: Config, asof: date) -> float:
    """Heat of one signal on `asof`; 0 outside the time window (or in the future)."""
    d = sig.day
    if d is None:
        return 0.0
    age = (asof - d).days
    s = config.settings
    if age < 0 or age > s.days:
        return 0.0
    if sig.weight_hint > 0:
        base = sig.weight_hint
    else:
        weights = [config.intents[i].weight for i in sig.intents if i in config.intents]
        base = max(weights, default=0.5) + 0.25 * max(len(weights) - 1, 0)   # several intents = clearer need
    heat = base * sig.relevance
    if sig.noise:
        heat *= s.noise_factor
    if sig.urgency:
        heat *= 0.4 + 0.2 * sig.urgency          # Claude: 1 → ×0.6, 3 → ×1.0, 5 → ×1.4
    return heat * 0.5 ** (age / s.half_life_days)


@dataclass
class Cell:
    product: str
    industry: str
    region: str
    heat: float = 0.0
    count: int = 0
    z: float = 0.0
    signal_ids: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.product, self.industry, self.region)


def story_factors(signals: list[Signal], config: Config) -> dict[str, float]:
    """Many outlets repeat one story (zh-TW / zh-CN / English, wire reprints). Group signals
    of the same story — same product, within STORY_DAYS, and either the same organisation and
    intent or near-identical headlines — and count only the strongest report fully; the
    other reports add ECHO each (wide coverage is a little extra heat, not a new signal)."""
    sigs = [s for s in signals if s.day]
    norms = [normalize_title(s.title) for s in sigs]
    order = sorted(range(len(sigs)), key=lambda i: sigs[i].day)
    # Greedy, not transitive: a report joins a story only while it is within STORY_DAYS of the
    # story's FIRST report. Chaining pairs (A~B, B~C, …) would fold a company's steady stream of
    # expansion news over months into one "story" and undercount exactly the busiest accounts.
    stories: list[list[int]] = []
    for i in order:
        si = sigs[i]
        home = None
        for members in reversed(stories):
            first = sigs[members[0]]
            if (si.day - first.day).days > STORY_DAYS:
                continue
            for j in members:
                sj = sigs[j]
                if not set(si.products) & set(sj.products):
                    continue
                same_org = bool(set(si.orgs) & set(sj.orgs)) and si.intents[:1] == sj.intents[:1]
                if same_org or SequenceMatcher(None, norms[i], norms[j]).ratio() >= 0.75:
                    home = members
                    break
            if home is not None:
                break
        if home is None:
            stories.append([i])
        else:
            home.append(i)
    groups = {k: members for k, members in enumerate(stories)}
    factors = {}
    for members in groups.values():
        def strength(i):
            s = sigs[i]
            w = max((config.intents[k].weight for k in s.intents if k in config.intents), default=0.5)
            return (w * s.relevance * (s.urgency or 3), s.day)
        lead = max(members, key=strength)
        echo = min(ECHO, ECHO_CAP / max(len(members) - 1, 1))
        for i in members:
            factors[sigs[i].id] = 1.0 if i == lead else echo
    return factors


def build_grid(signals: list[Signal], config: Config, asof: date,
               factors: dict[str, float] | None = None) -> dict[tuple[str, str, str], Cell]:
    grid: dict[tuple[str, str, str], Cell] = {}
    for p in config.products:
        for ind in industry_keys(config, p.key):
            for reg in config.region_keys:
                grid[(p.key, ind, reg)] = Cell(p.key, ind, reg)
    factors = factors if factors is not None else story_factors(signals, config)
    for sig in signals:
        f = factors.get(sig.id, 1.0)
        h = signal_heat(sig, config, asof) * f
        if h <= 0:
            continue
        for p in sig.products:
            cell = grid.get((p, sig.industries.get(p, OTHER), sig.region))
            if cell is None:
                continue
            cell.heat += h
            cell.count += 1 if f == 1.0 and not sig.noise else 0   # count stories, not reprints or stock chatter
            cell.signal_ids.append(sig.id)
    for p in config.products:
        getis_ord(grid, industry_keys(config, p.key), config.region_keys, p.key, config.settings.neighbor_weight)
    return grid


def getis_ord(grid: dict, industries: list[str], regions: list[str], product: str, alpha: float = 0.3) -> None:
    """Gi* z-score for every cell of one product's grid.

    Weights: the cell itself 1, each neighbour (same industry in another region, or same region
    in another industry) `alpha`. alpha = 1 is the plain binary Gi*; a smaller alpha keeps one
    busy row or column from lighting up every cell in it.
    """
    cells = [grid[(product, i, r)] for i in industries for r in regions]
    n = len(cells)
    xs = [c.heat for c in cells]
    mean = sum(xs) / n
    s = math.sqrt(max(sum(x * x for x in xs) / n - mean * mean, 0.0))
    row = {i: sum(grid[(product, i, r)].heat for r in regions) for i in industries}
    col = {r: sum(grid[(product, i, r)].heat for i in industries) for r in regions}
    k = len(industries) + len(regions) - 2          # neighbours, excluding the cell itself
    w_sum = 1 + alpha * k
    w_sq = 1 + alpha * alpha * k
    denom = s * math.sqrt((n * w_sq - w_sum * w_sum) / (n - 1)) if n > 1 else 0.0
    for c in cells:
        local = c.heat + alpha * (row[c.industry] + col[c.region] - 2 * c.heat)
        c.z = (local - mean * w_sum) / denom if denom > 0 else 0.0


def level_of(cell: Cell, config: Config) -> str:
    """Catch-all cells (industry "other", region unknown) still feed the statistics but are never
    reported as hot / warm: there is no market to point a salesperson at."""
    s = config.settings
    if cell.industry == OTHER or cell.region == UNKNOWN_REGION:
        return "cold" if cell.z <= -s.z_hot else "neutral"
    if cell.z >= s.z_hot and cell.count >= s.min_signals:
        return "hot"
    if cell.z >= s.z_warm and cell.count >= 1:
        return "warm"
    if cell.z <= -s.z_hot:
        return "cold"
    return "neutral"


def trend_of(level: str, heat: float, past_levels: list[str], past_heat: list[float]) -> str:
    """Emerging-hot-spot style label from the current state and earlier weekly slices (oldest first)."""
    hot_past = sum(1 for l in past_levels if l == "hot")
    mean_past = sum(past_heat) / len(past_heat) if past_heat else 0.0
    growing = bool(past_heat) and heat >= 1.3 * mean_past and heat > 0
    if level == "hot":
        if not past_levels or hot_past == 0:
            return "new"
        if hot_past >= 0.8 * len(past_levels):
            return "persistent"
        if growing:
            return "intensifying"
        return "sporadic"
    if hot_past and "hot" in past_levels[-4:]:   # hot within the last month
        return "diminishing"
    if level == "warm" and growing and mean_past > 0:
        return "rising"
    return ""


def analyse(signals: list[Signal], config: Config, asof: date, coverage_start: date | None = None) -> dict:
    """Current grid + weekly history. History slices whose window starts before the data
    coverage are skipped (they would look artificially cold)."""
    s = config.settings
    factors = story_factors(signals, config)
    current = build_grid(signals, config, asof, factors)
    slices: list[date] = []
    for k in range(s.history_weeks, 0, -1):
        t = asof - timedelta(weeks=k)
        if coverage_start is None or t - timedelta(days=s.days) >= coverage_start:
            slices.append(t)
    past = [build_grid(signals, config, t, factors) for t in slices]

    cells = []
    for key, c in current.items():
        lvl = level_of(c, config)
        past_levels = [level_of(g[key], config) for g in past]
        past_heat = [g[key].heat for g in past]
        cells.append({
            "product": c.product,
            "industry": c.industry,
            "region": c.region,
            "heat": round(c.heat, 3),
            "count": c.count,
            "z": round(c.z, 2),
            "level": lvl,
            "trend": trend_of(lvl, c.heat, past_levels, past_heat),
            "history": [round(h, 3) for h in past_heat] + [round(c.heat, 3)],
            "signal_ids": c.signal_ids,
        })
    return {"cells": cells, "slices": [t.isoformat() for t in slices] + [asof.isoformat()], "factors": factors}


def hotspots(cells: list[dict], limit: int = 30) -> list[dict]:
    """Cells worth a salesperson's attention: hot / warm / diminishing, strongest first."""
    keep = [c for c in cells if (c["count"] and c["level"] in ("hot", "warm")) or c["trend"] == "diminishing"]
    order = {"hot": 0, "warm": 1, "neutral": 2, "cold": 3}
    return sorted(keep, key=lambda c: (order[c["level"]], -c["z"], -c["heat"]))[:limit]

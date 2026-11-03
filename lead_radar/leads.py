"""Turn signals into a ranked prospect list (潛在客戶名單).

For each organisation:  raw = Σ signal heat × industry fit × hotspot boost × intent diversity
and score = 100 × (1 − e^(−raw / 6)), graded A / B / C / D.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, timedelta

from .config import Config
from .hotspot import LEVELS, TRENDS, signal_heat, story_factors
from .signals import OTHER, Signal

SCALE = 6.0
SUBJECT, MENTIONED, SNIPPET_ONLY = 1.0, 0.5, 0.3
DECAY = 0.75   # a company's k-th strongest signal counts DECAY**k: more coverage, diminishing returns


def _title_pos(org: str, title: str, config: Config) -> int:
    """Where the company first appears in the headline (-1 = not in it); watch-list aliases count."""
    acc = config.account(org)
    names = (acc.aliases if acc else []) + [org]
    low = title.lower()
    hits = [low.find(n.lower()) for n in names if n and n.lower() in low]
    return min(hits) if hits else -1


def org_weights(sig: Signal, config: Config) -> dict[str, float]:
    """How much of a signal belongs to each company it names. The headline's subject (the first
    company in it) gets it all; a company the headline only mentions — usually the customer or
    partner in "漢唐、帆宣卡位台積電赴美大單" — half; one named only in the snippet, 0.3."""
    pos = {o: _title_pos(o, sig.title, config) for o in sig.orgs}
    in_title = [p for p in pos.values() if p >= 0]
    first = min(in_title) if in_title else None
    return {o: (SNIPPET_ONLY if p < 0 else SUBJECT if p == first else MENTIONED) for o, p in pos.items()}
BASE_HALF_LIFE = 14.0


def _grade(score: float, grades: dict[str, float]) -> str:
    for g in ("A", "B", "C"):
        if score >= grades.get(g, 101):
            return g
    return "D"


def build_leads(signals: list[Signal], cells: list[dict], config: Config, asof: date,
                first_seen: dict[str, str] | None = None, factors: dict[str, float] | None = None) -> list[dict]:
    cell_by_key = {(c["product"], c["industry"], c["region"]): c for c in cells}
    factors = factors if factors is not None else story_factors(signals, config)
    by_org: dict[str, list[tuple[Signal, float]]] = defaultdict(list)
    for sig in signals:
        h = signal_heat(sig, config, asof) * factors.get(sig.id, 1.0)
        if h <= 0:
            continue
        for org, w in org_weights(sig, config).items():
            by_org[org].append((sig, h * w))

    leads = []
    first_seen = first_seen or {}
    for org, items in by_org.items():
        account = config.account(org)
        # Diminishing returns: large listed companies get many reports; the strongest counts fully,
        # each further one DECAY times the one before, so volume alone cannot max out the score.
        items = sorted(items, key=lambda t: -t[1])
        items = [(sig, h * DECAY ** k) for k, (sig, h) in enumerate(items)]
        per_product: Counter = Counter()
        for sig, h in items:
            for p in sig.products:
                per_product[p] += h
        product = (account.products[0] if account and account.products and per_product[account.products[0]] else None) \
            or per_product.most_common(1)[0][0]
        p = config.product(product)

        industries = Counter(sig.industries.get(product, OTHER) for sig, _ in items if product in sig.products)
        industry = account.industry if account and account.industry in p.industries else industries.most_common(1)[0][0]
        regions = Counter(sig.region for sig, _ in items)
        region = account.region if account and account.region in config.regions else regions.most_common(1)[0][0]

        intent_heat: Counter = Counter()
        for sig, h in items:
            for i in sig.intents[:2]:
                intent_heat[i] += h
        top_intent = intent_heat.most_common(1)[0][0] if intent_heat else ""

        fit = 1.2 if account else (1.0 if industry in p.industries else 0.7)
        cell = cell_by_key.get((product, industry, region), {})
        z = max(cell.get("z", 0.0), 0.0)
        hot_boost = 1 + 0.15 * min(z, 4.0)
        diversity = 1 + min(0.1 * (len(intent_heat) - 1), 0.4) if intent_heat else 1.0
        # Each signal counts once, however many product lines it touches; per_product only picks
        # the lead product (summing it would count a signal inferred as "titanium + nickel" twice).
        raw = sum(h for _, h in items) * fit * hot_boost * diversity
        # Longer half-lives (segments: 45 d) accumulate proportionally more heat; scale with it so
        # scores stay comparable instead of all saturating near 100.
        scale = SCALE * config.settings.half_life_days / BASE_HALF_LIFE
        score = round(100 * (1 - math.exp(-raw / scale)), 1)

        days = sorted(sig.published for sig, _ in items if sig.published)
        firsts = sorted(first_seen.get(sig.id, sig.published) for sig, _ in items)
        evidence = sorted(items, key=lambda t: -t[1])[:6]
        intent = config.intents.get(top_intent)
        leads.append({
            "org": org,
            "account": bool(account),
            "score": score,
            "grade": _grade(score, config.settings.grades),
            "product": product,
            "products": [k for k, _ in per_product.most_common()],
            "industry": industry,
            "region": region,
            "top_intent": top_intent,
            "intents": [k for k, _ in intent_heat.most_common()],
            "signals": sum(1 for sig, _ in items if factors.get(sig.id, 1.0) == 1.0),
            "first_seen": firsts[0] if firsts else "",
            "last_signal": days[-1] if days else "",
            "new": bool(firsts) and firsts[0] >= (asof - timedelta(days=7)).isoformat(),
            "hotspot": {"level": cell.get("level", "neutral"), "trend": cell.get("trend", ""), "z": cell.get("z", 0.0)},
            "next_action": intent.action if intent else "以產品型錄與應用案例做首次接觸,確認需求窗口(採購 / 研發 / 廠務)",
            "pitch": p.pitch,
            "need": next((sig.need for sig, _ in evidence if sig.need), ""),
            "evidence": [sig.id for sig, _ in evidence],
        })
    leads.sort(key=lambda l: (-l["score"], l["org"]))
    return leads


def labels(config: Config) -> dict:
    """Display names for every key the report uses."""
    return {
        "products": {p.key: p.name for p in config.products},
        "industries": {**{k: v.name for k, v in config.industries.items()}, OTHER: "其他應用"},
        "regions": {k: v.name for k, v in config.regions.items()},
        "intents": {k: v.name for k, v in config.intents.items()},
        "levels": LEVELS,
        "trends": {k: v for k, v in TRENDS.items() if k},
        "product_industries": {p.key: p.industries + [OTHER] for p in config.products},
    }

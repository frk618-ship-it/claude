"""No-AI fallback: a rule-based overview when the Claude digest is unavailable.

Used when the API call fails (no credits, outage) so the report never shows an empty
summary. It is marked `source="auto"`, and a later Claude digest replaces it.
"""

from __future__ import annotations

from collections import Counter

from .config import Config
from .models import NewsItem
from .site import SIGNAL_KINDS, item_kind, short_name
from .summarizer import Digest
from .translate import to_traditional

AUTO = "auto"


def _title(item: NewsItem) -> str:
    return item.title_zh or to_traditional(item.title)


def auto_digest(items: list[NewsItem], config: Config) -> Digest:
    if not items:
        return Digest(source=AUTO)
    dicts = [i.to_dict() for i in items]
    signal = [i for i, d in zip(items, dicts) if item_kind(d) in SIGNAL_KINDS]
    topics = {e.name for e in config.entities if e.is_topic}
    companies = Counter(short_name(e) for i in signal for e in i.entities if e not in topics)
    cats = Counter(i.categories[0] for i in signal if i.categories)

    top_names = "、".join(f"{n}({c} 則)" for n, c in companies.most_common(4))
    overview = f"本期蒐集 {len(items)} 則,其中新聞與公司公告 {len(signal)} 則"
    overview += f";報導最多的是 {top_names}。" if top_names else "。"
    if cats:
        overview += "依主題:" + "、".join(f"{c} {n} 則" for c, n in cats.most_common(5)) + "。"

    ranked = sorted(signal, key=lambda i: (i.importance, i.score), reverse=True)
    trends = []
    for cat, n in cats.most_common(4):
        lead = next(i for i in ranked if i.categories and i.categories[0] == cat)
        trends.append(f"{cat}({n} 則):{_title(lead)}")
    watch = [f"{_title(i)}({i.source})" for i in ranked[:3]]
    return Digest(overview=overview, key_trends=trends, watch_points=watch, source=AUTO)


def finalize_digest(digest: Digest, items: list[NewsItem], config: Config) -> Digest:
    """Keep a Claude overview; otherwise (re)build the rule-based one over all items."""
    if digest.overview and digest.source != AUTO:
        return digest
    return auto_digest(items, config)

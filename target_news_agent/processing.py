"""Filtering, entity matching, classification, de-duplication and ranking."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from opencc import OpenCC

from .config import Config, Entity
from .models import NewsItem

log = logging.getLogger(__name__)

GENERAL_CATEGORY = "產業動態"
TITLE_SIMILARITY = 0.88
_T2S = OpenCC("t2s")


def _contains(text_lower: str, term: str) -> bool:
    """Case-insensitive containment; ASCII words need word boundaries ("ITO" ≠ "Toyota")."""
    t = term.lower()
    if t.isascii() and re.fullmatch(r"[\w .&-]+", t):
        return re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", text_lower) is not None
    return t in text_lower


def has_any(text: str, terms: list[str]) -> bool:
    low = text.lower()
    return any(_contains(low, t) for t in terms)


def normalize_title(title: str) -> str:
    # Simplified-Chinese form so zh-TW and zh-CN reprints of one story compare equal.
    t = _T2S.convert(unicodedata.normalize("NFKC", title)).lower()
    return re.sub(r"[\W_]+", "", t)


def item_id(item: NewsItem) -> str:
    return hashlib.sha1(normalize_title(item.title).encode("utf-8")).hexdigest()[:16]


def match_entities(item: NewsItem, entities: list[Entity], context_terms: list[str]) -> list[str]:
    low = item.text.lower()

    def has_ctx(e: Entity) -> bool:
        return any(_contains(low, t) for t in (e.context_terms or context_terms))

    matched = []
    for e in entities:
        if e.is_topic:
            continue
        if any(_contains(low, a) for a in e.aliases) and (not e.require_context or has_ctx(e)):
            matched.append(e.name)
    if not matched:
        topic = next((e for e in entities if e.name == item.query_entity and e.is_topic), None)
        if topic is not None and has_ctx(topic):
            matched.append(topic.name)
    return matched


def classify(item: NewsItem, categories: dict[str, list[str]]) -> list[str]:
    cats = [name for name, kws in categories.items() if has_any(item.text, kws)]
    return cats or [GENERAL_CATEGORY]


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Drop exact and near-duplicate headlines, merging entity tags into the kept item."""
    kept: list[NewsItem] = []
    by_link: dict[str, NewsItem] = {}
    norms: list[str] = []
    for item in sorted(items, key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc)):
        norm = normalize_title(item.title)
        dup = by_link.get(item.link)
        if dup is None:
            for k, n in zip(kept, norms):
                if n == norm or (
                    min(len(n), len(norm)) >= 8 and SequenceMatcher(None, n, norm).ratio() >= TITLE_SIMILARITY
                ):
                    dup = k
                    break
        if dup is not None:
            for e in item.entities:
                if e not in dup.entities:
                    dup.entities.append(e)
            continue
        kept.append(item)
        norms.append(norm)
        by_link[item.link] = item
    return kept


CATEGORY_WEIGHTS = {
    "擴產與投資": 1.5,
    "併購與合作": 1.5,
    "營收與財報": 1.0,
    "客戶與供應鏈": 1.0,
    "政策與貿易": 1.0,
    "原物料與價格": 0.8,
    "技術與新產品": 0.8,
}


def score(item: NewsItem, entity_index: dict[str, Entity], now: datetime) -> float:
    s = 1.0
    s += sum(0.5 if entity_index.get(e) and entity_index[e].is_topic else 1.0 for e in item.entities)
    s += sum(CATEGORY_WEIGHTS.get(c, 0.0) for c in item.categories)
    if item.published:
        age_days = max((now - item.published).total_seconds() / 86400, 0)
        s += max(0.0, 1.0 - age_days / 7)
    return s


def process(items: list[NewsItem], config: Config, now: datetime | None = None) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    s = config.settings
    cutoff = now - timedelta(days=s.days)
    index = {e.name: e for e in config.entities}

    relevant = []
    kept: Counter[str] = Counter()
    dropped: dict[str, list[str]] = {}
    too_old: Counter[str] = Counter()
    for item in items:
        if item.published and item.published < cutoff:
            too_old[item.query_entity] += 1
            continue
        item.entities = match_entities(item, config.entities, s.context_terms)
        if not item.entities:
            dropped.setdefault(item.query_entity, []).append(item.title)
            continue
        kept[item.query_entity] += 1
        relevant.append(item)
    # Per-search tuning aid: how much each search keeps, and what the context check throws away.
    for name, titles in dropped.items():
        log.info("filter %-30s kept %3d, dropped %3d  e.g. %s", name, kept[name], len(titles), " | ".join(titles[:3]))
    for name, n in too_old.items():
        log.info("filter %-30s %3d items older than %d days skipped", name, n, s.days)

    unique = dedupe(relevant)
    for item in unique:
        item.id = item_id(item)
        item.categories = classify(item, s.categories)
        item.score = score(item, index, now)
    unique.sort(key=lambda i: (i.score, i.published or now), reverse=True)
    # Busy topics (e.g. gold price news) keep only their newest max_items items.
    caps = {e.name: e.max_items for e in config.entities if e.max_items}
    if caps:
        newest = sorted(unique, key=lambda i: i.published or now, reverse=True)
        taken: Counter[str] = Counter()
        drop = set()
        for item in newest:
            if len(item.entities) == 1 and item.entities[0] in caps:
                name = item.entities[0]
                taken[name] += 1
                if taken[name] > caps[name]:
                    drop.add(id(item))
        unique = [i for i in unique if id(i) not in drop]
    return unique


def hot_keywords(items: list[NewsItem], terms: list[str], top: int = 15) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for item in items:
        low = item.text.lower()
        for t in terms:
            if _contains(low, t):
                counts[t] += 1
    return counts.most_common(top)

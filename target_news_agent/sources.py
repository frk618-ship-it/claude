"""News sources: Google News RSS, Bing News RSS and arbitrary RSS/Atom feeds."""

from __future__ import annotations

import html
import logging
import re
import time
import urllib.request
from calendar import timegm
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import quote_plus

import feedparser

from .config import Config, Entity
from .models import NewsItem

log = logging.getLogger(__name__)

Fetcher = Callable[[str], bytes]

USER_AGENT = "Mozilla/5.0 (compatible; TargetNewsAgent/1.0)"
_TAG_RE = re.compile(r"<[^>]+>")


def http_fetch(url: str, timeout: float = 20.0) -> bytes:
    # urllib honours HTTPS_PROXY / HTTP_PROXY from the environment.
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def build_query(entity: Entity, context_terms: list[str]) -> str:
    """Search query for one entity.

    Companies: exact-phrase OR of aliases, plus a context clause when the
    company is broad (so target news isn't drowned out by unrelated news).
    Topics: aliases are already keyword queries, OR them as-is.
    """
    if entity.is_topic:
        return " OR ".join(f"({a})" if " " in a else a for a in entity.aliases)
    names = " OR ".join(f'"{a}"' for a in entity.aliases)
    query = f"({names})"
    context_terms = entity.context_terms or context_terms
    if entity.require_context and entity.query_context and context_terms:
        ctx = " OR ".join(f'"{t}"' if " " in t else t for t in context_terms)
        query += f" ({ctx})"
    return query


def google_news_url(query: str, edition: dict[str, str], days: int) -> str:
    q = quote_plus(f"{query} when:{days}d")
    return (
        f"https://news.google.com/rss/search?q={q}"
        f"&hl={edition['hl']}&gl={edition['gl']}&ceid={quote_plus(edition['ceid'])}"
    )


def bing_news_url(query: str, edition: dict[str, str]) -> str:
    market = f"{edition['hl'].split('-')[0]}-{edition['gl']}"
    return f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt={market}"


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
    return None


def parse_feed(data: bytes, *, edition: str = "", query_entity: str = "", default_source: str = "") -> list[NewsItem]:
    feed = feedparser.parse(data)
    items = []
    for entry in feed.entries:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue
        source = ""
        src = entry.get("source")
        if isinstance(src, dict):
            source = src.get("title", "") or ""
        # Google News titles look like "headline - Publisher"; split the publisher off.
        if " - " in title:
            head, _, tail = title.rpartition(" - ")
            if not source or tail.strip() == source:
                source = source or tail.strip()
                title = head.strip()
        snippet = strip_html(entry.get("summary", ""))
        if snippet.startswith(title):
            # Google News descriptions mostly repeat the headline.
            snippet = snippet[len(title):].strip(" - ")
            if source and snippet.strip() == source:
                snippet = ""
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=source or default_source,
                published=_entry_datetime(entry),
                snippet=snippet,
                edition=edition,
                query_entity=query_entity,
            )
        )
    return items


def collect(config: Config, fetch: Fetcher = http_fetch, *, sleep: Callable[[float], None] = time.sleep) -> list[NewsItem]:
    """Run every configured query and return the raw (unfiltered) items."""
    s = config.settings
    jobs: list[tuple[str, str, str, str]] = []  # (url, edition, entity, source label)
    for entity in config.entities:
        query = build_query(entity, s.query_context_terms)
        for ed in entity.editions:
            edition = s.editions[ed]
            if s.google_news:
                jobs.append((google_news_url(query, edition, s.days), ed, entity.name, "Google News"))
            if s.bing_news:
                jobs.append((bing_news_url(query, edition), ed, entity.name, "Bing News"))
    for feed in s.feeds:
        jobs.append((feed["url"], feed.get("edition", ""), "", feed.get("name", "")))

    items: list[NewsItem] = []
    failures = 0
    for i, (url, ed, entity_name, label) in enumerate(jobs):
        if i and s.request_delay:
            sleep(s.request_delay)
        try:
            data = fetch(url)
        except Exception as exc:  # network errors must not abort the whole run
            failures += 1
            log.warning("fetch failed (%s, %s, %s): %s", label, entity_name or url, ed, exc)
            continue
        got = parse_feed(data, edition=ed, query_entity=entity_name, default_source=label)
        log.info("%-12s %-30s %-6s %3d items", label, entity_name or url, ed, len(got))
        items.extend(got)
    if jobs and failures == len(jobs):
        raise RuntimeError("all news source requests failed; check network / proxy settings")
    return items


def iter_feed_urls(config: Config) -> Iterable[str]:
    """Convenience for --dry-run: list every URL that would be fetched."""
    s = config.settings
    for entity in config.entities:
        query = build_query(entity, s.query_context_terms)
        for ed in entity.editions:
            if s.google_news:
                yield google_news_url(query, s.editions[ed], s.days)
            if s.bing_news:
                yield bing_news_url(query, s.editions[ed])
    for feed in s.feeds:
        yield feed["url"]

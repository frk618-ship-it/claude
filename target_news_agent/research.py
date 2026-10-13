"""Journal papers and preprints on sputtering-target manufacturing technology.

Sources: OpenAlex (scholarly works index, free, no key required), the arXiv API,
and journal RSS feeds (e.g. ScienceDirect). "Target" is a generic word in English,
so a paper is kept only when its title/abstract is clearly about sputtering
targets; it is then grouped by manufacturing topic (sintering, casting, grain
control, bonding, ...).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote, urlencode

import feedparser

from .config import ResearchSettings
from .processing import _contains, normalize_title
from .sources import Fetcher, _entry_datetime, http_fetch, strip_html

log = logging.getLogger(__name__)

GENERAL_TOPIC = "其他靶材研究"

# A paper must hit one of these, or mention "target" together with a sputtering word.
STRONG_TERMS = [
    "sputtering target", "sputter target", "sputtering targets", "sputter targets",
    "target material", "targets for sputtering", "magnetron target", "rotary target", "rotatable target",
    "ITO target", "IGZO target", "AZO target", "靶材", "濺鍍靶", "溅射靶", "スパッタリングターゲット", "스퍼터링 타겟",
]
SPUTTER_WORDS = ["sputter", "magnetron", "PVD", "physical vapor deposition", "濺鍍", "溅射", "スパッタ"]


@dataclass
class Paper:
    title: str
    link: str
    published: datetime | None
    venue: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    doi: str = ""
    source: str = ""          # OpenAlex / arXiv / feed name
    topics: list[str] = field(default_factory=list)
    id: str = ""
    summary: str = ""         # filled by the optional LLM digest
    importance: int = 0

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.abstract}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "link": self.link,
            "published": self.published.isoformat() if self.published else None,
            "venue": self.venue,
            "authors": self.authors,
            "abstract": self.abstract,
            "doi": self.doi,
            "source": self.source,
            "topics": self.topics,
            "summary": self.summary,
            "importance": self.importance,
        }


def paper_id(p: Paper) -> str:
    key = p.doi.lower() if p.doi else normalize_title(p.title)
    return "p-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _match(low: str, term: str) -> bool:
    """Like processing._contains, but English words of 4+ letters also match with endings
    (powder -> powders, alloy -> alloyed, target -> targets); short acronyms stay exact (ITO, HIP, SPS)."""
    t = term.lower()
    if t.isascii() and len(t) >= 4 and re.fullmatch(r"[a-z0-9 .&-]+", t):
        return re.search(rf"(?<![a-z0-9]){re.escape(t)}", low) is not None
    return _contains(low, t)


def is_relevant(p: Paper) -> bool:
    low = p.text.lower()
    if any(_match(low, t) for t in STRONG_TERMS):
        return True
    return _match(low, "target") and any(_match(low, w) for w in SPUTTER_WORDS)


def classify_topics(p: Paper, topics: dict[str, list[str]]) -> list[str]:
    low = p.text.lower()
    found = [name for name, kws in topics.items() if any(_match(low, k) for k in kws)]
    return found or [GENERAL_TOPIC]


# ---------- OpenAlex ----------

def _abstract_from_index(index: dict | None) -> str:
    if not index:
        return ""
    words: list[tuple[int, str]] = [(pos, w) for w, positions in index.items() for pos in positions]
    return " ".join(w for _, w in sorted(words))


def openalex_url(query: str, since: date, per_page: int, mailto: str = "", api_key: str = "") -> str:
    params = {
        "search": query,
        "filter": f"from_publication_date:{since.isoformat()}",
        "sort": "publication_date:desc",
        "per-page": str(per_page),
    }
    if mailto:
        params["mailto"] = mailto
    if api_key:
        params["api_key"] = api_key
    return "https://api.openalex.org/works?" + urlencode(params)


def parse_openalex(data: bytes) -> list[Paper]:
    out = []
    for w in json.loads(data).get("results", []):
        title = strip_html(w.get("display_name") or w.get("title") or "")
        if not title:
            continue
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        loc = w.get("primary_location") or {}
        venue = ((loc.get("source") or {}).get("display_name")) or ""
        link = w.get("doi") or loc.get("landing_page_url") or w.get("id", "")
        published = None
        if w.get("publication_date"):
            published = datetime.fromisoformat(w["publication_date"]).replace(tzinfo=timezone.utc)
        authors = [a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author", {}).get("display_name")]
        out.append(
            Paper(
                title=title,
                link=link,
                published=published,
                venue=venue,
                authors=authors,
                abstract=_abstract_from_index(w.get("abstract_inverted_index")),
                doi=doi,
                source="OpenAlex",
            )
        )
    return out


# ---------- arXiv ----------

def arxiv_url(query: str, max_results: int) -> str:
    q = f'abs:"{query}"' if " " in query else f"abs:{query}"
    return (
        "https://export.arxiv.org/api/query?search_query=" + quote(q)
        + f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )


def parse_feed_papers(data: bytes, source: str) -> list[Paper]:
    """arXiv Atom or a journal RSS feed."""
    feed = feedparser.parse(data)
    out = []
    for e in feed.entries:
        title = strip_html(e.get("title", ""))
        link = e.get("link", "")
        if not title or not link:
            continue
        authors = [a.get("name", "") for a in e.get("authors", []) if a.get("name")]
        doi = e.get("arxiv_doi", "") or e.get("prism_doi", "") or e.get("dc_identifier", "").replace("doi:", "")
        out.append(
            Paper(
                title=re.sub(r"\s+", " ", title),
                link=link,
                published=_entry_datetime(e),
                venue=e.get("prism_publicationname", "") or ("arXiv" if source == "arXiv" else source),
                authors=authors,
                abstract=strip_html(e.get("summary", "")),
                doi=doi if doi.startswith("10.") else "",
                source=source,
            )
        )
    return out


# ---------- collect ----------

def collect_papers(
    rs: ResearchSettings,
    fetch: Fetcher = http_fetch,
    *,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Paper]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=rs.days)).date()
    jobs: list[tuple[str, str]] = []
    if rs.openalex:
        mailto = rs.mailto or os.environ.get("OPENALEX_MAILTO", "")
        api_key = os.environ.get("OPENALEX_API_KEY", "")
        jobs += [(openalex_url(q, since, rs.per_query, mailto, api_key), "OpenAlex") for q in rs.queries]
    if rs.arxiv:
        jobs += [(arxiv_url(q, rs.per_query), "arXiv") for q in rs.arxiv_queries or rs.queries[:3]]
    jobs += [(f["url"], f.get("name", "journal")) for f in rs.feeds]

    raw: list[Paper] = []
    for i, (url, source) in enumerate(jobs):
        if i and rs.request_delay:
            sleep(rs.request_delay)
        try:
            data = fetch(url)
            got = parse_openalex(data) if source == "OpenAlex" else parse_feed_papers(data, source)
        except Exception as exc:  # one failing source must not stop the run
            log.warning("paper source failed (%s %s): %s", source, url[:80], exc)
            continue
        log.info("%-10s %3d papers  %s", source, len(got), url[:90])
        raw.extend(got)
    return process_papers(raw, rs, since)


def process_papers(papers: list[Paper], rs: ResearchSettings, since: date) -> list[Paper]:
    kept: list[Paper] = []
    by_doi: dict[str, Paper] = {}
    by_title: dict[str, Paper] = {}
    for p in papers:
        if p.published and p.published.date() < since:
            continue
        if not is_relevant(p):
            continue
        title_key = normalize_title(p.title)
        existing = (by_doi.get(p.doi.lower()) if p.doi else None) or by_title.get(title_key)
        if existing:
            # Same paper from two sources: keep the richer record.
            if len(p.abstract) > len(existing.abstract):
                existing.abstract = p.abstract
            existing.venue = existing.venue or p.venue
            existing.doi = existing.doi or p.doi
            continue
        p.id = paper_id(p)
        p.topics = classify_topics(p, rs.topics)
        kept.append(p)
        by_title[title_key] = p
        if p.doi:
            by_doi[p.doi.lower()] = p
    return sorted(kept, key=lambda p: p.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

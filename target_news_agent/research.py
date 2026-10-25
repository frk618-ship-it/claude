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
# Title phrases that name the target itself as the subject.
STRONG_TERMS = [
    "sputtering target", "sputter target", "target material", "targets for sputtering",
    "magnetron target", "rotary target", "rotatable target", "planar target", "cylindrical target",
    "靶材", "濺鍍靶", "溅射靶", "スパッタリングターゲット", "스퍼터링 타겟",
    # target recycling: spent targets are always sputtering targets
    "spent target", "spent sputtering target", "廢靶", "废靶", "殘靶", "残靶",
]
# Words that, next to "target" in a title, mean the paper is about making the target.
MANUFACTURING_WORDS = [
    "fabricat", "prepar", "manufactur", "produc", "synthes", "sinter", "densif", "hot press", "hot-press",
    "hot isostatic", "spark plasma", "powder", "cast", "melt", "ingot", "roll", "forg", "anneal",
    "microstructur", "grain", "texture", "recrystalliz", "purity", "purif", "impurit", "bond", "backing plate",
    "recycl", "reclaim", "recover", "leach", "regenerat", "reuse", "refurbish", "scrap", "spent",
    "relative density", "crack", "erosion", "utilization", "nodule",
    "燒結", "烧结", "製備", "制备", "晶粒", "純度", "纯度", "綁定", "绑定", "回收", "再生", "浸出",
]
# Abstract sentences where the target itself is processed or characterised (not just used to sputter films).
_T = r"(?:sputtering |sputter )?targets?"
_M = (
    r"(?:fabricat\w*|prepar\w*|manufactur\w*|produc\w*|sinter\w*|densif\w*|hot[- ]press\w*|cast\w*|melt\w*|"
    r"roll\w*|forg\w*|anneal\w*|bond\w*|recycl\w*|purif\w*)"
)
TARGET_PROCESS_PATTERNS = [
    re.compile(rf"\b{_T}\s+(?:were|was|are|is|have been|has been)\s+(?:\w+\s+)?{_M}"),
    re.compile(rf"\b(?:fabrication|preparation|manufacturing|production|sintering|densification|casting|rolling|"
               rf"forging|annealing|bonding|recycling|purification)\s+(?:of|for)\s+(?:the\s+|a\s+|an\s+)?(?:[\w-]+\s+){{0,3}}{_T}\b"),
    re.compile(rf"\b{_T}\s+(?:microstructure|density|grain|purity|texture|bonding|utili[sz]ation|erosion|cracking|"
               rf"impurit\w*|homogeneity|composition uniformity)"),
    re.compile(rf"\b(?:relative density|grain size|purity|microstructure|texture|impurity content)\s+of\s+(?:the\s+)?"
               rf"(?:[\w-]+\s+){{0,3}}{_T}\b"),
    # Recycling: metals recovered from spent / scrap targets.
    re.compile(rf"\b(?:spent|waste|scrap|discarded|end-of-life|residual)\s+(?:[\w-]+\s+){{0,3}}{_T}\b"),
    re.compile(rf"\b(?:recovery|recovering|recycling|reclamation|leaching|regeneration)\s+of\s+(?:[\w-]+\s+){{0,4}}from\s+"
               rf"(?:the\s+)?(?:[\w-]+\s+){{0,3}}{_T}\b"),
]
SPUTTER_WORDS = ["sputter", "magnetron", "PVD", "physical vapor deposition", "HiPIMS", "濺鍍", "溅射", "スパッタ"]
# "Target" in other fields: nuclear / accelerator targets, laser-plasma targets, drug targets.
OTHER_TARGET_CONTEXT = [
    "nuclear", "accelerator", "cyclotron", "isotope", "neutron", "proton beam", "ion beam", "radioisotope",
    "laser-produced plasma", "laser produced plasma", "euv source", "extreme ultraviolet", "fusion", "spallation",
    "x-ray source", "tumor", "cancer", "drug", "protein", "receptor", "bnct", "neutron capture",
    # fusion devices: divertor "target plates", plasma "recycling"
    "tokamak", "divertor", "scrape-off layer", "gyrokinetic", "edge plasma", "plasma-wall", "plasma wall", "stellarator",
    "plasma-facing", "plasma facing",
]
# Isotope-labelled targets ("120 Sn target", "48Ca targets") are nuclear-physics targets.
ISOTOPE_TARGET = re.compile(r"\b\d{2,3}\s?[A-Z][a-z]?\s+targets?\b")
TARGET_IN_TITLE = re.compile(r"(?<![a-z])targets?(?![a-z])|靶|ターゲット|타겟")
NOT_A_PAPER = re.compile(r"^(?:decision letter|review for|author response|reply to|response to|erratum|corrigendum|retraction)\b", re.I)
NON_PAPER_TYPES = {"peer-review", "paratext", "erratum", "retraction", "editorial"}


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
    title_zh: str = ""        # Traditional Chinese display text (see translate.py)
    abstract_zh: str = ""

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
            "title_zh": self.title_zh,
            "abstract_zh": self.abstract_zh,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Paper":
        return cls(
            title=d["title"],
            link=d.get("link", ""),
            published=datetime.fromisoformat(d["published"]) if d.get("published") else None,
            venue=d.get("venue", ""),
            authors=list(d.get("authors", [])),
            abstract=d.get("abstract", ""),
            doi=d.get("doi", ""),
            source=d.get("source", ""),
            topics=list(d.get("topics", [])),
            id=d.get("id", ""),
            summary=d.get("summary", ""),
            importance=int(d.get("importance", 0)),
            title_zh=d.get("title_zh", ""),
            abstract_zh=d.get("abstract_zh", ""),
        )


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
    """True when the paper is about making / characterising sputtering targets.

    Merely depositing films "from a ZnO target" does not count: the title must name the
    target as its subject, or the abstract must describe processing the target itself.
    """
    if NOT_A_PAPER.match(p.title.strip()):
        return False
    title = p.title.lower()
    if any(_match(title, t) for t in STRONG_TERMS):
        return True
    text = f"{p.text}\n{p.venue}".lower()  # the journal name tells nuclear targets apart too
    # Below here "target" alone is ambiguous: rule out nuclear / laser-plasma / biomedical targets.
    if any(_match(text, w) for w in OTHER_TARGET_CONTEXT) or ISOTOPE_TARGET.search(p.title):
        return False
    if TARGET_IN_TITLE.search(title) and any(_match(title, w) for w in MANUFACTURING_WORDS):
        return True
    abstract = p.abstract.lower()
    return any(rx.search(abstract) for rx in TARGET_PROCESS_PATTERNS)


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
    # title_and_abstract.search skips full-text-only matches, which are mostly papers that merely use a target.
    q = query.replace(",", " ")
    params = {
        "filter": f"title_and_abstract.search:{q},from_publication_date:{since.isoformat()}",
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
        if not title or w.get("type") in NON_PAPER_TYPES:
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

def arxiv_url(queries: str | list[str], max_results: int) -> str:
    """One arXiv API call for all terms (OR-ed): arXiv rate-limits back-to-back requests."""
    terms = [queries] if isinstance(queries, str) else queries
    q = " OR ".join(f'abs:"{t}"' if " " in t else f"abs:{t}" for t in terms)
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

ARXIV_DELAY = 5.0  # arXiv asks clients to wait a few seconds between API calls


def _slow_fetch(url: str) -> bytes:
    return http_fetch(url, timeout=60)


def collect_papers(
    rs: ResearchSettings,
    fetch: Fetcher = _slow_fetch,
    *,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stats: list[dict] | None = None,
) -> list[Paper]:
    """Fetch every source and keep the relevant, recent papers. When `stats` is given, one
    {source, url, fetched, relevant, error} record per source is appended to it."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=rs.days)).date()
    jobs: list[tuple[str, str]] = []
    if rs.openalex:
        mailto = rs.mailto or os.environ.get("OPENALEX_MAILTO", "")
        api_key = os.environ.get("OPENALEX_API_KEY", "")
        jobs += [(openalex_url(q, since, rs.per_query, mailto, api_key), "OpenAlex") for q in rs.queries]
    if rs.arxiv:
        jobs.append((arxiv_url(rs.arxiv_queries or rs.queries[:3], rs.per_query), "arXiv"))
    jobs += [(f["url"], f.get("name", "journal")) for f in rs.feeds]

    raw: list[Paper] = []
    for i, (url, source) in enumerate(jobs):
        if i:
            delay = max(rs.request_delay, ARXIV_DELAY) if source == "arXiv" else rs.request_delay
            if delay:
                sleep(delay)
        attempts = 2 if source == "arXiv" else 1  # arXiv often answers 429 / times out once
        got = None
        for attempt in range(attempts):
            try:
                data = fetch(url)
                got = parse_openalex(data) if source == "OpenAlex" else parse_feed_papers(data, source)
                break
            except Exception as exc:  # one failing source must not stop the run
                if attempt + 1 < attempts:
                    log.info("paper source retry (%s): %s", source, exc)
                    sleep(15)
                else:
                    log.warning("paper source failed (%s %s): %s", source, url[:80], exc)
                    if stats is not None:
                        stats.append({"source": source, "url": url, "fetched": None, "relevant": 0, "error": str(exc)[:120]})
        if got is None:
            continue
        relevant = sum(1 for p in got if not (p.published and p.published.date() < since) and is_relevant(p))
        log.info("%-10s %3d papers, %2d relevant  %s", source, len(got), relevant, url[:90])
        if stats is not None:
            stats.append({"source": source, "url": url, "fetched": len(got), "relevant": relevant, "error": ""})
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

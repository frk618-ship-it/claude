"""Where demand signals come from: news searches (product × industry, product × tender,
watch-list accounts), custom RSS feeds and manually imported CSV rows (trade-show leads,
inbound inquiries, customs records, tender notices copied from procurement portals)."""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from target_news_agent.sources import bing_news_url, google_news_url, http_fetch, parse_feed

from .config import Config
from .signals import Signal, classify, signal_id

log = logging.getLogger(__name__)

Fetcher = Callable[[str], bytes]

TENDER_TERMS = ["招標", "採購", "招标", "采购", "tender", "RFQ", "procurement"]
REGION_EDITION = {"TW": "zh-TW", "CN": "zh-CN", "JP": "ja", "KR": "ko"}


@dataclass
class Job:
    label: str          # what this search is for, e.g. "鈦合金 × 航太"
    query: str
    edition: str


def _or(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"' if " " in t else t for t in terms)


def build_jobs(config: Config) -> list[Job]:
    jobs: list[Job] = []
    for p in config.products:
        prod = _or(p.search)
        for ind_key in p.industries:
            ind = config.industries[ind_key]
            q = f"({prod}) ({_or(ind.search[:4] or ind.keywords[:4])})"
            jobs += [Job(f"{p.name} × {ind.name}", q, ed) for ed in p.editions]
        jobs += [Job(f"{p.name} × 招標採購", f"({prod}) ({_or(TENDER_TERMS)})", ed) for ed in p.editions]
    for a in config.accounts:
        terms: list[str] = []
        for key in a.products or [p.key for p in config.products]:
            p = config.product(key)
            if p:
                terms += [t for t in p.search[:3] if t not in terms]
        eds = {REGION_EDITION.get(a.region, "en")} | ({"en"} if a.region in ("US", "EU") else set())
        q = f"({_or(a.aliases)}) ({_or(terms[:6])})"
        jobs += [Job(f"客戶:{a.name}", q, ed) for ed in sorted(eds) if ed in config.settings.editions]
    return jobs


def feed_urls(config: Config, job: Job) -> list[tuple[str, str]]:
    s = config.settings
    edition = s.editions[job.edition]
    urls = []
    if s.google_news:
        urls.append((google_news_url(job.query, edition, s.days), "Google News"))
    if s.bing_news:
        urls.append((bing_news_url(job.query, edition), "Bing News"))
    return urls


def iter_urls(config: Config):
    for job in build_jobs(config):
        for url, label in feed_urls(config, job):
            yield job, url, label
    for f in config.settings.feeds:
        yield Job(f.get("name", "RSS"), "", f.get("edition", "")), f["url"], f.get("name", "RSS")


def collect(config: Config, fetch: Fetcher = http_fetch, *, sleep: Callable[[float], None] = time.sleep) -> list[Signal]:
    """Run every search; return classified signals (items that name no product line are dropped)."""
    signals: list[Signal] = []
    jobs = list(iter_urls(config))
    failures = 0
    for i, (job, url, label) in enumerate(jobs):
        if i and config.settings.request_delay:
            sleep(config.settings.request_delay)
        try:
            data = fetch(url)
        except Exception as exc:  # one failed search must not abort the run
            failures += 1
            log.warning("fetch failed (%s, %s): %s", job.label, job.edition, exc)
            continue
        items = parse_feed(data, edition=job.edition, query_entity=job.label, default_source=label)
        kept = 0
        for it in items:
            sig = classify(
                config, title=it.title, snippet=it.snippet, link=it.link, source=it.source,
                published=it.published, edition=job.edition, origin="feed" if not job.query else "news",
            )
            if sig:
                signals.append(sig)
                kept += 1
        log.info("%-28s %-6s %3d items, %3d signals", job.label, job.edition, len(items), kept)
    if jobs and failures == len(jobs):
        raise RuntimeError("all searches failed; check network / proxy settings")
    return signals


# ── manual CSV import ───────────────────────────────────────────────────

CSV_FIELDS = ["date", "org", "title", "product", "industry", "region", "intent", "note", "link", "source", "weight"]


def load_csv(path: str | Path, config: Config) -> list[Signal]:
    """Rows from trade shows, inquiries, customs data or procurement portals.

    Columns (UTF-8, header row): date, org, title, product, industry, region, intent, note, link, source, weight.
    Lines starting with "#" are comments. Empty product / industry / region / intent are inferred from title + note like news;
    product may list several keys separated by "/" (e.g. "titanium/nickel").
    """
    out: list[Signal] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]   # "#" lines are comments
        for row in csv.DictReader(lines):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            title = row.get("title") or row.get("note") or ""
            if not title:
                continue
            org = row.get("org", "")
            text_title = f"{org} {title}".strip() if org and org not in title else title
            sig = classify(
                config, title=text_title, snippet=row.get("note", ""), link=row.get("link", ""),
                source=row.get("source") or "手動匯入", published=row.get("date", ""), origin="manual",
            )
            products = [p for p in row.get("product", "").replace(",", "/").split("/") if config.product(p.strip())]
            if sig is None:
                if not products:
                    log.warning("skipped (no product line): %s", title)
                    continue
                sig = Signal(id="", title=text_title, snippet=row.get("note", ""), link=row.get("link", ""),
                             source=row.get("source") or "手動匯入", published=row.get("date", "")[:10], origin="manual")
            if products:
                sig.products = [p.strip() for p in products]
                sig.industries = {p: sig.industries.get(p, "other") for p in sig.products}
            ind = row.get("industry", "")
            if ind in config.industries:
                sig.industries = {p: ind for p in sig.products}
            if row.get("region") in config.regions:
                sig.region = row["region"]
            if row.get("intent") in config.intents:
                sig.intents = [row["intent"]] + [i for i in sig.intents if i != row["intent"]]
            if org:
                sig.orgs = [org] + [o for o in sig.orgs if o != org]
            try:
                sig.weight_hint = float(row.get("weight") or 0)
            except ValueError:
                sig.weight_hint = 0.0
            sig.relevance = 1.0
            sig.id = "m-" + signal_id(f"{org}|{title}|{sig.published}")
            out.append(sig)
    return out

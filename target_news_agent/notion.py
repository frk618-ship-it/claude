"""Sync news items and research papers into Notion databases.

Uses the Notion REST API directly (standard library only). Needs an internal
integration token (NOTION_TOKEN) that has been shared with the target page or
database. `create_database` builds a database with the expected properties under
a parent page; `sync` adds one row per news item, skipping items already there.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Callable

from .models import NewsItem
from .research import Paper

log = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_TEXT = 2000  # Notion's per rich-text-object limit

# Property name -> Notion property schema. Names are what the person sees in Notion.
PROPERTIES = {
    "標題": {"title": {}},
    "日期": {"date": {}},
    "公司": {"multi_select": {}},
    "公司類型": {"multi_select": {}},
    "類別": {"multi_select": {}},
    "來源": {"select": {}},
    "語系": {"select": {}},
    "重要度": {"number": {}},
    "分數": {"number": {}},
    "摘要": {"rich_text": {}},
    "連結": {"url": {}},
    "ID": {"rich_text": {}},
}

# Research papers live in their own database: different fields from news.
PAPER_PROPERTIES = {
    "標題": {"title": {}},
    "日期": {"date": {}},
    "主題": {"multi_select": {}},
    "期刊": {"select": {}},
    "作者": {"rich_text": {}},
    "來源": {"select": {}},
    "重要度": {"number": {}},
    "摘要": {"rich_text": {}},
    "原文摘要": {"rich_text": {}},
    "DOI": {"rich_text": {}},
    "連結": {"url": {}},
    "ID": {"rich_text": {}},
}

# Tracked companies (from the watchlist): one row per company, updated with each report's counts.
COMPANY_PROPERTIES = {
    "名稱": {"title": {}},
    "類型": {"select": {}},
    "地區": {"select": {}},
    "股票代號": {"rich_text": {}},
    "搜尋別名": {"rich_text": {}},
    "本期則數": {"number": {}},
    "最近報導": {"date": {}},
}

NEWS_TITLE, PAPERS_TITLE, COMPANIES_TITLE = "靶材產業新聞", "靶材技術文獻", "靶材追蹤公司"

Transport = Callable[[str, str, dict | None], dict]


class NotionError(RuntimeError):
    pass


def notion_configured() -> bool:
    return bool(os.environ.get("NOTION_TOKEN") and os.environ.get("NOTION_DATABASE_ID"))


def http_transport(token: str, *, retries: int = 3, sleep: Callable[[float], None] = time.sleep) -> Transport:
    def call(method: str, path: str, body: dict | None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(retries + 1):
            req = urllib.request.Request(
                f"{API}{path}",
                data=data,
                method=method,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                    sleep(float(exc.headers.get("Retry-After") or 2 ** attempt))
                    continue
                detail = exc.read().decode("utf-8", "replace")
                raise NotionError(f"Notion API {method} {path} -> {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                raise NotionError(f"Notion API {method} {path}: cannot connect ({exc.reason})") from exc
        raise NotionError(f"Notion API {method} {path}: retries exhausted")

    return call


def _text(value: str) -> list[dict]:
    value = value[:MAX_TEXT]
    return [{"type": "text", "text": {"content": value}}] if value else []


def _option(name: str) -> str:
    # Select option names may not contain commas.
    return name.replace(",", " ")[:100]


def item_properties(item: NewsItem, roles: dict[str, str] | None = None) -> dict:
    """roles: entity name -> role label (生產商, 濺鍍設備商, 封裝廠 ...) for the 公司類型 column."""
    types = list(dict.fromkeys(roles[e] for e in item.entities if roles and e in roles))
    props: dict = {
        "標題": {"title": _text(item.title_zh or item.title)},
        "公司": {"multi_select": [{"name": _option(e)} for e in item.entities]},
        "公司類型": {"multi_select": [{"name": _option(t)} for t in types]},
        "類別": {"multi_select": [{"name": _option(c)} for c in item.categories]},
        "分數": {"number": round(item.score, 2)},
        "摘要": {"rich_text": _text(item.summary or item.snippet_zh or item.snippet)},
        "連結": {"url": item.link or None},
        "ID": {"rich_text": _text(item.id)},
    }
    if item.published:
        props["日期"] = {"date": {"start": item.published.date().isoformat()}}
    if item.source:
        props["來源"] = {"select": {"name": _option(item.source)}}
    if item.edition:
        props["語系"] = {"select": {"name": _option(item.edition)}}
    if item.importance:
        props["重要度"] = {"number": item.importance}
    return props


def paper_properties(paper: Paper) -> dict:
    authors = ", ".join(paper.authors[:10]) + (" et al." if len(paper.authors) > 10 else "")
    props: dict = {
        "標題": {"title": _text(paper.title_zh or paper.title)},
        "主題": {"multi_select": [{"name": _option(t)} for t in paper.topics]},
        "作者": {"rich_text": _text(authors)},
        "摘要": {"rich_text": _text(paper.summary or paper.abstract_zh)},
        "原文摘要": {"rich_text": _text(paper.abstract)},
        "DOI": {"rich_text": _text(paper.doi)},
        "連結": {"url": paper.link or None},
        "ID": {"rich_text": _text(paper.id)},
    }
    if paper.published:
        props["日期"] = {"date": {"start": paper.published.date().isoformat()}}
    if paper.venue:
        props["期刊"] = {"select": {"name": _option(paper.venue)}}
    if paper.source:
        props["來源"] = {"select": {"name": _option(paper.source)}}
    if paper.importance:
        props["重要度"] = {"number": paper.importance}
    return props


def create_database(
    call: Transport, parent_page_id: str, title: str = "靶材產業新聞", properties: dict | None = None
) -> str:
    resp = call(
        "POST",
        "/databases",
        {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": _text(title),
            "properties": properties or PROPERTIES,
        },
    )
    return resp["id"]


def _plain(rich: list[dict]) -> str:
    return "".join(r.get("plain_text") or r.get("text", {}).get("content", "") for r in rich)


def _norm_id(page_id: str) -> str:
    return page_id.replace("-", "").lower()


def find_or_create_database(call: Transport, parent_page_id: str, title: str, properties: dict) -> str:
    """The database titled `title` directly under the parent page; created if it isn't there yet."""
    resp = call("POST", "/search", {"query": title, "filter": {"property": "object", "value": "database"}, "page_size": 100})
    for db in resp.get("results", []):
        parent = db.get("parent", {})
        if _plain(db.get("title", [])) == title and _norm_id(parent.get("page_id", "")) == _norm_id(parent_page_id):
            return db["id"]
    return create_database(call, parent_page_id, title, properties)


def ensure_properties(call: Transport, database_id: str, properties: dict) -> list[str]:
    """Add columns that a database created by an older version is missing; returns the names added."""
    have = call("GET", f"/databases/{database_id}", None).get("properties", {})
    missing = {name: schema for name, schema in properties.items() if name not in have and "title" not in schema}
    if missing:
        call("PATCH", f"/databases/{database_id}", {"properties": missing})
    return list(missing)


def company_properties(entity, count: int, last_date: str | None) -> dict:
    from .config import ROLE_LABELS

    props: dict = {
        "名稱": {"title": _text(entity.name)},
        "類型": {"select": {"name": _option(ROLE_LABELS.get(entity.role, entity.role))}},
        "股票代號": {"rich_text": _text(entity.ticker)},
        "搜尋別名": {"rich_text": _text(", ".join(entity.aliases))},
        "本期則數": {"number": count},
    }
    if entity.region:
        props["地區"] = {"select": {"name": _option(entity.region)}}
    if last_date:
        props["最近報導"] = {"date": {"start": last_date}}
    return props


def sync_companies(
    call: Transport,
    database_id: str,
    entities: list,
    counts: dict[str, int],
    last_dates: dict[str, str],
    *,
    delay: float = 0.35,
    sleep: Callable[[float], None] | None = None,
) -> tuple[int, int]:
    """One row per tracked company (topics excluded); returns (created, updated)."""
    sleep = sleep or time.sleep
    rows: dict[str, dict] = {}
    body: dict = {"page_size": 100}
    while True:
        resp = call("POST", f"/databases/{database_id}/query", body)
        for page in resp.get("results", []):
            name = _plain(page.get("properties", {}).get("名稱", {}).get("title", []))
            if name:
                rows[name] = page
        if not resp.get("has_more"):
            break
        body["start_cursor"] = resp["next_cursor"]

    created = updated = 0
    for e in entities:
        if e.is_topic:
            continue
        count = counts.get(e.name, 0)
        last = last_dates.get(e.name)
        page = rows.get(e.name)
        if page is not None:
            props = page.get("properties", {})
            old_count = props.get("本期則數", {}).get("number")
            old_last = (props.get("最近報導", {}).get("date") or {}).get("start")
            if old_count == count and (last is None or old_last == last):
                continue
        if created + updated and delay:
            sleep(delay)
        if page is None:
            call("POST", "/pages", {"parent": {"database_id": database_id}, "properties": company_properties(e, count, last)})
            created += 1
        else:
            call("PATCH", f"/pages/{page['id']}", {"properties": company_properties(e, count, last)})
            updated += 1
    return created, updated


def existing_ids(call: Transport, database_id: str, since: str | None) -> set[str]:
    """IDs of rows dated on/after `since` (YYYY-MM-DD; None = every row), so re-runs don't duplicate."""
    ids: set[str] = set()
    body: dict = {"page_size": 100}
    if since:
        body["filter"] = {"property": "日期", "date": {"on_or_after": since}}
    while True:
        resp = call("POST", f"/databases/{database_id}/query", body)
        for page in resp.get("results", []):
            rich = page.get("properties", {}).get("ID", {}).get("rich_text", [])
            if rich:
                ids.add("".join(r.get("plain_text") or r.get("text", {}).get("content", "") for r in rich))
        if not resp.get("has_more"):
            return ids
        body["start_cursor"] = resp["next_cursor"]


def sync(
    items: list,
    call: Transport,
    database_id: str,
    *,
    properties: Callable[[object], dict] = item_properties,
    delay: float = 0.35,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Add each record (NewsItem, or Paper with properties=paper_properties) as a row.

    Returns the number of rows created; rows already present (by ID) are skipped.
    """
    if not items:
        return 0
    sleep = sleep or time.sleep
    # Undated records could match rows of any date, so then check the whole database.
    if all(i.published for i in items):
        since = (min(i.published for i in items) - timedelta(days=1)).date().isoformat()
    else:
        since = None
    seen = existing_ids(call, database_id, since)
    created = 0
    for item in items:
        if item.id in seen:
            continue
        if created and delay:
            sleep(delay)  # Notion allows ~3 requests/second per integration
        call("POST", "/pages", {"parent": {"database_id": database_id}, "properties": properties(item)})
        seen.add(item.id)
        created += 1
    return created

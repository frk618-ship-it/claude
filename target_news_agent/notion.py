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


def item_properties(item: NewsItem) -> dict:
    props: dict = {
        "標題": {"title": _text(item.title_zh or item.title)},
        "公司": {"multi_select": [{"name": _option(e)} for e in item.entities]},
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
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Add each record (NewsItem, or Paper with properties=paper_properties) as a row.

    Returns the number of rows created; rows already present (by ID) are skipped.
    """
    if not items:
        return 0
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

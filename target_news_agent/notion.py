"""Sync news items into a Notion database.

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
        "標題": {"title": _text(item.title)},
        "公司": {"multi_select": [{"name": _option(e)} for e in item.entities]},
        "類別": {"multi_select": [{"name": _option(c)} for c in item.categories]},
        "分數": {"number": round(item.score, 2)},
        "摘要": {"rich_text": _text(item.summary or item.snippet)},
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


def create_database(call: Transport, parent_page_id: str, title: str = "靶材產業新聞") -> str:
    resp = call(
        "POST",
        "/databases",
        {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": _text(title),
            "properties": PROPERTIES,
        },
    )
    return resp["id"]


def existing_ids(call: Transport, database_id: str, since: str) -> set[str]:
    """IDs of rows dated on/after `since` (YYYY-MM-DD), so re-runs don't duplicate."""
    ids: set[str] = set()
    body: dict = {"filter": {"property": "日期", "date": {"on_or_after": since}}, "page_size": 100}
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
    items: list[NewsItem],
    call: Transport,
    database_id: str,
    *,
    delay: float = 0.35,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Add each item as a row; returns the number of rows created."""
    dated = [i.published for i in items if i.published]
    if not items:
        return 0
    since = (min(dated) - timedelta(days=1)).date().isoformat() if dated else "1970-01-01"
    seen = existing_ids(call, database_id, since)
    created = 0
    for item in items:
        if item.id in seen:
            continue
        if created and delay:
            sleep(delay)  # Notion allows ~3 requests/second per integration
        call("POST", "/pages", {"parent": {"database_id": database_id}, "properties": item_properties(item)})
        seen.add(item.id)
        created += 1
    return created

"""SQLite record of items already reported, so each run only surfaces new news."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import NewsItem


class SeenStore:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS seen ("
            " id TEXT PRIMARY KEY, title TEXT, link TEXT, first_seen TEXT)"
        )

    def filter_new(self, items: list[NewsItem]) -> list[NewsItem]:
        if not items:
            return []
        ids = [i.id for i in items]
        placeholders = ",".join("?" * len(ids))
        seen = {row[0] for row in self.conn.execute(f"SELECT id FROM seen WHERE id IN ({placeholders})", ids)}
        return [i for i in items if i.id not in seen]

    def mark(self, items: list[NewsItem]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen (id, title, link, first_seen) VALUES (?, ?, ?, ?)",
            [(i.id, i.title, i.link, now) for i in items],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

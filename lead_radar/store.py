"""SQLite history of every demand signal, so heat can be compared week over week."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .signals import Signal


class SignalStore:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, published TEXT, first_seen TEXT, data TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    def upsert(self, signals: list[Signal], seen: date) -> int:
        """Insert new signals; refresh existing ones but never drop Claude enrichment. Returns # new."""
        new = 0
        for s in signals:
            row = self.conn.execute("SELECT data FROM signals WHERE id = ?", (s.id,)).fetchone()
            if row is None:
                new += 1
                self.conn.execute(
                    "INSERT INTO signals (id, published, first_seen, data) VALUES (?, ?, ?, ?)",
                    (s.id, s.published, seen.isoformat(), json.dumps(s.to_dict(), ensure_ascii=False)),
                )
                continue
            old = Signal.from_dict(json.loads(row[0]))
            if old.enriched and not s.enriched:
                continue
            self.conn.execute("UPDATE signals SET published = ?, data = ? WHERE id = ?",
                              (s.published, json.dumps(s.to_dict(), ensure_ascii=False), s.id))
        self.conn.commit()
        return new

    def all(self, since: date | None = None) -> list[Signal]:
        q, args = "SELECT data FROM signals", ()
        if since:
            q, args = q + " WHERE published >= ?", (since.isoformat(),)
        return [Signal.from_dict(json.loads(r[0])) for r in self.conn.execute(q + " ORDER BY published", args)]

    def first_seen(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT id, first_seen FROM signals"))

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def mark_coverage(self, run_day: date, days: int) -> date:
        """News searches reach `days` back, so data is complete from (first run − days) on."""
        from datetime import timedelta

        start = run_day - timedelta(days=days)
        old = self.get_meta("coverage_start")
        if old and date.fromisoformat(old) <= start:
            return date.fromisoformat(old)
        self.set_meta("coverage_start", start.isoformat())
        return start

    def close(self) -> None:
        self.conn.close()

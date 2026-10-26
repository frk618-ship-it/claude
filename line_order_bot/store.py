"""SQLite storage: customer drafts, inquiries (詢價單) and their event log."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

from .parser import OrderFields

STATUS_LABELS = {
    "new": "待報價",
    "claimed": "處理中",
    "need_info": "待客戶補充",
    "quoted": "已報價",
    "accepted": "客戶接受報價",
    "confirmed": "訂單成立",
    "declined": "客戶婉拒報價",
    "rejected": "公司婉拒",
    "cancelled": "客戶取消",
    "expired": "報價逾期",
}
OPEN_STATUSES = ("new", "claimed", "need_info", "quoted", "accepted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inquiries (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    customer_name TEXT DEFAULT '',
    company TEXT DEFAULT '',
    product TEXT DEFAULT '',
    weight_kg REAL,
    weight_text TEXT DEFAULT '',
    due_date TEXT DEFAULT '',
    due_text TEXT DEFAULT '',
    destination TEXT DEFAULT '',
    note TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    assignee TEXT DEFAULT '',
    price REAL,
    currency TEXT DEFAULT '',
    price_unit TEXT DEFAULT '',
    total REAL,
    lead_time TEXT DEFAULT '',
    lead_date TEXT DEFAULT '',
    valid_until TEXT DEFAULT '',
    quote_note TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    quoted_at TEXT DEFAULT '',
    reminded_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS inquiries_user ON inquiries(user_id, created_at);
CREATE INDEX IF NOT EXISTS inquiries_status ON inquiries(status, created_at);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id TEXT NOT NULL,
    at TEXT NOT NULL,
    actor TEXT DEFAULT '',
    kind TEXT NOT NULL,
    detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS drafts (
    user_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.lock = threading.RLock()

    # ------------------------------------------------------------ drafts

    def get_draft(self, user_id: str) -> OrderFields | None:
        row = self.conn.execute("SELECT data FROM drafts WHERE user_id = ?", (user_id,)).fetchone()
        return OrderFields(**json.loads(row["data"])) if row else None

    def save_draft(self, user_id: str, fields: OrderFields, now: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO drafts (user_id, data, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                (user_id, json.dumps(asdict(fields), ensure_ascii=False), now),
            )

    def clear_draft(self, user_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))

    # ------------------------------------------------------------ inquiries

    def create_inquiry(self, user_id: str, customer_name: str, fields: OrderFields, day: str, now: str) -> str:
        """`day` is YYYYMMDD; ids look like Q20261026-001."""
        with self.lock, self.conn:
            prefix = f"Q{day}-"
            n = self.conn.execute("SELECT COUNT(*) FROM inquiries WHERE id LIKE ?", (prefix + "%",)).fetchone()[0]
            inquiry_id = f"{prefix}{n + 1:03d}"
            data = asdict(fields)
            self.conn.execute(
                "INSERT INTO inquiries (id, user_id, customer_name, company, product, weight_kg, weight_text, due_date, "
                "due_text, destination, note, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'new', ?, ?)",
                (inquiry_id, user_id, customer_name, data["company"], data["product"], data["weight_kg"],
                 data["weight_text"], data["due_date"], data["due_text"], data["destination"], data["note"], now, now),
            )
            self._event(inquiry_id, now, user_id, "created", "")
        return inquiry_id

    def get(self, inquiry_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM inquiries WHERE id = ?", (inquiry_id.upper(),)).fetchone()
        return dict(row) if row else None

    def update(self, inquiry_id: str, now: str, actor: str = "", event: str = "", detail: str = "", **fields) -> None:
        with self.lock, self.conn:
            cols = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(
                f"UPDATE inquiries SET {cols + ', ' if cols else ''}updated_at = ? WHERE id = ?",
                (*fields.values(), now, inquiry_id),
            )
            if event:
                self._event(inquiry_id, now, actor, event, detail)

    def list(self, statuses: tuple[str, ...] | None = None, user_id: str | None = None, limit: int = 20) -> list[dict]:
        sql, args = "SELECT * FROM inquiries WHERE 1=1", []
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        if user_id:
            sql += " AND user_id = ?"
            args.append(user_id)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    def events(self, inquiry_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM events WHERE inquiry_id = ? ORDER BY seq", (inquiry_id,))
        return [dict(r) for r in rows]

    def _event(self, inquiry_id: str, at: str, actor: str, kind: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO events (inquiry_id, at, actor, kind, detail) VALUES (?, ?, ?, ?, ?)",
            (inquiry_id, at, actor, kind, detail),
        )

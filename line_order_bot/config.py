"""Load config/line_order_bot.yaml; LINE credentials come from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Settings:
    company_name: str = "本公司"
    timezone: str = "Asia/Taipei"
    db: str = "data/line_order_bot/orders.sqlite3"
    staff_group_id: str = ""
    staff_user_ids: list[str] = field(default_factory=list)
    default_currency: str = "TWD"
    quote_valid_days: int = 7
    remind_after_hours: float = 4.0
    min_order_kg: float = 0.0
    use_claude: bool = True
    channel_secret: str = ""
    channel_access_token: str = ""

    @property
    def staff_targets(self) -> list[str]:
        """Where staff notifications are pushed: the staff group, else each staff member."""
        return [self.staff_group_id] if self.staff_group_id else list(self.staff_user_ids)


def load_config(path: str | Path) -> Settings:
    p = Path(path)
    raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    return Settings(
        company_name=str(raw.get("company_name", "本公司")),
        timezone=str(raw.get("timezone", "Asia/Taipei")),
        db=str(raw.get("db", "data/line_order_bot/orders.sqlite3")),
        staff_group_id=str(os.environ.get("LINE_STAFF_GROUP_ID") or raw.get("staff_group_id", "") or ""),
        staff_user_ids=[str(u) for u in raw.get("staff_user_ids", []) or []],
        default_currency=str(raw.get("default_currency", "TWD")).upper(),
        quote_valid_days=int(raw.get("quote_valid_days", 7)),
        remind_after_hours=float(raw.get("remind_after_hours", 4)),
        min_order_kg=float(raw.get("min_order_kg", 0) or 0),
        use_claude=bool(raw.get("use_claude", True)),
        channel_secret=os.environ.get("LINE_CHANNEL_SECRET", ""),
        channel_access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
    )

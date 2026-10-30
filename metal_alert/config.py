"""Load ``config/metal_alert.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class MetalSpec:
    name: str        # 顯示名稱,例如 黃金
    code: str        # 元素符號,例如 Au
    symbol: str      # Yahoo Finance 代號,例如 GC=F
    unit: str = "USD/oz"


@dataclass
class PriceAlert:
    code: str
    below: float | None = None
    above: float | None = None


@dataclass
class Config:
    metals: list[MetalSpec]
    indicators: dict[str, dict]
    levels: dict[str, float]
    interval_minutes: int = 60
    history_range: str = "60d"
    bar_interval: str = "1h"
    daily_range: str = "1y"
    chart_bars: int = 240
    cooldown_hours: float = 6
    forward_bars: int = 24
    tz: str = "Asia/Taipei"
    price_alerts: list[PriceAlert] = field(default_factory=list)

    def ind(self, name: str) -> dict:
        return self.indicators.get(name) or {}


DEFAULT_LEVELS = {"strong_buy": 60, "buy": 30, "sell": -30, "strong_sell": -60}


def load_config(path: str | Path = "config/metal_alert.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    s = raw.get("settings") or {}
    metals = [MetalSpec(str(m["name"]), str(m["code"]), str(m["symbol"]), str(m.get("unit", "USD/oz")))
              for m in raw.get("metals") or []]
    alerts = [PriceAlert(str(a["code"]), a.get("below"), a.get("above")) for a in raw.get("price_alerts") or []]
    return Config(
        metals=metals,
        indicators=raw.get("indicators") or {},
        levels={**DEFAULT_LEVELS, **(raw.get("levels") or {})},
        interval_minutes=int(s.get("interval_minutes", 60)),
        history_range=str(s.get("history_range", "60d")),
        bar_interval=str(s.get("bar_interval", "1h")),
        daily_range=str(s.get("daily_range", "1y")),
        chart_bars=int(s.get("chart_bars", 240)),
        cooldown_hours=float(s.get("cooldown_hours", 6)),
        forward_bars=int(s.get("forward_bars", 24)),
        tz=str(s.get("tz", "Asia/Taipei")),
        price_alerts=alerts,
    )

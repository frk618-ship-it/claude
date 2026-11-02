"""Load `config/lead_radar.yaml` into typed objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Product:
    key: str
    name: str
    keywords: list[str]
    search: list[str]
    industries: list[str]
    editions: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    pitch: str = ""


@dataclass
class Industry:
    key: str
    name: str
    keywords: list[str]
    search: list[str] = field(default_factory=list)


@dataclass
class Intent:
    key: str
    name: str
    weight: float
    keywords: list[str]
    action: str = ""


@dataclass
class Region:
    key: str
    name: str
    keywords: list[str]


@dataclass
class Account:
    name: str
    aliases: list[str]
    industry: str = ""
    region: str = ""
    products: list[str] = field(default_factory=list)


@dataclass
class Settings:
    days: int = 30
    half_life_days: float = 14
    history_weeks: int = 8
    request_delay: float = 0.6
    default_editions: list[str] = field(default_factory=lambda: ["zh-TW", "en"])
    editions: dict[str, dict[str, str]] = field(default_factory=dict)
    google_news: bool = True
    bing_news: bool = False
    feeds: list[dict[str, str]] = field(default_factory=list)
    z_hot: float = 1.96
    z_warm: float = 1.0
    min_signals: int = 2
    neighbor_weight: float = 0.3
    grades: dict[str, float] = field(default_factory=lambda: {"A": 70, "B": 45, "C": 25})
    noise_terms: list[str] = field(default_factory=list)
    noise_factor: float = 0.3
    exclude_orgs: list[str] = field(default_factory=list)


@dataclass
class Config:
    settings: Settings
    products: list[Product]
    industries: dict[str, Industry]
    intents: dict[str, Intent]
    regions: dict[str, Region]
    accounts: list[Account]

    def product(self, key: str) -> Product | None:
        return next((p for p in self.products if p.key == key), None)

    def account(self, name: str) -> Account | None:
        return next((a for a in self.accounts if a.name == name), None)

    @property
    def region_keys(self) -> list[str]:
        return list(self.regions)


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    s = raw.get("settings", {}) or {}
    hs = s.get("hotspot", {}) or {}
    settings = Settings(
        days=int(s.get("days", 30)),
        half_life_days=float(s.get("half_life_days", 14)),
        history_weeks=int(s.get("history_weeks", 8)),
        request_delay=float(s.get("request_delay", 0.6)),
        default_editions=list(s.get("default_editions", ["zh-TW", "en"])),
        editions=dict(s.get("editions", {})),
        google_news=bool(s.get("google_news", True)),
        bing_news=bool(s.get("bing_news", False)),
        feeds=list(s.get("feeds", []) or []),
        z_hot=float(hs.get("z_hot", 1.96)),
        z_warm=float(hs.get("z_warm", 1.0)),
        min_signals=int(hs.get("min_signals", 2)),
        neighbor_weight=float(hs.get("neighbor_weight", 0.3)),
        grades={k: float(v) for k, v in (s.get("grades") or {"A": 70, "B": 45, "C": 25}).items()},
        noise_terms=[str(t) for t in s.get("noise_terms", []) or []],
        noise_factor=float(s.get("noise_factor", 0.3)),
        exclude_orgs=[str(t) for t in s.get("exclude_orgs", []) or []],
    )
    industries = {
        k: Industry(key=k, name=v["name"], keywords=[str(x) for x in v.get("keywords", [])], search=[str(x) for x in v.get("search", [])])
        for k, v in (raw.get("industries") or {}).items()
    }
    products = []
    for p in raw.get("products", []) or []:
        unknown = [i for i in p.get("industries", []) if i not in industries]
        if unknown:
            raise ValueError(f"product {p['key']}: unknown industries {unknown}")
        products.append(
            Product(
                key=p["key"],
                name=p["name"],
                keywords=[str(x) for x in p.get("keywords", [])],
                search=[str(x) for x in p.get("search", [])],
                industries=list(p.get("industries", [])),
                editions=list(p.get("editions", []) or settings.default_editions),
                exclude=[str(x) for x in p.get("exclude", []) or []],
                pitch=p.get("pitch", ""),
            )
        )
    intents = {
        k: Intent(key=k, name=v["name"], weight=float(v.get("weight", 1)), keywords=[str(x) for x in v.get("keywords", [])], action=v.get("action", ""))
        for k, v in (raw.get("intents") or {}).items()
    }
    regions = {k: Region(key=k, name=v["name"], keywords=[str(x) for x in v.get("keywords", [])]) for k, v in (raw.get("regions") or {}).items()}
    accounts = [
        Account(
            name=a["name"],
            aliases=[str(x) for x in a.get("aliases", [a["name"]])],
            industry=a.get("industry", ""),
            region=a.get("region", ""),
            products=list(a.get("products", []) or []),
        )
        for a in raw.get("accounts", []) or []
    ]
    return Config(settings, products, industries, intents, regions, accounts)

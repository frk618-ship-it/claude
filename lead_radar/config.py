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
    pitch_source: str = ""            # where the pitch's specs come from (catalogue, or "general industry specs")


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
    ticker: str = ""
    segment: str = ""                 # key of a segment (see Segment) this account belongs to
    subsegment: str = ""
    fit: dict[str, int] = field(default_factory=dict)   # product key -> 0–3 judged product fit
    note: str = ""                    # why it fits / what it might buy


@dataclass
class Segment:
    """A customer group analysed on its own (e.g. Taiwan semiconductor equipment makers):
    hotspots on a sub-segment × product grid and a ranked map of every account in it."""
    key: str
    name: str
    description: str = ""
    industries: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    editions: list[str] = field(default_factory=list)
    subsegments: dict[str, str] = field(default_factory=dict)   # key -> display name
    query_terms: list[str] = field(default_factory=list)       # added to account-name searches
    days: int = 0                     # 0 = settings.days; segments move slower, so a longer window helps
    half_life_days: float = 0


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
    drop_terms: list[str] = field(default_factory=list)
    primary_segment: str = ""        # the radar's main page (/leads/); empty = the first segment
    links: list[dict[str, str]] = field(default_factory=list)   # [{name, url}] shown as buttons on every page
    exclude_orgs: list[str] = field(default_factory=list)


@dataclass
class Config:
    settings: Settings
    products: list[Product]
    industries: dict[str, Industry]
    intents: dict[str, Intent]
    regions: dict[str, Region]
    accounts: list[Account]
    segments: dict[str, Segment] = field(default_factory=dict)

    @property
    def segment_order(self) -> list[str]:
        """Segments with the primary one (the radar's main page) first."""
        keys = list(self.segments)
        main = self.settings.primary_segment or (keys[0] if keys else "")
        return [main] + [k for k in keys if k != main] if main else keys

    def product(self, key: str) -> Product | None:
        return next((p for p in self.products if p.key == key), None)

    def account(self, name: str) -> Account | None:
        return next((a for a in self.accounts if a.name == name), None)

    @property
    def region_keys(self) -> list[str]:
        return list(self.regions)


ALLOWED = {
    "products": {"key", "name", "keywords", "search", "industries", "editions", "exclude", "pitch", "pitch_source"},
    "industries": {"name", "keywords", "search"},
    "intents": {"name", "weight", "keywords", "action"},
    "regions": {"name", "keywords"},
    "accounts": {"name", "aliases", "industry", "region", "products", "ticker", "segment", "subsegment", "fit", "note"},
}


def _check_keys(raw: dict) -> None:
    """A half-width comma inside a one-line {...} mapping silently splits a value into a new key
    ("action: 先做 A,再做 B" → key "再做 B"); catch that instead of truncating the text."""
    for section, allowed in ALLOWED.items():
        entries = raw.get(section) or []
        items = entries.items() if isinstance(entries, dict) else ((e.get("key") or e.get("name"), e) for e in entries)
        for name, entry in items:
            extra = set(entry or {}) - allowed
            if extra:
                raise ValueError(f"{section} {name}: unknown fields {sorted(extra)} — "
                                 "a value containing a half-width comma must be quoted, e.g. note: \"A,B\"")


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    _check_keys(raw)
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
        drop_terms=[str(t) for t in s.get("drop_terms", []) or []],
        primary_segment=str(s.get("primary_segment", "") or ""),
        links=[{"name": str(l["name"]), "url": str(l["url"])} for l in s.get("links", []) or []
               if isinstance(l, dict) and l.get("name") and str(l.get("url", "")).startswith(("https://", "http://"))],
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
                pitch_source=p.get("pitch_source", "") or "未標示",
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
            ticker=str(a.get("ticker", "") or ""),
            segment=a.get("segment", ""),
            subsegment=a.get("subsegment", ""),
            fit={k: int(v) for k, v in (a.get("fit") or {}).items()},
            note=a.get("note", ""),
        )
        for a in raw.get("accounts", []) or []
    ]
    segments = {}
    for k, v in (raw.get("segments") or {}).items():
        unknown = [i for i in v.get("industries", []) if i not in industries]
        if unknown:
            raise ValueError(f"segment {k}: unknown industries {unknown}")
        segments[k] = Segment(
            key=k,
            name=v["name"],
            description=v.get("description", ""),
            industries=list(v.get("industries", [])),
            regions=list(v.get("regions", [])),
            editions=list(v.get("editions", []) or settings.default_editions),
            query_terms=[str(t) for t in v.get("query_terms", []) or []],
            subsegments={sk: (sv["name"] if isinstance(sv, dict) else str(sv)) for sk, sv in (v.get("subsegments") or {}).items()},
            days=int(v.get("days", 0) or 0),
            half_life_days=float(v.get("half_life_days", 0) or 0),
        )
    if settings.primary_segment and settings.primary_segment not in segments:
        raise ValueError(f"settings.primary_segment {settings.primary_segment}: no such segment")
    for a in accounts:
        if a.segment and a.segment not in segments:
            raise ValueError(f"account {a.name}: unknown segment {a.segment}")
    return Config(settings, products, industries, intents, regions, accounts, segments)

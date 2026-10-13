"""Load the watchlist / settings YAML into typed objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROLE_LABELS = {"producer": "生產商", "distributor": "銷售商", "topic": "產業主題"}


@dataclass
class Entity:
    name: str
    role: str
    aliases: list[str]
    region: str = ""
    ticker: str = ""
    require_context: bool = False
    editions: list[str] = field(default_factory=list)

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_topic(self) -> bool:
        return self.role == "topic"


@dataclass
class Settings:
    days: int = 3
    request_delay: float = 0.6
    default_editions: list[str] = field(default_factory=lambda: ["zh-TW", "en"])
    editions: dict[str, dict[str, str]] = field(default_factory=dict)
    google_news: bool = True
    bing_news: bool = False
    feeds: list[dict[str, str]] = field(default_factory=list)
    context_terms: list[str] = field(default_factory=list)
    query_context_terms: list[str] = field(default_factory=list)
    categories: dict[str, list[str]] = field(default_factory=dict)
    hot_terms: list[str] = field(default_factory=list)


@dataclass
class Config:
    settings: Settings
    entities: list[Entity]


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    s = raw.get("settings", {}) or {}
    sources = s.get("sources", {}) or {}
    settings = Settings(
        days=int(s.get("days", 3)),
        request_delay=float(s.get("request_delay", 0.6)),
        default_editions=list(s.get("default_editions", ["zh-TW", "en"])),
        editions=dict(s.get("editions", {}) or {}),
        google_news=bool(sources.get("google_news", True)),
        bing_news=bool(sources.get("bing_news", False)),
        feeds=list(sources.get("feeds", []) or []),
        context_terms=[str(t) for t in s.get("context_terms", [])],
        query_context_terms=[str(t) for t in s.get("query_context_terms") or s.get("context_terms", [])],
        categories={k: [str(t) for t in v] for k, v in (s.get("categories", {}) or {}).items()},
        hot_terms=[str(t) for t in s.get("hot_terms", [])],
    )

    entities = []
    for e in raw.get("entities", []) or []:
        aliases = [str(a) for a in e.get("aliases", [])]
        if not aliases:
            raise ValueError(f"entity {e.get('name')!r} has no aliases")
        editions = list(e.get("editions") or settings.default_editions)
        unknown = [ed for ed in editions if ed not in settings.editions]
        if unknown:
            raise ValueError(f"entity {e.get('name')!r} uses unknown editions {unknown}")
        entities.append(
            Entity(
                name=str(e["name"]),
                role=str(e.get("role", "producer")),
                aliases=aliases,
                region=str(e.get("region", "")),
                ticker=str(e.get("ticker", "") or ""),
                require_context=bool(e.get("require_context", False)),
                editions=editions,
            )
        )
    return Config(settings=settings, entities=entities)

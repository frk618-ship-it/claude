from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime | None
    snippet: str = ""
    edition: str = ""
    query_entity: str = ""          # entity whose search query surfaced this item
    entities: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    score: float = 0.0
    id: str = ""
    # filled by the optional LLM digest
    summary: str = ""
    importance: int = 0
    # Traditional Chinese display text (see translate.py); empty = original is already fine
    title_zh: str = ""
    snippet_zh: str = ""

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.snippet}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "link": self.link,
            "source": self.source,
            "published": self.published.isoformat() if self.published else None,
            "snippet": self.snippet,
            "edition": self.edition,
            "entities": self.entities,
            "categories": self.categories,
            "score": round(self.score, 2),
            "summary": self.summary,
            "importance": self.importance,
            "title_zh": self.title_zh,
            "snippet_zh": self.snippet_zh,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NewsItem":
        published = datetime.fromisoformat(d["published"]) if d.get("published") else None
        return cls(
            title=d["title"],
            link=d.get("link", ""),
            source=d.get("source", ""),
            published=published,
            snippet=d.get("snippet", ""),
            edition=d.get("edition", ""),
            entities=list(d.get("entities", [])),
            categories=list(d.get("categories", [])),
            score=float(d.get("score", 0.0)),
            id=d.get("id", ""),
            summary=d.get("summary", ""),
            importance=int(d.get("importance", 0)),
            title_zh=d.get("title_zh", ""),
            snippet_zh=d.get("snippet_zh", ""),
        )

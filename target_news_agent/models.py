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
        }

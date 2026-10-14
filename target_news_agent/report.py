"""Render the Markdown and JSON reports."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .config import Config
from .models import NewsItem
from .processing import GENERAL_CATEGORY, hot_keywords
from .research import GENERAL_TOPIC, Paper
from .summarizer import Digest


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def _item_line(item: NewsItem) -> str:
    date = item.published.strftime("%m/%d") if item.published else "--/--"
    stars = f" {'★' * item.importance}" if item.importance else ""
    line = f"- **[{_md_escape(item.title_zh or item.title)}]({item.link})**{stars}  \n  {date} · {item.source or '未知來源'} · {', '.join(item.entities)}"
    if item.summary:
        line += f"  \n  > {item.summary}"
    return line


def render_markdown(
    items: list[NewsItem], config: Config, digest: Digest, generated: datetime, papers: list[Paper] | None = None
) -> str:
    s = config.settings
    scope = f"範圍:最近 {s.days} 天 · 新聞 {len(items)} 則"
    if papers is not None and config.research:
        scope += f" · 技術文獻 {len(papers)} 篇(最近 {config.research.days} 天)"
    lines = [
        f"# 靶材產業新聞動態 — {generated.strftime('%Y-%m-%d')}",
        "",
        f"{scope} · 產生時間 {generated.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
    ]
    if not items:
        lines += ["本期沒有新的相關新聞。", ""]
    else:
        lines += _news_sections(items, config, digest)
    if papers is not None:
        lines += _paper_sections(papers, config)
    return "\n".join(lines)


def _paper_line(p: Paper) -> str:
    date = p.published.strftime("%Y/%m/%d") if p.published else "日期不明"
    authors = ", ".join(p.authors[:3]) + (" 等" if len(p.authors) > 3 else "")
    stars = f" {'★' * p.importance}" if p.importance else ""
    meta = " · ".join(x for x in (date, p.venue, authors) if x)
    line = f"- **[{_md_escape(p.title_zh or p.title)}]({p.link})**{stars}  \n  {meta}"
    if p.summary:
        line += f"  \n  > {p.summary}"
    return line


def _paper_sections(papers: list[Paper], config: Config) -> list[str]:
    lines = ["## 技術文獻:靶材製造技術", ""]
    if not papers:
        return lines + ["本期沒有新的靶材製造技術文獻。", ""]
    order = list(config.research.topics) if config.research else []
    by_topic: dict[str, list[Paper]] = defaultdict(list)
    for p in papers:
        by_topic[p.topics[0] if p.topics else GENERAL_TOPIC].append(p)
    for topic in [*order, GENERAL_TOPIC]:
        if by_topic.get(topic):
            lines += [f"### {topic}({len(by_topic[topic])})", ""] + [_paper_line(p) for p in by_topic[topic]] + [""]
    return lines


def _news_sections(items: list[NewsItem], config: Config, digest: Digest) -> list[str]:
    s = config.settings
    lines: list[str] = []

    if digest.overview:
        lines += ["## 本期總覽", "", digest.overview, ""]
        if digest.key_trends:
            lines += ["**產業趨勢**", ""] + [f"- {t}" for t in digest.key_trends] + [""]
        if digest.watch_points:
            lines += ["**後續追蹤**", ""] + [f"- {t}" for t in digest.watch_points] + [""]

    top = sorted(items, key=lambda i: (i.importance, i.score), reverse=True)[:5]
    lines += ["## 重點新聞", ""] + [_item_line(i) for i in top] + [""]

    by_cat: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        by_cat[item.categories[0] if item.categories else GENERAL_CATEGORY].append(item)
    lines += ["## 依類別", ""]
    for cat in [*s.categories.keys(), GENERAL_CATEGORY]:
        if by_cat.get(cat):
            lines += [f"### {cat}({len(by_cat[cat])})", ""] + [_item_line(i) for i in by_cat[cat]] + [""]

    by_entity: dict[str, int] = defaultdict(int)
    for item in items:
        for e in item.entities:
            by_entity[e] += 1
    lines += ["## 公司 / 主題新聞量", "", "| 名稱 | 類型 | 地區 | 則數 |", "|---|---|---|---|"]
    for e in sorted(config.entities, key=lambda e: -by_entity.get(e.name, 0)):
        if by_entity.get(e.name):
            lines.append(f"| {e.name} | {e.role_label} | {e.region} | {by_entity[e.name]} |")
    lines.append("")

    hot = hot_keywords(items, s.hot_terms)
    if hot:
        lines += ["## 熱門關鍵字", "", " · ".join(f"{t}({n})" for t, n in hot), ""]
    return lines


def write_reports(
    items: list[NewsItem],
    config: Config,
    digest: Digest,
    out_dir: str | Path,
    generated: datetime,
    papers: list[Paper] | None = None,
) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = generated.strftime("%Y-%m-%d")
    md_path = out / f"{stem}.md"
    json_path = out / f"{stem}.json"
    md_path.write_text(render_markdown(items, config, digest, generated, papers), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "generated": generated.isoformat(),
                "days": config.settings.days,
                "digest": digest.model_dump(),
                "items": [i.to_dict() for i in items],
                **(
                    {"papers": [p.to_dict() for p in papers], "research_days": config.research.days if config.research else None}
                    if papers is not None
                    else {}
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return md_path, json_path

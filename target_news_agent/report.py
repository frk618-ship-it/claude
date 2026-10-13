"""Render the Markdown and JSON reports."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .config import Config
from .models import NewsItem
from .processing import GENERAL_CATEGORY, hot_keywords
from .summarizer import Digest


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def _item_line(item: NewsItem) -> str:
    date = item.published.strftime("%m/%d") if item.published else "--/--"
    stars = f" {'★' * item.importance}" if item.importance else ""
    line = f"- **[{_md_escape(item.title)}]({item.link})**{stars}  \n  {date} · {item.source or '未知來源'} · {', '.join(item.entities)}"
    if item.summary:
        line += f"  \n  > {item.summary}"
    return line


def render_markdown(items: list[NewsItem], config: Config, digest: Digest, generated: datetime) -> str:
    s = config.settings
    lines = [
        f"# 靶材產業新聞動態 — {generated.strftime('%Y-%m-%d')}",
        "",
        f"範圍:最近 {s.days} 天 · 新聞 {len(items)} 則 · 產生時間 {generated.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
    ]
    if not items:
        lines += ["本期沒有新的相關新聞。", ""]
        return "\n".join(lines)

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
    return "\n".join(lines)


def write_reports(items: list[NewsItem], config: Config, digest: Digest, out_dir: str | Path, generated: datetime) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = generated.strftime("%Y-%m-%d")
    md_path = out / f"{stem}.md"
    json_path = out / f"{stem}.json"
    md_path.write_text(render_markdown(items, config, digest, generated), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "generated": generated.isoformat(),
                "days": config.settings.days,
                "digest": digest.model_dump(),
                "items": [i.to_dict() for i in items],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return md_path, json_path

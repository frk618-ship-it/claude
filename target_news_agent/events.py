"""Group reprints of one story into a single event for the news pages.

The collector already drops near-identical headlines; what is left are the same story told by many
outlets in different words (a filing re-posted by ten portals, one order reported in five languages).
Two items are one event when they name a common tracked company, were published within EVENT_DAYS of
each other, and their headlines (Traditional-Chinese title, company names and outlet tails removed)
share at least EVENT_SIMILARITY of their character pairs, measured against the shorter headline.
Company names are removed first so that two different stories about one company do not match on the
name alone. Grouping is only for display: reports, Notion and the Markdown keep every item.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from .config import Config
from .processing import _T2S

EVENT_SIMILARITY = 0.5
EVENT_DAYS = 3
# outlet tails some feeds append to the headline: "... 作者 智通財經 - Investing.com 香港", "... - 產業"
_TAIL = re.compile(r"\s*(?:作者|提供者|執筆|执笔)\s*\S.*$|\s+[-|｜]\s+[^-|｜]*$")


# words every story in this field shares; they say nothing about which story it is
_GENERIC = {
    "sputtering", "sputter", "target", "targets", "material", "materials", "advanced", "metal", "metals", "the", "and", "for",
    "with", "from", "into", "its", "new", "high", "purity", "inc", "corp", "corporation", "ltd", "co", "group", "news",
}
_GENERIC_ZH = ("溅射靶材", "溅镀靶材", "靶材", "溅射", "溅镀", "材料")
_WORD = re.compile(r"[a-z0-9][a-z0-9.,%]*")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+")


def _names(config: Config) -> list[str]:
    names = {_plain(a) for e in config.entities if not e.is_topic for a in [e.name, *e.aliases]}
    return sorted((n for n in names if len(n) >= 2), key=len, reverse=True)


def _plain(text: str) -> str:
    """Simplified-Chinese, lower-case form so zh-TW / zh-CN / full-width variants compare equal."""
    return _T2S.convert(unicodedata.normalize("NFKC", text)).lower()


def _grams(item: dict, names: list[str]) -> set[str]:
    """Words for Latin text, character pairs for Chinese / Japanese / Korean runs; names and generic terms dropped."""
    text = _plain(_TAIL.sub("", item.get("title_zh") or item.get("title") or ""))
    for n in names + list(_GENERIC_ZH):
        text = text.replace(n, " ")
    words = {w.strip(".,") for w in _WORD.findall(text)}
    grams = {w for w in words if len(w) >= 3 and w not in _GENERIC and not w.replace(".", "").replace(",", "").isdigit()}
    for run in _CJK.findall(text):
        grams |= {run[i:i + 2] for i in range(len(run) - 1)}
    return grams


def _day(item: dict) -> date | None:
    try:
        return date.fromisoformat((item.get("published") or "")[:10])
    except ValueError:
        return None


def group_events(items: list[dict], config: Config) -> list[list[dict]]:
    """Items grouped into events, in the order each event's first item appears."""
    names = _names(config)
    topics = {e.name for e in config.entities if e.is_topic}
    events: list[dict] = []
    for it in items:
        grams = _grams(it, names)
        ents = {e for e in it.get("entities", []) if e not in topics}
        day = _day(it)
        best, best_sim = None, 0.0
        if len(grams) >= 4 and ents:
            for ev in events:
                if not ev["grams"] or not ents & ev["ents"]:
                    continue
                if day and any(d and abs((day - d).days) > EVENT_DAYS for d in ev["days"]):
                    continue
                sim = max(len(grams & g) / min(len(grams), len(g)) for g in ev["grams"])
                if sim >= EVENT_SIMILARITY and sim > best_sim:
                    best, best_sim = ev, sim
        if best is None:
            events.append({"ents": set(ents), "grams": [grams] if len(grams) >= 4 else [], "days": [day], "items": [it]})
        else:
            best["items"].append(it)
            best["grams"].append(grams)
            best["days"].append(day)
            best["ents"] |= ents
    return [ev["items"] for ev in events]

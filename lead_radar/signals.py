"""Demand signals: what one news item / tender / inquiry says about who needs which product.

A signal is classified on four axes — product line, application industry, intent (tender,
expansion, shortage …) and region — and attributed to the organisations it mentions.
Those axes are the coordinates of the hotspot grid (see hotspot.py).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from target_news_agent.processing import _contains, normalize_title

from .config import Account, Config

OTHER = "other"          # industry outside the product's target list
OTHER_NAME = "其他應用"
UNKNOWN_REGION = "OTHER"


@dataclass
class Signal:
    id: str
    title: str
    link: str = ""
    source: str = ""
    published: str = ""                 # YYYY-MM-DD
    snippet: str = ""
    edition: str = ""
    origin: str = "news"                # news / feed / manual
    products: list[str] = field(default_factory=list)
    industries: dict[str, str] = field(default_factory=dict)   # product key -> industry key
    intents: list[str] = field(default_factory=list)           # strongest first
    region: str = UNKNOWN_REGION
    orgs: list[str] = field(default_factory=list)              # buyer-side organisations (lead candidates)
    accounts: list[str] = field(default_factory=list)          # watch-list accounts matched
    relevance: float = 1.0              # 1.0 = product named in the headline, 0.6 = only in the snippet
    noise: bool = False                 # stock-market chatter
    weight_hint: float = 0.0            # manual signals: explicit weight (0 = from intents)
    # filled by the optional Claude enrichment
    need: str = ""                      # one-line demand summary
    urgency: int = 0                    # 1–5 demand strength judged by Claude (0 = not judged)
    suppliers: list[str] = field(default_factory=list)
    title_zh: str = ""
    enriched: bool = False

    @property
    def day(self) -> date | None:
        try:
            return date.fromisoformat(self.published[:10])
        except ValueError:
            return None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Signal":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def signal_id(title: str, link: str = "") -> str:
    key = normalize_title(title) or link
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _hits(text_lower: str, terms: list[str]) -> int:
    return sum(1 for t in terms if _contains(text_lower, t))


def _strip(text: str, phrases: list[str]) -> str:
    for p in phrases:
        text = re.sub(re.escape(p), " ", text, flags=re.IGNORECASE)
    return text


# ── organisation names ──────────────────────────────────────────────────

_CN_SUFFIX = (
    "股份有限公司|有限公司|集團|集团|公司|科技|航太|航空|航天|重工|電子|电子|半導體|半导体|光電|光电|材料|精密|"
    "工業|工业|醫療|医疗|生技|能源|電力|电力|化工|造船|船舶|鋼鐵|钢铁|電機|电机|新材"
)
_CN_ORG = re.compile(rf"(?:^|[\s,，、:：;；「」『』()（）|｜/【】])([一-鿿]{{2,6}}?(?:{_CN_SUFFIX}))")
_EN_SUFFIX = r"Inc|Corp|Corporation|Co|Ltd|Limited|LLC|GmbH|AG|SE|plc|Group|Holdings|Aerospace|Technologies|Industries|Energy|Medical|Materials"
_EN_ORG = re.compile(rf"\b((?:[A-Z][\w&'-]*\s){{0,3}}[A-Z][\w&'-]*\s(?:{_EN_SUFFIX})\b\.?)")
_JP_ORG = re.compile(r"(株式会社[぀-ヿ一-鿿A-Za-z]{2,10}|[぀-ヿ一-鿿A-Za-z]{2,10}株式会社)")
# Generic words that look like a company name prefix but are not one.
_GENERIC = re.compile(
    r"^(台灣|臺灣|台湾|中國|中国|國內|国内|全球|美國|美国|日本|韓國|韩国|歐洲|欧洲|我國|我国|國產|国产|本土|大陸|大陆|"
    r"國際|国际|國家|国家|產業|产业|業者|业者|上市|新創|新创|相關|相关|各大|多家|這家|这家|該|该|宣布|預計|预计|今年|明年|首家|一家|兩家|三家|零件|零组件|零組件|設備|设备|製程|制程|關鍵|关键|高階|高阶|先進|先进|半導體|半导体|機械|机械)"
)
_EN_GENERIC = {"The", "A", "An", "New", "Global", "US", "U.S.", "China", "Taiwan", "Japan", "Korea", "Europe"}


def extract_orgs(text: str) -> list[str]:
    """Best-effort organisation names from a headline + snippet (Claude enrichment does this better)."""
    found: list[str] = []
    for m in _CN_ORG.finditer(text):
        name = m.group(1)
        stem = re.sub(rf"(?:{_CN_SUFFIX})$", "", name)
        if len(stem) >= 2 and not _GENERIC.match(name):
            found.append(name)
    for m in _EN_ORG.finditer(text):
        words = m.group(1).rstrip(".").split()
        while words and words[0] in _EN_GENERIC:
            words.pop(0)
        if len(words) >= 2:
            found.append(" ".join(words))
    found += _JP_ORG.findall(text)
    out: list[str] = []
    for f in found:
        if f not in out:
            out.append(f)
    return out


def _is_excluded(org: str, excluded: list[str]) -> bool:
    low = org.lower()
    return any(e.lower() in low for e in excluded)


def match_accounts(text_lower: str, accounts: list[Account]) -> list[Account]:
    return [a for a in accounts if any(_contains(text_lower, al) for al in a.aliases)]


# ── classification ──────────────────────────────────────────────────────

def classify(
    config: Config,
    *,
    title: str,
    snippet: str = "",
    link: str = "",
    source: str = "",
    published: date | datetime | str | None = None,
    edition: str = "",
    origin: str = "news",
) -> Signal | None:
    """Place one item on the grid. None when it is not a demand signal: no product line named,
    a drop term in the headline (market-report ads, games, reviews), or — for news — no
    application industry and no watch-list account (a bare mention such as a titanium watch)."""
    if _hits(title.lower(), config.settings.drop_terms):
        return None
    raw = f"{title}\n{snippet}"
    products, relevance = [], 0.6
    for p in config.products:
        text = _strip(raw, p.exclude).lower()
        if _hits(text, p.keywords):
            products.append(p.key)
            if _hits(_strip(title, p.exclude).lower(), p.keywords):
                relevance = 1.0
    low = raw.lower()
    title_low = title.lower()
    accounts = match_accounts(low, config.accounts)
    if not products:
        # Equipment makers' news rarely names a material ("京鼎擴建泰國二期"): for accounts with a
        # judged product fit, the products they are likely to buy (fit ≥ 2) stand in, at lower relevance.
        inferred = [p.key for p in config.products if any(a.fit.get(p.key, 0) >= 2 for a in accounts)]
        if not inferred:
            return None
        products, relevance = inferred, 0.6

    # Industry per product: title hits count double; a matched account's industry wins.
    industries: dict[str, str] = {}
    for key in products:
        p = config.product(key)
        acc = next((a.industry for a in accounts if a.industry in p.industries and (not a.products or key in a.products)), "")
        if acc:
            industries[key] = acc
            continue
        scores = {
            i: _hits(low, config.industries[i].keywords) + _hits(title_low, config.industries[i].keywords)
            for i in p.industries
        }
        best = max(scores, key=lambda i: scores[i], default="")
        industries[key] = best if best and scores[best] > 0 else OTHER
    if origin != "manual" and not accounts and all(v == OTHER for v in industries.values()):
        return None

    intents = [k for k, it in sorted(config.intents.items(), key=lambda kv: -kv[1].weight) if _hits(low, it.keywords)]

    region = next((a.region for a in accounts if a.region in config.regions), "")
    if not region:
        counts = {k: _hits(low, r.keywords) for k, r in config.regions.items()}
        best = max(counts, key=lambda k: counts[k], default="")
        if best and counts[best] > 0:
            region = best
    if not region:
        region = config.settings.editions.get(edition, {}).get("region", "") or UNKNOWN_REGION
    if region not in config.regions:
        region = UNKNOWN_REGION

    excluded = config.settings.exclude_orgs
    orgs = [a.name for a in accounts]
    for o in extract_orgs(raw):
        if not _is_excluded(o, excluded) and not any(o in x or x in o for x in orgs):
            orgs.append(o)
    orgs = [o for o in orgs if not _is_excluded(o, excluded)]
    noise = _hits(title_low, config.settings.noise_terms) > 0
    if noise:
        orgs = [a.name for a in accounts]   # stock chatter: no leads from broker / ticker names

    if isinstance(published, datetime):
        pub = published.astimezone(timezone.utc).date().isoformat() if published.tzinfo else published.date().isoformat()
    elif isinstance(published, date):
        pub = published.isoformat()
    else:
        pub = (published or "")[:10]

    return Signal(
        id=signal_id(title, link),
        title=title,
        link=link,
        source=source,
        published=pub,
        snippet=snippet,
        edition=edition,
        origin=origin,
        products=products,
        industries=industries,
        intents=intents,
        region=region,
        orgs=orgs,
        accounts=[a.name for a in accounts],
        relevance=relevance,
        noise=noise,
    )


def reclassify(sig: Signal, config: Config) -> Signal | None:
    """Re-run the current rules on a stored news signal, so config changes apply to history.
    Manual rows and Claude-enriched signals are kept as they are."""
    if sig.origin == "manual" or sig.enriched:
        return sig
    new = classify(config, title=sig.title, snippet=sig.snippet, link=sig.link, source=sig.source,
                   published=sig.published, edition=sig.edition, origin=sig.origin)
    if new is not None:
        new.id = sig.id
    return new


def dedupe(signals: list[Signal]) -> list[Signal]:
    """One signal per headline (zh-TW / zh-CN reprints share an id); keep the richer copy."""
    by_id: dict[str, Signal] = {}
    for s in signals:
        old = by_id.get(s.id)
        if old is None or (len(s.snippet) > len(old.snippet) and not old.enriched):
            by_id[s.id] = s
    return list(by_id.values())


def summarize_counts(signals: list[Signal]) -> dict[str, Counter]:
    return {
        "products": Counter(p for s in signals for p in s.products),
        "intents": Counter(i for s in signals for i in s.intents[:1]),
        "regions": Counter(s.region for s in signals),
    }

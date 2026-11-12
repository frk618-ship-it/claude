"""Demand signals: what one news item / tender / inquiry says about who needs which product.

A signal is classified on four axes — product line, application industry, intent (tender,
expansion, shortage …) and region — and attributed to the organisations it mentions.
Those axes are the coordinates of the hotspot grid (see hotspot.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from target_news_agent.processing import _contains, normalize_title

from .config import Account, Config

OTHER = "other"          # industry outside the product's target list
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
    inferred: bool = False              # products inferred from a listed company's fit (no material word)
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


def intent_hit(text_lower: str, it) -> bool:
    """Does the text show this intent? Strong keywords always count; weak ones (投資、採購) only
    without a void term (增資、基金、鋼價開盤…) and with a context term when the intent lists any."""
    if _hits(text_lower, it.keywords):
        return True
    if not it.weak or not _hits(text_lower, it.weak) or _hits(text_lower, it.void):
        return False
    return not it.context or _hits(text_lower, it.context) > 0


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
    r"東協|东协|東南亞|东南亚|新加坡|馬來西亞|越南|泰國|印度|歐美|欧美|高科技|"
    r"特化|電子材料|电子材料|"
    r"國際|国际|國家|国家|產業|产业|業者|业者|上市|新創|新创|相關|相关|各大|多家|這家|这家|該|该|宣布|預計|预计|今年|明年|首家|一家|兩家|三家|零件|零组件|零組件|設備|设备|製程|制程|關鍵|关键|高階|高阶|先進|先进|半導體|半导体|機械|机械)"
)
# Verbs / connectors inside a candidate: the name, if any, is what follows the last one
# ("汎銓打造半導體" → none, "佳霖攜手青輝半導體" → 青輝半導體, "據報美光科技" → 美光科技).
_STOP = re.compile(
    r"打造|共同|攜手|携手|聯手|联手|帶動|带动|跨足|引爆|強化|强化|直擊|直击|看旺|看好|據報|据报|從未|从未|做好|承接|啟動|启动|牽動|牵动|"
    r"最新|歡迎|欢迎|所有|重量級|重量级|高純度|高纯度|財報|财报|傷害|伤害|推動|推动|布局|佈局|進軍|进军|切入|投入|加速|整合|發展|发展|"
    r"成為|成为|全面|旗下|入股|拿下|助攻|擴大|扩大|營收|营收|法說|個股|概念|族群|供應鏈|供应链|大廠|大厂|龍頭|龙头|廠商|厂商|業者|业者|"
    r"搶攻|抢攻|攸關|民生|受惠|搶進|抢进|轉型|转型|鎖定|锁定|瞄準|瞄准|迎來|迎来|衝刺|冲刺|挺進|挺进|深耕|攻進|攻进|導入|导入|供貨|供货|出貨|出货|"
    r"把|將|将|的|讓|让|被|向|對|对|為|为|從|从|於|于|在"
)
_EN_GENERIC = {"The", "A", "An", "New", "Global", "US", "U.S.", "China", "Taiwan", "Japan", "Korea", "Europe"}


def extract_orgs(text: str) -> list[str]:
    """Best-effort organisation names from a headline + snippet (Claude enrichment does this better)."""
    found: list[str] = []
    for m in _CN_ORG.finditer(text):
        name = m.group(1)
        parts = _STOP.split(name)
        name = parts[-1]
        stem = re.sub(rf"(?:{_CN_SUFFIX})$", "", name)
        if len(stem) >= 2 and not _GENERIC.match(name) and not _GENERIC.match(stem):
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


def account_in_name(name: str, accounts: list[Account]) -> Account | None:
    """The listed company a looser name refers to: 「弘塑集團」 contains the alias 弘塑 → 弘塑科技.
    Only CJK aliases of 2+ characters, so short Latin tickers do not match inside words."""
    low = name.lower()
    for a in accounts:
        if a.name == name or any(len(al) >= 2 and re.search(r"[一-鿿]", al) and al.lower() in low for al in a.aliases):
            return a
    return None


def material_terms(config: Config, sig: "Signal") -> dict[str, list[str]]:
    """Material words the item itself names, per product line ({"target": ["靶材", "PVD"]}).
    Empty when the product line was inferred from the company's judged fit."""
    raw = f"{sig.title}\n{sig.snippet}"
    out: dict[str, list[str]] = {}
    for p in config.products:
        text = _strip(raw, p.exclude).lower()
        hits = [k for k in p.keywords if _contains(text, k)]
        if hits:
            out[p.key] = hits[:3]
    return out


def _spans(text_lower: str, term: str) -> list[tuple[int, int]]:
    term = term.lower()
    return [(m.start(), m.start() + len(term)) for m in re.finditer(re.escape(term), text_lower)] if term else []


def match_accounts(text_lower: str, accounts: list[Account]) -> list[Account]:
    """Listed companies the text names. A short alias that only appears inside another listed
    company's longer name is not a mention (「台塑化」 names 台塑化, not 台塑)."""
    found = [a for a in accounts if any(_contains(text_lower, al) for al in a.aliases)]
    if len(found) < 2:
        return found
    spans = {a.name: [s for al in a.aliases for s in _spans(text_lower, al)] for a in found}

    def inside_other(a: Account) -> bool:
        own = spans[a.name]
        others = [s for b in found if b is not a for s in spans[b.name]]
        return bool(own) and all(any(o[0] <= s[0] and s[1] <= o[1] and (o[1] - o[0]) > (s[1] - s[0]) for o in others) for s in own)

    return [a for a in found if not inside_other(a)]


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
        if not inferred or not any(intent_hit(low, it) for it in config.intents.values()):
            return None     # without a material word, only demand news counts (擴產、訂單、缺料…), not stock chatter
        products, relevance, was_inferred = inferred, 0.6, True
    else:
        was_inferred = False

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

    intents = [k for k, it in sorted(config.intents.items(), key=lambda kv: -kv[1].weight) if intent_hit(low, it)]

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
        acc = account_in_name(o, config.accounts)
        if acc:                       # 弘塑集團 / 日月光半導體 / 美光科技 → the listed company
            if acc.name not in orgs:
                orgs.append(acc.name)
            continue
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
        inferred=was_inferred,
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

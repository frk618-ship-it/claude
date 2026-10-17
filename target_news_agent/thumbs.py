"""Pick a content-related thumbnail for each news card.

News feeds carry no images we may reuse, so the site draws its own: a picture for what
the story is about (expansion, earnings, a fab, a price ...) plus, when the headline
names one, the material's element symbol (Ta, Cu, ITO ...).
"""

from __future__ import annotations

import re

# Headline word -> material badge. Order matters: the first match wins.
MATERIALS: list[tuple[str, re.Pattern]] = [
    (sym, re.compile(pat, re.I))
    for sym, pat in [
        ("ITO", r"\bITO\b|氧化銦錫|氧化铟锡"),
        ("IGZO", r"IGZO"),
        ("Ta", r"鉭|钽|tantalum|タンタル|탄탈"),
        ("Ru", r"釕|钌|ruthenium|ルテニウム|루테늄"),
        ("Co", r"鈷|钴|cobalt|コバルト|코발트"),
        ("W", r"鎢|钨|tungsten|タングステン|텅스텐"),
        ("Mo", r"鉬|钼|molybdenum|モリブデン|몰리브덴"),
        ("Ti", r"鈦|钛|titanium|チタン|티타늄"),
        ("In", r"銦|铟|indium|インジウム|인듐"),
        ("Pt", r"鉑|铂|白金|platinum|プラチナ|백금"),
        ("Pd", r"鈀|钯|palladium|パラジウム|팔라듐"),
        ("Ag", r"銀價|银价|白銀|白银|\bsilver\b|銀価格|은값"),
        ("Au", r"金價|金价|黃金|黄金|\bgold\b|金価格|금값"),
        ("Cu", r"銅|铜|copper|\b銅\b|구리"),
        ("Al", r"鋁|铝|aluminum|aluminium|アルミ|알루미늄"),
        ("Ni", r"鎳|镍|nickel|ニッケル|니켈"),
    ]
]

CATEGORY_MOTIFS = {
    "擴產與投資": ("factory", "擴產"),
    "營收與財報": ("bars", "財報"),
    "併購與合作": ("handshake", "合作"),
    "原物料與價格": ("tag", "價格"),
    "技術與新產品": ("flask", "技術"),
    "客戶與供應鏈": ("truck", "供應鏈"),
    "回收與循環": ("recycle", "回收"),
    "政策與貿易": ("globe", "政策"),
}
ROLE_MOTIFS = {"equipment": ("chamber", "濺鍍設備"), "process": ("wafer", "晶圓廠"), "packaging": ("chip", "封裝")}
KIND_MOTIFS = {
    "metals": ("coin", "貴金屬"),
    "recycling": ("recycle", "回收"),
    "market": ("market", "行情"),
    "forum": ("forum", "討論"),
    "research": ("research", "市場報告"),
    "profile": ("profile", "公司資料"),
    "filing": ("filing", "公告"),
}


def material(text: str) -> str:
    for sym, pat in MATERIALS:
        if pat.search(text or ""):
            return sym
    return ""


def pick(item: dict, roles: dict[str, str] | None = None) -> tuple[str, str, str]:
    """(icon name, caption, material symbol) for a news item dict."""
    title = f"{item.get('title', '')} {item.get('title_zh', '')}"
    kind = item.get("kind", "news")
    sym = material(title)
    if kind in ("metals", "recycling", "market", "forum", "research", "profile"):
        motif, caption = KIND_MOTIFS[kind]
    else:
        item_roles = [roles.get(e, "") for e in item.get("entities", [])] if roles else item.get("roles", [])
        cats = item.get("categories") or []
        role = next((r for r in item_roles if r in ROLE_MOTIFS), "")
        if cats and cats[0] in CATEGORY_MOTIFS and cats[0] != "客戶與供應鏈":
            motif, caption = CATEGORY_MOTIFS[cats[0]]
        elif role:
            motif, caption = ROLE_MOTIFS[role]
        elif cats and cats[0] in CATEGORY_MOTIFS:
            motif, caption = CATEGORY_MOTIFS[cats[0]]
        elif kind == "filing":
            motif, caption = KIND_MOTIFS["filing"]
        else:
            motif, caption = "target", "靶材"
    return motif, caption, sym

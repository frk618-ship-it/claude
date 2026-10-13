"""Parse free-text material descriptions (Chinese / English) and score how well two specs match.

Handles the wording common in sputtering-target and specialty-metal procurement:
purity as "4N5" / "99.995%", dimensions as "Φ300x6.35mm" / "D76.2*3" / '3" x 0.125"' /
"127x406x6mm", element names in Chinese or as symbols, and product forms (靶材、粉、顆粒…).
"""

from __future__ import annotations

import re

from .models import MaterialSpec

# Chinese (traditional + simplified) and English names -> canonical symbol.
ELEMENTS: dict[str, list[str]] = {
    "Ag": ["銀", "银", "silver"],
    "Al": ["鋁", "铝", "aluminum", "aluminium"],
    "Au": ["金", "gold"],
    "Co": ["鈷", "钴", "cobalt"],
    "Cr": ["鉻", "铬", "chromium", "chrome"],
    "Cu": ["銅", "铜", "copper"],
    "Fe": ["鐵", "铁", "iron"],
    "Ge": ["鍺", "锗", "germanium"],
    "Hf": ["鉿", "铪", "hafnium"],
    "In": ["銦", "铟", "indium"],
    "Ir": ["銥", "铱", "iridium"],
    "Mg": ["鎂", "镁", "magnesium"],
    "Mn": ["錳", "锰", "manganese"],
    "Mo": ["鉬", "钼", "molybdenum"],
    "Nb": ["鈮", "铌", "niobium"],
    "Ni": ["鎳", "镍", "nickel"],
    "Pd": ["鈀", "钯", "palladium"],
    "Pt": ["鉑", "铂", "白金", "platinum"],
    "Rh": ["銠", "铑", "rhodium"],
    "Ru": ["釕", "钌", "ruthenium"],
    "Sc": ["鈧", "钪", "scandium"],
    "Si": ["矽", "硅", "silicon"],
    "Sn": ["錫", "锡", "tin"],
    "Ta": ["鉭", "钽", "tantalum"],
    "Ti": ["鈦", "钛", "titanium"],
    "V": ["釩", "钒", "vanadium"],
    "W": ["鎢", "钨", "tungsten"],
    "Y": ["釔", "钇", "yttrium"],
    "Zn": ["鋅", "锌", "zinc"],
    "Zr": ["鋯", "锆", "zirconium"],
}
# Compounds / alloys traded under their own name. Checked before single elements.
COMPOUNDS: dict[str, list[str]] = {
    "ITO": ["ITO", "氧化銦錫", "氧化铟锡", "indium tin oxide"],
    "IGZO": ["IGZO", "銦鎵鋅氧", "铟镓锌氧"],
    "AZO": ["AZO", "氧化鋅鋁", "氧化锌铝", "aluminum doped zinc oxide"],
    "SiO2": ["SiO2", "二氧化矽", "二氧化硅", "silicon dioxide", "silica"],
    "Al2O3": ["Al2O3", "氧化鋁", "氧化铝", "alumina", "aluminum oxide"],
    "TiO2": ["TiO2", "二氧化鈦", "二氧化钛", "titanium dioxide"],
    "ZnO": ["ZnO", "氧化鋅", "氧化锌", "zinc oxide"],
    "Ta2O5": ["Ta2O5", "五氧化二鉭", "五氧化二钽", "tantalum pentoxide"],
    "Nb2O5": ["Nb2O5", "五氧化二鈮", "五氧化二铌", "niobium pentoxide"],
    "WTi": ["WTi", "TiW", "鎢鈦", "钨钛", "鈦鎢", "钛钨"],
    "NiCr": ["NiCr", "鎳鉻", "镍铬", "nichrome"],
    "NiV": ["NiV", "鎳釩", "镍钒"],
    "AlCu": ["AlCu", "鋁銅", "铝铜"],
    "AlSi": ["AlSi", "鋁矽", "铝硅"],
    "MoNb": ["MoNb", "鉬鈮", "钼铌"],
    "CoCrPt": ["CoCrPt", "鈷鉻鉑", "钴铬铂"],
    "TiN": ["TiN", "氮化鈦", "氮化钛", "titanium nitride"],
    "SiC": ["SiC", "碳化矽", "碳化硅", "silicon carbide"],
}

# Constituents of compounds whose name is not a formula; others are read from the formula itself.
COMPOUND_PARTS = {"ITO": {"In", "Sn"}, "IGZO": {"In", "Ga", "Zn"}, "AZO": {"Al", "Zn"}}

FORMS: dict[str, list[str]] = {
    "target": ["靶材", "靶", "濺鍍靶", "溅射靶", "sputtering target", "target"],
    "powder": ["粉末", "粉", "powder"],
    "pellet": ["蒸鍍料", "蒸镀料", "錠片", "pellet", "pellets", "evaporation material"],
    "granule": ["顆粒", "颗粒", "粒", "granule", "granules", "shot"],
    "wire": ["線材", "线材", "絲", "丝", "線", "线", "wire"],
    "foil": ["箔", "foil"],
    "plate": ["板材", "板", "片材", "sheet", "plate"],
    "rod": ["棒材", "棒", "rod", "bar"],
    "ingot": ["錠", "锭", "ingot"],
    "tube": ["管材", "管", "tube", "rotary target", "旋轉靶", "旋转靶"],
}

_NUM = r"(\d+(?:\.\d+)?)"
_PURITY_N = re.compile(r"(?<![A-Za-z0-9])([2-7])\s*N\s*([1-9])?(?![A-Za-z])", re.I)
_PURITY_PCT = re.compile(r"(9\d(?:\.\d+)?)\s*%")
_PURITY_BARE = re.compile(r"(?<![\d.])(99\.9\d*)(?![\d.])")
_INCH = r'(?:\s*(?:"|”|″|in(?:ch(?:es)?)?\b|吋|英吋))'
_DIM_SEP = r"\s*[x×X*＊]\s*"
_DIA_PREFIX = r"(?:[ΦφΦ⌀Ø]|\bD(?=\s*\d)|\bdia\.?|直徑|直径|外徑|外径)\s*"
_DIA_SUFFIX = r"(?P<dia2>\s*(?:dia\.?|diameter|直徑|直径))?"
_DIMS = re.compile(
    rf"(?P<dia>{_DIA_PREFIX})?{_NUM}(?P<u1>{_INCH}|\s*mm)?{_DIA_SUFFIX}(?:{_DIM_SEP}(?:[tT]\s*)?{_NUM}(?P<u2>{_INCH}|\s*mm)?)"
    rf"(?:{_DIM_SEP}{_NUM}(?P<u3>{_INCH}|\s*mm)?)?",
    re.I,
)
_SINGLE_DIA = re.compile(rf"{_DIA_PREFIX}{_NUM}(?P<u>{_INCH}|\s*mm)?", re.I)
_THICK = re.compile(rf"(?:厚度?|thk\.?|thickness)\s*[:：]?\s*{_NUM}\s*(?P<u>{_INCH}|mm)?", re.I)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]*|[一-鿿]+")
_FORMULA = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}\b")
# Backing plates / bonding are not the target material ("鈦靶 含銅背板", "bonded to a copper backing plate").
_BACKING = re.compile(
    r"[含附帶带綁绑]?[\u4e00-\u9fff]{0,2}背板|(?:bonded\s+(?:to|on)\s+(?:an?\s+)?)?[A-Za-z]+\s+backing(?:\s+plate)?|backing\s+plate",
    re.I,
)
_STOP = {"mm", "pcs", "pc", "the", "and", "for", "with", "of", "dia", "thk", "x", "n", "in", "inch", "high", "purity", "高純", "高纯", "純度", "纯度", "規格", "规格"}


def _alias_pattern(alias: str) -> re.Pattern:
    if re.fullmatch(r"[A-Za-z0-9 ]+", alias):
        # Latin aliases: case-insensitive whole words, except short symbols which are case-sensitive.
        if len(alias) <= 6 and alias[0].isupper():
            return re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])")
        return re.compile(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", re.I)
    return re.compile(re.escape(alias))


_COMPOUND_PATTERNS = [(sym, [_alias_pattern(a) for a in sorted(aliases, key=len, reverse=True)]) for sym, aliases in COMPOUNDS.items()]
_ELEMENT_PATTERNS = [
    (sym, [_alias_pattern(a) for a in sorted([sym, *aliases], key=len, reverse=True)]) for sym, aliases in ELEMENTS.items()
]
_FORM_PATTERNS = [(form, [_alias_pattern(a) for a in sorted(aliases, key=len, reverse=True)]) for form, aliases in FORMS.items()]


def _to_mm(value: str, unit: str | None) -> float:
    v = float(value)
    return round(v * 25.4, 2) if unit and not unit.strip().lower().startswith("mm") else v


def parse_purity(text: str) -> float | None:
    m = _PURITY_N.search(text)
    if m:
        nines, extra = int(m.group(1)), m.group(2)
        value = "9" * nines + (extra or "")
        return float(value[:2] + "." + value[2:])
    m = _PURITY_PCT.search(text) or _PURITY_BARE.search(text)
    if m:
        return float(m.group(1))
    return None


def parse_dims(text: str) -> tuple[list[float], str]:
    """Return (dimensions in mm sorted descending, "round" | "rect" | "")."""
    m = _DIMS.search(text)
    if m:
        groups = m.groups()
        # groups: dia, n1, u1, dia2, n2, u2, n3, u3 — a unit given only once applies to all numbers.
        nums = [groups[1], groups[4], groups[6]]
        units = [m.group("u1"), m.group("u2"), m.group("u3")]
        default_unit = next((u for u in reversed(units) if u), None)
        dims = [_to_mm(n, u or default_unit) for n, u in zip(nums, units) if n]
        shape = "round" if m.group("dia") or m.group("dia2") or len(dims) == 2 else "rect"
        return sorted(dims, reverse=True), shape
    dims: list[float] = []
    shape = ""
    m = _SINGLE_DIA.search(text)
    if m:
        dims.append(_to_mm(m.group(1), m.group("u")))
        shape = "round"
    t = _THICK.search(text)
    if t:
        dims.append(_to_mm(t.group(1), t.group("u")))
    return sorted(dims, reverse=True), shape


def parse_materials(text: str) -> list[str]:
    found: list[str] = []
    masked = _BACKING.sub(" ", text)
    for sym, patterns in _COMPOUND_PATTERNS:
        for p in patterns:
            if p.search(masked):
                found.append(sym)
                masked = p.sub(" ", masked)
                break
    # Remove purity / dimension tokens so "4N" or "D" are not read as elements.
    masked = _PURITY_N.sub(" ", masked)
    masked = _DIMS.sub(" ", masked)
    for sym, patterns in _ELEMENT_PATTERNS:
        for p in patterns:
            # "金" alone is gold, but skip it inside common words (合金、金屬、黃金價格 is still gold).
            if sym == "Au" and p.pattern == "金":
                if re.search(r"(?<![合五])金(?!屬|属)", masked):
                    found.append(sym)
                break
            if p.search(masked):
                found.append(sym)
                break
    # Unknown chemical formulas such as "GeSbTe" — unless they just spell out a known compound (In2O3 in ITO).
    compounds = [f for f in found if f not in ELEMENTS]
    parts = set().union(*(_constituents(c) for c in compounds)) if compounds else set()
    for f in _FORMULA.findall(masked):
        if f not in found and f not in ELEMENTS and not (_constituents(f) - {"O", "N", "C"}) <= parts:
            found.append(f)
            compounds.append(f)
            parts |= _constituents(f)
    # "鋁銅合金 AlCu" -> AlCu only; constituent elements are implied by the compound.
    return list(dict.fromkeys(m for m in found if m in compounds or m not in parts))


def _constituents(symbol: str) -> set[str]:
    return COMPOUND_PARTS.get(symbol) or set(re.findall(r"[A-Z][a-z]?", symbol))


def parse_form(text: str) -> str:
    best = ("", -1)
    for form, patterns in _FORM_PATTERNS:
        for p in patterns:
            m = p.search(text)
            if m and len(m.group(0)) > best[1]:
                best = (form, len(m.group(0)))
    return best[0]


def parse_spec(text: str) -> MaterialSpec:
    text = (text or "").strip()
    dims, shape = parse_dims(text)
    spec = MaterialSpec(
        raw=text,
        materials=parse_materials(text),
        purity=parse_purity(text),
        form=parse_form(text),
        dims_mm=dims,
        shape=shape,
    )
    rest = _DIMS.sub(" ", _PURITY_N.sub(" ", _PURITY_PCT.sub(" ", text)))
    spec.keywords = [t for t in _TOKEN.findall(rest) if t.lower() not in _STOP and len(t) > 1][:12]
    return spec


def _dims_close(a: list[float], b: list[float], tol: float) -> tuple[int, int]:
    """(matched count, compared count) comparing dimensions pairwise in sorted order."""
    n = min(len(a), len(b))
    hit = 0
    for x, y in zip(a[:n], b[:n]):
        if abs(x - y) <= max(tol * max(x, y), 0.15):
            hit += 1
    return hit, n


def match_score(query: MaterialSpec, offer: MaterialSpec, dim_tolerance: float = 0.03) -> tuple[int, list[str]]:
    """Score 0-100 how well an offer matches the requested spec, with human-readable notes."""
    score = 100
    notes: list[str] = []

    if query.materials:
        if not offer.materials:
            score -= 25
            notes.append("報價未標示材質")
        elif set(query.materials) == set(offer.materials):
            pass
        elif set(query.materials) & set(offer.materials):
            score -= 30
            notes.append(f"材質部分相符({'/'.join(offer.materials)})")
        else:
            return 0, [f"材質不同({'/'.join(offer.materials)})"]

    if query.purity is not None:
        if offer.purity is None:
            score -= 15
            notes.append("未標示純度")
        elif offer.purity + 1e-9 < query.purity:
            score -= 35
            notes.append(f"純度較低({offer.purity_label})")
        elif offer.purity > query.purity + 1e-9:
            score -= 5
            notes.append(f"純度較高({offer.purity_label})")

    if query.form:
        if not offer.form:
            score -= 5
        elif offer.form != query.form:
            score -= 30
            notes.append(f"型態不同({offer.form})")

    if query.dims_mm:
        if not offer.dims_mm:
            score -= 20
            notes.append("未標示尺寸")
        else:
            hit, n = _dims_close(query.dims_mm, offer.dims_mm, dim_tolerance)
            if len(query.dims_mm) != len(offer.dims_mm) or hit < n:
                score -= 15 + 20 * (n - hit)
                notes.append("尺寸不同(" + "x".join(f"{d:g}" for d in offer.dims_mm) + "mm)")
            if query.shape and offer.shape and query.shape != offer.shape:
                score -= 10
                notes.append("形狀不同")

    return max(score, 0), notes


def match_level(score: int) -> str:
    if score >= 85:
        return "符合"
    if score >= 60:
        return "近似"
    if score > 0:
        return "差異大"
    return "不符"

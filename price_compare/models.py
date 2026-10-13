"""Data model shared by the spec parser, quote sources and the comparison engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class MaterialSpec:
    """A parsed material description, e.g. "鈦靶 4N Φ300x6.35mm"."""

    raw: str
    materials: list[str] = field(default_factory=list)  # normalised symbols / compounds: ["Ti"], ["ITO"]
    purity: float | None = None  # percent, 99.99 for 4N
    form: str = ""  # target / powder / pellet / wire / foil / rod / ingot / granule / plate
    dims_mm: list[float] = field(default_factory=list)  # sorted descending
    shape: str = ""  # round / rect / ""
    keywords: list[str] = field(default_factory=list)  # remaining free text tokens

    @property
    def purity_label(self) -> str:
        return purity_label(self.purity)

    def summary(self) -> str:
        parts = ["/".join(self.materials) or "?"]
        if self.purity:
            parts.append(self.purity_label)
        if self.form:
            parts.append(FORM_LABELS.get(self.form, self.form))
        if self.dims_mm:
            dims = "x".join(f"{d:g}" for d in self.dims_mm)
            parts.append(("Φ" if self.shape == "round" else "") + dims + "mm")
        return " ".join(parts)


FORM_LABELS = {
    "target": "靶材",
    "powder": "粉末",
    "pellet": "顆粒/錠片",
    "granule": "顆粒",
    "wire": "線材",
    "foil": "箔/片",
    "plate": "板材",
    "rod": "棒材",
    "ingot": "錠",
    "tube": "管材",
}


def purity_label(p: float | None) -> str:
    """99.99 -> '4N', 99.995 -> '4N5'."""
    if p is None:
        return ""
    if p >= 100:
        return "100%"
    digits = f"{p:.6f}".rstrip("0").rstrip(".")
    integer, _, frac = digits.partition(".")
    if integer != "99":
        return f"{digits}%"
    nines = 2 + len(frac) - len(frac.lstrip("9"))
    rest = frac.lstrip("9")
    return f"{nines}N{rest}" if rest else f"{nines}N"


@dataclass
class Quote:
    source: str  # "報價紀錄" / "Claude 網路搜尋" / "PChome" ...
    vendor: str
    title: str
    price: float
    currency: str = "TWD"
    unit: str = "pcs"  # pcs / kg / g / lb / oz / set
    pack_qty: float = 1.0  # price covers this many units (e.g. 100 g pack -> unit g, pack_qty 100)
    url: str = ""
    region: str = ""
    quote_date: str = ""  # ISO date of the price, when known
    moq: str = ""
    lead_time: str = ""
    note: str = ""
    price_type: str = "list"  # list (公開標價) / quote (詢價報價) / reference (參考行情)
    spec_text: str = ""  # the seller's own spec wording
    # filled in by compare.analyse()
    spec: MaterialSpec | None = None
    match_score: int = 0
    match_level: str = ""
    match_notes: list[str] = field(default_factory=list)
    price_twd: float | None = None
    basis: str = ""  # "每片" / "每公斤"
    unit_price_twd: float | None = None
    outlier: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spec"] = self.spec.summary() if self.spec else ""
        return d

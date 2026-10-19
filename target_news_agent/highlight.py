"""Mark keywords in summary text: company names, figures and target / process terms.

The text is escaped piece by piece, so the output is safe HTML:
<b class="kw-co">  tracked company (from the watchlist)
<b class="kw-num"> a figure with a unit (億美元, %, 倍 ...)
<mark class="kw-term"> a target / sputtering / process term or a target material
"""

from __future__ import annotations

import html
import re

from .config import Config

TERMS = [
    # targets & sputtering
    "濺鍍靶材", "靶材", "濺鍍", "溅射", "磁控濺鍍", "PVD", "物理氣相沉積", "背板", "旋轉靶",
    # demand drivers
    "擴產", "擴充", "擴建", "新廠", "建廠", "資本支出", "設備投資", "產能", "量產", "國產化", "在地化",
    "先進封裝", "CoWoS", "SoIC", "HBM", "矽中介層", "凸塊", "RDL", "扇出", "面板級封裝", "FOPLP",
    "先進製程", "2奈米", "3奈米",
    # recycling & metals
    "回收", "循環", "精煉", "再生", "貴金屬", "金價", "銀價", "高純",
    "ITO", "IGZO", "鉭", "鈦", "鈷", "鎢", "鉬", "釕", "銦", "鈀", "鉑", "銅", "鋁", "鎳",
]

_METALS = ["鉭", "鈦", "鈷", "鎢", "鉬", "釕", "銦", "鈀", "鉑", "銅", "鋁", "鎳", "銀", "金"]
TERMS += [f"{m}靶{suffix}" for m in _METALS for suffix in ("材", "")]  # 鉭靶材 / 鉭靶 as one term

_UNIT = (
    r"(?:%|％|個百分點|bps|億美元|億元|億日圓|億韓元|億歐元|億人民幣|兆韓元|兆日圓|兆元|兆|億|萬片|萬噸|萬|"
    r"美元|元|日圓|韓元|歐元|倍|片|奈米|nm|吋|噸|家)"
)
_NUM = rf"(?:US\$|NT\$|\$)?\d[\d,]*(?:\.\d+)?(?:\s*[-~～至]\s*\d[\d,]*(?:\.\d+)?)?\s*{_UNIT}"


def _alt(words: list[str]) -> str:
    """Longest first, so 濺鍍靶材 wins over 靶材; Latin words need word boundaries."""
    parts = []
    for w in sorted(set(words), key=len, reverse=True):
        e = re.escape(w)
        parts.append(rf"(?<![A-Za-z0-9]){e}(?![A-Za-z0-9])" if w.isascii() else e)
    return "|".join(parts)


class Highlighter:
    def __init__(self, config: Config | None = None):
        companies: list[str] = []
        if config:
            for e in config.entities:
                if e.is_topic:
                    continue
                companies.append(re.sub(r"\s*\(.*\)$", "", e.name))
                companies += [a for a in e.aliases if (len(a) >= 2 if not a.isascii() else len(a) >= 3)]
        groups = []
        if companies:
            groups.append(f"(?P<co>{_alt(companies)})")
        groups.append(f"(?P<num>{_NUM})")
        groups.append(f"(?P<term>{_alt(TERMS)})")
        self.rx = re.compile("|".join(groups))

    def __call__(self, text: str) -> str:
        if not text:
            return ""
        out, pos = [], 0
        for m in self.rx.finditer(text):
            out.append(html.escape(text[pos:m.start()]))
            word = html.escape(m.group(0))
            if m.lastgroup == "co":
                out.append(f'<b class="kw-co">{word}</b>')
            elif m.lastgroup == "num":
                out.append(f'<b class="kw-num">{word}</b>')
            else:
                out.append(f'<mark class="kw-term">{word}</mark>')
            pos = m.end()
        out.append(html.escape(text[pos:]))
        return "".join(out)

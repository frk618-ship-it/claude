"""Decision-tree estimate of material demand from the scale of an expansion.

    headline amount (NT$)                       「華邦電通過資本支出 731.63 億元擴建 12 吋廠」→ 731.63 億
      └─ intent        × factor                  擴產 1.0 / 招標 1.0 / 訂單 0.5 / 歲修 0.3(其他意圖不估)
          └─ segment → sub-segment → product     記憶體 × 靶材 0.3% …(settings.estimate.tree)
              └─ × the account's fit / 3         適配 3 = 全額,2 = 2/3 …
                  = estimated yearly material value (NT$), the largest project per account

The value becomes a 0–100 score on a log scale (NT$1M → 0, NT$1B → 100) that adds
settings.estimate.weight × score to the overall score. Every coefficient is a rough planning assumption kept in the config — not measured
consumption — and should be replaced with real figures as sales learns them.
"""

from __future__ import annotations

import math
import re

from .config import Account, Config
from .signals import Signal

_UNIT = {"兆": 1e12, "億": 1e8, "千萬": 1e7, "百萬": 1e6, "萬": 1e4}
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(兆|億|千萬|百萬|萬)\s*(美元|美金|日圓|日元|歐元|人民幣|新台幣|台幣|元)?")
# amounts that are not a project size: revenue, profit, market value, share price
_NOT_SCALE = re.compile(r"(營收|獲利|淨利|純益|盈餘|市值|股價|EPS|毛利|虧損|現增|增資|配息|股利|年薪|罰)[^,\uFF0C;\uFF1B\u3002!\uFF01?\uFF1F\u3001\s]{0,8}$")
LOW, HIGH = 1e6, 1e9          # NT$: score 0 at ≤ 1M, 100 at ≥ 1B


# an amount is a project size only next to a spending word: 「斥資 110 億」「731 億元擴建」「拿下 2.4 億美元訂單」.
# Backlogs, industry totals and customers' prepayments (在手訂單 1,351 億、廠務訂單近 9,000 億、預付 357 億) are not.
_CUE_BEFORE = re.compile(r"(斥資|砸|耗資|投資|投入|資本支出|追加|加碼|擴建|擴產|採購|興建|預算|拿下|得標|簽約|接獲|斥资|投资)[^,\uFF0C;\uFF1B\u3002!\uFF01?\uFF1F]{0,10}$")
_CUE_AFTER = re.compile(r"^[^,\uFF0C;\uFF1B\u3002!\uFF01?\uFF1F]{0,6}(擴建|擴產|建廠|設廠|蓋廠|投資|資本支出|採購|興建|添購)")
_NOT_PROJECT = re.compile(r"(在手訂單|訂單近|訂單能見度|預付|市場規模|產值)[^,\uFF0C;\uFF1B\u3002]{0,10}$")


def parse_amount(text: str, fx: dict[str, float]) -> float:
    """Largest project-size amount in a headline, in NT$ (0 when there is none)."""
    best = 0.0
    for m in _AMOUNT.finditer(text):
        before, after = text[max(0, m.start() - 14):m.start()], text[m.end():m.end() + 10]
        if _NOT_SCALE.search(before) or _NOT_PROJECT.search(before):
            continue
        if not (_CUE_BEFORE.search(before) or _CUE_AFTER.search(after)):
            continue
        cur = m.group(3) or "元"
        rate = fx.get(cur, fx.get({"美金": "美元", "日元": "日圓", "新台幣": "元", "台幣": "元"}.get(cur, cur), 1.0))
        best = max(best, float(m.group(1).replace(",", "")) * _UNIT[m.group(2)] * rate)
    return best


def value_score(value: float) -> float:
    if value <= LOW:
        return 0.0
    return round(min(100.0, (math.log10(value) - math.log10(LOW)) / (math.log10(HIGH) - math.log10(LOW)) * 100), 1)


def estimate_signal(sig: Signal, account: Account, config: Config) -> dict | None:
    """The tree applied to one signal for one account; None when a branch is missing."""
    est = config.settings.estimate
    factor = next((est["intents"][i] for i in sig.intents if i in est["intents"]), 0.0)
    shares = (est["tree"].get(account.segment) or {}).get(account.subsegment) or {}
    amount = parse_amount(sig.title, est["fx"])
    if not factor or not shares or not amount:
        return None
    intent = next(i for i in sig.intents if i in est["intents"])
    by_product = {p: amount * factor * share * account.fit.get(p, 0) / 3 for p, share in shares.items() if account.fit.get(p, 0)}
    if not by_product:
        return None
    product = max(by_product, key=by_product.get)
    return {
        "value": round(sum(by_product.values())), "product": product, "by_product": {p: round(v) for p, v in by_product.items()},
        "amount": round(amount), "intent": intent, "factor": factor, "share": shares[product],
        "fit": account.fit.get(product, 0), "signal": sig.id, "title": sig.title,
    }


def estimate_account(account: Account, signals: list[Signal], config: Config) -> dict:
    """The account's largest estimated project among signals it is the headline subject of."""
    best = None
    for s in signals:
        if s.noise:
            continue
        e = estimate_signal(s, account, config)
        if e and (best is None or e["value"] > best["value"]):
            best = e
    if best is None:
        return {"value": 0, "score": 0.0, "summary": ""}
    names = {p.key: p.name for p in config.products}
    iname = config.intents[best["intent"]].name if best["intent"] in config.intents else best["intent"]
    best["score"] = value_score(best["value"])
    best["summary"] = (f"約 NT${best['value'] / 1e6:,.1f} 百萬 / 年(主要為{names.get(best['product'], best['product'])}):"
                       f"報導金額 NT${best['amount'] / 1e8:,.1f} 億 × {iname}係數 {best['factor']:g} × "
                       f"{names.get(best['product'], best['product'])}占比 {best['share'] * 100:g}% × 適配 {best['fit']}/3")
    return best

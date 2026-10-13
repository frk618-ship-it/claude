"""Optional Claude analyst commentary on top of the computed metrics.

Only the numbers computed by this tool are sent; the model is told not to invent
figures. Runs only when Anthropic credentials are available.
"""

from __future__ import annotations

import json
import logging
import os

from pydantic import BaseModel

from .analysis import Analysis
from .metrics import AMOUNT_KEYS

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """你是台灣股市的半導體材料產業研究員,擅長財報分析。讀者是投資與策略規劃主管。

你會收到 <financials> 標籤內的 JSON,包含標的公司與同業的財務比率、月營收、評分與規則警示,以及產業分析重點。
請只根據提供的數字與事實撰寫,不要捏造任何數字、事件、客戶或產品;資料不足時直接說明。
用繁體中文(台灣用語)、專業精簡。金額單位為 {unit}。

輸出欄位:
- summary:3-5 句,總結標的公司目前的成長、獲利、財務體質與現金流狀況。
- strengths:2-5 條優勢,每條引用具體數字。
- risks:2-5 條風險或疑慮,每條引用具體數字。
- peer_view:2-4 句,與同業相比的相對位置(獲利、效率、財務結構、評價)。
- watch_items:2-5 條後續追蹤指標(例如下月營收、毛利率、存貨天數)與觀察理由。
"""


class Commentary(BaseModel):
    summary: str = ""
    strengths: list[str] = []
    risks: list[str] = []
    peer_view: str = ""
    watch_items: list[str] = []


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class Commentator:
    def __init__(self, model: str | None = None, client=None):
        import anthropic

        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("FIN_ANALYZER_MODEL", DEFAULT_MODEL)

    def comment(self, payload: dict, unit_label: str) -> Commentary:
        user = (
            "<financials>\n"
            + json.dumps(payload, ensure_ascii=False, indent=1, default=str)
            + "\n</financials>\n\n請撰寫標的公司的財報分析評論。"
        )
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT.format(unit=unit_label),
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "medium"},
                output_format=Commentary,
                # Server-side fallback: if a request is declined, the API retries it on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self.anthropic.APIError as exc:
            log.warning("LLM commentary failed: %s", exc)
            return Commentary()
        if response.stop_reason in ("refusal", "max_tokens"):
            log.warning("LLM commentary stopped (%s); report generated without it", response.stop_reason)
            return Commentary()
        return response.parsed_output or Commentary()


def llm_payload(target: Analysis, peers: list[Analysis], industry_notes: list[str], unit: float) -> dict:
    def compact(a: Analysis, quarters: int) -> dict:
        periods = a.data.periods[-quarters:]
        return {
            "company": a.data.label,
            "segment": a.segment,
            "latest_quarter": a.latest,
            "quarterly": {
                p: {k: _round(v, unit if k in AMOUNT_KEYS else 1)
                    for k, v in a.quarterly[p].items() if v is not None}
                for p in periods
            },
            "valuation": {k: _round(v, unit if k in AMOUNT_KEYS else 1) for k, v in a.valuation.items() if v is not None},
            "piotroski_f": a.fscore.score,
            "altman_z2": None if a.zscore.value is None else {"value": round(a.zscore.value, 2), "zone": a.zscore.zone},
            "signals": [f"[{s.level}] {s.topic}:{s.message}" for s in a.signals],
        }

    out = compact(target, 8)
    if target.monthly:
        months = sorted(target.monthly)[-13:]
        out["monthly_revenue"] = {m: {k: _round(v, unit if k in AMOUNT_KEYS else 1) for k, v in target.monthly[m].items() if v is not None} for m in months}
    return {"target": out, "peers": [compact(p, 1) for p in peers], "industry_notes": industry_notes}


def _round(v, unit: float = 1):
    if isinstance(v, float):
        return round(v / unit, 4)
    return v

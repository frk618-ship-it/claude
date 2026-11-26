"""白銀的一週預測改用「白銀 AI 冠軍模型」(https://silver-ai.streamlit.app/)。

每個交易日美東收盤後,「白銀 AI 預測」workflow 會從私人備份 repo 還原 App 的冠軍模型、
抓最新行情、只做預測(不訓練),把未來 7 個交易日的逐日預測寫到 ``data/metal_alert/silver_ai.json``
(由 silver-ai-trader 的 ``python -m silver_trader.export`` 產生)。

這裡讀取該檔,把白銀一週預測的中心線換成 AI 模型的逐日預測;50% / 80% 價格區間沿用統計模型
(forecast.py)的波動與殘差分位數。檔案不存在、格式不對或太舊時,改回統計模型並在頁面說明原因。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

FILE = "silver_ai.json"
SYMBOL = "SI=F"
MAX_AGE_DAYS = 4          # 週五收盤後產生的預測要撐過週末;超過就視為過期
MIN_DAYS_COVERED = 3      # 一週 5 個交易日中,AI 預測至少要涵蓋幾天才採用


def load(data_dir: Path, now: datetime | None = None) -> tuple[dict | None, str]:
    """回傳 (AI 預測, 無法使用時的原因)。"""
    p = Path(data_dir) / FILE
    try:
        ai = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "尚未產生 AI 預測"
    except (OSError, ValueError) as exc:
        return None, f"AI 預測檔無法讀取:{exc}"
    try:
        if ai.get("schema") != 1 or ai.get("symbol") != SYMBOL:
            return None, "AI 預測檔格式不符"
        gen = datetime.fromisoformat(ai["generated_at"])
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
        prices = {r["date"]: float(r["price"]) for r in ai["path"]}
        if not prices or min(prices.values()) <= 0:
            return None, "AI 預測檔沒有有效的價格"
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"AI 預測檔格式不符:{exc}"
    age = ((now or datetime.now(timezone.utc)) - gen).total_seconds() / 86400
    if age > MAX_AGE_DAYS:
        return None, f"AI 預測已過期({gen.astimezone(timezone.utc):%Y-%m-%d} 產生,超過 {MAX_AGE_DAYS} 天)"
    ai["_prices"] = prices
    return ai, ""


def center_for(ai: dict, days: list[str]) -> dict[str, float] | None:
    """AI 預測涵蓋這一週的哪幾天;涵蓋太少就不採用。"""
    c = {d: ai["_prices"][d] for d in days if d in ai["_prices"]}
    return c if len(c) >= MIN_DAYS_COVERED and days[-1] in c else None


def model_info(ai: dict) -> dict:
    m = ai.get("model") or {}
    return {
        "kind": "silver-ai",
        "name": "白銀 AI 冠軍模型",
        "app_url": ai.get("app_url"),
        "algorithm": m.get("algorithm"),
        "target": m.get("pred_target_name") or m.get("pred_target"),
        "lookback": m.get("lookback"),
        "n_factors": m.get("n_factors"),
        "holdout_da": m.get("holdout_da"),
        "holdout_n": m.get("holdout_n"),
        "trained_at": m.get("trained_at"),
        "source": m.get("source"),
        "generated_at": ai.get("generated_at"),
        "base_date": ai.get("base_date"),
        "drivers": ai.get("drivers") or [],
    }

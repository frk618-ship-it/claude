"""Turn assessments into alerts (level changes, custom price lines) and deliver them.

State (last level / price per metal, last alert time per direction) lives in a JSON file so
an hourly run only alerts on changes; every alert is appended to a JSON-lines log.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

from .config import Config
from .signals import DIRECTION

log = logging.getLogger(__name__)

STRENGTH = {"strong_buy": 2, "buy": 1, "neutral": 0, "sell": 1, "strong_sell": 2}


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def detect(assessments: list[dict], state: dict, cfg: Config, now: int) -> tuple[list[dict], dict]:
    """Return (new alerts, updated state). ``now`` is unix seconds."""
    alerts: list[dict] = []
    new_state = {k: dict(v) for k, v in state.items()}
    cooldown = cfg.cooldown_hours * 3600
    for a in assessments:
        code, level = a["code"], a["level"]
        prev = state.get(code) or {}
        st = new_state.setdefault(code, {})
        last = dict(prev.get("last_alert") or {})
        side = DIRECTION[level]
        prev_level = prev.get("level", "neutral")  # first run: a metal already in a zone alerts once
        if side and level != prev_level:
            stronger = DIRECTION.get(prev_level) == side and STRENGTH[level] > STRENGTH[prev_level]
            if stronger or now - last.get(side, 0) >= cooldown:
                alerts.append(_alert(a, "signal", side, f"{a['name']} {a['level_name']}(分數 {a['score']:+.0f})"))
                last[side] = now

        prev_price = prev.get("price")
        for pa in cfg.price_alerts:
            if pa.code != code or prev_price is None:
                continue
            if pa.below is not None and prev_price > pa.below >= a["price"]:
                alerts.append(_alert(a, "price", "buy", f"{a['name']} 跌破 {pa.below:,} {a['unit']}(現價 {a['price']:,})"))
            if pa.above is not None and prev_price < pa.above <= a["price"]:
                alerts.append(_alert(a, "price", "sell", f"{a['name']} 突破 {pa.above:,} {a['unit']}(現價 {a['price']:,})"))

        st.update(level=level, score=a["score"], price=a["price"], ts=a["ts"], last_alert=last)
    return alerts, new_state


def _alert(a: dict, kind: str, side: str, title: str) -> dict:
    p = a["plan"]
    stop, target = (p["buy_stop"], p["buy_target"]) if side == "buy" else (p["sell_stop"], p["sell_target"])
    reasons = [f"{v['name']} {v['value']}:{v['comment']}" for v in sorted(a["votes"], key=lambda v: -abs(v["vote"]))
               if (v["vote"] > 0) == (side == "buy") and abs(v["vote"]) >= 0.4][:4]
    return {
        "ts": a["ts"], "code": a["code"], "name": a["name"], "kind": kind, "side": side,
        "level": a["level"], "level_name": a["level_name"], "score": a["score"], "price": a["price"],
        "unit": a["unit"], "title": title, "reasons": reasons, "trend": a["trend_text"],
        "stop": stop, "target": target,
    }


def append_log(path: Path, alerts: list[dict], keep: int = 500) -> list[dict]:
    """Append alerts to the JSON-lines log, keeping the newest ``keep``; returns the whole log."""
    rows = read_log(path) + alerts
    rows = rows[-keep:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return rows


def read_log(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def format_text(alert: dict, when: str = "") -> str:
    icon = "🟢 進場" if alert["side"] == "buy" else "🔴 出場"
    lines = [f"【貴金屬預警】{icon}|{alert['title']}", f"價格 {alert['price']:,} {alert['unit']}" + (f"({when})" if when else "")]
    if alert.get("trend"):
        lines.append(f"趨勢:{alert['trend']}")
    lines += [f"・{r}" for r in alert.get("reasons") or []]
    if alert.get("stop") and alert.get("target"):
        lines.append(f"參考停損 {alert['stop']:,}/目標 {alert['target']:,}(ATR)")
    lines.append("僅為技術指標訊號,不構成投資建議。")
    return "\n".join(lines)


def _post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def notify(alerts: list[dict], texts: list[str], env: dict | None = None, post=_post_json) -> list[str]:
    """Send to every configured channel; returns the channels used. Failures are logged, not raised."""
    env = os.environ if env is None else env
    if not alerts:
        return []
    used = []
    body = "\n\n".join(texts)
    hook = env.get("METAL_ALERT_WEBHOOK_URL", "").strip()
    if hook:
        # Slack / Teams / Google Chat read "text"; Discord reads "content".
        try:
            post(hook, {"text": body, "content": body[:1900]})
            used.append("webhook")
        except Exception as exc:
            log.warning("webhook notify failed: %s", exc)
    token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        try:
            post(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat, "text": body[:4000]})
            used.append("telegram")
        except Exception as exc:
            log.warning("telegram notify failed: %s", exc)
    return used

"""One check cycle: fetch → analyse → detect alerts → save state and snapshot → notify."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import alerts as al
from .config import Config
from .signals import analyse
from .yahoo import Fetcher, fetch_bars, http_fetch

log = logging.getLogger(__name__)


def collect(cfg: Config, fetch: Fetcher = http_fetch, retries: int = 2) -> tuple[list[dict], list[str]]:
    metals, errors = [], []
    for spec in cfg.metals:
        try:
            bars, _ = fetch_bars(spec.symbol, cfg.bar_interval, cfg.history_range, fetch, retries)
            try:
                daily, _ = fetch_bars(spec.symbol, "1d", cfg.daily_range, fetch, retries)
            except Exception as exc:  # trend filter falls back to hourly data only
                log.warning("%s daily bars unavailable: %s", spec.symbol, exc)
                daily = []
            a = analyse(spec, bars, daily, cfg)
            log.info("%-4s %10.2f score %+6.1f %s", spec.code, a["price"], a["score"], a["level_name"])
            metals.append(a)
        except Exception as exc:
            log.error("%s (%s) failed: %s", spec.name, spec.symbol, exc)
            errors.append(f"{spec.name}({spec.symbol})資料取得失敗:{exc}")
    return metals, errors


def run_once(cfg: Config, data_dir: Path, fetch: Fetcher = http_fetch, notify: bool = True,
             now: int | None = None, env: dict | None = None, retries: int = 2, record: bool = True) -> dict:
    """Run a check and return the snapshot written to ``data_dir/latest.json``.

    ``record=False`` only computes the snapshot (for building the page elsewhere): state, the
    alert log and ``latest.json`` are left untouched, so no alert is consumed without being sent.
    """
    now = int(time.time()) if now is None else now
    metals, errors = collect(cfg, fetch, retries)
    state_path, log_path = data_dir / "state.json", data_dir / "alerts.jsonl"
    new_alerts: list[dict] = []
    if metals and record:
        state = al.load_state(state_path)
        new_alerts, state = al.detect(metals, state, cfg, now)
        al.save_state(state_path, state)
    history = al.append_log(log_path, new_alerts) if new_alerts else al.read_log(log_path)
    snapshot = {
        "generated_at": now,
        "next_run": now + cfg.interval_minutes * 60,
        "interval_minutes": cfg.interval_minutes,
        "cooldown_hours": cfg.cooldown_hours,
        "tz": cfg.tz,
        "levels": cfg.levels,
        "metals": metals,
        "alerts": history[-100:],
        "new_alerts": new_alerts,
        "errors": errors,
    }
    if not record:
        return snapshot
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "latest.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    if new_alerts:
        tz = ZoneInfo(cfg.tz)
        texts = [al.format_text(a, datetime.fromtimestamp(a["ts"], tz).strftime("%m/%d %H:%M")) for a in new_alerts]
        for t in texts:
            log.warning("ALERT\n%s", t)
        if notify:
            used = al.notify(new_alerts, texts, env)
            log.info("notified via %s", ", ".join(used) or "(no channel configured)")
    return snapshot


def load_snapshot(data_dir: Path) -> dict | None:
    try:
        return json.loads((data_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

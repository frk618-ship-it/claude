import json
import math
import random
import time
from datetime import datetime, timedelta, timezone

import pytest
from conftest import ROOT

from metal_alert import forecast as F
from metal_alert import silver_ai
from metal_alert.config import load_config
from metal_alert.engine import run_once
from metal_alert.page import render_page
from metal_alert.yahoo import Bar

CONFIG = ROOT / "config" / "metal_alert.yaml"
NOW = int(time.time())


def _daily(n=1300, seed=3, end=NOW - 86400, base=30.0):
    rnd = random.Random(seed)
    p, out = base, []
    for i in range(n):
        p *= math.exp(rnd.gauss(0, 0.015))
        out.append(Bar(end - (n - 1 - i) * 86400, p, p, p, p))
    return out


def _ai_file(tmp_path, daily, move=0.05, days=7, generated=None, **extra):
    dates = F.next_weekdays(daily[-1].ts, days)
    base = daily[-1].close
    path = [{"date": d, "price": round(base * (1 + move * (k + 1) / days), 4), "p_up": 60.0} for k, d in enumerate(dates)]
    doc = {"schema": 1, "symbol": "SI=F", "source": "silver-ai-trader", "app_url": "https://silver-ai.streamlit.app/",
           "generated_at": (generated or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
           "model": {"source": "冠軍模型", "algorithm": "BiGRU", "pred_target": "logreturn", "pred_target_name": "對數報酬率",
                     "lookback": 10, "n_factors": 25, "holdout_da": 52.3, "holdout_n": 75, "trained_at": "2026-11-25T08:00:00+00:00"},
           "base_date": dates[0], "base_price": base, "path": path,
           "drivers": [{"name": "黃金價格 (GC=F)", "effect": 0.0123}, {"name": "美元指數 (DXY)", "effect": -0.0081}]}
    doc.update(extra)
    (tmp_path / silver_ai.FILE).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


# ---------- forecast with an outside centre line ----------

def test_center_replaces_median_and_keeps_bands():
    daily = _daily()
    days = F.next_weekdays(daily[-1].ts)
    base = daily[-1].close
    center = {d: base * (1 + 0.01 * (k + 1)) for k, d in enumerate(days)}
    f = F.forecast(daily, center=center)
    plain = F.forecast(daily)
    for r, d in zip(f["path"], days):
        assert r["p50"] == pytest.approx(center[d], abs=0.011)
        assert r["p10"] < r["p25"] < r["p50"] < r["p75"] < r["p90"]
    # band widths come from the statistical model (same volatility), only the centre moves
    for a, b in zip(f["path"], plain["path"]):
        assert (a["p90"] - a["p10"]) / a["p50"] == pytest.approx((b["p90"] - b["p10"]) / b["p50"], rel=0.02)
    assert f["expected_pct"] == pytest.approx(5.0, abs=0.05)
    assert f["p_up"] > plain["p_up"] and f["stat"]["expected_pct"] == plain["expected_pct"]
    assert plain["stat"] is None


def test_center_missing_days_fall_back_to_statistical_drift():
    daily = _daily()
    days = F.next_weekdays(daily[-1].ts)
    plain = F.forecast(daily)
    f = F.forecast(daily, center={days[-1]: plain["path"][-1]["p50"] * 1.02})
    assert f["path"][0]["p50"] == plain["path"][0]["p50"]          # day 1 not covered -> statistical
    assert f["path"][-1]["p50"] == pytest.approx(plain["path"][-1]["p50"] * 1.02, abs=0.011)


# ---------- the AI file ----------

def test_load_validates_and_expires(tmp_path):
    daily = _daily()
    assert silver_ai.load(tmp_path)[1] == "尚未產生 AI 預測"
    _ai_file(tmp_path, daily)
    ai, why = silver_ai.load(tmp_path)
    assert ai and not why and len(ai["_prices"]) == 7
    _ai_file(tmp_path, daily, generated=datetime.now(timezone.utc) - timedelta(days=5))
    ai, why = silver_ai.load(tmp_path)
    assert ai is None and "過期" in why
    _ai_file(tmp_path, daily, symbol="GC=F")
    assert silver_ai.load(tmp_path)[0] is None
    (tmp_path / silver_ai.FILE).write_text("{not json", encoding="utf-8")
    assert "無法讀取" in silver_ai.load(tmp_path)[1]


def test_center_for_requires_the_week_end():
    ai = {"_prices": {"2026-11-30": 1.0, "2026-12-01": 1.0, "2026-12-02": 1.0}}
    assert silver_ai.center_for(ai, ["2026-11-30", "2026-12-01", "2026-12-02"]) is not None
    assert silver_ai.center_for(ai, ["2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03"]) is None
    assert silver_ai.center_for(ai, ["2026-12-02", "2026-12-03", "2026-12-04"]) is None


# ---------- engine: only silver uses the AI model ----------

def _fetch_for(daily):
    def fetch(url):
        bars = daily if "interval=1d" in url else [Bar(NOW - (600 - i) * 3600, b.close, b.close, b.close, b.close)
                                                    for i, b in enumerate(daily[-600:])]
        return json.dumps({"chart": {"error": None, "result": [{"meta": {}, "timestamp": [b.ts for b in bars],
            "indicators": {"quote": [{k: [getattr(b, k) for b in bars] for k in ("open", "high", "low", "close", "volume")}]}}]}}).encode()
    return fetch


def test_engine_uses_ai_for_silver_only(tmp_path):
    cfg = load_config(CONFIG)
    daily = _daily()
    _ai_file(tmp_path, daily, move=0.04)
    snap = run_once(cfg, tmp_path, fetch=_fetch_for(daily), notify=False, record=False)
    fc = {m["code"]: m["forecast"] for m in snap["metals"]}
    assert fc["Ag"]["model"]["kind"] == "silver-ai" and fc["Ag"]["model"]["algorithm"] == "BiGRU"
    assert fc["Ag"]["expected_pct"] == pytest.approx(4 * 5 / 7, abs=0.1)     # AI's 5th trading day of 7
    assert all("model" not in fc[c] for c in ("Au", "Pt", "Pd"))
    html = render_page(snap)
    assert "白銀 AI 冠軍模型" in html and "renderAiForecast" in html


def test_engine_falls_back_without_ai_file(tmp_path):
    cfg = load_config(CONFIG)
    daily = _daily()
    snap = run_once(cfg, tmp_path, fetch=_fetch_for(daily), notify=False, record=False)
    ag = next(m for m in snap["metals"] if m["code"] == "Ag")["forecast"]
    assert ag["model"] == {"kind": "stat", "fallback_reason": "尚未產生 AI 預測"}
    assert ag["stat"] is None and ag["backtest"] is not None


def test_engine_falls_back_when_ai_dates_do_not_cover_this_week(tmp_path):
    cfg = load_config(CONFIG)
    daily = _daily()
    old = _daily(end=NOW - 20 * 86400)          # predictions for two weeks ago
    _ai_file(tmp_path, old)
    snap = run_once(cfg, tmp_path, fetch=_fetch_for(daily), notify=False, record=False)
    ag = next(m for m in snap["metals"] if m["code"] == "Ag")["forecast"]
    assert ag["model"]["kind"] == "stat" and "日期" in ag["model"]["fallback_reason"]


def test_engine_shows_direct_multi_day_model(tmp_path):
    cfg = load_config(CONFIG)
    daily = _daily()
    doc = _ai_file(tmp_path, daily, move=0.03, days=5, drivers_horizon=5)
    doc["model"].update(method="direct", horizon=5, holdout_da_h=58.1, holdout_n_h=71,
                        pred_target="multi", pred_target_name="多日報酬率（直接預測 T+1…T+N）")
    (tmp_path / silver_ai.FILE).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    snap = run_once(cfg, tmp_path, fetch=_fetch_for(daily), notify=False, record=False)
    ag = next(m for m in snap["metals"] if m["code"] == "Ag")["forecast"]
    assert ag["model"]["method"] == "direct" and ag["model"]["horizon"] == 5
    assert ag["model"]["holdout_da_h"] == 58.1 and ag["model"]["drivers_horizon"] == 5
    assert ag["expected_pct"] == pytest.approx(3.0, abs=0.1)

import json
import math
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import ROOT

from metal_alert import alerts as al
from metal_alert import indicators as ta
from metal_alert.cli import main
from metal_alert.config import PriceAlert, load_config
from metal_alert.engine import run_once
from metal_alert.page import render_page
from metal_alert.signals import analyse, level_for
from metal_alert.web import Monitor, make_handler
from metal_alert.yahoo import Bar, fetch_bars, parse_chart

CONFIG = ROOT / "config" / "metal_alert.yaml"
T0 = 1_790_000_000


def make_bars(closes, step=3600, start=T0):
    return [Bar(start + i * step, c, c * 1.002, c * 0.998, c, 100) for i, c in enumerate(closes)]


def chart_json(bars) -> bytes:
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"symbol": "GC=F"},
        "timestamp": [b.ts for b in bars],
        "indicators": {"quote": [{
            "open": [b.open for b in bars], "high": [b.high for b in bars], "low": [b.low for b in bars],
            "close": [b.close for b in bars], "volume": [b.volume for b in bars],
        }]},
    }]}}).encode()


def wave(n, base=2000.0, amp=40.0, drift=0.0):
    return [base + amp * math.sin(i / 9) + drift * i for i in range(n)]


def selloff(n=600):
    """Sideways market, then a sharp drop at the end (should read as a relative low)."""
    closes = wave(n - 30)
    last = closes[-1]
    return closes + [last - 6 * (i + 1) for i in range(30)]


def rally(n=600):
    closes = wave(n - 30)
    last = closes[-1]
    return closes + [last + 6 * (i + 1) for i in range(30)]


@pytest.fixture
def cfg():
    return load_config(CONFIG)


# ---------- indicators ----------

def test_sma_and_ema():
    assert ta.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    e = ta.ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2.0 and e[3] == pytest.approx(3.0) and e[4] == pytest.approx(4.0)


def test_rsi_extremes():
    up = ta.rsi([float(i) for i in range(30)], 14)
    assert up[13] is None and up[-1] == 100.0
    down = ta.rsi([float(30 - i) for i in range(30)], 14)
    assert down[-1] == pytest.approx(0.0)


def test_bollinger_constant_series():
    mid, up, lo = ta.bollinger([5.0] * 25, 20, 2)
    assert mid[-1] == up[-1] == lo[-1] == 5.0 and mid[18] is None


def test_kd_williams_cci_atr_ranges():
    closes = wave(200)
    hi, lo = [c + 2 for c in closes], [c - 2 for c in closes]
    k, d = ta.stochastic_kd(hi, lo, closes)
    wr = ta.williams_r(hi, lo, closes)
    atr = ta.atr(hi, lo, closes)
    assert all(0 <= v <= 100 for v in k if v is not None)
    assert all(-100 <= v <= 0 for v in wr if v is not None)
    assert all(v > 0 for v in atr if v is not None)
    assert ta.cci(hi, lo, closes)[-1] is not None


def test_macd_cross_sign():
    line, sig, hist = ta.macd([float(i) for i in range(60)])
    assert line[-1] > 0 and hist[-1] == pytest.approx(line[-1] - sig[-1])


# ---------- signals ----------

def test_level_for(cfg):
    assert level_for(75, cfg.levels) == "strong_buy"
    assert level_for(35, cfg.levels) == "buy"
    assert level_for(0, cfg.levels) == "neutral"
    assert level_for(-40, cfg.levels) == "sell"
    assert level_for(-90, cfg.levels) == "strong_sell"


def test_selloff_reads_as_entry(cfg):
    spec = cfg.metals[0]
    a = analyse(spec, make_bars(selloff()), make_bars(wave(120), step=86400, start=T0 - 120 * 86400), cfg)
    assert a["score"] >= cfg.levels["buy"], a["votes"]
    assert a["level"] in ("buy", "strong_buy")
    assert a["plan"]["buy_stop"] < a["price"] < a["plan"]["buy_target"]
    assert len(a["chart"]["close"]) == cfg.chart_bars
    assert {"buy", "sell", "forward_bars"} <= set(a["backtest"])


def test_rally_reads_as_exit(cfg):
    a = analyse(cfg.metals[0], make_bars(rally()), [], cfg)
    assert a["score"] <= cfg.levels["sell"], a["votes"]
    assert a["level"] in ("sell", "strong_sell")


# ---------- yahoo ----------

def test_parse_chart_skips_missing():
    bars = make_bars([1.0, 2.0, 3.0])
    raw = json.loads(chart_json(bars))
    raw["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
    parsed, meta = parse_chart(json.dumps(raw).encode())
    assert [b.close for b in parsed] == [1.0, 3.0] and meta["symbol"] == "GC=F"


def test_fetch_bars_retries_other_host():
    calls = []

    def fetch(url):
        calls.append(url)
        if "query1" in url:
            raise OSError("429")
        return chart_json(make_bars([1.0, 2.0]))

    bars, _ = fetch_bars("GC=F", "1h", "5d", fetch, retries=1)
    assert len(bars) == 2 and "query2" in calls[-1]


# ---------- alerts ----------

def _assessment(level, score, price=100.0, code="Au", ts=T0):
    return {"code": code, "name": "黃金", "level": level, "level_name": level, "score": score, "price": price,
            "unit": "USD/oz", "ts": ts, "trend_text": "盤整", "votes": [
                {"name": "RSI", "value": "25", "vote": 0.8, "comment": "超賣"}],
            "plan": {"buy_stop": 95, "buy_target": 110, "sell_stop": 105, "sell_target": 90}}


def test_detect_level_change_and_cooldown(cfg):
    alerts, state = al.detect([_assessment("buy", 40)], {}, cfg, T0)
    assert len(alerts) == 1 and alerts[0]["side"] == "buy" and alerts[0]["reasons"]
    # same level next hour: nothing
    alerts, state = al.detect([_assessment("buy", 45)], state, cfg, T0 + 3600)
    assert alerts == []
    # strengthening to strong_buy ignores the cooldown
    alerts, state = al.detect([_assessment("strong_buy", 70)], state, cfg, T0 + 7200)
    assert len(alerts) == 1
    # back to neutral, then buy again within cooldown: suppressed
    _, state = al.detect([_assessment("neutral", 0)], state, cfg, T0 + 3 * 3600)
    alerts, state = al.detect([_assessment("buy", 35)], state, cfg, T0 + 4 * 3600)
    assert alerts == []
    # sell side has its own cooldown
    alerts, _ = al.detect([_assessment("sell", -40)], state, cfg, T0 + 5 * 3600)
    assert len(alerts) == 1 and alerts[0]["side"] == "sell" and alerts[0]["stop"] == 105


def test_price_alert_cross(cfg):
    cfg.price_alerts = [PriceAlert("Au", below=95, above=None)]
    _, state = al.detect([_assessment("neutral", 0, price=100)], {}, cfg, T0)
    alerts, _ = al.detect([_assessment("neutral", 0, price=94)], state, cfg, T0 + 3600)
    assert len(alerts) == 1 and alerts[0]["kind"] == "price"


def test_notify_channels():
    sent = []
    alert = al._alert(_assessment("buy", 40), "signal", "buy", "黃金 留意進場")
    used = al.notify([alert], [al.format_text(alert)], env={
        "METAL_ALERT_WEBHOOK_URL": "https://hooks.example/x", "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"},
        post=lambda url, payload: sent.append((url, payload)))
    assert used == ["webhook", "telegram"] and "不構成投資建議" in sent[0][1]["text"]
    assert al.notify([alert], ["x"], env={}) == []


def test_notify_email():
    mails = []
    alert = al._alert(_assessment("buy", 40), "signal", "buy", "黃金 留意進場")
    env = {"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "me@example.com", "SMTP_PASSWORD": "x",
           "ALERT_EMAIL_TO": "a@example.com, b@example.com"}
    used = al.notify([alert, alert], [al.format_text(alert)], env=env, post=lambda *a: None,
                     send_email=lambda e, subject, body: mails.append((subject, body)))
    assert used == ["email"] and mails[0][0] == "【貴金屬預警】進場|黃金 留意進場 等 2 則"
    assert "預警網頁" not in mails[0][1]

    env["METAL_ALERT_PAGE_URL"] = "https://example.github.io/claude/secret/metals/"
    al.notify([alert], [al.format_text(alert)], env=env, send_email=lambda e, subject, body: mails.append((subject, body)))
    assert mails[-1][1].endswith("預警網頁(圖表、指標明細與預警紀錄):https://example.github.io/claude/secret/metals/")

    def boom(*a):
        raise OSError("auth failed")

    assert al.notify([alert], ["x"], env=env, send_email=boom) == []  # failure is logged, not raised


def test_send_email_builds_message(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, **kw):
            sent["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(al.smtplib, "SMTP", FakeSMTP)
    al._send_email({"SMTP_HOST": "smtp.office365.com", "SMTP_PORT": "587", "SMTP_USER": "me@example.com",
                    "SMTP_PASSWORD": "x", "ALERT_EMAIL_TO": "a@example.com;b@example.com"}, "主旨", "內容")
    msg = sent["msg"]
    assert sent["tls"] and sent["login"] == "me@example.com" and sent["server"] == ("smtp.office365.com", 587)
    assert msg["To"] == "a@example.com, b@example.com" and msg["From"] == "me@example.com" and msg["Subject"] == "主旨"


# ---------- engine / page / web ----------

def _fake_fetch(url):
    if "interval=1d" in url:
        return chart_json(make_bars(wave(120), step=86400, start=T0 - 120 * 86400))
    return chart_json(make_bars(selloff()))


def test_run_once_writes_snapshot_and_alerts(cfg, tmp_path):
    snap = run_once(cfg, tmp_path, fetch=_fake_fetch, notify=False, now=T0 + 10**6)
    assert len(snap["metals"]) == len(cfg.metals) and not snap["errors"]
    assert snap["new_alerts"]  # first run inside the buy zone alerts once
    assert (tmp_path / "latest.json").exists() and (tmp_path / "state.json").exists()
    assert len(al.read_log(tmp_path / "alerts.jsonl")) == len(snap["new_alerts"])
    again = run_once(cfg, tmp_path, fetch=_fake_fetch, notify=False, now=T0 + 10**6 + 3600)
    assert again["new_alerts"] == []


def test_run_once_read_only(cfg, tmp_path):
    snap = run_once(cfg, tmp_path, fetch=_fake_fetch, notify=False, record=False)
    assert snap["metals"] and snap["new_alerts"] == [] and not any(tmp_path.iterdir())


def test_run_once_reports_fetch_errors(cfg, tmp_path):
    def broken(url):
        raise OSError("offline")

    snap = run_once(cfg, tmp_path, fetch=broken, notify=False, retries=0)
    assert snap["metals"] == [] and len(snap["errors"]) == len(cfg.metals)


def test_render_page_embeds_data():
    html = render_page({"metals": [], "note": "</script>"}, live=True)
    assert "const LIVE = true" in html and "<\\/script>" in html and "/*__DATA__*/" not in html


def test_site_command(cfg, tmp_path):
    data = tmp_path / "data"
    run_once(cfg, data, fetch=_fake_fetch, notify=False)
    assert main(["-c", str(CONFIG), "-d", str(data), "site", "-o", str(tmp_path / "site")]) == 0
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "貴金屬價格預警" in page and 'id="fabnav"' in page and 'href="../news/index.html"' in page
    assert main(["-c", str(CONFIG), "-d", str(tmp_path / "empty"), "site", "-o", str(tmp_path / "x")]) == 1


def test_web_api(cfg, tmp_path):
    monitor = Monitor(cfg, tmp_path, runner=lambda c, d: run_once(c, d, fetch=_fake_fetch, notify=False))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(monitor))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/latest")
        r = urllib.request.urlopen(urllib.request.Request(base + "/api/refresh", method="POST"))
        assert len(json.loads(r.read())["metals"]) == len(cfg.metals)
        assert json.loads(urllib.request.urlopen(base + "/api/latest").read())["metals"]
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "const LIVE = true" in page
        assert monitor.due_in() > 0
    finally:
        httpd.shutdown()

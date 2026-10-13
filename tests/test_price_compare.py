import json
import threading
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from types import SimpleNamespace as NS
from urllib.parse import quote

import pytest
from conftest import ROOT

from price_compare.cli import main
from price_compare.compare import analyse
from price_compare.config import Settings, load_config
from price_compare.fx import FxConverter, parse_bot_csv
from price_compare.models import Quote, purity_label
from price_compare.report import render_json, render_markdown
from price_compare.search import run_search
from price_compare.sources import (
    ClaudeMarketSearch, QuoteList, QuoteOut, append_quote_book, load_quote_book, parse_pchome, search_quote_book,
)
from price_compare.spec import match_level, match_score, parse_spec
from price_compare.web import make_handler

DEMO_BOOK = ROOT / "examples" / "demo_quotes.csv"
TODAY = date(2026, 10, 13)


@pytest.fixture
def settings():
    return Settings(quote_book=str(DEMO_BOOK), live_fx=False, fx_rates={"USD": 31.5, "CNY": 4.4})


def fx(settings):
    return FxConverter("TWD", settings.fx_rates)


@pytest.mark.parametrize(
    "text, materials, purity, form, dims, shape",
    [
        ("鈦靶 4N5 Φ300x6.35mm", ["Ti"], 99.995, "target", [300.0, 6.35], "round"),
        ('Titanium sputtering target, 99.995%, 3" dia x 0.125" thick', ["Ti"], 99.995, "target", [76.2, 3.17], "round"),
        ("ITO靶材 In2O3:SnO2=90:10 99.99% 127x406x6mm", ["ITO"], 99.99, "target", [406.0, 127.0, 6.0], "rect"),
        ("高純銅靶 6N D76.2*3", ["Cu"], 99.9999, "target", [76.2, 3.0], "round"),
        ("鋁銅合金靶 AlCu 5N 直徑300 厚度 12", ["AlCu"], 99.999, "target", [300.0, 12.0], "round"),
        ("金蒸鍍料 99.999% 顆粒", ["Au"], 99.999, "pellet", [], ""),
        ("鉭粉 3N5 325目", ["Ta"], 99.95, "powder", [], ""),
        ("GeSbTe target 4N", ["GeSbTe"], 99.99, "target", [], ""),
        ("鈦靶 4N5 Φ300x6.35mm 含銅背板", ["Ti"], 99.995, "target", [300.0, 6.35], "round"),
        ("合金鋼板", [], None, "plate", [], ""),
    ],
)
def test_parse_spec(text, materials, purity, form, dims, shape):
    s = parse_spec(text)
    assert s.materials == materials
    assert s.purity == purity
    assert s.form == form
    assert s.dims_mm == dims
    assert s.shape == shape


def test_purity_label():
    assert [purity_label(p) for p in (99.9, 99.99, 99.995, 99.9999, 99.5)] == ["3N", "4N", "4N5", "6N", "2N5"]


def test_match_score_levels():
    q = parse_spec("鈦靶 4N5 Φ300x6.35mm")
    assert match_score(q, parse_spec("Ti target 99.995% 300 mm dia x 6.35 mm"))[0] == 100
    s, notes = match_score(q, parse_spec("Ti target 3N5 Φ300x6.35"))
    assert match_level(s) == "近似" and "純度較低" in notes[0]
    assert match_score(q, parse_spec("鋁靶 5N Φ300x6.35"))[0] == 0
    s, notes = match_score(q, parse_spec("鈦靶 4N5 Φ450x10"))
    assert s < 60 and any("尺寸不同" in n for n in notes)


def test_quote_book_search_filters_material(settings):
    spec = parse_spec("鈦靶 4N5 Φ300x6.35mm")
    vendors = {q.vendor for q in search_quote_book(spec, settings.quote_book)}
    assert "示範供應商F" not in vendors  # aluminium target
    assert {"示範供應商A", "示範供應商B", "示範供應商C"} <= vendors


def test_analyse_converts_scores_and_flags(settings):
    spec = parse_spec("鈦靶 4N5 Φ300x6.35mm")
    comp = analyse(spec, search_quote_book(spec, settings.quote_book), settings, fx(settings), today=TODAY)
    by_vendor = {q.vendor: q for q in comp.quotes}

    assert by_vendor["示範供應商B"].unit_price_twd == pytest.approx(1650 * 31.5)
    assert by_vendor["示範供應商C"].match_level == "近似"
    assert by_vendor["示範供應商D"].outlier == "過舊"  # older than max_quote_age_days
    assert by_vendor["示範供應商E"].basis == "每公斤"
    (stats,) = comp.stats
    assert stats.basis == "每片/個" and stats.count == 3
    assert stats.low == pytest.approx(9800 * 4.4) and stats.high == 58000
    assert stats.best.vendor == "示範供應商B"  # cheapest exact match, not the cheaper lower-purity one


def test_unit_normalisation_and_outliers(settings):
    spec = parse_spec("金 5N 顆粒")
    quotes = [
        Quote("x", "V1", "Gold granules 99.999%", 250, "USD", "g", 10),  # 25 USD/g
        Quote("x", "V2", "金顆粒 5N", 800, "TWD", "g", 1),
        Quote("x", "V3", "金顆粒 5N", 2_600_000, "TWD", "kg", 1),
        Quote("x", "V4", "金顆粒 5N", 90_000_000, "TWD", "kg", 1),  # way above the median
    ]
    comp = analyse(spec, quotes, settings, fx(settings), today=TODAY)
    by = {q.vendor: q for q in comp.quotes}
    assert by["V1"].unit_price_twd == pytest.approx(25 * 31.5 * 1000)
    assert by["V2"].unit_price_twd == pytest.approx(800_000)
    assert by["V4"].outlier == "偏高"
    assert comp.stats[0].count == 3


def test_bot_csv_parser():
    text = (
        "﻿幣別,匯率,現金,即期,遠期10天,遠期30天,遠期60天,遠期90天,遠期120天,遠期150天,遠期180天,匯率,現金,即期,遠期10天\n"
        "USD,本行買入,31.1,31.4,31.3,31.2,31.1,31.0,30.9,30.8,30.7,本行賣出,31.8,31.6,31.5\n"
        "JPY,本行買入,0.2,0.21,0,0,0,0,0,0,0,本行賣出,0.22,0.215,0\n"
    )
    assert parse_bot_csv(text) == {"USD": 31.5, "JPY": 0.2125}
    conv = FxConverter("TWD", {"USD": 30})
    assert conv.refresh(fetch=lambda url: text.encode()) and conv.rates["USD"] == 31.5
    assert not FxConverter("TWD").refresh(fetch=lambda url: (_ for _ in ()).throw(OSError("offline")))


def test_parse_pchome():
    data = json.dumps({"prods": [{"Id": "ABC-123", "name": "鈦合金螺絲", "describe": "M3", "price": 199}, {"Id": "x", "price": 0}]})
    (q,) = parse_pchome(data)
    assert q.price == 199 and q.url.endswith("/prod/ABC-123")


class FakeMessages:
    def __init__(self, responses, parsed):
        self.responses = list(responses)
        self.parsed = parsed
        self.create_calls = []
        self.parse_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.responses.pop(0)

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return NS(stop_reason="end_turn", parsed_output=self.parsed)


def fake_client():
    paused = NS(stop_reason="pause_turn", content=[
        NS(type="server_tool_use", id="s1"),
        NS(type="web_search_tool_result", content=[NS(url="https://shop.example.com/ti-target")]),
    ])
    done = NS(stop_reason="end_turn", content=[
        NS(type="web_fetch_tool_result", content=NS(url="https://shop.example.com/ti-target")),
        NS(type="text", text="Example Shop:Ti target 4N5 Φ300x6.35mm,USD 1,580/片"),
    ])
    parsed = QuoteList(
        quotes=[
            QuoteOut(vendor="Example Shop", title="Titanium Sputtering Target", spec_text="99.995% 300mm dia x 6.35mm",
                     price=1580, currency="USD", unit="pcs", url="https://shop.example.com/ti-target"),
            QuoteOut(vendor="Made Up", title="Ti target", spec_text="4N5 Φ300x6.35", price=1200, currency="USD",
                     unit="pcs", url="https://elsewhere.example.org/x"),
            QuoteOut(vendor="Zero", title="Ti target", spec_text="4N5", price=0, currency="USD", unit="pcs"),
        ],
        market_summary="鈦靶價格持穩。",
    )
    msgs = FakeMessages([paused, done], parsed)
    return NS(beta=NS(messages=msgs)), msgs


def test_claude_search_resumes_pause_turn_and_flags_unseen_urls():
    client, msgs = fake_client()
    searcher = ClaudeMarketSearch(client=client, user_location={"country": "TW"})
    quotes = searcher.search(parse_spec("鈦靶 4N5 Φ300x6.35mm"), "10 片")

    assert len(msgs.create_calls) == 2
    resumed = msgs.create_calls[1]["messages"]
    assert [m["role"] for m in resumed] == ["user", "assistant"]  # no extra "continue" message
    tool_types = {t["type"] for t in msgs.create_calls[0]["tools"]}
    assert tool_types == {"web_search_20260209", "web_fetch_20260209"}
    assert msgs.create_calls[0]["fallbacks"] == "default"
    assert "需求數量:10 片" in msgs.create_calls[0]["messages"][0]["content"]
    assert "https://shop.example.com/ti-target" in msgs.parse_calls[0]["messages"][0]["content"]

    assert [q.vendor for q in quotes] == ["Example Shop", "Made Up"]  # zero price dropped
    assert "請人工確認" not in quotes[0].note
    assert "請人工確認" in quotes[1].note
    assert searcher.market_summary == "鈦靶價格持穩。"


def test_run_search_combines_sources(settings):
    client, _ = fake_client()
    comp = run_search("鈦靶 4N5 Φ300x6.35mm", settings, client=client, fx=fx(settings), today=TODAY)
    sources = {q.source for q in comp.quotes}
    assert sources == {"報價紀錄", "Claude 網路搜尋"}
    assert comp.market_summary == "鈦靶價格持穩。"
    md = render_markdown(comp, __import__("datetime").datetime(2026, 10, 13, 9, 0))
    assert "比價摘要" in md and "Example Shop" in md and "市場概況" in md
    data = json.loads(render_json(comp, __import__("datetime").datetime(2026, 10, 13, 9, 0)))
    assert data["parsed_spec"]["materials"] == ["Ti"] and data["summary"][0]["count"] >= 4


def test_cli_search_and_add(tmp_path, monkeypatch, capsys):
    book = tmp_path / "quotes.csv"
    book.write_text(DEMO_BOOK.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"live_fx: false\nfx_rates: {{USD: 31.5, CNY: 4.4}}\nsources:\n  quote_book: {book}\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    assert main(["-c", str(cfg), "add", "--vendor", "新供應商", "--title", "鈦靶", "--spec", "4N5 Φ300x6.35mm",
                 "--price", "50000", "--date", "2026-10-01"]) == 0
    assert load_quote_book(book)[-1].vendor == "新供應商"

    out = tmp_path / "out"
    assert main(["-c", str(cfg), "search", "-o", str(out), "鈦靶", "4N5", "Φ300x6.35mm"]) == 0
    printed = capsys.readouterr().out
    assert "新供應商" in printed and "TWD 50,000" in printed
    assert len(list(out.glob("*.md"))) == len(list(out.glob("*.csv"))) == len(list(out.glob("*.json"))) == 1


def test_append_quote_book_roundtrip(tmp_path):
    path = tmp_path / "q.csv"
    append_quote_book(path, [Quote("x", "V", "鈦靶", 1.5, "USD", "g", 100, quote_date="2026-10-01")])
    append_quote_book(path, [Quote("x", "W", "銅靶", 2, "USD")])
    rows = load_quote_book(path)
    assert [(r.vendor, r.pack_qty, r.unit) for r in rows] == [("V", 100.0, "g"), ("W", 1.0, "pcs")]


def test_default_config_loads():
    s = load_config(ROOT / "config" / "price_compare.yaml")
    assert s.base_currency == "TWD" and s.web_search and "USD" in s.fx_rates


def test_web_ui(settings, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(settings, str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        home = urllib.request.urlopen(base + "/").read().decode()
        assert "物料比價" in home and "name='q'" in home
        page = urllib.request.urlopen(base + "/search?book=1&q=" + quote("鈦靶 4N5 Φ300x6.35mm <b>")).read().decode()
        assert "示範供應商B" in page and "比價摘要" in page and "<b>" not in page.split("<main>")[1].split("value=")[1][:80]
        csv_name = next(tmp_path.glob("*.csv")).name
        body = urllib.request.urlopen(base + "/download?f=" + quote(csv_name)).read().decode("utf-8-sig")
        assert body.startswith("match_level")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/download?f=" + quote("../config/price_compare.yaml"))
    finally:
        server.shutdown()
        server.server_close()


def test_build_site(settings, tmp_path):
    from datetime import datetime

    from price_compare.report import write_reports
    from price_compare.site import build_site

    src, out = tmp_path / "reports", tmp_path / "site"
    spec = parse_spec("鈦靶 4N5 Φ300x6.35mm <script>")
    comp = analyse(spec, search_quote_book(spec, settings.quote_book), settings, fx(settings), today=TODAY)
    md, js, cs = write_reports(comp, src, datetime(2026, 10, 13, 9, 0))
    (src / "broken.json").write_text("{not json", encoding="utf-8")

    assert build_site(src, out, "https://github.com/o/r/actions/workflows/price-compare.yml") == 1
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "新增比價" in index and "<script>" not in index.split("<main>")[1].split("<script>document")[0]
    report = (out / f"{js.stem}.html").read_text(encoding="utf-8")
    assert "比價摘要" in report and "示範供應商B" in report and "TWD 51,975" in report
    assert (out / cs.name).exists() and (out / md.name).exists() and (out / ".nojekyll").exists()
    assert main(["site", "-i", str(src), "-o", str(tmp_path / "site2")]) == 0

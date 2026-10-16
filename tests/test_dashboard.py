import json
from datetime import datetime, timezone

import pytest
from conftest import ROOT
from test_fin_analyzer import DEMO_CONFIG, make_company

from fin_analyzer.analysis import analyze
from fin_analyzer.cli import main
from fin_analyzer.config import Company, guess_market, load_fin_config
from fin_analyzer.dashboard import build_dashboard_data, load_fx, render_html
from fin_analyzer.yahoo import fetch_fx, fetch_yahoo, frames_to_quarters

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


def test_guess_market():
    assert guess_market("1785") == "TW"
    assert guess_market("5016.T") == "JP"
    assert guess_market("300666.SZ") == "CN" and guess_market("600206.SS") == "CN"
    assert guess_market("SOLS") == "US"
    assert Company("4042.T").currency == "JPY"


YAHOO_FRAMES = {
    "income": {
        "2025-06-30 00:00:00": {"Total Revenue": 1000.0, "Cost Of Revenue": 700.0, "Operating Income": 120.0,
                                "Pretax Income": 130.0, "Net Income Common Stockholders": 90.0, "Basic EPS": float("nan")},
        "2025-03-31 00:00:00": {"Total Revenue": 900.0, "Gross Profit": 260.0},
    },
    "balance": {"2025-06-30 00:00:00": {"Total Assets": 5000.0, "Ordinary Shares Number": 100.0, "Stockholders Equity": 2500.0}},
    "cashflow": {"2025-06-30 00:00:00": {"Operating Cash Flow": 150.0, "Capital Expenditure": -60.0}},
}


def test_frames_to_quarters():
    q = frames_to_quarters(YAHOO_FRAMES)
    assert list(q) == ["2025Q1", "2025Q2"]
    assert q["2025Q2"]["revenue"] == 1000 and q["2025Q2"]["cogs"] == 700
    assert q["2025Q2"]["eps"] is None  # NaN dropped
    assert q["2025Q2"]["capex"] == 60  # outflow -> positive spend
    assert q["2025Q2"]["shares"] == 100


class FakeFrame:
    def __init__(self, d):
        self.d = d

    def to_dict(self):
        return self.d


class FakeHistory(dict):
    pass


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol
        self.quarterly_income_stmt = FakeFrame(YAHOO_FRAMES["income"])
        self.quarterly_balance_sheet = FakeFrame(YAHOO_FRAMES["balance"])
        self.quarterly_cashflow = FakeFrame(YAHOO_FRAMES["cashflow"])
        self.info = {"shortName": "JX Advanced Metals", "financialCurrency": "JPY"}

    def history(self, **kwargs):
        rate = {"JPYTWD=X": 0.21, "USDTWD=X": 32.1}.get(self.symbol, 1500.0)
        return FakeHistory(Close={"2025-07-01 00:00:00+09:00": rate * 0.99, "2025-07-02 00:00:00+09:00": rate})


def test_fetch_yahoo_with_fake_ticker():
    d = fetch_yahoo("5016.T", ticker_factory=FakeTicker)
    assert d.name == "JX Advanced Metals" and d.currency == "JPY"
    row = d.quarters["2025Q2"]
    assert row["gross_profit"] == 300 and row["non_operating"] == 10  # derived
    assert row["eps"] == pytest.approx(0.9)  # net income / shares when EPS missing
    assert d.last_close == ("2025-07-02", 1500.0)
    assert fetch_fx(["JPY", "USD", "TWD"], "TWD", ticker_factory=FakeTicker) == {"JPY": 0.21, "USD": 32.1}


def test_dashboard_converts_currencies(tmp_path):
    cfg = load_fin_config(DEMO_CONFIG)
    cfg.data_dir = str(tmp_path)
    tw, jp = make_company(), make_company()
    jp.ticker, jp.currency = "J", "JPY"
    analyses = [analyze(tw, cfg, Company("T", "台廠", market="TW")), analyze(jp, cfg, Company("J", "日廠", market="JP"))]
    cfg.target = analyses[0].company
    data = build_dashboard_data(analyses, cfg, NOW)
    t, j = data["companies"]
    assert t["is_focus"] and not j["is_focus"]
    # 4 quarters x 1100 TWD = 4400 -> /1e8 ; JPY amounts scaled by the JPY rate.
    assert t["latest"]["revenue_ttm"] == pytest.approx(4400 / 1e8)
    assert j["latest"]["revenue_ttm"] == pytest.approx(4400 * 0.21 / 1e8)
    assert j["latest"]["gross_margin_ttm"] == pytest.approx(0.30)  # ratios are not converted
    assert t["monthly"] == {} and data["unit_label"] == "億台幣"

    (tmp_path / "fx.json").write_text(json.dumps({"base": "TWD", "rates": {"JPY": 0.2}, "asof": "2026-10-15"}))
    fx = load_fx(cfg)
    assert fx["rates"]["JPY"] == 0.2 and fx["rates"]["USD"] == 32.0 and fx["asof"] == "2026-10-15"

    html = render_html({**data, "title": "<b>x</b>", "industry_notes": ["</script><script>alert(1)</script>"]})
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "</script><script>alert(1)" not in html  # payload cannot close the script tag


def test_dashboard_cli_demo(tmp_path):
    assert main(["dashboard", "-c", str(DEMO_CONFIG), "--data-dir", str(ROOT / "examples" / "demo_financials"), "-o", str(tmp_path)]) == 0
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "/*__DATA__*/" not in html and "示範靶材" in html
    data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
    assert {c["currency"] for c in data["companies"]} == {"TWD", "JPY", "USD", "CNY"}
    assert all(c["latest"]["revenue_ttm"] for c in data["companies"])
    assert (tmp_path / "comparison.csv").read_text(encoding="utf-8-sig").startswith("代號,公司")
    # Companies without data are listed, not fatal.
    assert main(["dashboard", "-c", str(DEMO_CONFIG), "--data-dir", str(tmp_path / "empty"), "-o", str(tmp_path / "x")]) == 1

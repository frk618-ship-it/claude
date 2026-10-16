import json
from datetime import datetime, timezone

import pytest
from conftest import ROOT
from test_fin_analyzer import DEMO_CONFIG, make_company

from fin_analyzer.analysis import analyze
from fin_analyzer.cli import main
from fin_analyzer.config import Company, guess_market, load_fin_config
from fin_analyzer.dashboard import build_dashboard_data, load_fx, render_html
from fin_analyzer.yahoo import adjust_for_splits, fetch_fx, fetch_yahoo, fill_quarter_from_annual, frames_to_quarters

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
    # Data warnings and the methodology notes start collapsed.
    assert '<details class="fold" id="banners-fold" hidden>' in html
    assert '<details class="fold">\n        <summary><h2>資料與計算說明</h2>' in html
    data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
    assert {c["currency"] for c in data["companies"]} == {"TWD", "JPY", "USD", "CNY"}
    assert all(c["latest"]["revenue_ttm"] for c in data["companies"])
    assert (tmp_path / "comparison.csv").read_text(encoding="utf-8-sig").startswith("代號,公司")
    # Companies without data are listed, not fatal.
    assert main(["dashboard", "-c", str(DEMO_CONFIG), "--data-dir", str(tmp_path / "empty"), "-o", str(tmp_path / "x")]) == 1


def test_cn_direct_method_operating_cash_flow():
    q = frames_to_quarters({"cashflow": {"2026-06-30": {"Cash Flowsfromusedin Operating Activities Direct": 143.0,
                                                         "Capital Expenditure": -288.0}}})
    assert q["2026Q2"]["operating_cf"] == 143 and q["2026Q2"]["capex"] == 288


def test_fill_quarter_from_annual():
    quarters = {p: {"revenue": v, "eps": 0.1} for p, v in {"2025Q1": 100.0, "2025Q2": 110.0, "2025Q4": 130.0}.items()}
    annual = {"2025Q4": {"revenue": 460.0, "eps": 0.5}, "2024Q4": {"revenue": 400.0}}
    assert fill_quarter_from_annual(quarters, annual) == ["2025Q3"]
    assert quarters["2025Q3"]["revenue"] == 120  # FY - other three quarters
    assert quarters["2025Q3"]["eps"] is None  # EPS is not additive
    assert "2024Q3" not in quarters  # 2024 has no quarters -> nothing derived


def test_adjust_for_splits():
    rows = {"2026Q1": {"shares": 57.0, "eps": 1595.0}, "2026Q4": {"shares": 572.0, "eps": 40.0}}
    assert adjust_for_splits(rows, {"2017-09-27": 0.1, "2026-09-29": 10.0}) == ["2026Q1"]
    assert rows["2026Q1"] == {"shares": 570.0, "eps": 159.5}
    assert rows["2026Q4"] == {"shares": 572.0, "eps": 40.0}  # already post-split


class AnnualOnlyTicker(FakeTicker):
    """Japanese filer on Yahoo: EPS-only quarters, semi-annual balance sheets, annual statements."""

    def __init__(self, symbol):
        super().__init__(symbol)
        self.quarterly_income_stmt = FakeFrame({"2026-06-30": {"Basic EPS": 56.8}})
        self.quarterly_balance_sheet = FakeFrame({
            d: {"Total Assets": 2000.0, "Stockholders Equity": 1000.0, "Ordinary Shares Number": 10.0, "Inventory": 300.0}
            for d in ("2025-03-31", "2026-03-31")})
        self.quarterly_cashflow = FakeFrame({})
        self.income_stmt = FakeFrame({
            "2026-03-31": {"Total Revenue": 1200.0, "Cost Of Revenue": 900.0, "Operating Income": 150.0,
                           "Net Income Common Stockholders": 100.0, "Basic EPS": 10.0},
            "2025-03-31": {"Total Revenue": 1000.0, "Cost Of Revenue": 760.0, "Net Income Common Stockholders": 80.0},
        })
        self.cashflow = FakeFrame({"2026-03-31": {"Operating Cash Flow": 130.0, "Capital Expenditure": -50.0}})


def test_annual_fallback_for_annual_only_filers(tmp_path):
    from fin_analyzer.models import load_company, save_company

    d = fetch_yahoo("4042.T", ticker_factory=AnnualOnlyTicker)
    assert "年度財報" in d.note and d.annual["2026Q1"]["gross_profit"] == 300
    save_company(d, tmp_path)
    d = load_company("4042.T", tmp_path)
    assert d.annual["2026Q1"]["revenue"] == 1200  # annual.csv round-trip

    a = analyze(d, load_fin_config(DEMO_CONFIG))
    assert a.latest == "2026Q1"  # not the EPS-only 2026Q2 row
    m = a.latest_metrics
    assert m["revenue_ttm"] == 1200 and m["revenue_ttm_yoy"] == pytest.approx(0.2)
    assert m["gross_margin_ttm"] == pytest.approx(0.25) and m["operating_margin_ttm"] == pytest.approx(0.125)
    assert m["roe_ttm"] == pytest.approx(0.1) and m["eps_ttm"] == 10
    assert m["fcf_ttm"] == 80
    assert m["dio"] == pytest.approx(300 / 900 * 365)  # annual COGS -> 365-day basis

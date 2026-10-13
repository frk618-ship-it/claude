import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from conftest import ROOT

from fin_analyzer.analysis import RISK, analyze, peer_ranking
from fin_analyzer.cli import main
from fin_analyzer.commentary import Commentary, Commentator, llm_payload
from fin_analyzer.config import load_fin_config
from fin_analyzer.metrics import altman_z2, piotroski, quarter_metrics, valuation
from fin_analyzer.models import CompanyData, load_company, save_company, shift_month, shift_period
from fin_analyzer.monthly import monthly_metrics
from fin_analyzer.report import render_markdown
from fin_analyzer.sources import fetch_company, statements_to_quarters

DEMO_CONFIG = ROOT / "examples" / "demo_config.yaml"
NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def make_company(quarters: int = 8, gm_path=None) -> CompanyData:
    """Simple company: revenue +10%/yr, flat balance sheet, round numbers."""
    d = CompanyData(ticker="T", name="測試")
    periods = [shift_period("2024Q1", i) for i in range(quarters)]
    for i, p in enumerate(periods):
        rev = 1000.0 * (1.1 ** (i // 4))
        gm = gm_path[i] if gm_path else 0.30
        ni = rev * 0.1
        d.quarters[p] = dict(
            revenue=rev, cogs=rev * (1 - gm), gross_profit=rev * gm, operating_expenses=rev * 0.1,
            operating_income=rev * (gm - 0.1), non_operating=0.0, pretax_income=rev * 0.125, income_tax=rev * 0.025,
            net_income=ni, net_income_parent=ni, eps=ni / 100,
            cash=500.0, receivables=400.0, inventory=300.0, current_assets=1200.0, ppe=800.0, total_assets=2000.0,
            payables=200.0, short_term_debt=100.0, long_term_debt=200.0, current_liabilities=600.0,
            total_liabilities=800.0, share_capital=1000.0, retained_earnings=300.0, equity=1200.0, equity_parent=1000.0,
            operating_cf=ni * 1.2, capex=50.0, depreciation=40.0, interest_expense=5.0, dividends_paid=0.0,
        )
    return d


def test_period_helpers():
    assert shift_period("2025Q1", -1) == "2024Q4"
    assert shift_period("2025Q4", 1) == "2026Q1"
    assert shift_period("2025Q2", -4) == "2024Q2"
    assert shift_month("2025-01", -1) == "2024-12"
    assert shift_month("2025-11", 14) == "2027-01"


def test_quarter_metrics():
    d = make_company()
    qm = quarter_metrics(d)
    m = qm["2025Q4"]
    assert m["gross_margin"] == pytest.approx(0.30)
    assert m["revenue_yoy"] == pytest.approx(0.10)
    assert m["revenue_qoq"] == pytest.approx(0.0)
    assert m["net_margin"] == pytest.approx(0.10)
    # TTM: net income 4 * 110 = 440 over avg parent equity 1000.
    assert m["roe_ttm"] == pytest.approx(0.44)
    assert m["roa_ttm"] == pytest.approx(0.22)
    # DuPont identity
    assert m["net_margin_ttm"] * m["asset_turnover_ttm"] * m["equity_multiplier"] == pytest.approx(m["roe_ttm"])
    assert m["current_ratio"] == pytest.approx(2.0)
    assert m["quick_ratio"] == pytest.approx(1.5)
    assert m["debt_ratio"] == pytest.approx(0.4)
    assert m["net_debt"] == pytest.approx(-200.0)
    assert m["dso"] == pytest.approx(400 / 1100 * 91.25)
    assert m["dio"] == pytest.approx(300 / (1100 * 0.7) * 91.25)
    assert m["ccc"] == pytest.approx(m["dso"] + m["dio"] - m["dpo"])
    assert m["ocf_to_net_income_ttm"] == pytest.approx(1.2)
    assert m["fcf_ttm"] == pytest.approx(440 * 1.2 - 200)
    assert m["interest_coverage_ttm"] == pytest.approx((137.5 * 4 + 20) / 20)
    # First quarter has no history -> growth / TTM undefined, not zero.
    assert qm["2024Q1"]["revenue_yoy"] is None and qm["2024Q1"]["roe_ttm"] is None


def test_growth_undefined_from_loss():
    d = make_company()
    d.quarters["2024Q4"]["eps"] = -0.5
    assert quarter_metrics(d)["2025Q4"]["eps_yoy"] is None


def test_scores_and_valuation():
    d = make_company()
    qm = quarter_metrics(d)
    fs = piotroski(d, qm, "2025Q4")
    assert fs.score is not None and 0 <= fs.score <= 9
    assert dict(fs.details)["ROA 為正"] is True
    # Not enough history -> no score.
    assert piotroski(d, qm, "2024Q2").score is None

    z = altman_z2(d, qm, "2025Q4")
    expected = 6.56 * 600 / 2000 + 3.26 * 300 / 2000 + 6.72 * (137.5 * 4 + 20) / 2000 + 1.05 * 1200 / 800
    assert z.value == pytest.approx(expected) and z.zone == "安全區"

    d.prices = [("2026-10-12", 50.0)]
    v = valuation(d, qm, "2025Q4", par_value=10)
    assert v["shares"] == 100 and v["bvps"] == 10
    assert v["pb"] == pytest.approx(5.0)
    assert v["pe_ttm"] == pytest.approx(50 / 4.4)


def test_monthly_metrics():
    d = CompanyData(ticker="T")
    for i in range(24):
        m = shift_month("2024-01", i)
        d.monthly_revenue[m] = 100.0 + (20 if m.startswith("2025") else 0) + (50 if m == "2025-12" else 0)
    mm = monthly_metrics(d)
    assert mm["2025-03"]["yoy"] == pytest.approx(0.2)
    assert mm["2025-03"]["ytd_yoy"] == pytest.approx(0.2)
    assert mm["2025-03"]["mom"] == pytest.approx(0.0)
    assert mm["2025-12"]["record_high"] == 1.0 and mm["2025-12"]["high_12m"] == 1.0
    assert mm["2024-06"]["yoy"] is None


def test_signals_flag_gross_margin_decline():
    d = make_company(gm_path=[0.30, 0.30, 0.30, 0.30, 0.29, 0.27, 0.25, 0.22])
    cfg = load_fin_config(DEMO_CONFIG)
    a = analyze(d, cfg)
    risks = [s.message for s in a.signals if s.level == RISK]
    assert any("連續 3 季下滑" in r for r in risks)
    assert any("較去年同季下滑" in r for r in risks)


def test_save_load_roundtrip(tmp_path):
    d = make_company()
    d.monthly_revenue = {"2025-01": 1.0e9}
    d.prices = [("2026-10-12", 88.5)]
    save_company(d, tmp_path)
    back = load_company("T", tmp_path)
    assert back.name == "測試"
    assert back.quarters["2025Q4"]["revenue"] == pytest.approx(1100.0)
    assert back.monthly_revenue == {"2025-01": 1.0e9} and back.last_close == ("2026-10-12", 88.5)
    with pytest.raises(FileNotFoundError):
        load_company("NOPE", tmp_path)


def _rows(stmt_rows):
    return [{"date": date, "type": t, "value": v, "origin_name": o} for date, t, v, o in stmt_rows]


FINMIND = {
    "TaiwanStockFinancialStatements": _rows([
        ("2025-03-31", "Revenue", 1000, "營業收入合計"),
        ("2025-03-31", "CostOfGoodsSold", 800, "營業成本合計"),
        ("2025-03-31", "EPS", 0.5, "基本每股盈餘（元）"),
        ("2025-06-30", "Revenue", 1200, "營業收入合計"),
        ("2025-06-30", "GrossProfit", 300, "營業毛利（毛損）"),
        ("2025-06-30", "SomeOddCode", 77, "營業利益（損失）"),  # matched via origin_name
        ("2025-06-30", "Revenue_per", 100, "營業收入合計"),  # common-size row, ignored
    ]),
    "TaiwanStockBalanceSheet": _rows([
        ("2025-03-31", "TotalAssets", 5000, "資產總額"),
        ("2025-06-30", "TotalAssets", 5200, "資產總額"),
        ("2025-06-30", "Liabilities", 2000, "負債總額"),
    ]),
    "TaiwanStockCashFlowsStatement": _rows([  # cumulative year-to-date
        ("2025-03-31", "CashFlowsFromOperatingActivities", 150, "營業活動之淨現金流入（流出）"),
        ("2025-03-31", "PropertyAndPlantAndEquipment", -40, "取得不動產、廠房及設備"),
        ("2025-06-30", "CashFlowsFromOperatingActivities", 400, "營業活動之淨現金流入（流出）"),
        ("2025-06-30", "PropertyAndPlantAndEquipment", -100, "取得不動產、廠房及設備"),
    ]),
    "TaiwanStockMonthRevenue": [
        {"date": "2025-02-01", "revenue": 330, "revenue_year": 2025, "revenue_month": 1},
        {"date": "2025-03-01", "revenue": 340, "revenue_year": 2025, "revenue_month": 2},
    ],
    "TaiwanStockPrice": [{"date": "2025-07-01", "close": 61.5}, {"date": "2025-06-30", "close": 60.0}],
    "TaiwanStockInfo": [{"stock_id": "1785", "stock_name": "光洋科"}],
}


def test_statements_to_quarters_maps_and_decumulates():
    q = statements_to_quarters({
        "income": FINMIND["TaiwanStockFinancialStatements"],
        "balance": FINMIND["TaiwanStockBalanceSheet"],
        "cashflow": FINMIND["TaiwanStockCashFlowsStatement"],
    })
    assert q["2025Q2"]["revenue"] == 1200  # _per row ignored
    assert q["2025Q2"]["operating_income"] == 77
    assert q["2025Q1"]["operating_cf"] == 150 and q["2025Q2"]["operating_cf"] == 250
    assert q["2025Q1"]["capex"] == 40 and q["2025Q2"]["capex"] == 60  # outflow -> positive spend
    assert q["2025Q2"]["total_liabilities"] == 2000


def test_fetch_company_with_fake_api():
    calls = []

    def fake(dataset, params):
        calls.append((dataset, params))
        return FINMIND[dataset]

    d = fetch_company("1785", "2025-01-01", fake, delay=0)
    assert d.name == "光洋科"
    assert d.quarters["2025Q1"]["gross_profit"] == 200  # derived from revenue - cogs
    assert d.quarters["2025Q2"]["cogs"] == 900
    assert d.monthly_revenue == {"2025-01": 330.0, "2025-02": 340.0}
    assert d.last_close == ("2025-07-01", 61.5)
    assert {c[0] for c in calls} >= {"TaiwanStockFinancialStatements", "TaiwanStockMonthRevenue"}


def test_example_config_targets_taiwan_semiconductor_materials():
    cfg = load_fin_config(ROOT / "config" / "fin_targets.yaml")
    assert cfg.target.ticker == "1785" and cfg.target.name == "光洋科"
    assert len(cfg.peers) >= 3 and cfg.industry_notes
    assert cfg.thresholds["debt_ratio_max"] == 0.60
    assert cfg.finmind_ytd["cashflow"] is True


def test_demo_report_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.chdir(ROOT)
    assert main(["analyze", "-c", str(DEMO_CONFIG), "-o", str(tmp_path)]) == 0
    md = next(tmp_path.glob("DEMO-A-*.md")).read_text(encoding="utf-8")
    for section in ("## 重點訊號", "## 關鍵指標速覽", "## 杜邦分析", "## 月營收", "## 綜合評分", "## 評價", "## 同業比較", "虛構"):
        assert section in md
    data = json.loads(next(tmp_path.glob("DEMO-A-*.json")).read_text(encoding="utf-8"))
    assert data["target"]["ticker"] == "DEMO-A" and len(data["peers"]) == 2
    assert next(tmp_path.glob("*-quarterly.csv")).read_text(encoding="utf-8-sig").startswith("period,")


def test_cli_fetch_writes_data(tmp_path, monkeypatch):
    monkeypatch.setattr("fin_analyzer.cli.http_fetch", lambda ds, params: FINMIND[ds])
    monkeypatch.setattr("fin_analyzer.sources.time.sleep", lambda s: None)
    cfg = ROOT / "config" / "fin_targets.yaml"
    assert main(["fetch", "-c", str(cfg), "--tickers", "1785", "--data-dir", str(tmp_path)]) == 0
    assert load_company("1785", tmp_path).quarters["2025Q2"]["revenue"] == 1200


def test_peer_ranking_direction():
    a = analyze(make_company(), load_fin_config(DEMO_CONFIG))
    worse = make_company()
    worse.ticker = "W"
    for row in worse.quarters.values():
        row["inventory"] = 900.0
    b = analyze(worse, load_fin_config(DEMO_CONFIG))
    ranks = peer_ranking([a, b], ["dio", "gross_margin"])
    assert ranks["T"]["dio"] == 1 and ranks["W"]["dio"] == 2  # fewer inventory days is better


class FakeMessages:
    def __init__(self):
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=Commentary(summary="摘要", strengths=["s"], risks=["r"], peer_view="p", watch_items=["w"]),
        )


def test_commentary_with_fake_client():
    cfg = load_fin_config(DEMO_CONFIG)
    a = analyze(make_company(), cfg)
    messages = FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    payload = llm_payload(a, [], cfg.industry_notes, cfg.amount_unit)
    c = Commentator(client=client).comment(payload, "億元")
    assert c.summary == "摘要"
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert "<financials>" in call["messages"][0]["content"]
    md = render_markdown(a, [], cfg, c, NOW)
    assert "## 分析摘要(Claude)" in md and "**同業相對位置**" in md

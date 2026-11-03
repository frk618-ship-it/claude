"""Tests for the lead radar (商機雷達): classification, hotspot statistics, lead scoring, reports."""

import json
import math
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from conftest import ROOT
from lead_radar.config import load_config
from lead_radar.hotspot import Cell, analyse, build_grid, getis_ord, signal_heat, trend_of
from lead_radar.leads import build_leads
from lead_radar.report import build_report, render_csv, render_markdown, write_reports
from lead_radar.signals import Signal, classify, extract_orgs
from lead_radar.sources import build_jobs, collect, load_csv

CONFIG = ROOT / "config" / "lead_radar.yaml"
DEMO = ROOT / "examples" / "lead_radar_demo.csv"
ASOF = date(2026, 11, 2)


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


def sig(cfg, title, days_ago=1, **kw):
    s = classify(cfg, title=title, published=(ASOF - timedelta(days=days_ago)).isoformat(), **kw)
    assert s is not None, title
    return s


def test_config_loads(cfg):
    assert [p.key for p in cfg.products] == ["target", "titanium", "nickel"]
    for p in cfg.products:
        assert all(i in cfg.industries for i in p.industries)
    assert cfg.intents["tender"].weight > cfg.intents["policy"].weight
    assert all(a.region in cfg.regions for a in cfg.accounts)


def test_classify_product_industry_intent_region(cfg):
    s = sig(cfg, "漢翔擴建台中廠 航太鈦合金零件產能倍增")
    assert s.products == ["titanium"] and s.industries["titanium"] == "aerospace"
    assert s.intents[0] == "expansion" and s.region == "TW"
    assert s.orgs[0] == "漢翔航空" and s.accounts == ["漢翔航空"]   # watch-list alias → canonical name
    t = sig(cfg, "Arizona fab issues RFQ for sputtering target supply", edition="en")
    assert t.products == ["target"] and t.industries["target"] == "semiconductor"
    assert t.intents[0] == "tender" and t.region == "US"
    n = sig(cfg, "燃氣輪機訂單滿載 Inconel 718 高溫合金供不應求")
    assert n.products == ["nickel"] and n.industries["nickel"] == "power" and "shortage" in n.intents


def test_classify_rejects_unrelated_and_false_titanium(cfg):
    assert classify(cfg, title="台積電法說會 營收創新高") is None
    assert classify(cfg, title="鈦媒體:AI 新創融資熱") is None             # media outlet name, not the metal
    assert classify(cfg, title="二氧化鈦塗料漲價") is None                   # pigment
    chatter = classify(cfg, title="鈦合金概念股漲停 航太族群走強 漢翔大漲")
    assert chatter.noise and chatter.orgs == ["漢翔航空"]                    # stock chatter: down-weighted, no ticker leads
    assert classify(cfg, title="Titanium Court game trailer: aerospace fantasy") is None          # drop term
    assert classify(cfg, title="Titanium Castings Market to Reach USD 7.19 Billion by 2033 aerospace") is None
    assert classify(cfg, title="Seiko's new titanium watch goes beyond looks") is None          # no industry, no account
    assert classify(cfg, title="某某公司 純鈦 手動匯入", origin="manual") is not None             # manual rows are kept


def test_region_falls_back_to_edition(cfg):
    assert sig(cfg, "航空機向けチタン合金の需要拡大", edition="ja").region == "JP"
    assert sig(cfg, "titanium demand rises for aircraft", edition="en").region == "OTHER"


def test_extract_orgs():
    orgs = extract_orgs("宏翼航太擴建新廠,Skyward Aerostructures Ltd 同步擴產;台灣鈦材需求增")
    assert "宏翼航太" in orgs and "Skyward Aerostructures Ltd" in orgs
    assert not any(o.startswith("台灣") for o in orgs)


def test_excluded_orgs_are_not_leads(cfg):
    s = sig(cfg, "鑫科材料擴產鈦靶 半導體濺鍍靶材產能提升")
    assert not any("鑫科" in o for o in s.orgs)


def test_heat_decays_and_respects_window(cfg):
    s = sig(cfg, "宏翼航太公開招標 鈦合金板材採購", days_ago=0)
    h0 = signal_heat(s, cfg, ASOF)
    assert h0 >= cfg.intents["tender"].weight
    assert signal_heat(s, cfg, ASOF + timedelta(days=cfg.settings.half_life_days)) == pytest.approx(h0 / 2)
    assert signal_heat(s, cfg, ASOF + timedelta(days=cfg.settings.days + 1)) == 0
    assert signal_heat(s, cfg, ASOF - timedelta(days=1)) == 0                   # not yet published


def test_getis_ord_matches_formula():
    inds, regs = ["a", "b", "c"], ["x", "y", "z", "w"]
    heat = {("a", "x"): 9.0, ("a", "y"): 3.0, ("b", "x"): 2.0, ("c", "w"): 1.0}
    grid = {("p", i, r): Cell("p", i, r, heat=heat.get((i, r), 0.0)) for i in inds for r in regs}
    alpha = 0.5
    getis_ord(grid, inds, regs, "p", alpha)
    xs = [c.heat for c in grid.values()]
    n = len(xs)
    mean = sum(xs) / n
    s = math.sqrt(sum(x * x for x in xs) / n - mean ** 2)
    for (p, i, r), c in grid.items():
        w = {(i2, r2): (1.0 if (i2, r2) == (i, r) else alpha if (i2 == i or r2 == r) else 0.0) for i2 in inds for r2 in regs}
        sw, sw2 = sum(w.values()), sum(v * v for v in w.values())
        local = sum(w[k] * grid[("p",) + k].heat for k in w)
        z = (local - mean * sw) / (s * math.sqrt((n * sw2 - sw * sw) / (n - 1)))
        assert c.z == pytest.approx(z)
    assert grid[("p", "a", "x")].z == max(c.z for c in grid.values())


def test_trend_labels():
    assert trend_of("hot", 5, [], []) == "new"
    assert trend_of("hot", 5, ["neutral"] * 4, [0, 0, 1, 1]) == "new"
    assert trend_of("hot", 5, ["hot"] * 5, [5] * 5) == "persistent"
    assert trend_of("hot", 9, ["hot", "neutral", "neutral", "warm"], [4, 1, 1, 3]) == "intensifying"
    assert trend_of("neutral", 1, ["hot", "hot", "neutral", "neutral"], [5, 5, 2, 1]) == "diminishing"
    assert trend_of("warm", 4, ["neutral", "neutral"], [1, 1]) == "rising"


def test_demo_report_finds_hotspots_and_leads(cfg, tmp_path):
    signals = load_csv(DEMO, cfg)
    assert len(signals) == 45 and all(s.origin == "manual" and s.id.startswith("m-") for s in signals)
    rep = build_report(signals, cfg, ASOF, coverage_start=date(2026, 8, 1))
    hot = {(c["product"], c["industry"], c["region"]): c for c in rep["hotspots"]}
    assert hot[("titanium", "aerospace", "TW")]["level"] == "hot"
    assert hot[("target", "semiconductor", "US")]["level"] == "hot"
    assert hot[("target", "display", "CN")]["trend"] == "diminishing"         # busy in Aug–Sep, quiet now
    assert hot[("titanium", "hydrogen", "EU")]["trend"] == "new"
    leads = {l["org"]: l for l in rep["leads"]}
    assert leads["Mesa Semiconductor Corp"]["grade"] == "A" and leads["Mesa Semiconductor Corp"]["product"] == "target"
    assert leads["宏翼航太"]["grade"] == "A" and leads["宏翼航太"]["hotspot"]["level"] == "hot"
    assert "東嶺面板" not in leads                                              # its signals are outside the window
    assert rep["leads"] == sorted(rep["leads"], key=lambda l: -l["score"])
    assert rep["stats"]["grades"]["A"] >= 3

    paths = write_reports(rep, tmp_path)
    assert [p.name for p in paths] == ["2026-11-02.json", "2026-11-02.md", "2026-11-02-leads.csv"]
    assert json.loads(paths[0].read_text(encoding="utf-8"))["asof"] == "2026-11-02"
    md = render_markdown(rep)
    assert "## 需求熱點" in md and "Mesa Semiconductor Corp" in md
    csv_text = render_csv(rep)
    assert csv_text.startswith("﻿等級,分數,公司")


def test_history_skips_slices_before_coverage(cfg):
    signals = load_csv(DEMO, cfg)
    full = analyse(signals, cfg, ASOF, coverage_start=date(2026, 8, 1))
    first_run = analyse(signals, cfg, ASOF, coverage_start=ASOF - timedelta(days=cfg.settings.days))
    assert len(full["slices"]) > 1 and first_run["slices"] == [ASOF.isoformat()]
    trends = {c["trend"] for c in first_run["cells"] if c["level"] == "hot"}
    assert trends <= {"new"}                                                      # no history yet: every hot spot is new


def test_account_fit_raises_score(cfg):
    a = sig(cfg, "漢翔 航太鈦合金板材 招標")
    b = sig(cfg, "某某航太 航太鈦合金板材 招標")
    b.orgs = ["某某航太"]
    grid = analyse([a, b], cfg, ASOF)["cells"]
    leads = {l["org"]: l for l in build_leads([a, b], grid, cfg, ASOF)}
    assert leads["漢翔航空"]["account"] and leads["漢翔航空"]["score"] > leads["某某航太"]["score"]


def test_build_jobs_and_collect_offline(cfg):
    jobs = build_jobs(cfg)
    labels = {j.label for j in jobs}
    assert "純鈦 / 鈦合金 × 航太" in labels and "鎳基合金 × 招標採購" in labels and "客戶:漢翔航空" in labels
    rss = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Aero maker expands titanium alloy plant - Daily</title><link>https://ex.com/1</link>
        <pubDate>Sun, 01 Nov 2026 08:00:00 GMT</pubDate><description>aerospace</description></item>
      <item><title>Unrelated headline - Daily</title><link>https://ex.com/2</link><pubDate>Sun, 01 Nov 2026 08:00:00 GMT</pubDate></item>
    </channel></rss>"""
    small = load_config(CONFIG)
    small.products, small.accounts = small.products[1:2], []
    small.products[0].industries = ["aerospace"]
    small.products[0].editions = ["en"]
    got = collect(small, fetch=lambda url: rss, sleep=lambda s: None)
    assert len(got) == 2 and all(s.products == ["titanium"] for s in got)        # same item from both searches
    assert got[0].industries["titanium"] == "aerospace" and got[0].source == "Daily"


def test_claude_enrichment_applies_structured_facts(cfg):
    from lead_radar.enrich import Enricher, SignalFacts, SignalFactsBatch

    s = sig(cfg, "Skyward Aerostructures Ltd wins titanium order from Pacific Airframe Inc")
    facts = SignalFacts(id=s.id, buyers=["Pacific Airframe Inc"], suppliers=["Skyward Aerostructures Ltd"], products=["titanium"],
                        industry="aerospace", intents=["contract", "bogus"], region="US", urgency=4,
                        need="機身結構件需要鈦合金板材", title_zh="Skyward 取得 Pacific Airframe 鈦合金訂單")

    class FakeBeta:
        def __init__(self):
            self.messages = SimpleNamespace(parse=self.parse)

        def parse(self, **kw):
            assert "<signals>" in kw["messages"][0]["content"] and kw["output_format"] is SignalFactsBatch
            return SimpleNamespace(stop_reason="end_turn", parsed_output=SignalFactsBatch(items=[facts]))

    enricher = Enricher(cfg, client=SimpleNamespace(beta=FakeBeta()))
    assert enricher.enrich([s]) == 1
    assert s.orgs == ["Pacific Airframe Inc"] and s.suppliers == ["Skyward Aerostructures Ltd"]
    assert s.intents == ["contract"] and s.region == "US" and s.urgency == 4 and s.enriched
    assert enricher.enrich([s]) == 0                                              # already enriched


def test_store_keeps_enrichment(cfg, tmp_path):
    from lead_radar.store import SignalStore

    st = SignalStore(tmp_path / "s.sqlite3")
    s = sig(cfg, "宏翼航太公開招標 鈦合金板材採購")
    s.enriched, s.need = True, "需要板材"
    assert st.upsert([s], ASOF) == 1
    plain = sig(cfg, "宏翼航太公開招標 鈦合金板材採購")
    assert st.upsert([plain], ASOF) == 0
    assert st.all()[0].need == "需要板材"
    assert st.mark_coverage(ASOF, 30) == ASOF - timedelta(days=30)
    assert st.mark_coverage(ASOF + timedelta(days=7), 30) == ASOF - timedelta(days=30)   # earliest coverage kept
    st.close()


def test_page_embeds_report_and_nav(cfg, tmp_path):
    from lead_radar.page import render_page, write_site

    rep = build_report(load_csv(DEMO, cfg), cfg, ASOF, coverage_start=date(2026, 8, 1))
    html = render_page(rep)
    assert "/*__DATA__*/null" not in html and '"asof": "2026-11-02"' in html
    page = write_site(rep, tmp_path / "leads").read_text(encoding="utf-8")
    assert 'id="fabnav"' in page and re.search(r'href="\.\./leads/"[^>]*aria-current=page', page)
    assert "const DATA = null" in render_page(None)


def test_cli_demo(tmp_path, monkeypatch):
    from lead_radar.cli import main

    monkeypatch.chdir(ROOT)
    assert main(["demo", "-o", str(tmp_path)]) == 0
    assert (tmp_path / "index.html").exists() and (tmp_path / "2026-11-02-leads.csv").exists()


def test_reprints_of_one_story_count_once(cfg):
    from lead_radar.hotspot import ECHO, story_factors

    a = sig(cfg, "Norsk Titanium wins Rolls-Royce order for titanium aero engine part", days_ago=3)
    b = sig(cfg, "Rolls-Royce places order with Norsk Titanium for 3D printed titanium aircraft parts", days_ago=2)
    c = sig(cfg, "Norsk Titanium Wins Rolls-Royce Order for RPD Titanium aerospace Component - News", days_ago=1)
    other = sig(cfg, "漢翔 航太鈦合金板材 公開招標", days_ago=1)
    f = story_factors([a, b, c, other], cfg)
    assert sorted(f[x.id] for x in (a, b, c)) == [ECHO, ECHO, 1.0] and f[other.id] == 1.0
    cell = next(x for x in analyse([a, b, c], cfg, ASOF)["cells"] if x["count"])
    assert cell["count"] == 1                                                    # one story, not three signals
    leads = {l["org"]: l for l in build_leads([a, b, c], analyse([a, b, c], cfg, ASOF)["cells"], cfg, ASOF)}
    assert leads["Rolls-Royce"]["signals"] == 1


def test_catch_all_cells_are_never_hotspots(cfg):
    sigs = [sig(cfg, f"Titanium aircraft part order {i}", days_ago=i % 5) for i in range(12)]
    for s in sigs:
        assert s.region == "OTHER"
    cells = analyse(sigs, cfg, ASOF)["cells"]
    assert all(c["level"] in ("neutral", "cold") for c in cells if c["region"] == "OTHER" or c["industry"] == "other")


def test_reclassify_applies_current_rules(cfg):
    from lead_radar.signals import reclassify

    stored = Signal(id="x1", title="Titanium Court game launches on PS5", published="2026-11-01", products=["titanium"],
                    industries={"titanium": "other"}, origin="news")
    assert reclassify(stored, cfg) is None
    kept = sig(cfg, "漢翔 航太鈦合金板材 公開招標")
    kept.id = "keep-id"
    assert reclassify(kept, cfg).id == "keep-id"
    manual = Signal(id="m-1", title="Titanium Court", origin="manual")
    assert reclassify(manual, cfg) is manual


# ── segment analysis (台灣半導體設備廠) ──────────────────────────────────

SEEDS = ROOT / "data" / "lead_radar" / "seeds" / "tw_semi_equipment.csv"
PROD_SEEDS = ROOT / "data" / "lead_radar" / "seeds" / "tw_semi_production.csv"


def test_segment_config(cfg):
    seg = cfg.segments["tw_semi_equipment"]
    members = [a for a in cfg.accounts if a.segment == "tw_semi_equipment"]
    assert len(members) >= 20 and all(a.subsegment in seg.subsegments for a in members)
    assert all(set(a.fit) <= {"target", "titanium", "nickel"} and all(0 <= v <= 3 for v in a.fit.values()) for a in members)
    assert next(a for a in members if a.name == "京鼎精密").note.endswith("真空與製程零件可用鈦材")   # comma inside a quoted note survives
    assert cfg.intents["qualification"].action.endswith("爭取第二供應商資格")


def test_config_rejects_comma_split_fields(tmp_path):
    bad = CONFIG.read_text(encoding="utf-8").replace('action: "立即索取標案 / RFQ 規格,確認投標資格、交期與截止日"', "action: 立即索取標案,確認投標資格")
    p = tmp_path / "bad.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="must be quoted"):
        load_config(p)


def test_equipment_news_infers_products_from_fit(cfg):
    s = sig(cfg, "京鼎董事會通過泰國二期投資 擴建廠區")
    assert s.orgs[0] == "京鼎精密" and set(s.products) == {"titanium", "nickel"} and s.relevance == 0.6
    assert s.industries == {"titanium": "semi_equipment", "nickel": "semi_equipment"}
    assert classify(cfg, title="某設備廠擴建新廠") is None                           # no product, no fitted account
    assert classify(cfg, title="鈦昇玻璃基板雷射設備 小量產") is None                  # 「鈦昇」 is a company name, not titanium


def test_segment_jobs(cfg):
    jobs = build_jobs(cfg, "tw_semi_equipment")
    labels = {j.label for j in jobs}
    assert "客戶:京鼎精密" in labels and all(j.edition == "zh-TW" for j in jobs)
    q = next(j.query for j in jobs if j.label == "客戶:京鼎精密")
    assert "Foxsemicon" in q and "擴產" in q
    assert not any(j.label == "客戶:京鼎精密" for j in build_jobs(cfg))           # general run leaves segment accounts to the segment run


def test_seed_csv_keeps_named_company(cfg):
    rows = load_csv(SEEDS, cfg)
    assert len(rows) == 11 and all(r.origin == "manual" and r.link.startswith("https://") for r in rows)
    jd = next(r for r in rows if "泰國二期" in r.title)
    assert jd.orgs == ["京鼎精密"] and jd.intents[0] == "expansion"
    ld = next(r for r in rows if "亞利桑那" in r.title)
    assert ld.orgs == ["家登精密"]                                                   # no guessed "零件精密"


def test_segment_report_ranks_every_account(cfg):
    from lead_radar.segment import build_segment_report, render_accounts_csv, render_segment_markdown

    rows = load_csv(SEEDS, cfg)
    noise = sig(cfg, "Boeing titanium aircraft order", days_ago=2)                    # outside the segment
    rep = build_segment_report(rows + [noise], cfg, "tw_semi_equipment", date(2026, 11, 3))
    members = [a.name for a in cfg.accounts if a.segment == "tw_semi_equipment"]
    accs = {a["org"]: a for a in rep["accounts"]}
    assert set(accs) == set(members) and rep["segment"]["days"] == 120
    assert noise.id not in {s["id"] for s in rep["signals"]}
    # fit alone: 0.4 × fit; news adds 0.6 × signal score
    quiet = accs["公準精密"]
    assert quiet["signals"] == 0 and quiet["opportunity"] == pytest.approx(40.0) and quiet["tier"] == "持續觀察"
    assert accs["京鼎精密"]["signals"] >= 1 and accs["京鼎精密"]["opportunity"] > quiet["opportunity"]
    assert accs["京鼎精密"]["best_product"] == "nickel" and accs["天虹科技"]["best_product"] == "target"
    assert [a["opportunity"] for a in rep["accounts"]] == sorted((a["opportunity"] for a in rep["accounts"]), reverse=True)
    cells = {(c["subsegment"], c["product"]): c for c in rep["segment_cells"]}
    assert cells[("parts", "titanium")]["count"] >= 2
    assert all(c["level"] in ("neutral", "cold") for c in rep["segment_cells"] if c["subsegment"] == "other")
    md = render_segment_markdown(rep)
    assert "## 次產業 × 產品線熱點" in md and "京鼎精密" in md
    assert render_accounts_csv(rep).startswith("﻿順位,公司,代號")


def test_cli_segment_analyze(tmp_path, monkeypatch):
    from lead_radar.cli import main

    monkeypatch.chdir(ROOT)
    db = tmp_path / "s.sqlite3"
    assert main(["--db", str(db), "import", "--no-llm", str(SEEDS), str(PROD_SEEDS)]) == 0
    out, site = tmp_path / "seg", tmp_path / "site"
    assert main(["--db", str(db), "analyze", "--asof", "2026-11-03", "-o", str(out), "--site", str(site)]) == 0   # every segment
    for key in ("tw_semi_equipment", "tw_semi_production"):
        assert (out / key / "2026-11-03-accounts.csv").exists() and "客群分析" in (out / key / "2026-11-03.md").read_text(encoding="utf-8")
    main_page = (site / "index.html").read_text(encoding="utf-8")            # the primary segment is the radar's front page
    assert '"segment": {"key": "tw_semi_equipment"' in main_page and 'href="../news/index.html"' in main_page
    other = (site / "tw_semi_production" / "index.html").read_text(encoding="utf-8")
    assert '"segment": {"key": "tw_semi_production"' in other and 'href="../../news/index.html"' in other
    rebuilt = tmp_path / "rebuilt"
    assert main(["site", "-i", str(out), "-o", str(rebuilt)]) == 0
    assert (rebuilt / "index.html").exists() and (rebuilt / "tw_semi_production" / "index.html").exists()
    assert main(["analyze", "--segment", "nope"]) == 2


def test_inferred_products_need_a_demand_intent(cfg):
    assert classify(cfg, title="弘塑展望佳 早盤逆勢走揚") is None                        # stock chatter, no material word
    assert classify(cfg, title="帆宣(6196) 個股概覽") is None
    s = sig(cfg, "日月光半導體向弘塑取得機器設備,計約10億元")
    assert s.orgs[0] == "弘塑科技" and s.intents[0] == "contract"
    noisy = sig(cfg, "目標價喊1170元!帆宣在手訂單看到3年後")
    assert noisy.noise and noisy.orgs == ["帆宣系統"]


def test_reprint_echo_is_capped(cfg):
    from lead_radar.hotspot import ECHO_CAP, story_factors

    reprints = [sig(cfg, f"家登9月營收創高 拿下韓系大廠先進封裝載具訂單 版本{i}", days_ago=1 + i % 3) for i in range(12)]
    f = story_factors(reprints, cfg)
    assert sorted(f.values())[-1] == 1.0 and sum(f.values()) == pytest.approx(1.0 + ECHO_CAP)


def test_segment_scores_do_not_saturate(cfg):
    from lead_radar.segment import segment_config

    seg_cfg = segment_config(cfg, "tw_semi_equipment")
    stories = [sig(seg_cfg, t, days_ago=d) for t, d in [("京鼎泰國二期擴產 新廠動工", 5), ("京鼎接獲設備模組訂單", 30), ("京鼎缺料 交期吃緊", 60)]]
    cells = analyse(stories, seg_cfg, ASOF)["cells"]
    lead = next(l for l in build_leads(stories, cells, seg_cfg, ASOF) if l["org"] == "京鼎精密")
    # three stories: clearly scored, not maxed out (each story counts once, not once per inferred product)
    assert 25 < lead["score"] < 95



def test_primary_segment_and_production_segment(cfg):
    assert cfg.settings.primary_segment == "tw_semi_equipment"
    assert cfg.segment_order == ["tw_semi_equipment", "tw_semi_production"]
    prod = cfg.segments["tw_semi_production"]
    assert set(prod.subsegments) == {"foundry", "memory", "thinning", "packaging", "testing"}
    members = {a.name: a for a in cfg.accounts if a.segment == "tw_semi_production"}
    assert {"台積電", "南亞科", "昇陽半導體", "日月光投控", "力成科技", "京元電子"} <= set(members)
    assert all(a.subsegment in prod.subsegments for a in members.values())
    assert sum(1 for a in cfg.accounts if a.name == "台積電") == 1                  # moved into the segment, not duplicated
    s = sig(cfg, "南亞科 5A 新廠擴產 資本支出上修")
    assert s.orgs[0] == "南亞科" and s.products == ["target"] and s.industries["target"] == "semiconductor"


def test_production_segment_report(cfg):
    from lead_radar.segment import build_segment_report

    rep = build_segment_report(load_csv(PROD_SEEDS, cfg), cfg, "tw_semi_production", date(2026, 11, 3))
    accs = {a["org"]: a for a in rep["accounts"]}
    assert accs["南亞科"]["signals"] == 1 and accs["南亞科"]["best_product"] == "target"
    assert accs["日月光投控"]["signals"] == 1 and accs["力成科技"]["signals"] == 1        # one report names three packagers
    assert accs["欣銓科技"]["opportunity"] == pytest.approx(0.4 * 100 / 3, abs=0.1)        # fit 1, no news
    assert [p["key"] for p in rep["segment_pages"]] == ["tw_semi_equipment", "tw_semi_production"]
    assert rep["segment_pages"][0]["primary"] and not rep["segment_pages"][1]["primary"]


def test_headline_subject_gets_the_signal(cfg):
    from lead_radar.leads import MENTIONED, SNIPPET_ONLY, SUBJECT, org_weights

    s = sig(cfg, "漢唐(2404)、帆宣(6196)卡位台積電赴美大單 在手訂單看到2028", snippet="京鼎也受惠")
    w = org_weights(s, cfg)
    assert w["漢唐集成"] == SUBJECT and w["帆宣系統"] == MENTIONED and w["台積電"] == MENTIONED
    assert w.get("京鼎精密", SNIPPET_ONLY) == SNIPPET_ONLY


def test_news_volume_has_diminishing_returns(cfg):
    from lead_radar.leads import DECAY

    one = [sig(cfg, "南亞科 5A 新廠擴產", days_ago=1)]
    many = one + [sig(cfg, f"南亞科第{i}座新廠擴建 產能擴充計畫{i}號", days_ago=1 + i * 11) for i in range(1, 10)]
    score = lambda sigs: next(l for l in build_leads(sigs, analyse(sigs, cfg, ASOF)["cells"], cfg, ASOF) if l["org"] == "南亞科")["score"]
    s1, s10 = score(one), score(many)
    assert s10 > s1 and s10 < 100 * (1 - math.exp(-sum(DECAY ** k for k in range(10)) * 4 / 6)) + 1   # bounded by the geometric sum
    assert classify(cfg, title="台積電明年全面漲價 晶圓代工喊漲3-6%") is None or "shortage" not in classify(cfg, title="台積電明年全面漲價 晶圓代工喊漲3-6%").intents


def test_steady_news_is_not_chained_into_one_story(cfg):
    from lead_radar.hotspot import STORY_DAYS, story_factors

    # one expansion report every 8 days for 4 months: each pair is "same company, same intent,
    # within STORY_DAYS", but chaining them would make one 120-day story
    stream = [sig(cfg, f"華邦電擴產計畫第{i}階段 新廠設備進駐", days_ago=i * 8) for i in range(15)]
    f = story_factors(stream, cfg)
    full = sum(1 for v in f.values() if v == 1.0)
    assert full >= 15 * 8 // (STORY_DAYS + 8)                                       # a new story at least every STORY_DAYS
    days = sorted(s.day for s in stream if f[s.id] == 1.0)
    assert all((b - a).days > 0 for a, b in zip(days, days[1:]))


def test_header_links_reach_every_segment_page(cfg, tmp_path):
    from lead_radar.page import render_page
    from lead_radar.segment import build_segment_report

    assert cfg.settings.links == [{"name": "業務報告", "page": "report"}]           # a site page: site password only, no Claude login
    rep = build_segment_report(load_csv(PROD_SEEDS, cfg), cfg, "tw_semi_production", date(2026, 11, 3))
    assert rep["links"] == cfg.settings.links and '"links": [{"name": "業務報告", "page": "report"}]' in render_page(rep)
    text = CONFIG.read_text(encoding="utf-8")
    for bad in ('page: "../x"', 'url: "javascript:alert(1)"'):
        p = tmp_path / "c.yaml"
        p.write_text(text.replace("page: report", bad), encoding="utf-8")
        assert load_config(p).settings.links == []                                    # only slugs and http(s) urls


def test_signal_with_two_products_counts_once(cfg):
    # 京鼎 (fit titanium 2, nickel 3) gets both products inferred; the same news for a
    # one-product account must score the same, not half
    two = sig(cfg, "京鼎董事會通過泰國二期投資 擴建新廠", days_ago=3)
    one = sig(cfg, "京鼎董事會通過泰國二期投資 擴建新廠", days_ago=3)
    assert set(two.products) == {"titanium", "nickel"}
    one.products, one.industries = ["nickel"], {"nickel": two.industries["nickel"]}
    score = lambda s: next(l for l in build_leads([s], analyse([s], cfg, ASOF)["cells"], cfg, ASOF) if l["org"] == "京鼎精密")
    l2, l1 = score(two), score(one)
    assert l2["score"] == pytest.approx(l1["score"], abs=0.5)
    assert l2["product"] == "titanium" or l2["product"] == "nickel"


def test_pitch_shows_its_spec_source(cfg):
    from lead_radar.page import render_page
    from lead_radar.segment import build_segment_report, render_accounts_csv

    assert all(p.pitch_source.startswith("一般產業常見規格") for p in cfg.products)
    rep = build_segment_report(load_csv(SEEDS, cfg), cfg, "tw_semi_equipment", date(2026, 11, 3))
    assert all(a["pitch_source"] for a in rep["accounts"]) and all(l["pitch_source"] for l in rep["leads"])
    assert render_accounts_csv(rep).splitlines()[0].endswith("切入重點,規格依據")
    assert "規格依據:" in render_markdown(rep) or not rep["leads"]
    assert '"pitch_source": "一般產業常見規格' in render_page(rep)
    assert render_csv(rep).splitlines()[0].split(",")[-2] == "規格依據"


def test_segment_page_uses_one_overall_score(cfg):
    from lead_radar.segment import build_segment_report, render_accounts_csv

    rows = load_csv(SEEDS, cfg) + [sig(cfg, "台積電擴產 帆宣受惠 廠務訂單", days_ago=2), sig(cfg, "Apple titanium iPhone supply deal", days_ago=2)]
    rep = build_segment_report(rows, cfg, "tw_semi_equipment", date(2026, 11, 3))
    members = {a.name for a in cfg.accounts if a.segment == "tw_semi_equipment"}
    accounts = {a["org"]: a for a in rep["accounts"]}
    # the lead list is the segment's accounts, scored by the overall score with its parts
    assert {l["org"] for l in rep["leads"]} == members
    for l in rep["leads"]:
        a = accounts[l["org"]]
        assert l["score"] == a["opportunity"] and l["grade"] == a["tier"]
        assert l["fit_part"] + l["signal_part"] == pytest.approx(l["score"], abs=0.2)
    # companies outside the list only keep their signal strength
    others = {o["org"] for o in rep["other_mentions"]}
    assert others and not others & members and "台積電" in others
    assert render_csv(rep).splitlines()[0].startswith("﻿分級,綜合分數,公司")
    assert render_accounts_csv(rep).splitlines()[0].startswith("﻿順位,公司,代號,次產業,綜合分數,產品適配分,訊號分,分級")


def test_segment_lead_hotspot_is_the_segment_cell(cfg):
    from lead_radar.segment import build_segment_report

    rep = build_segment_report(load_csv(SEEDS, cfg), cfg, "tw_semi_equipment", date(2026, 11, 3))
    cells = {(c["subsegment"], c["product"]): c for c in rep["segment_cells"]}
    accs = {a["org"]: a for a in rep["accounts"]}
    for l in rep["leads"]:
        a = accs[l["org"]]
        assert l["hotspot"]["level"] == cells[(a["subsegment"], a["best_product"])]["level"]


def test_segment_page_has_one_company_list(cfg):
    from lead_radar.page import render_page
    from lead_radar.segment import build_segment_report

    html = render_page(build_segment_report(load_csv(SEEDS, cfg), cfg, "tw_semi_equipment", date(2026, 11, 3)))
    # the card list is hidden on segment pages; filters and other companies move to the table
    assert '$("#leads-sec").hidden = true' in html and 'appendChild(document.querySelector("#leads-sec .filters"))' in html
    assert "if (SEGMODE) { renderAccRows(list); renderOthers(); return; }" in html



def test_business_report_page(tmp_path, monkeypatch):
    from lead_radar.business_report import markdown_to_html, write_report_page
    from lead_radar.cli import main

    h = markdown_to_html("# 標題\n\n段落 **粗體** [連結](https://example.com)\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n- [ ] 待辦\n- 項目\n\n1. 一\n2. 二")
    assert "<h1>標題</h1>" in h and "<strong>粗體</strong>" in h and 'href="https://example.com"' in h
    assert "<table>" in h and "<td>2</td>" in h and 'class="task"' in h and "<ol><li>一</li>" in h
    assert "<script" not in markdown_to_html("<script>alert(1)</script> [x](javascript:alert(1))")   # escaped; non-http links stay text
    assert write_report_page(tmp_path / "missing.md", tmp_path / "out") is None
    monkeypatch.chdir(ROOT)
    db = tmp_path / "s.sqlite3"
    assert main(["--db", str(db), "import", "--no-llm", str(SEEDS)]) == 0
    out = tmp_path / "seg"
    assert main(["--db", str(db), "analyze", "--asof", "2026-11-03", "-o", str(out)]) == 0
    site = tmp_path / "site"
    assert main(["site", "-i", str(out), "-o", str(site)]) == 0
    page = (site / "report" / "index.html").read_text(encoding="utf-8")
    assert "半導體客群商機開發業務報告" in page and 'href="../index.html"' in page and 'href="../../news/index.html"' in page

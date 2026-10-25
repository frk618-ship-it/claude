import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from conftest import CONFIG, FIXTURES

from target_news_agent.cli import main
from target_news_agent.config import load_config
from target_news_agent.models import NewsItem
from target_news_agent.processing import classify, has_any, process
from target_news_agent.report import render_markdown
from target_news_agent.sources import build_query, collect, google_news_url, parse_feed
from target_news_agent.store import SeenStore
from target_news_agent.summarizer import Digest, ItemDigest, ItemDigestBatch, Overview, Summarizer

NOW = datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)
SAMPLE = (FIXTURES / "google_news_sample.xml").read_bytes()


@pytest.fixture(autouse=True)
def no_metal_quotes(monkeypatch):
    """CLI runs must not reach the quote sources; tests that need quotes patch fetch_quotes again."""
    monkeypatch.setattr("target_news_agent.cli.fetch_quotes", lambda specs, fetch: [])


@pytest.fixture
def config():
    return load_config(CONFIG)


def test_config_loads_watchlist(config):
    roles = {e.role for e in config.entities}
    assert {"producer", "distributor", "topic"} <= roles
    assert all(e.aliases for e in config.entities)


def test_build_query_adds_context_for_broad_companies(config):
    solstice = next(e for e in config.entities if e.name.startswith("Solstice"))
    q = build_query(solstice, config.settings.query_context_terms)
    assert q.startswith('("Solstice Advanced Materials" OR') and '"Honeywell"' in q and "(靶材 OR 濺鍍" in q
    jx = next(e for e in config.entities if e.name.startswith("JX"))
    assert "靶材" not in build_query(jx, config.settings.query_context_terms)
    url = google_news_url(q, config.settings.editions["zh-TW"], 3)
    assert "when%3A3d" in url and "ceid=TW%3Azh-Hant" in url


def test_parse_feed_splits_publisher_and_drops_repeated_snippet():
    items = parse_feed(SAMPLE, edition="zh-TW", query_entity="x")
    first = items[0]
    assert first.title == "江豐電子擬定增擴產超高純金屬濺射靶材"
    assert first.source == "證券時報"
    assert first.snippet == ""
    assert first.published == datetime(2026, 10, 12, 2, tzinfo=timezone.utc)


def test_word_boundary_matching():
    assert has_any("New ITO target line", ["ITO"])
    assert not has_any("Toyota unveils new hybrid", ["ITO"])


def test_process_filters_matches_dedupes_and_classifies(config):
    items = process(parse_feed(SAMPLE, query_entity="江豐電子 (KFMI)"), config, now=NOW)
    titles = [i.title for i in items]

    # Honeywell aerospace news lacks target context -> dropped; target news kept.
    assert not any("aerospace" in t for t in titles)
    hon = next(i for i in items if "Spokane" in i.title)
    assert hon.entities == ["Solstice (原 Honeywell 電子材料)", "台積電 (TSMC)"]  # supplier and customer
    sols = next(i for i in items if "Solstice Advanced Materials" in i.title)
    assert sols.entities == ["Solstice (原 Honeywell 電子材料)"]
    # The bare word "Solstice" (an unrelated battery product) must not match.
    assert not any("Factorial" in t for t in titles)
    assert "擴產與投資" in hon.categories and "客戶與供應鏈" in hon.categories

    # Traditional / simplified duplicates collapse into one item.
    assert sum("超高純" in t or "超高纯" in t for t in titles) == 1

    # Outside the 3-day window, and unrelated news, are dropped.
    assert not any("Old news" in t or "Toyota" in t for t in titles)

    sam = next(i for i in items if "光洋科" in i.title)
    assert "營收與財報" in sam.categories
    assert all(i.id for i in items)


def test_collect_uses_fetcher_and_survives_partial_failures(config):
    config.entities = config.entities[:2]
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("boom")
        return SAMPLE

    raw = collect(config, fetch, sleep=lambda s: None)
    assert len(calls) == sum(len(e.editions) for e in config.entities)
    assert raw

    with pytest.raises(RuntimeError):
        collect(config, lambda url: (_ for _ in ()).throw(OSError("down")), sleep=lambda s: None)


def test_seen_store(tmp_path, config):
    items = process(parse_feed(SAMPLE), config, now=NOW)
    store = SeenStore(tmp_path / "seen.db")
    assert len(store.filter_new(items)) == len(items)
    store.mark(items[:1])
    assert len(store.filter_new(items)) == len(items) - 1


def test_render_markdown(config):
    items = process(parse_feed(SAMPLE), config, now=NOW)
    md = render_markdown(items, config, Digest(overview="總覽", key_trends=["趨勢A"]), NOW)
    assert "## 本期總覽" in md and "趨勢A" in md
    assert "### 擴產與投資" in md
    assert "| Solstice (原 Honeywell 電子材料) | 生產商 |" in md
    assert "## 熱門關鍵字" in md
    assert "本期沒有新的相關新聞" in render_markdown([], config, Digest(), NOW)


class FakeMessages:
    def __init__(self):
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        schema = kwargs["output_format"]
        if schema is ItemDigestBatch:
            ids = [line.split('"')[3] for line in kwargs["messages"][0]["content"].splitlines() if '"id"' in line]
            parsed = ItemDigestBatch(
                items=[ItemDigest(id=i, summary="摘要", importance=9, category="併購與合作") for i in ids]
            )
        else:
            parsed = Overview(overview="本期總覽", key_trends=["t"], watch_points=["w"])
        return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)


def test_summarizer_with_fake_client(config):
    items = process(parse_feed(SAMPLE), config, now=NOW)
    messages = FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    digest = Summarizer(list(config.settings.categories), client=client).digest(items)

    assert digest.overview == "本期總覽"
    assert all(i.summary == "摘要" and i.importance == 5 for i in items)
    assert all(i.categories[0] == "併購與合作" for i in items)
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert "<news>" in call["messages"][0]["content"]


def test_cli_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("target_news_agent.sources.http_fetch", lambda url: SAMPLE)
    monkeypatch.setattr("target_news_agent.sources.time.sleep", lambda s: None)
    monkeypatch.setattr("target_news_agent.cli.collect", lambda cfg: collect(cfg, lambda url: SAMPLE, sleep=lambda s: None))
    monkeypatch.setattr("target_news_agent.cli.process", lambda raw, cfg: process(raw, cfg, now=NOW))
    monkeypatch.setattr("target_news_agent.cli.collect_papers", lambda rs: sample_papers(rs))

    args = ["run", "-c", str(CONFIG), "-o", str(tmp_path / "out"), "--db", str(tmp_path / "seen.db"), "--only", "Solstice", "光洋科"]
    assert main(args) == 0
    reports = list((tmp_path / "out").glob("*.md"))
    assert len(reports) == 1
    assert "Spokane" in reports[0].read_text(encoding="utf-8")

    # Second run the same day: nothing new, and the morning's items stay in the report.
    assert main(args) == 0
    assert "Spokane" in reports[0].read_text(encoding="utf-8")

    # Without Claude the report still gets a rule-based overview, marked "auto".
    report = reports[0].with_suffix(".json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["digest"]["source"] == "auto" and "本期蒐集" in data["digest"]["overview"]
    assert "自動整理" in reports[0].read_text(encoding="utf-8")

    # A scheduled Claude task fills the summaries: pending -> write JSON -> apply-digest.
    pending = tmp_path / "pending.json"
    assert main(["pending", str(report), "-c", str(CONFIG), "-o", str(pending)]) == 0
    todo = json.loads(pending.read_text(encoding="utf-8"))
    assert todo["need_overview"] and todo["news"] and "濺鍍靶材" in todo["instructions"]["news"]
    first = todo["news"][0]["id"]
    written = tmp_path / "written.json"
    written.write_text(json.dumps({
        "news": [{"id": first, "summary": "光洋科擴產。", "importance": 9, "category": "擴產與投資", "title_zh": "標題"},
                 {"id": "unknown", "summary": "ignored"}],
        "overview": "本期重點。", "key_trends": ["趨勢一"], "watch_points": ["追蹤一"],
    }, ensure_ascii=False), encoding="utf-8")
    assert main(["apply-digest", str(written), str(report), "-c", str(CONFIG)]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    item = next(i for i in data["items"] if i["id"] == first)
    assert item["summary"] == "光洋科擴產。" and item["importance"] == 5 and item["categories"][0] == "擴產與投資"
    assert item["title_zh"] == "標題"
    assert data["digest"] == {"overview": "本期重點。", "key_trends": ["趨勢一"], "watch_points": ["追蹤一"], "source": "claude"}
    # A later run without Claude keeps the Claude overview instead of the fallback.
    assert main(args) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["digest"]["source"] == "claude"


def test_korean_category_keywords(config):
    item = NewsItem(title="JX금속, 스퍼터링 타겟 증설…3분기 매출 사상 최대", link="", source="", published=NOW)
    assert {"擴產與投資", "營收與財報"} <= set(classify(item, config.settings.categories))


class FakeNotion:
    """Records Notion API calls; the query endpoint returns rows already in the database."""

    def __init__(self, existing_ids=()):
        self.calls = []
        self.existing = list(existing_ids)

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path.endswith("/query"):
            # Two pages of results to exercise pagination.
            if "start_cursor" not in body:
                rows = self.existing[:1]
                return {"results": [self._row(i) for i in rows], "has_more": len(self.existing) > 1, "next_cursor": "c2"}
            return {"results": [self._row(i) for i in self.existing[1:]], "has_more": False}
        if path == "/databases":
            return {"id": "db-123"}
        return {"id": "page"}

    @staticmethod
    def _row(item_id):
        return {"properties": {"ID": {"rich_text": [{"plain_text": item_id}]}}}


def test_notion_sync_creates_rows_and_skips_existing(config):
    from target_news_agent import notion

    items = process(parse_feed(SAMPLE), config, now=NOW)
    fake = FakeNotion(existing_ids=[items[0].id, items[1].id])
    created = notion.sync(items, fake, "db-1", sleep=lambda s: None)

    assert created == len(items) - 2
    pages = [b for m, p, b in fake.calls if p == "/pages"]
    assert len(pages) == created
    props = pages[0]["properties"]
    assert props["標題"]["title"][0]["text"]["content"] == items[2].title
    assert props["日期"]["date"]["start"] == items[2].published.date().isoformat()
    assert {o["name"] for o in props["公司"]["multi_select"]} == set(items[2].entities)
    assert props["連結"]["url"] == items[2].link
    assert pages[0]["parent"] == {"database_id": "db-1"}

    # Running again with everything present creates nothing.
    again = FakeNotion(existing_ids=[i.id for i in items])
    assert notion.sync(items, again, "db-1", sleep=lambda s: None) == 0


def test_notion_setup_and_sync_cli(tmp_path, monkeypatch, config):
    from target_news_agent import notion

    fake = FakeNotion()
    monkeypatch.setattr(notion, "http_transport", lambda token: fake)
    monkeypatch.setenv("NOTION_TOKEN", "secret")

    assert main(["notion-setup", "--parent", "page-1"]) == 0
    method, path, body = fake.calls[0]
    assert path == "/databases" and body["parent"]["page_id"] == "page-1"
    assert set(body["properties"]) == set(notion.PROPERTIES)

    items = process(parse_feed(SAMPLE), config, now=NOW)
    report = tmp_path / "2026-10-13.json"
    report.write_text(json.dumps({"items": [i.to_dict() for i in items]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-123")
    assert main(["notion-sync", "--reports-dir", str(tmp_path)]) == 0
    assert sum(1 for m, p, b in fake.calls if p == "/pages") == len(items)


def test_build_site_from_reports(tmp_path, config):
    from target_news_agent.site import build_site, item_kind

    items = process(parse_feed(SAMPLE), config, now=NOW)
    reports = tmp_path / "reports"
    reports.mkdir()
    for date, subset in (("2026-10-12", items[:1]), ("2026-10-13", items)):
        data = {"generated": f"{date}T08:30:00+08:00", "days": 3, "digest": {}, "items": [i.to_dict() for i in subset]}
        (reports / f"{date}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (reports / f"{date}.md").write_text("# report", encoding="utf-8")
    (reports / "notes.json").write_text("{}", encoding="utf-8")  # not a dated report; ignored

    out = tmp_path / "site" / "news"
    build_site(reports, out, config)

    index = (out / "index.html").read_text(encoding="utf-8")
    # The homepage combines the latest days; duplicates across days appear once.
    assert "最近 2 天 · 2026-10-12 ~ 2026-10-13" in index and index != (out / "2026-10-13.html").read_text(encoding="utf-8")
    assert index.split('id="list"')[1].count('<article class="card') == len(items)
    assert 'href="index.html" aria-current=page' in index and 'href="2026-10-13.html"' in index
    assert (out / "2026-10-12.html").exists()
    assert not (out / "data").exists()  # raw MD/JSON are no longer published
    assert index.count('<article class="card') >= len(items)
    assert "Spokane" in index and 'href="2026-10-12.html"' in index
    assert "Solstice" in index and "生產商 · 台灣" in index
    assert ".md\"" not in index and ".json\"" not in index and "Markdown" not in index
    assert "<script>alert" not in index
    # Share-preview card: description + Open Graph with absolute image URL when site_url is set.
    assert '<meta name="description"' in index and '<meta property="og:title" content="靶材產業新聞摘要' in index
    assert (out / "og.png").exists()
    config.settings.site_url = "https://example.org/news"
    build_site(reports, out, config)
    page = (out / "2026-10-12.html").read_text(encoding="utf-8")
    assert '<meta property="og:image" content="https://example.org/news/og.png">' in page
    assert '<meta property="og:url" content="https://example.org/news/2026-10-12.html">' in page

    assert item_kind({"title": "1785 光洋科 - 心得 - 股市爆料同學會", "source": "CMoney"}) == "forum"
    assert item_kind({"title": "欧莱新材(688530):募集说明书", "source": "中财网"}) == "filing"
    assert item_kind({"title": "江丰电子10月12日主力资金净卖出", "source": "证券之星"}) == "market"
    assert item_kind({"title": "Copper Sputtering Target Market report", "source": "x", "entities": ["產業綜合:Sputtering Target"]}) == "research"
    assert item_kind({"title": "有研新材前三季度营收84.62亿元", "source": "观点网"}) == "news"


def test_site_escapes_untrusted_titles(tmp_path, config):
    from target_news_agent.site import build_site

    reports = tmp_path / "reports"
    reports.mkdir()
    evil = {"title": '<script>alert(1)</script>"', "link": 'https://x.test/"onmouseover="a', "source": "<b>", "published": "2026-10-13T00:00:00+00:00",
            "entities": ["光洋科 (Solar Applied Materials)"], "categories": [], "score": 1, "id": "x"}
    (reports / "2026-10-13.json").write_text(json.dumps({"items": [evil]}), encoding="utf-8")
    build_site(reports, tmp_path / "out", config)
    page = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page
    assert 'href="https://x.test/&quot;onmouseover=&quot;a"' in page


def test_site_drops_non_http_links(tmp_path, config):
    from target_news_agent.site import build_site

    reports = tmp_path / "reports"
    reports.mkdir()
    item = {"title": "bad link", "link": "javascript:alert(1)", "source": "x", "published": None, "entities": ["光洋科"], "id": "y"}
    (reports / "2026-10-13.json").write_text(json.dumps({"items": [item]}), encoding="utf-8")
    build_site(reports, tmp_path / "out", config)
    assert "javascript:" not in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")


def test_site_root_redirect(tmp_path, monkeypatch):
    out = tmp_path / "site"
    assert main(["site", "-c", str(CONFIG), "-i", str(tmp_path), "-o", str(out / "news"), "--root-redirect", str(out)]) == 0
    root = (out / "index.html").read_text(encoding="utf-8")
    assert "url=news/" in root and (out / "news" / "index.html").exists()



OPENALEX = (FIXTURES / "openalex_sample.json").read_bytes()
ARXIV = (FIXTURES / "arxiv_sample.xml").read_bytes()
JOURNAL = (FIXTURES / "journal_rss_sample.xml").read_bytes()


def fake_paper_fetch(url):
    if "api.openalex.org" in url:
        return OPENALEX
    if "arxiv.org" in url:
        return ARXIV
    return JOURNAL


def sample_papers(rs):
    from target_news_agent.research import collect_papers

    rs.queries, rs.arxiv_queries = rs.queries[:1], rs.arxiv_queries[:1]
    rs.feeds = rs.feeds[:1]
    return collect_papers(rs, fake_paper_fetch, now=NOW, sleep=lambda s: None)


def test_parse_openalex():
    from target_news_agent.research import parse_openalex

    papers = parse_openalex(OPENALEX)
    p = papers[0]
    assert p.doi == "10.1016/j.ceramint.2026.10.001"
    assert p.link == "https://doi.org/10.1016/j.ceramint.2026.10.001"
    assert p.venue == "Ceramics International" and p.authors[:2] == ["A. Chen", "B. Lin"]
    assert p.abstract == "ITO targets reached 99.6% relative density"
    assert papers[3].link == "https://example.org/w4"


def test_collect_papers_filters_dedupes_and_classifies(config):
    papers = sample_papers(config.research)
    by_title = {p.title: p for p in papers}

    assert not any("cancer" in t for t in by_title)                 # generic "target" dropped
    assert not any("cross rolling" in t for t in by_title)          # older than 30 days
    assert not any("AZO films" in t for t in by_title)              # journal paper not about targets

    ito = [p for p in papers if p.title.startswith("Densification")]
    assert len(ito) == 1                                             # OpenAlex + arXiv copy merged
    assert ito[0].doi and "99.6%" in ito[0].abstract and "SPS" in ito[0].abstract
    assert ito[0].topics[0] == "粉末冶金與燒結" and "陶瓷與氧化物靶" in ito[0].topics

    assert by_title["Diffusion bonding of copper targets to CuCr backing plates for magnetron sputtering"].topics[0] == "綁定與背板"
    assert "旋轉靶" in by_title["Erosion profile and target utilization of rotatable sputtering targets"].topics
    recycling = by_title["Recycling of spent molybdenum sputtering targets by hydrogen reduction"]
    assert recycling.topics == ["回收與再利用"] and recycling.source == "Thin Solid Films"
    assert all(p.id.startswith("p-") for p in papers)
    assert [p.published for p in papers] == sorted([p.published for p in papers], reverse=True)


def test_paper_source_failure_does_not_stop_run(config):
    from target_news_agent.research import collect_papers

    rs = config.research
    rs.queries, rs.arxiv_queries, rs.feeds = rs.queries[:1], rs.arxiv_queries[:1], rs.feeds[:1]

    def flaky(url):
        if "openalex" in url:
            raise OSError("down")
        return fake_paper_fetch(url)

    stats = []
    papers = collect_papers(rs, flaky, now=NOW, sleep=lambda s: None, stats=stats)
    assert any(p.source == "arXiv" for p in papers)
    # per-source stats for check-papers: the failed source is recorded, the others with counts
    by = {s["source"]: s for s in stats}
    assert by["OpenAlex"]["fetched"] is None and "down" in by["OpenAlex"]["error"]
    assert by["arXiv"]["fetched"] > 0 and 0 < by["arXiv"]["relevant"] <= by["arXiv"]["fetched"]


def test_target_compound_words_are_not_targets():
    from target_news_agent.research import Paper, is_relevant

    mk = lambda t: Paper(title=t, link="https://x", published=None, venue="J. Materials Research and Technology", authors=[], abstract="", doi="", source="t")
    assert not is_relevant(mk("Quantitative structure-property mapping and target-driven design of Voronoi porous tantalum"))
    assert is_relevant(mk("Microstructure and grain refinement of tantalum targets by cross rolling"))


def test_relevance_tiers():
    from target_news_agent.research import Paper, relevance

    mk = lambda t, a="", v="Vacuum": Paper(title=t, link="https://x", published=None, venue=v, authors=[], abstract=a, doi="", source="t")
    # core: making / characterising the target itself
    assert relevance(mk("Densification of ITO sputtering targets by spark plasma sintering")) == "core"
    # related: the target is a variable or the subject of a sputtering study
    assert relevance(mk("Target poisoning and hysteresis in reactive magnetron sputtering of AlN")) == "related"
    assert relevance(mk("Effect of target density on sputtered ITO films")) == "related"
    assert relevance(mk("Optical properties of AlN films", "Films were grown by HiPIMS; the influence of the target temperature on deposition rate was studied.")) == "related"
    assert relevance(mk("Arcing behaviour in pulsed DC magnetron sputtering of aluminium oxide")) == "related"
    # not relevant: films merely deposited from a target, or targets outside sputtering
    assert relevance(mk("Optical properties of ZnO films", "ZnO films were deposited by RF magnetron sputtering from a ceramic ZnO target.")) == ""
    assert relevance(mk("Hot target for isotope production", "proton beam irradiation", v="Nucl. Instrum. Methods")) == ""
    assert relevance(mk("Target-driven design of porous tantalum scaffolds", "sputtered coating")) == ""
    # "target temperature" of an application, not of a sputtering target
    assert relevance(mk("Development of interfaces in oxide matrix composites", "after heat treatment at 1400 C, the target temperature for oxide CMCs; coatings by RF-magnetron sputtering")) == ""


def test_journal_feeds_are_unique():
    # one entry per journal; a duplicated feed would only waste requests
    from target_news_agent.config import load_config

    feeds = load_config("config/watchlist.yaml").research.feeds
    assert len(feeds) >= 15 and len({f["url"] for f in feeds}) == len(feeds) and len({f["name"] for f in feeds}) == len(feeds)


def test_papers_in_report_and_site(tmp_path, config):
    from target_news_agent.report import render_markdown, write_reports
    from target_news_agent.site import build_site

    papers = sample_papers(config.research)
    papers[0].summary, papers[0].importance = "研究摘要", 4
    md = render_markdown([], config, Digest(), NOW, papers)
    assert "## 技術文獻:靶材製造技術" in md and "### 粉末冶金與燒結" in md and "本期沒有新的相關新聞" in md
    assert "研究摘要" in md and "A. Chen, B. Lin, C. Wang 等" in md

    reports = tmp_path / "reports"
    write_reports([], config, Digest(), reports, NOW, papers)
    data = json.loads((reports / "2026-10-13.json").read_text(encoding="utf-8"))
    assert len(data["papers"]) == len(papers) and data["research_days"] == 30

    build_site(reports, tmp_path / "site", config)
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "技術文獻:靶材製造技術" in page and "回收與再利用" in page and "研究摘要" in page


def test_digest_papers_with_fake_client(config):
    papers = sample_papers(config.research)
    messages = FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    Summarizer(list(config.settings.categories), client=client).digest_papers(papers, ["併購與合作"])
    assert all(p.summary == "摘要" and p.importance == 5 for p in papers)
    assert "<papers>" in messages.calls[0]["messages"][0]["content"]


def test_cli_run_marks_papers_seen(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("target_news_agent.cli.collect", lambda cfg: [])
    monkeypatch.setattr("target_news_agent.cli.collect_papers", lambda rs: sample_papers(rs))
    args = ["run", "-c", str(CONFIG), "-o", str(tmp_path / "out"), "--db", str(tmp_path / "seen.db")]
    assert main(args) == 0
    first = json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))
    assert first["papers"]
    assert main(args) == 0
    second = json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))
    assert [p["id"] for p in second["papers"]] == [p["id"] for p in first["papers"]]  # kept, not duplicated
    assert main(args + ["--no-papers"]) == 0
    assert len(json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))["papers"]) == len(first["papers"])
    out2 = tmp_path / "out2"
    assert main(["run", "-c", str(CONFIG), "-o", str(out2), "--db", str(tmp_path / "seen.db"), "--no-papers"]) == 0
    assert "papers" not in json.loads(next(out2.glob("*.json")).read_text(encoding="utf-8"))


def test_research_matching_handles_word_endings():
    from target_news_agent.research import Paper, classify_topics, is_relevant

    p = Paper("Sintered W-Ti alloys for sputtering targets from nano powders", "https://x", None)
    assert is_relevant(p)
    assert classify_topics(p, {"粉末": ["powder"], "合金": ["alloy"]}) == ["粉末", "合金"]
    assert not is_relevant(Paper("Targeted delivery of ITOX inhibitors", "https://x", None))
    assert classify_topics(Paper("Toyota sputtering target", "https://x", None), {"ITO": ["ITO"]}) == ["其他靶材研究"]


def test_notion_sync_papers(config):
    from target_news_agent import notion

    papers = sample_papers(config.research)
    papers[0].summary, papers[0].importance = "研究摘要", 4
    fake = FakeNotion(existing_ids=[papers[1].id])
    created = notion.sync(papers, fake, "db-p", properties=notion.paper_properties, sleep=lambda s: None)
    assert created == len(papers) - 1

    rows = [b["properties"] for m, p, b in fake.calls if p == "/pages"]
    first = rows[0]
    assert first["標題"]["title"][0]["text"]["content"] == papers[0].title
    assert first["摘要"]["rich_text"][0]["text"]["content"] == "研究摘要"
    assert first["重要度"]["number"] == 4
    assert {o["name"] for o in first["主題"]["multi_select"]} == set(papers[0].topics)
    assert set(first) <= set(notion.PAPER_PROPERTIES)
    ito = next(r for r in rows if r["標題"]["title"][0]["text"]["content"].startswith("Densification"))
    assert ito["DOI"]["rich_text"][0]["text"]["content"] == "10.1016/j.ceramint.2026.10.001"
    assert ito["期刊"]["select"]["name"] == "Ceramics International"
    assert "A. Chen" in ito["作者"]["rich_text"][0]["text"]["content"]

    # Undated records: the duplicate check must scan the whole database (no date filter).
    from target_news_agent.research import Paper
    undated = Paper("Bonding of ITO sputtering targets", "https://x", None, id="p-undated")
    scan = FakeNotion()
    notion.sync([undated], scan, "db-p", properties=notion.paper_properties, sleep=lambda s: None)
    query = next(b for m, p, b in scan.calls if p.endswith("/query"))
    assert "filter" not in query


def test_notion_setup_both_and_sync_papers_cli(tmp_path, monkeypatch, config):
    from target_news_agent import notion
    from target_news_agent.report import write_reports

    fake = FakeNotion()
    monkeypatch.setattr(notion, "http_transport", lambda token: fake)
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    assert main(["notion-setup", "--parent", "page-1"]) == 0
    created = [b for m, p, b in fake.calls if p == "/databases"]
    assert [set(b["properties"]) for b in created] == [set(notion.PROPERTIES), set(notion.PAPER_PROPERTIES), set(notion.COMPANY_PROPERTIES)]
    assert created[1]["title"][0]["text"]["content"] == "靶材技術文獻"

    papers = sample_papers(config.research)
    write_reports([], config, Digest(), tmp_path, NOW, papers)
    fake.calls.clear()
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    monkeypatch.setenv("NOTION_PAPERS_DATABASE_ID", "db-p")
    assert main(["notion-sync", "--reports-dir", str(tmp_path)]) == 0
    pages = [b for m, p, b in fake.calls if p == "/pages"]
    assert len(pages) == len(papers) and all(b["parent"] == {"database_id": "db-p"} for b in pages)

    monkeypatch.delenv("NOTION_PAPERS_DATABASE_ID")
    with pytest.raises(SystemExit):
        main(["notion-sync", "--reports-dir", str(tmp_path)])


def test_papers_only_merges_into_todays_report(tmp_path, monkeypatch, config):
    from datetime import datetime as real_datetime
    from zoneinfo import ZoneInfo

    from target_news_agent.report import write_reports

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("target_news_agent.cli.collect", lambda cfg: pytest.fail("news must not be fetched"))
    monkeypatch.setattr("target_news_agent.cli.collect_papers", lambda rs: sample_papers(rs))

    items = process(parse_feed(SAMPLE), config, now=NOW)
    today = real_datetime.now(ZoneInfo("Asia/Taipei"))
    write_reports(items, config, Digest(overview="原本的總覽"), tmp_path, today)

    args = ["run", "-c", str(CONFIG), "-o", str(tmp_path), "--db", str(tmp_path / "seen.db"), "--papers-only"]
    assert main(args) == 0
    data = json.loads((tmp_path / f"{today:%Y-%m-%d}.json").read_text(encoding="utf-8"))
    assert len(data["items"]) == len(items) and data["digest"]["overview"] == "原本的總覽"
    first = {p["id"] for p in data["papers"]}
    assert first

    # Running again finds no new papers but keeps the ones already in today's report.
    assert main(args) == 0
    data = json.loads((tmp_path / f"{today:%Y-%m-%d}.json").read_text(encoding="utf-8"))
    assert {p["id"] for p in data["papers"]} == first and len(data["items"]) == len(items)


def test_site_library_and_homepage_fallback(tmp_path, config):
    from target_news_agent.report import write_reports
    from target_news_agent.site import build_site

    papers = sample_papers(config.research)
    reports = tmp_path / "reports"
    write_reports([], config, Digest(), reports, NOW.replace(day=9), papers)   # papers collected 4 days ago
    for day in (11, 12, 13):                                                   # nothing new in the last 3 days
        write_reports([], config, Digest(), reports, NOW.replace(day=day), [])
    out = tmp_path / "site"
    build_site(reports, out, config)

    library = (out / "papers.html").read_text(encoding="utf-8")
    assert library.count("<article class=\"card") == len(papers)
    assert "靶材技術文獻庫" in library and 'data-filter="ent" data-value="粉末冶金與燒結"' in library
    assert 'data-filter="kind" data-value="arXiv"' in library

    home = (out / "index.html").read_text(encoding="utf-8")
    assert "本期無新收錄" in home and "Densification" in home and 'href="papers.html"' in home
    assert 'href="../financials/"' in home and "財務儀表板" in home  # dashboard published next to /news/
    old = (out / "2026-10-09.html").read_text(encoding="utf-8")
    assert "本期新收錄 4 篇" in old


@pytest.mark.parametrize(
    "title,abstract,expected",
    [
        # about making / characterising targets -> keep
        ("Effect of sintering temperature on the microstructure of W-Ti targets", "", True),
        ("High-density IZO ceramic targets prepared by pressureless sintering", "", True),
        ("Grain refinement of high purity copper by equal channel angular pressing",
         "The grain size of the Cu targets was reduced to 15 um after ECAP and annealing.", True),
        ("Electron beam melting of tantalum", "Ta targets were fabricated by electron beam melting and cross rolling.", True),
        ("Diffusion bonding of copper targets to CuCr backing plates", "", True),
        ("Recycling of spent molybdenum sputtering targets", "", True),
        ("ITO 靶材燒結緻密化研究", "", True),
        # only *using* a target to grow films -> drop
        ("Optical properties of Ta-doped ZnO thin films prepared by DC sputtering",
         "Films were prepared by sputtering a ZnO:Ta target at 100 W on glass substrates.", False),
        ("Thermoelectric BiCuSeO thin films", "The Bi2O3 target was sputtered at 80 W and the films were annealed.", False),
        ("Novel drug target for lung cancer therapy", "", False),
        ('Decision letter for "Sintering of ITO sputtering targets"', "", False),
        # "target" in nuclear physics / laser plasma (seen in the first live run) -> drop
        ("High-vacuum fabrication and characterization of thin Ba targets for nuclear reaction and spectroscopy", "", False),
        ("Fabrication of 120 Sn target with Indium interlayer", "", False),
        ("Laboratory-scale dual-pulse laser-produced plasma extreme ultraviolet source with continuous target", "", False),
        ("Microstructure, thermal performance, and hydrogen diffusion of magnetron-sputtered Ta-Ti binary films on Cu substrate for AB-BNCT targets", "", False),
        # same title words, but in a sputtering context -> keep
        ("Fabrication of Sn targets with indium interlayer", "Targets for magnetron sputtering were bonded with In.", True),
    ],
)
def test_paper_relevance_requires_target_manufacturing(title, abstract, expected):
    from target_news_agent.research import Paper, is_relevant

    assert is_relevant(Paper(title, "https://x", None, abstract=abstract)) is expected


def test_openalex_skips_peer_review_records():
    from target_news_agent.research import parse_openalex

    data = json.dumps({"results": [
        {"display_name": "Review for ITO targets", "type": "peer-review", "publication_date": "2026-10-01"},
        {"display_name": "Sintering of ITO sputtering targets", "type": "article", "publication_date": "2026-10-01"},
    ]}).encode()
    assert [p.title for p in parse_openalex(data)] == ["Sintering of ITO sputtering targets"]


def test_arxiv_retries_once(config):
    from target_news_agent.research import collect_papers

    rs = config.research
    rs.openalex, rs.feeds, rs.arxiv_queries = False, [], ["sputtering target"]
    calls, sleeps = [], []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("HTTP Error 429: Too Many Requests")
        return ARXIV

    papers = collect_papers(rs, fetch, now=NOW, sleep=sleeps.append)
    assert len(calls) == 2 and 15 in sleeps and papers



def test_arxiv_queries_are_combined_into_one_request(config):
    from urllib.parse import unquote

    from target_news_agent.research import collect_papers

    rs = config.research
    rs.openalex, rs.feeds = False, []
    rs.arxiv_queries = ["sputtering target", "target microstructure"]
    calls = []
    collect_papers(rs, lambda url: calls.append(url) or ARXIV, now=NOW, sleep=lambda s: None)
    assert len(calls) == 1
    assert 'abs:"sputtering target" OR abs:"target microstructure"' in unquote(calls[0])



def test_papers_only_targets_latest_report_across_midnight(tmp_path, monkeypatch, config):
    """A papers-only run just after midnight must update yesterday's report, not create an empty one."""
    from target_news_agent.report import write_reports

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("target_news_agent.cli.collect_papers", lambda rs: sample_papers(rs))
    items = process(parse_feed(SAMPLE), config, now=NOW)
    yesterday = NOW.replace(year=2020)  # any date that is not "today"
    write_reports(items, config, Digest(), tmp_path, yesterday)

    assert main(["run", "-c", str(CONFIG), "-o", str(tmp_path), "--db", str(tmp_path / "seen.db"), "--papers-only"]) == 0
    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert files == [f"{yesterday:%Y-%m-%d}.json"]
    data = json.loads((tmp_path / files[0]).read_text(encoding="utf-8"))
    assert len(data["items"]) == len(items) and data["papers"]


def test_needs_translation_detects_languages():
    from target_news_agent.translate import needs_translation, to_traditional

    assert needs_translation("Materion raises 2026 guidance")
    assert needs_translation("銅建値6万円上げ、最高値を更新 JX金属")        # Japanese (kana)
    assert needs_translation("머티리온 스퍼터링 타겟")                      # Korean
    assert not needs_translation("有研新材前三季度营收84.62亿元")            # Chinese: OpenCC only
    assert not needs_translation("IGZO 靶材出貨")
    assert not needs_translation("ITO")                                     # a lone acronym
    assert to_traditional("半导体靶材业务亮眼") == "半導體靶材業務亮眼"
    assert to_traditional("回复定增审核问询函并更新申请文件") == "回覆定增審核問詢函並更新申請文件"  # wording kept


def test_translate_records_routes_text(config):
    from target_news_agent.models import NewsItem
    from target_news_agent.research import Paper
    from target_news_agent.translate import translate_records

    zh_cn = NewsItem("有研新材前三季度营收84.62亿元", "https://a", "东方财富", None)
    zh_tw = NewsItem("光洋科9月營收年增25%", "https://b", "經濟日報", None)
    en = NewsItem("Materion raises 2026 guidance", "https://c", "Reuters", None)
    ja = NewsItem("銅建値6万円上げ、最高値を更新 JX金属", "https://d", "日経", None)
    done = NewsItem("Already done", "https://e", "x", None, title_zh="已翻譯")
    paper = Paper("Sintering of ITO sputtering targets", "https://f", None, abstract="ITO targets reached 99.6% density.")

    seen = []

    def fake_llm(entries):
        seen.extend(t for _, t in entries)
        return {k: f"譯:{t[:10]}" for k, t in entries}

    n = translate_records([zh_cn, zh_tw, en, ja, done], [paper], fake_llm)
    assert zh_cn.title_zh == "有研新材前三季度營收84.62億元"   # OpenCC, no LLM
    assert zh_tw.title_zh == ""                                # already Traditional
    assert en.title_zh.startswith("譯:") and ja.title_zh.startswith("譯:")
    assert done.title_zh == "已翻譯"                           # not re-translated
    assert paper.title_zh.startswith("譯:") and paper.abstract_zh.startswith("譯:")
    assert n == 4 and set(seen) == {en.title, ja.title, paper.title, paper.abstract}

    # Without Claude, English stays as is but Chinese is still converted.
    en2 = NewsItem("Plansee expands capacity", "https://g", "x", None)
    cn2 = NewsItem("江丰电子扩产", "https://h", "x", None)
    assert translate_records([en2, cn2], None, None) == 0
    assert en2.title_zh == "" and cn2.title_zh == "江豐電子擴產"

    # A failing translator never breaks the run.
    en3 = NewsItem("Tosoh news", "https://i", "x", None)
    assert translate_records([en3], None, lambda e: (_ for _ in ()).throw(RuntimeError("boom"))) == 0


def test_summarizer_translate_with_fake_client(config):
    from target_news_agent.summarizer import Translation, TranslationBatch
    from target_news_agent.translate import llm_translator

    calls = []

    class Msgs:
        def parse(self, **kw):
            calls.append(kw)
            ids = [line.split('"')[3] for line in kw["messages"][0]["content"].splitlines() if '"id"' in line]
            return SimpleNamespace(stop_reason="end_turn", parsed_output=TranslationBatch(items=[Translation(id=i, zh="中文") for i in ids]))

    s = Summarizer([], client=SimpleNamespace(beta=SimpleNamespace(messages=Msgs())))
    out = llm_translator(s)([("t1", "Hello sputtering target"), ("t2", "Another one")])
    assert out == {"t1": "中文", "t2": "中文"}
    assert calls[0]["output_config"] == {"effort": "low"} and "<texts>" in calls[0]["messages"][0]["content"]


def test_site_shows_translated_titles(tmp_path, config):
    from target_news_agent.site import build_site

    reports = tmp_path / "reports"
    reports.mkdir()
    items = [
        {"title": "Materion raises guidance", "title_zh": "Materion 上調財測", "link": "https://x", "source": "Reuters",
         "published": "2026-10-13T00:00:00+00:00", "entities": ["Materion"], "id": "a"},
        {"title": "有研新材营收增长", "link": "https://y", "source": "东方财富",
         "published": "2026-10-13T00:00:00+00:00", "entities": ["有研新材 (GRINM)"], "id": "b"},
    ]
    papers = [{"title": "Sintering of ITO targets", "title_zh": "ITO 靶材燒結", "abstract": "Long English abstract",
               "abstract_zh": "中文摘要", "link": "https://z", "published": "2026-10-01", "topics": ["粉末冶金與燒結"], "id": "p-1"}]
    (reports / "2026-10-13.json").write_text(json.dumps({"items": items, "papers": papers}, ensure_ascii=False), encoding="utf-8")
    build_site(reports, tmp_path / "out", config)
    page = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "Materion 上調財測" in page and 'title="原文:Materion raises guidance"' in page
    assert "有研新材營收增長" in page and "東方財富" in page      # Simplified converted at display time too
    assert "ITO 靶材燒結" in page and "中文摘要" in page and "Long English abstract" not in page


def test_cards_show_title_and_summary():
    from target_news_agent.site import _item_html, _paper_html

    news = {"title": "Materion expands target plant", "title_zh": "Materion 擴建靶材廠", "link": "https://x.test/a",
            "summary": "Materion 宣布擴產。", "entities": ["Materion"], "kind": "news", "published": "2026-10-13T00:00:00+00:00"}
    card = _item_html(news)
    assert card.startswith('<article class="card k-news has-thumb"') and 'data-kind="news"' in card
    assert "Materion 擴建靶材廠" in card and '<p class="sum">Materion 宣布擴產。</p>' in card
    assert 'title="原文:Materion expands target plant"' in card

    # No summary: fall back to the feed snippet, but never repeat the title as the summary.
    assert '<p class="sum">' not in _item_html({**news, "summary": "", "snippet": "Materion expands"})
    assert '<p class="sum">新廠明年投產</p>' in _item_html({**news, "summary": "", "snippet_zh": "新廠明年投產"})

    paper = {"title": "Sintering of ITO targets", "title_zh": "ITO 靶材的燒結", "abstract": "Long English abstract.",
             "abstract_zh": "研究 ITO 靶材燒結。" * 40, "topics": ["粉末冶金與燒結"], "link": "https://x.test/p"}
    pc = _paper_html(paper)
    assert "ITO 靶材的燒結" in pc and "研究 ITO 靶材燒結。" in pc and "…</p>" in pc and "粉末冶金與燒結" in pc


def test_second_run_same_day_keeps_earlier_items(tmp_path, monkeypatch, config):
    from datetime import datetime as real_datetime
    from zoneinfo import ZoneInfo

    from target_news_agent.report import write_reports

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("target_news_agent.cli.collect", lambda cfg: [])
    monkeypatch.setattr("target_news_agent.cli.collect_papers", lambda rs: [])
    items = process(parse_feed(SAMPLE), config, now=NOW)
    today = real_datetime.now(ZoneInfo("Asia/Taipei"))
    write_reports(items, config, Digest(overview="早上的總覽"), tmp_path, today, sample_papers(config.research))

    assert main(["run", "-c", str(CONFIG), "-o", str(tmp_path), "--db", str(tmp_path / "seen.db")]) == 0
    data = json.loads((tmp_path / f"{today:%Y-%m-%d}.json").read_text(encoding="utf-8"))
    assert len(data["items"]) == len(items) and data["digest"]["overview"] == "早上的總覽"
    assert len(data["papers"]) == len(sample_papers(config.research))


def test_resummarize_fills_missing_summaries(tmp_path, monkeypatch, config):
    from target_news_agent.report import write_reports

    items = process(parse_feed(SAMPLE), config, now=NOW)
    papers = sample_papers(config.research)
    write_reports(items, config, Digest(), tmp_path, NOW, papers)

    class FakeSummarizer:
        def __init__(self, categories, model=None, background=""):
            pass

        def digest(self, its):
            for i in its:
                i.summary, i.importance = f"摘要:{i.id}", 3
            return Digest(overview="新的總覽")

        def digest_papers(self, ps, topics):
            for x in ps:
                x.summary = "文獻摘要"

        def translate(self, system, block):
            return {}

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr("target_news_agent.cli.Summarizer", FakeSummarizer)
    assert main(["resummarize", "-c", str(CONFIG), "--reports-dir", str(tmp_path)]) == 0
    data = json.loads((tmp_path / f"{NOW:%Y-%m-%d}.json").read_text(encoding="utf-8"))
    assert all(i["summary"] for i in data["items"]) and all(p["summary"] == "文獻摘要" for p in data["papers"])
    assert data["digest"]["overview"] == "新的總覽" and data["generated"] == NOW.isoformat()

    # Already summarized: nothing to do.
    monkeypatch.setattr("target_news_agent.cli.Summarizer", lambda *a, **k: FakeSummarizer(None))
    assert main(["resummarize", "-c", str(CONFIG), "--reports-dir", str(tmp_path)]) == 0


def test_filtered_cards_are_really_hidden():
    from target_news_agent.site import STYLE

    # Cards use display:grid, which would override the hidden attribute without this rule.
    assert "[hidden]{display:none!important}" in STYLE


def test_equipment_makers_need_sputtering_context(config):
    from target_news_agent.processing import match_entities

    def ents(title):
        return match_entities(NewsItem(title=title, link="", source="", published=NOW), config.entities, config.settings.context_terms)

    amat = "應用材料 (Applied Materials)"
    assert amat in ents("Applied Materials launches new PVD system for advanced logic")
    assert amat not in ents("Applied Materials beats estimates on etch demand")
    # The global context words (thin film, ITO ...) are not enough for an equipment maker.
    assert amat not in ents("Applied Materials thin film CVD revenue grows")
    assert "Evatec" in ents("Evatec opens new application lab")  # sputtering-only company: no context needed

    from target_news_agent.sources import build_query
    e = next(x for x in config.entities if x.name == amat)
    assert "sputtering" in build_query(e, config.settings.query_context_terms) and "靶材" not in build_query(e, config.settings.query_context_terms)


def test_ledger_groups_equipment_and_process(config):
    from collections import Counter

    from target_news_agent.site import _ledger

    html_ = _ledger(config, Counter({"Evatec": 2}))
    assert "濺鍍設備商" in html_ and "製程相關 · PVD 半導體廠" in html_ and "靶材生產商 · 日本" in html_
    assert "Evatec" in html_ and "2 則" in html_ and "台積電" in html_


def test_sputtering_as_slang_does_not_match_equipment_topic(config):
    from target_news_agent.processing import match_entities

    item = NewsItem(title="Sean Payton takes back play-calling to fix sputtering offense", link="", source="",
                    published=NOW, query_entity="設備綜合:Sputtering Equipment")
    assert match_entities(item, config.entities, config.settings.context_terms) == []
    item.title = "New magnetron sputtering system ships to display maker"
    assert match_entities(item, config.entities, config.settings.context_terms) == ["設備綜合:Sputtering Equipment"]


def test_chipmakers_only_match_pvd_or_capex_news(config):
    from target_news_agent.processing import match_entities

    def ents(title):
        return match_entities(NewsItem(title=title, link="", source="", published=NOW), config.entities, config.settings.context_terms)

    assert ents("台積電股價創新高") == []
    assert ents("台積電亞利桑那二廠擴產 2奈米提前量產") == ["台積電 (TSMC)"]
    assert ents("台積電資本支出上看 500 億美元") == ["台積電 (TSMC)"]
    assert ents("TSMC qualifies a new sputtering target supplier") == ["台積電 (TSMC)"]
    assert "英特爾 (Intel)" not in ents("Intelligence agencies boost capex on PVD systems")


def test_chipmaker_search_uses_names_only(config):
    from target_news_agent.sources import build_query

    tsmc = next(e for e in config.entities if e.name == "台積電 (TSMC)")
    assert build_query(tsmc, config.settings.query_context_terms) == '("台積電" OR "台积电" OR "TSMC")'
    assert tsmc.require_context  # the context check still runs locally


class FakeNotionWorkspace:
    """A tiny in-memory Notion: databases under pages, rows, search, schema reads and updates."""

    def __init__(self):
        self.dbs = {}   # id -> {"title", "parent", "properties", "rows": {page_id: properties}}
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/search":
            return {"results": [
                {"id": i, "title": [{"plain_text": d["title"]}], "parent": {"page_id": d["parent"]}}
                for i, d in self.dbs.items() if d["title"] == body["query"]
            ]}
        if path == "/databases":
            i = f"db{len(self.dbs) + 1}"
            self.dbs[i] = {"title": body["title"][0]["text"]["content"], "parent": body["parent"]["page_id"],
                           "properties": dict(body["properties"]), "rows": {}}
            return {"id": i}
        if path.startswith("/databases/") and path.endswith("/query"):
            d = self.dbs[path.split("/")[2]]
            return {"results": [{"id": pid, "properties": self._read(props)} for pid, props in d["rows"].items()], "has_more": False}
        if path.startswith("/databases/"):
            d = self.dbs[path.split("/")[2]]
            if method == "PATCH":
                d["properties"].update(body["properties"])
            return {"properties": d["properties"]}
        if path == "/pages":
            d = self.dbs[body["parent"]["database_id"]]
            unknown = set(body["properties"]) - set(d["properties"])
            assert not unknown, f"unknown columns {unknown}"
            pid = f"p{sum(len(x['rows']) for x in self.dbs.values()) + 1}"
            d["rows"][pid] = body["properties"]
            return {"id": pid}
        if path.startswith("/pages/"):
            pid = path.split("/")[2]
            for d in self.dbs.values():
                if pid in d["rows"]:
                    d["rows"][pid] = body["properties"]
            return {"id": pid}
        raise AssertionError(path)

    @staticmethod
    def _read(props):
        out = {}
        for k, v in props.items():
            if "title" in v or "rich_text" in v:
                key = "title" if "title" in v else "rich_text"
                out[k] = {key: [{"plain_text": t["text"]["content"]} for t in v[key]]}
            else:
                out[k] = v
        return out

    def rows(self, title):
        return next(d["rows"] for d in self.dbs.values() if d["title"] == title)


def test_notion_sync_with_parent_page_only(tmp_path, monkeypatch, config):
    from target_news_agent import notion
    from target_news_agent.report import write_reports

    ws = FakeNotionWorkspace()
    monkeypatch.setattr(notion.time, "sleep", lambda s: None)
    # A news database made by an older version, without the 公司類型 column.
    old_props = {k: v for k, v in notion.PROPERTIES.items() if k != "公司類型"}
    ws.dbs["old"] = {"title": "靶材產業新聞", "parent": "aaaa-bbbb", "properties": old_props, "rows": {}}
    monkeypatch.setattr(notion, "http_transport", lambda token: ws)
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    for k in ("NOTION_DATABASE_ID", "NOTION_PAPERS_DATABASE_ID", "NOTION_COMPANIES_DATABASE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "aaaabbbb")  # same page, written without dashes

    items = process(parse_feed(SAMPLE), config, now=NOW)
    write_reports(items, config, Digest(), tmp_path, NOW, sample_papers(config.research))
    args = ["notion-sync", "--reports-dir", str(tmp_path), "-c", str(CONFIG)]
    assert main(args) == 0

    assert len(ws.dbs) == 3 and "公司類型" in ws.dbs["old"]["properties"]   # reused + migrated, two created
    news = ws.rows("靶材產業新聞")
    assert len(news) == len(items)
    sols = next(r for r in news.values() if "Spokane" in r["標題"]["title"][0]["text"]["content"])
    assert {o["name"] for o in sols["公司類型"]["multi_select"]} == {"生產商", "製程相關(PVD 半導體廠)"}
    assert len(ws.rows("靶材技術文獻")) == len(sample_papers(config.research))

    companies = ws.rows("靶材追蹤公司")
    assert len(companies) == sum(1 for e in config.entities if not e.is_topic)
    tsmc = next(r for r in companies.values() if r["名稱"]["title"][0]["text"]["content"] == "台積電 (TSMC)")
    assert tsmc["類型"]["select"]["name"] == "製程相關(PVD 半導體廠)" and tsmc["本期則數"]["number"] == 1

    # Second run: nothing new to add, unchanged companies are not rewritten.
    ws.calls.clear()
    assert main(args) == 0
    assert not [c for c in ws.calls if c[1] == "/pages" or c[1].startswith("/pages/")]


def test_packaging_houses_need_packaging_or_pvd_context(config):
    from target_news_agent.processing import match_entities

    def ents(title):
        return match_entities(NewsItem(title=title, link="", source="", published=NOW), config.entities, config.settings.context_terms)

    assert ents("日月光擴充CoWoS先進封裝產能") == ["日月光投控 (ASE)"]
    assert ents("日月光股價創高") == []
    assert ents("動力成長 擴產可期") == []           # 「力成」 inside other words must not match
    assert ents("頎邦凸塊產能滿載") == ["頎邦 (Chipbond)"]


def test_metal_quotes_stooq_then_yahoo_fallback():
    from target_news_agent.metals import MetalSpec, fetch_quotes

    stooq = b"Date,Open,High,Low,Close,Volume\n2026-10-13,4100,4150,4090,4120.5,0\n2026-10-14,4120,4200,4110,4185.25,0\n"
    yahoo = json.dumps({"chart": {"result": [{"timestamp": [1792022400, 1792108800],
                        "indicators": {"quote": [{"close": [1650.0, 1633.5]}]}}]}}).encode()

    def fetch(url):
        if "stooq" in url and "xauusd" in url:
            return stooq
        if "stooq" in url:
            raise OSError("blocked")
        return yahoo

    au, pt = fetch_quotes([MetalSpec("黃金", "Au", "xauusd", "GC=F"), MetalSpec("鉑金", "Pt", "xptusd", "PL=F")], fetch)
    assert (au.price, au.prev, au.date, au.source) == (4185.25, 4120.5, "2026-10-14", "Stooq")
    assert round(au.change_pct, 2) == 1.57
    assert (pt.price, pt.source) == (1633.5, "Yahoo Finance") and pt.to_dict()["change_pct"] == -1.0
    assert fetch_quotes([MetalSpec("鈀金", "Pd", "xpdusd")], lambda url: (_ for _ in ()).throw(OSError())) == []


def test_busy_metal_topic_is_capped_and_labelled(config):
    from datetime import timedelta

    from target_news_agent.site import item_kind, short_name

    items = [NewsItem(title=f"金價 第{n}天 創新高", link=f"https://x/{n}", source="", published=NOW - timedelta(hours=n),
                      query_entity="行情綜合:貴金屬") for n in range(12)]
    kept = process(items, config, now=NOW)
    assert len(kept) == 4 and all(i.entities == ["行情綜合:貴金屬"] for i in kept)
    assert max(i.published for i in kept) == NOW  # the newest ones are kept
    assert item_kind({"title": "金價創新高", "entities": ["行情綜合:貴金屬"]}) == "metals"
    assert short_name("行情綜合:Precious Metals") == "貴金屬行情(英文)"


def test_metal_quotes_on_site_and_report(tmp_path, config):
    from target_news_agent.report import write_reports
    from target_news_agent.site import build_site

    metals = [{"name": "黃金", "code": "Au", "price": 4185.25, "prev": 4120.5, "date": "2026-10-14", "unit": "USD/oz",
               "source": "Stooq", "change_pct": 1.57},
              {"name": "鉑金", "code": "Pt", "price": 1633.5, "prev": 1650.0, "date": "2026-10-14", "unit": "USD/oz",
               "source": "Yahoo Finance", "change_pct": -1.0}]
    md, js = write_reports([], config, Digest(), tmp_path / "reports", NOW, None, metals)
    assert "| 黃金 (Au) | 4,185.25 USD/oz | +1.57% | 2026-10-14 |" in md.read_text(encoding="utf-8")
    assert json.loads(js.read_text(encoding="utf-8"))["metals"][1]["code"] == "Pt"
    build_site(tmp_path / "reports", tmp_path / "site", config)
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "貴金屬行情" in page and "4,185.25" in page and 'class="chg up">▲ +1.57%' in page and 'class="chg down">▼ -1.00%' in page


def test_metal_cap_counts_headlines_found_by_two_searches(config):
    from datetime import timedelta

    tw = [NewsItem(title=f"金價 第{n}天 創新高", link=f"https://x/{n}", source="", published=NOW - timedelta(hours=n),
                   query_entity="行情綜合:貴金屬") for n in range(10)]
    cn = [NewsItem(title=f"金价 第{n}天 创新高", link=f"https://y/{n}", source="", published=NOW - timedelta(hours=n),
                   query_entity="行情綜合:贵金属") for n in range(10)]
    kept = process(tw + cn, config, now=NOW)
    assert len(kept) <= 4


def test_target_recycling_topic(config, tmp_path):
    from target_news_agent.processing import match_entities
    from target_news_agent.site import build_site, item_kind

    recycling = [e for e in config.entities if e.role == "recycling"]
    assert {ed for e in recycling for ed in e.editions} == {"zh-TW", "zh-CN", "en", "ja"}
    assert all(e.is_topic and e.role_label == "靶材回收循環" for e in recycling)
    assert build_query(recycling[0], config.settings.context_terms).startswith("(靶材 回收) OR (廢靶 回收)")

    def ents(title, query_entity="回收綜合:Target Recycling"):
        item = NewsItem(title=title, link="", source="", published=NOW, query_entity=query_entity)
        return match_entities(item, config.entities, config.settings.context_terms)

    assert ents("Indium recycling plant reclaims spent ITO sputtering targets") == ["回收綜合:Target Recycling"]
    assert all(e.also_terms for e in recycling)  # recycling AND target words required
    assert ents("Sputtering target market to grow 6% a year") == []  # no recycling context
    # Real false positives from the first run: recycling news that has nothing to do with targets.
    assert ents("ニトリ、埼玉県に「リサイクルセンター（仮称）」開設、2028年度稼働へ", "回收綜合:ターゲットリサイクル") == []
    assert ents("废旧动力电池到高纯稀有金属！2027武汉再生金属与新能源材料回收展会", "回收綜合:靶材回收(简)") == []
    assert ents("使用済みITOターゲットからインジウムをリサイクル", "回收綜合:ターゲットリサイクル") == ["回收綜合:ターゲットリサイクル"]
    assert ents("光洋科擴大廢靶回收與貴金屬精煉產能", "回收綜合:靶材回收") == ["光洋科 (Solar Applied Materials)"]

    cats = config.settings.categories
    assert "回收與循環" in classify(NewsItem(title="光洋科擴大廢靶回收", link="", source="", published=NOW), cats)
    assert "回收與循環" not in classify(NewsItem(title="擴產投資回收期約五年", link="", source="", published=NOW), cats)

    rec = {"title": "Indium recycling from spent ITO targets", "source": "x", "entities": ["回收綜合:Target Recycling"]}
    assert item_kind(rec) == "recycling"
    assert item_kind({**rec, "title": "Sputtering Target Recycling Market CAGR 2026-2032"}) == "research"

    reports = tmp_path / "reports"
    reports.mkdir()
    items = [
        {**rec, "id": "a", "link": "https://example.com/a", "published": "2026-10-13T01:00:00+00:00", "categories": ["回收與循環"], "score": 2},
        {"id": "b", "title": "光洋科擴大廢靶回收", "link": "https://example.com/b", "source": "x", "published": "2026-10-13T02:00:00+00:00",
         "entities": ["光洋科 (Solar Applied Materials)"], "categories": ["回收與循環"], "score": 3},
        {"id": "c", "title": "江豐電子擴產", "link": "https://example.com/c", "source": "x", "published": "2026-10-13T03:00:00+00:00",
         "entities": ["江豐電子 (KFMI)"], "categories": ["擴產與投資"], "score": 1},
    ]
    (reports / "2026-10-13.json").write_text(json.dumps({"generated": "2026-10-13T08:30:00+08:00", "days": 7, "items": items}), encoding="utf-8")
    build_site(reports, tmp_path / "site", config)
    home = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    section = home[home.index('aria-labelledby="recycle"'):home.index("</section>", home.index('aria-labelledby="recycle"'))]
    assert "靶材回收循環" in section and "Indium recycling" in section and "光洋科擴大廢靶回收" in section
    assert "江豐電子擴產" not in section
    assert 'data-value="recycling"' in home and "k-recycling" in home


def test_news_thumbnails_follow_the_story():
    from target_news_agent.site import _item_html
    from target_news_agent.thumbs import pick

    base = {"title": "", "entities": [], "categories": [], "kind": "news"}
    assert pick({**base, "title": "光洋科鉭靶擴產", "categories": ["擴產與投資"]}) == ("factory", "擴產", "Ta")
    assert pick({**base, "title": "有研新材前三季淨利增長", "categories": ["營收與財報"]})[:2] == ("bars", "財報")
    # A fab story without a stronger category shows a wafer; supply-chain news about a fab too.
    assert pick({**base, "title": "TSMC qualifies supplier", "categories": ["客戶與供應鏈"], "roles": ["process"]})[0] == "wafer"
    assert pick({**base, "title": "Evatec ships tool", "roles": ["equipment"]})[0] == "chamber"
    assert pick({**base, "title": "日月光 CoWoS", "roles": ["packaging"]})[0] == "chip"
    assert pick({**base, "kind": "metals", "title": "金價創新高"}) == ("coin", "貴金屬", "Au")
    assert pick({**base, "title": "ITO target price"})[2] == "ITO"
    assert pick(base) == ("target", "靶材", "")

    card = _item_html({**base, "title": "光洋科鉭靶擴產", "categories": ["擴產與投資"], "link": "https://x"})
    assert '<div class="thumb" role="img" aria-label="擴產 · Ta">' in card and '#i-factory' in card and '<span class="sym">Ta</span>' in card


def test_target_recycling_papers_are_kept(config):
    from target_news_agent.research import Paper, classify_topics, is_relevant

    def P(title, abstract=""):
        return Paper(title, "https://x", None, abstract=abstract)

    # Titles about recovering metals from spent / scrap targets.
    assert is_relevant(P("Recovery of indium from spent ITO targets by sulfuric acid leaching"))
    assert is_relevant(P("Leaching behaviour of tantalum scrap targets in HF media"))
    assert is_relevant(P("廢靶材中釕的回收與精煉"))
    # Only the abstract names the spent target.
    assert is_relevant(P("A low-temperature route to In-Sn alloy",
                         "Indium and tin were reclaimed from waste ITO sputtering targets by hydrogen reduction."))
    assert is_relevant(P("Hydrometallurgical route for ruthenium",
                         "We report the recovery of ruthenium from end-of-life magnetron targets."))
    # Using a target to deposit films is still not about the target itself.
    assert not is_relevant(P("Optical properties of ZnO films",
                             "The ceramic disc was used as sputtering target to deposit films on glass."))
    assert classify_topics(P("Recovery of indium from spent ITO targets"), config.research.topics)[-1] == "回收與再利用"


def test_fusion_target_plates_and_metallurgical_recovery_are_not_recycling(config):
    from target_news_agent.research import Paper, classify_topics, is_relevant

    fusion = Paper("Gyrokinetic simulation of a low-recycling scrape-off layer with a lithium-free target plate",
                   "https://x", None, abstract="Edge plasma turbulence in a tokamak divertor ...")
    assert not is_relevant(fusion)
    cu = Paper("Microstructure and texture of cryo-rolled 6N copper for advanced sputtering targets", "https://x", None,
               abstract="Static recovery and recrystallization during annealing were studied.")
    assert is_relevant(cu) and "回收與再利用" not in classify_topics(cu, config.research.topics)


def test_homepage_merges_last_three_days(config):
    from target_news_agent.site import merge_recent

    reports = [
        ("2026-10-19", {"items": [{"id": "c"}, {"id": "b"}], "digest": {"overview": "新"}, "metals": [],
                        "papers": [{"id": "p2"}], "generated": "2026-10-19T08:00"}),
        ("2026-10-18", {"items": [{"id": "b"}, {"id": "a"}], "metals": [{"code": "Au"}], "papers": [{"id": "p1"}]}),
        ("2026-10-17", {"items": [{"id": "z"}]}),
        ("2026-10-16", {"items": [{"id": "old"}]}),
    ]
    m = merge_recent(reports)
    assert [i["id"] for i in m["items"]] == ["c", "b", "a", "z"]       # newest day first, no duplicates, 3 days only
    assert [p["id"] for p in m["papers"]] == ["p2", "p1"]
    assert m["digest"]["overview"] == "新" and m["metals"] == [{"code": "Au"}]  # latest quotes available
    assert m["span"] == ["2026-10-19", "2026-10-18", "2026-10-17"]


def test_forecast_page_and_digest_background(tmp_path, config):
    from target_news_agent.site import build_site
    from target_news_agent.summarizer import news_system_prompt

    assert config.forecasts and "CAGR" in config.market_context
    assert "產業背景" in news_system_prompt(["擴產與投資"], config.market_context)
    assert "產業背景" not in news_system_prompt(["擴產與投資"])

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-10-13.json").write_text(json.dumps({"generated": "2026-10-13T08:30:00+08:00", "items": []}), encoding="utf-8")
    config.forecasts = [{"source": "<b>Inst</b>", "report": "R", "url": "javascript:alert(1)", "context": "x",
                         "figures": [{"label": "2025", "value": "約 2,198 億", "note": "n"}],
                         "points": [{"title": "市場概況", "items": ["<script>bad</script>"]}]}]
    build_site(reports, tmp_path / "out", config)
    page = (tmp_path / "out" / "forecast.html").read_text(encoding="utf-8")
    assert "約 2,198 億" in page and "&lt;script&gt;bad" in page and "<script>bad" not in page
    assert "javascript:" not in page and "&lt;b&gt;Inst" in page
    assert 'href="../news/forecast.html"' in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")  # floating page switcher


def test_quiz_bank_and_page(tmp_path, config):
    from target_news_agent.quiz import build_bank
    from target_news_agent.site import build_site

    assert config.quiz and config.makers
    items = [
        {"id": "a", "title": "光洋科擴產靶材新廠", "entities": ["光洋科 (Solar Applied Materials)"], "importance": 5,
         "summary": "擴產。", "link": "https://example.com/a"},
        {"id": "b", "title": "<script>x</script> 台積電 與 光洋科", "entities": ["台積電 (TSMC)"], "importance": 4},
        {"id": "c", "title": "兩家公司", "entities": ["光洋科 (Solar Applied Materials)", "台積電 (TSMC)"], "importance": 5},
    ]
    bank = build_bank(config, items, seed="s")
    islands = {i["id"]: i for i in bank["islands"]}
    news = islands["news"]["questions"]
    assert [q["answer"] for q in news] == ["光洋科", "台積電"]  # two-company headline skipped
    assert "光洋科" not in news[0]["q"] and "○○" in news[0]["q"]
    assert "光洋科" not in news[1]["wrong"]  # a company named in the headline is never a distractor
    questions = [q for i in bank["islands"] for q in i["questions"]]
    for q in questions:
        assert len(q["wrong"]) == 3 and q["answer"] not in q["wrong"], q
    assert list(islands) == ["materials", "process", "news", "market", "makers", "play"]
    assert all(len(i["questions"]) <= 12 for i in bank["islands"])
    assert len({q["id"] for q in questions}) == len(questions)  # stable, unique ids for saved progress
    assert all(q["island"] == iid for iid, i in islands.items() for q in i["questions"])
    fixed_materials = [q["q"] for q in config.quiz if q.get("island") == "materials"]
    assert all(any(q["q"] == f for q in islands["materials"]["questions"]) for f in fixed_materials)  # fixed first
    assert bank["treasure"] in islands and bank["treasure"] != "play" and bank["tips"]
    assert all(a in islands and b in islands for a, b, _ in bank["links"])

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-10-13.json").write_text(json.dumps({"generated": "2026-10-13T08:30:00+08:00", "items": items}), encoding="utf-8")
    build_site(reports, tmp_path / "out", config)
    page = (tmp_path / "out" / "quiz.html").read_text(encoding="utf-8")
    assert '<script type="application/json" id="bank">' in page and "<script>x</script>" not in page
    assert 'href="../news/quiz.html"' in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")  # floating page switcher


def test_protect_site_encrypts_under_secret_path(tmp_path):
    from target_news_agent.protect import decrypt_page, protect

    src = tmp_path / "site"
    (src / "news").mkdir(parents=True)
    (src / "index.html").write_text("<meta http-equiv=refresh content='0; url=news/'>", encoding="utf-8")
    (src / "news" / "index.html").write_text("<title>靶材產業新聞摘要</title><p>機密內容</p>", encoding="utf-8")
    (src / "news" / "og.png").write_bytes(b"png")
    (src / "news" / "data.json").write_text("{}", encoding="utf-8")

    # no secrets: placeholder only, nothing from the site is published
    pages, status = protect(src, tmp_path / "out0", None, None)
    assert pages == 0 and "placeholder" in status
    assert sorted(p.name for p in (tmp_path / "out0").iterdir()) == [".nojekyll", "index.html", "robots.txt"]

    with pytest.raises(ValueError):
        protect(src, tmp_path / "bad", "short", "pw")

    secret = "abcDEF123_secret-path"
    pages, _ = protect(src, tmp_path / "out", secret, "密碼 pw")
    out = tmp_path / "out"
    page = (out / secret / "news" / "index.html").read_text(encoding="utf-8")
    assert pages == 1 and "機密內容" not in page and "靶材產業新聞摘要" not in page
    assert decrypt_page(page, "密碼 pw") == "<title>靶材產業新聞摘要</title><p>機密內容</p>"
    with pytest.raises(Exception):
        decrypt_page(page, "wrong")
    assert (out / secret / "news" / "og.png").exists() and not (out / secret / "news" / "data.json").exists()
    assert secret not in (out / "index.html").read_text(encoding="utf-8")  # the root never reveals the path
    assert "Disallow: /" in (out / "robots.txt").read_text(encoding="utf-8")


def test_protect_moves_absolute_site_links_under_secret_path(tmp_path):
    from target_news_agent.protect import decrypt_page, protect

    src = tmp_path / "site"
    for d in ("news", "financials"):
        (src / d).mkdir(parents=True)
    base = "https://u.github.io/repo/"
    (src / "news" / "index.html").write_text("<p>news</p>", encoding="utf-8")
    (src / "financials" / "index.html").write_text(
        f'<a id="news-link" href="{base}news/">新聞</a><a href="https://example.com/news/">x</a>', encoding="utf-8")
    secret = "abcDEF123_secret-path"
    protect(src, tmp_path / "out", secret, "pw", base)
    page = decrypt_page((tmp_path / "out" / secret / "financials" / "index.html").read_text(encoding="utf-8"), "pw")
    assert f'href="{base}{secret}/news/"' in page and 'href="https://example.com/news/"' in page


def test_charts_follow_the_mark_rules():
    from target_news_agent.charts import columns, hbar, index_lines, meter, nice_ticks, sparkline

    assert nice_ticks(0, 87, 3) == [0, 50, 100]
    bars = hbar([("擴產與投資", 36), ("價格", 0)], "則", total=73)
    assert "36則" in bars and 'data-tip="擴產與投資:36 則(占 49%)"' in bars and "#" not in bars
    cols = columns([("2026-10-24", 30), ("2026-10-25", 13)], highlight="2026-10-25", unit="則", label=lambda d: d[5:])
    assert cols.count('class="col on"') == 1 and cols.count('class="cv"') == 1  # only the focus day is labelled
    assert "#" not in sparkline([1, 2, 3])  # colours come from role tokens, never literal hex
    lines = index_lines([2025, 2026, 2031], ["實績", "預估", "預測"],
                        [{"name": "市場金額", "unit": "億日圓", "values": [2198, 2550, 3497]}, {"name": "出貨片數", "unit": "萬片", "values": [28.3, 30.0, 38.4]}])
    assert lines.count('stroke-dasharray="5 4"') == 2 and "159</tspan> 市場金額" in lines and "<table" in lines  # forecast dashed, end labels, table view
    assert "約 70%" in meter(0.7, "前兩大")


def test_site_pages_carry_the_charts(tmp_path, config):
    from target_news_agent.site import build_site

    reports = tmp_path / "reports"
    reports.mkdir()
    for day, price in (("2026-10-24", 4060.4), ("2026-10-25", 4007.9)):
        (reports / f"{day}.json").write_text(json.dumps({
            "generated": f"{day}T07:40:00+08:00", "days": 7,
            "items": [{"id": day, "title": "JX金屬增產", "link": "https://x", "entities": ["JX金屬 (JX Advanced Metals)"],
                       "categories": ["擴產與投資"], "score": 3, "source": "s", "published": day}],
            "metals": [{"name": "黃金", "code": "Au", "price": price, "date": day, "unit": "USD/oz", "source": "Yahoo Finance", "change_pct": -1.3}],
        }, ensure_ascii=False), encoding="utf-8")
    build_site(reports, tmp_path / "out", config)
    index = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert 'aria-labelledby="data"' in index and 'class="hbar"' in index and 'class="cols"' in index and 'class="spark"' in index
    assert "Noto+Serif" not in index  # one sans face throughout


def test_intranet_package_has_no_outside_loads(tmp_path):
    from target_news_agent.intranet import THREE_CDN, package

    base = "https://u.github.io/repo/"
    site = tmp_path / "site"
    (site / "news").mkdir(parents=True)
    (site / "financials").mkdir()
    (site / "news" / "quiz.html").write_text(
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC">'
        f"<script>const THREE_URL = '{THREE_CDN}';</script><a href=\"https://news.example.com/a\">原文</a>", encoding="utf-8")
    (site / "financials" / "index.html").write_text(f'<a href="{base}news/index.html">新聞</a>', encoding="utf-8")
    result = package(site, tmp_path / "out", base)
    quiz = (tmp_path / "out" / "site" / "news" / "quiz.html").read_text(encoding="utf-8")
    assert "fonts.g" not in quiz and "'./vendor/three.module.min.js'" in quiz and "https://news.example.com/a" in quiz  # article links stay
    assert 'href="../news/index.html"' in (tmp_path / "out" / "site" / "financials" / "index.html").read_text(encoding="utf-8")
    assert (tmp_path / "out" / "site" / "news" / "vendor" / "three.module.min.js").stat().st_size > 100_000
    assert (tmp_path / "out" / "nginx" / "target-news.conf").exists() and (tmp_path / "out" / "collector" / "daily-update.sh").exists()
    assert result == {"pages": 2, "external": {}}

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
    assert hon.entities == ["Solstice (原 Honeywell 電子材料)"]
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

    # Second run: everything already seen.
    assert main(args) == 0
    assert "本期沒有新的相關新聞" in reports[0].read_text(encoding="utf-8")


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
    assert index == (out / "2026-10-13.html").read_text(encoding="utf-8")
    assert (out / "2026-10-12.html").exists()
    assert (out / "data" / "2026-10-13.json").exists() and (out / "data" / "2026-10-13.md").exists()
    assert index.count('class="item') >= len(items)
    assert "Spokane" in index and 'href="2026-10-12.html"' in index
    assert "Solstice" in index and "生產商 · 台灣" in index
    assert "<script>alert" not in index

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

    papers = collect_papers(rs, flaky, now=NOW, sleep=lambda s: None)
    assert any(p.source == "arXiv" for p in papers)


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
    assert second["papers"] == []
    assert main(args + ["--no-papers"]) == 0
    assert "papers" not in json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))


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
    assert [set(b["properties"]) for b in created] == [set(notion.PROPERTIES), set(notion.PAPER_PROPERTIES)]
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
    yesterday = NOW.replace(day=12)
    write_reports([], config, Digest(), reports, yesterday, papers)       # papers collected yesterday
    write_reports([], config, Digest(), reports, NOW, [])                 # nothing new today
    out = tmp_path / "site"
    build_site(reports, out, config)

    library = (out / "papers.html").read_text(encoding="utf-8")
    assert library.count('class="item"') == len(papers)
    assert "靶材技術文獻庫" in library and 'data-filter="ent" data-value="粉末冶金與燒結"' in library
    assert 'data-filter="kind" data-value="arXiv"' in library

    home = (out / "index.html").read_text(encoding="utf-8")
    assert "本期無新收錄" in home and "Densification" in home and 'href="papers.html"' in home
    old = (out / "2026-10-12.html").read_text(encoding="utf-8")
    assert "本期新收錄 4 篇" in old

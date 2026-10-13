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

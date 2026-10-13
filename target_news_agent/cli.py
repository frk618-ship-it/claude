"""Command line entry point: `python -m target_news_agent run`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import load_config
from .processing import process
from .report import write_reports
from .sources import collect, iter_feed_urls
from .store import SeenStore
from .summarizer import Digest, Summarizer, llm_available

log = logging.getLogger("target_news_agent")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="target_news_agent", description="靶材生產商 / 銷售商新聞與產業動態蒐集 Agent")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="蒐集新聞並產生報告")
    run.add_argument("-c", "--config", default="config/watchlist.yaml")
    run.add_argument("-o", "--out", default="reports", help="報告輸出資料夾")
    run.add_argument("--days", type=int, help="覆寫設定檔的天數")
    run.add_argument("--only", nargs="+", metavar="NAME", help="只跑名稱包含這些字串的公司/主題")
    run.add_argument("--db", default="data/seen.sqlite3", help="已讀紀錄資料庫")
    run.add_argument("--include-seen", action="store_true", help="包含先前報告過的新聞")
    run.add_argument("--no-llm", action="store_true", help="不使用 Claude 摘要")
    run.add_argument("--model", help="Claude 模型 ID(預設 claude-opus-5-5 或 TARGET_NEWS_MODEL)")
    run.add_argument("--tz", default="Asia/Taipei", help="報告時區")

    ls = sub.add_parser("queries", help="列出將會抓取的 RSS 網址(不連網)")
    ls.add_argument("-c", "--config", default="config/watchlist.yaml")

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    config = load_config(args.config)

    if args.command == "queries":
        for url in iter_feed_urls(config):
            print(url)
        return 0

    if args.days:
        config.settings.days = args.days
    if args.only:
        config.entities = [e for e in config.entities if any(o.lower() in e.name.lower() for o in args.only)]
        if not config.entities:
            p.error("--only matched no entities")

    raw = collect(config)
    items = process(raw, config)
    log.warning("collected %d raw items -> %d relevant", len(raw), len(items))

    store = SeenStore(args.db)
    if not args.include_seen:
        items = store.filter_new(items)

    digest = Digest()
    if items and not args.no_llm:
        if llm_available():
            digest = Summarizer(list(config.settings.categories), model=args.model).digest(items)
        else:
            log.warning("ANTHROPIC_API_KEY not set; generating report without LLM summaries")

    generated = datetime.now(ZoneInfo(args.tz))
    md_path, json_path = write_reports(items, config, digest, args.out, generated)
    store.mark(items)
    store.close()
    print(f"{len(items)} items -> {md_path} , {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

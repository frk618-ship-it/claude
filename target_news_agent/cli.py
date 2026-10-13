"""Command line entry point: `python -m target_news_agent run`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import notion
from .config import load_config
from .models import NewsItem
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

    ns = sub.add_parser("notion-sync", help="把報告 JSON 的新聞寫入 Notion 資料庫(需 NOTION_TOKEN、NOTION_DATABASE_ID)")
    ns.add_argument("report", nargs="?", help="報告 JSON 路徑(預設 reports/ 中最新一份)")
    ns.add_argument("--reports-dir", default="reports")

    nsetup = sub.add_parser("notion-setup", help="在指定 Notion 頁面下建立新聞資料庫(需 NOTION_TOKEN)")
    nsetup.add_argument("--parent", required=True, help="Notion 父頁面 ID(頁面網址最後 32 碼)")
    nsetup.add_argument("--title", default="靶材產業新聞")

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    if args.command in ("notion-sync", "notion-setup"):
        try:
            return _notion_command(args, p)
        except notion.NotionError as exc:
            log.error("%s", exc)
            return 1

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


def _notion_command(args, p: argparse.ArgumentParser) -> int:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        p.error("NOTION_TOKEN is not set (create an internal integration at https://www.notion.so/my-integrations)")
    call = notion.http_transport(token)

    if args.command == "notion-setup":
        db_id = notion.create_database(call, args.parent, args.title)
        print(f"Created Notion database {db_id}\nSet NOTION_DATABASE_ID={db_id}")
        return 0

    database_id = os.environ.get("NOTION_DATABASE_ID")
    if not database_id:
        p.error("NOTION_DATABASE_ID is not set (run `notion-setup` first)")
    if args.report:
        path = Path(args.report)
    else:
        reports = sorted(Path(args.reports_dir).glob("????-??-??.json"))
        if not reports:
            p.error(f"no report JSON found in {args.reports_dir}")
        path = reports[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    items = [NewsItem.from_dict(d) for d in data.get("items", [])]
    created = notion.sync(items, call, database_id)
    print(f"{path}: {len(items)} items, {created} new rows in Notion")
    return 0


if __name__ == "__main__":
    sys.exit(main())

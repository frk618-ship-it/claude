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
from .research import Paper, collect_papers, is_relevant
from .site import build_site, write_redirect
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
    run.add_argument("--no-papers", action="store_true", help="不蒐集技術文獻(期刊/預印本)")
    run.add_argument("--papers-days", type=int, help="覆寫技術文獻的天數(預設 30)")
    run.add_argument("--papers-only", action="store_true", help="只蒐集技術文獻,併入今天既有的報告(不重抓新聞)")

    ls = sub.add_parser("queries", help="列出將會抓取的 RSS 網址(不連網)")
    ls.add_argument("-c", "--config", default="config/watchlist.yaml")

    st = sub.add_parser("site", help="由 reports/ 的報告 JSON 產生靜態網站(GitHub Pages 用)")
    st.add_argument("-c", "--config", default="config/watchlist.yaml")
    st.add_argument("-i", "--reports-dir", default="reports")
    st.add_argument("-o", "--out", default="site/news")
    st.add_argument("--root-redirect", metavar="DIR", help="在 DIR/index.html 放一個轉址到新聞網站的首頁(GitHub Pages 根目錄用)")

    ns = sub.add_parser(
        "notion-sync",
        help="把報告 JSON 的新聞 / 技術文獻寫入 Notion(需 NOTION_TOKEN,及 NOTION_DATABASE_ID 和/或 NOTION_PAPERS_DATABASE_ID)",
    )
    ns.add_argument("report", nargs="?", help="報告 JSON 路徑(預設 reports/ 中最新一份)")
    ns.add_argument("--reports-dir", default="reports")

    nsetup = sub.add_parser("notion-setup", help="在指定 Notion 頁面下建立新聞與技術文獻資料庫(需 NOTION_TOKEN)")
    nsetup.add_argument("--parent", required=True, help="Notion 父頁面 ID(頁面網址最後 32 碼)")
    nsetup.add_argument("--only", choices=["all", "news", "papers"], default="all", help="只建立其中一個資料庫")
    nsetup.add_argument("--title", default="靶材產業新聞", help="新聞資料庫名稱")
    nsetup.add_argument("--papers-title", default="靶材技術文獻", help="技術文獻資料庫名稱")

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

    if args.command == "site":
        written = build_site(args.reports_dir, args.out, config)
        if args.root_redirect:
            written.append(write_redirect(args.root_redirect, args.out))
        print(f"{len(written)} pages -> {args.out}")
        return 0

    if args.days:
        config.settings.days = args.days
    if args.only:
        config.entities = [e for e in config.entities if any(o.lower() in e.name.lower() for o in args.only)]
        if not config.entities:
            p.error("--only matched no entities")

    store = SeenStore(args.db)
    generated = datetime.now(ZoneInfo(args.tz))
    digest = Digest()
    existing_papers: list[Paper] = []
    if args.papers_only:
        if args.no_papers:
            p.error("--papers-only and --no-papers cannot be combined")
        # Add papers to the latest existing report (not strictly today's: a run that crosses
        # midnight would otherwise create an empty report); its news stays as is.
        reports = sorted(Path(args.out).glob("????-??-??.json"))
        items = []
        if reports:
            data = json.loads(reports[-1].read_text(encoding="utf-8"))
            if data.get("generated"):
                generated = datetime.fromisoformat(data["generated"])  # keep that report's date / file name
            items = [NewsItem.from_dict(d) for d in data.get("items", [])]
            digest = Digest(**(data.get("digest") or {}))
            # Re-check with the current filter, so a rerun also cleans out papers that no longer qualify.
            existing_papers = [p for p in (Paper.from_dict(d) for d in data.get("papers") or []) if is_relevant(p)]
    else:
        raw = collect(config)
        items = process(raw, config)
        log.warning("collected %d raw items -> %d relevant", len(raw), len(items))
        if not args.include_seen:
            items = store.filter_new(items)

    if items and not args.no_llm and not args.papers_only:
        if llm_available():
            digest = Summarizer(list(config.settings.categories), model=args.model).digest(items)
        else:
            log.warning("ANTHROPIC_API_KEY not set; generating report without LLM summaries")

    papers = existing_papers or None
    rs = config.research
    if rs and rs.enabled and not args.no_papers:
        if args.papers_days:
            rs.days = args.papers_days
        papers = collect_papers(rs)
        log.warning("collected %d relevant papers", len(papers))
        if not args.include_seen:
            papers = store.filter_new(papers)
        if papers and not args.no_llm and llm_available():
            Summarizer(list(config.settings.categories), model=args.model).digest_papers(papers, list(rs.topics))
        known = {p.id for p in papers}
        papers = papers + [p for p in existing_papers if p.id not in known]

    md_path, json_path = write_reports(items, config, digest, args.out, generated, papers)
    if not args.papers_only:
        store.mark(items)
    if papers:
        store.mark(papers)
    store.close()
    paper_note = f", {len(papers)} papers" if papers is not None else ""
    print(f"{len(items)} items{paper_note} -> {md_path} , {json_path}")
    return 0


def _notion_command(args, p: argparse.ArgumentParser) -> int:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        p.error("NOTION_TOKEN is not set (create an internal integration at https://www.notion.so/my-integrations)")
    call = notion.http_transport(token)

    if args.command == "notion-setup":
        if args.only in ("all", "news"):
            db_id = notion.create_database(call, args.parent, args.title)
            print(f"Created news database {db_id}\nSet NOTION_DATABASE_ID={db_id}")
        if args.only in ("all", "papers"):
            db_id = notion.create_database(call, args.parent, args.papers_title, notion.PAPER_PROPERTIES)
            print(f"Created papers database {db_id}\nSet NOTION_PAPERS_DATABASE_ID={db_id}")
        return 0

    news_db = os.environ.get("NOTION_DATABASE_ID")
    papers_db = os.environ.get("NOTION_PAPERS_DATABASE_ID")
    if not news_db and not papers_db:
        p.error("set NOTION_DATABASE_ID and/or NOTION_PAPERS_DATABASE_ID (run `notion-setup` first)")
    if args.report:
        path = Path(args.report)
    else:
        reports = sorted(Path(args.reports_dir).glob("????-??-??.json"))
        if not reports:
            p.error(f"no report JSON found in {args.reports_dir}")
        path = reports[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    if news_db:
        items = [NewsItem.from_dict(d) for d in data.get("items", [])]
        created = notion.sync(items, call, news_db)
        print(f"{path}: {len(items)} news items, {created} new rows in Notion")
    if papers_db:
        papers = [Paper.from_dict(d) for d in data.get("papers", [])]
        created = notion.sync(papers, call, papers_db, properties=notion.paper_properties)
        print(f"{path}: {len(papers)} papers, {created} new rows in Notion")
    return 0


if __name__ == "__main__":
    sys.exit(main())

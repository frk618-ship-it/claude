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
from .metals import fetch_quotes
from .sources import collect, http_fetch, iter_feed_urls
from .store import SeenStore
from .fallback import finalize_digest
from .summarizer import PAPER_PROMPT, Digest, Summarizer, llm_available, news_system_prompt
from .translate import TRANSLATE_PROMPT, llm_translator, needs_translation, translate_records

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
    run.add_argument("--no-translate", action="store_true", help="不把非繁體中文的標題/摘要翻成繁體中文")

    rs_ = sub.add_parser("resummarize", help="為既有報告補上 Claude 摘要與翻譯(需 ANTHROPIC_API_KEY)")
    rs_.add_argument("reports", nargs="*", help="報告 JSON 路徑(預設:reports/ 中尚有新聞缺摘要的報告)")
    rs_.add_argument("-c", "--config", default="config/watchlist.yaml")
    rs_.add_argument("--reports-dir", default="reports")
    rs_.add_argument("--model", help="Claude 模型 ID")
    rs_.add_argument("--force", action="store_true", help="已有摘要的報告也重新產生")

    pe = sub.add_parser("pending", help="列出報告中尚缺 Claude 摘要 / 翻譯的新聞與文獻(給排程任務的 Claude 撰寫)")
    pe.add_argument("report", nargs="?", help="報告 JSON 路徑(預設 reports/ 中最新一份)")
    pe.add_argument("-c", "--config", default="config/watchlist.yaml")
    pe.add_argument("--reports-dir", default="reports")
    pe.add_argument("-o", "--out", default="pending.json")
    pe.add_argument("--force", action="store_true", help="已有摘要的也列出")

    ap = sub.add_parser("apply-digest", help="把排程任務的 Claude 寫好的摘要 JSON 併入報告")
    ap.add_argument("file", help="摘要 JSON(格式見 README)")
    ap.add_argument("report", nargs="?", help="報告 JSON 路徑(預設 reports/ 中最新一份)")
    ap.add_argument("-c", "--config", default="config/watchlist.yaml")
    ap.add_argument("--reports-dir", default="reports")

    ls = sub.add_parser("queries", help="列出將會抓取的 RSS 網址(不連網)")
    ls.add_argument("-c", "--config", default="config/watchlist.yaml")

    st = sub.add_parser("site", help="由 reports/ 的報告 JSON 產生靜態網站(GitHub Pages 用)")
    st.add_argument("-c", "--config", default="config/watchlist.yaml")
    st.add_argument("-i", "--reports-dir", default="reports")
    st.add_argument("-o", "--out", default="site/news")
    st.add_argument("--root-redirect", metavar="DIR", help="在 DIR/index.html 放一個轉址到新聞網站的首頁(GitHub Pages 根目錄用)")

    pr = sub.add_parser("protect", help="把建好的網站加密、放到秘密路徑下(SITE_PATH / SITE_PASSWORD 環境變數)")
    pr.add_argument("--src", default="site", help="建好的網站資料夾")
    pr.add_argument("--out", default="public", help="要發布的資料夾")

    ns = sub.add_parser(
        "notion-sync",
        help="把報告 JSON 的新聞 / 技術文獻 / 追蹤公司寫入 Notion(需 NOTION_TOKEN,及 NOTION_PARENT_PAGE_ID 或各資料庫 ID)",
    )
    ns.add_argument("report", nargs="?", help="報告 JSON 路徑(預設 reports/ 中最新一份)")
    ns.add_argument("--reports-dir", default="reports")
    ns.add_argument("-c", "--config", default="config/watchlist.yaml")

    nsetup = sub.add_parser("notion-setup", help="在指定 Notion 頁面下建立新聞與技術文獻資料庫(需 NOTION_TOKEN)")
    nsetup.add_argument("--parent", required=True, help="Notion 父頁面 ID(頁面網址最後 32 碼)")
    nsetup.add_argument("--only", choices=["all", "news", "papers", "companies"], default="all", help="只建立其中一個資料庫")
    nsetup.add_argument("--title", default="靶材產業新聞", help="新聞資料庫名稱")
    nsetup.add_argument("--papers-title", default="靶材技術文獻", help="技術文獻資料庫名稱")
    nsetup.add_argument("--companies-title", default="靶材追蹤公司", help="追蹤公司資料庫名稱")

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    if args.command == "protect":
        from .protect import protect

        pages, status = protect(args.src, args.out, os.environ.get("SITE_PATH"), os.environ.get("SITE_PASSWORD"))
        print(status)
        return 0

    if args.command in ("notion-sync", "notion-setup"):
        try:
            return _notion_command(args, p)
        except notion.NotionError as exc:
            log.error("%s", exc)
            return 1

    config = load_config(args.config)

    if args.command == "resummarize":
        return _resummarize(args, config, p)
    if args.command == "pending":
        return _pending(args, config)
    if args.command == "apply-digest":
        return _apply(args, config)

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
    metals: list[dict] = []
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
            metals = data.get("metals") or []
            # Re-check with the current filter, so a rerun also cleans out papers that no longer qualify.
            existing_papers = [p for p in (Paper.from_dict(d) for d in data.get("papers") or []) if is_relevant(p)]
    else:
        raw = collect(config)
        items = process(raw, config)
        log.warning("collected %d raw items -> %d relevant", len(raw), len(items))
        if not args.include_seen:
            items = store.filter_new(items)
        # A second run on the same day adds to that day's report instead of replacing it.
        same_day = Path(args.out) / f"{generated:%Y-%m-%d}.json"
        if same_day.exists():
            data = json.loads(same_day.read_text(encoding="utf-8"))
            new_ids = {i.id for i in items}
            items = items + [i for i in (NewsItem.from_dict(d) for d in data.get("items", [])) if i.id not in new_ids]
            digest = Digest(**(data.get("digest") or {}))
            existing_papers = [p for p in (Paper.from_dict(d) for d in data.get("papers") or []) if is_relevant(p)]
            metals = data.get("metals") or []
        if config.metals:
            # Keep the earlier quotes when a source is down.
            metals = [q.to_dict() for q in fetch_quotes(config.metals, http_fetch)] or metals

    if items and not args.no_llm and not args.papers_only:
        if llm_available():
            digest = _keep(Summarizer(list(config.settings.categories), model=args.model, background=config.market_context).digest(items), digest)
        else:
            log.warning("ANTHROPIC_API_KEY not set; generating report without LLM summaries")
    if not args.papers_only:
        digest = finalize_digest(digest, items, config)  # rule-based overview when Claude produced none

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
            Summarizer(list(config.settings.categories), model=args.model, background=config.market_context).digest_papers(papers, list(rs.topics))
        known = {p.id for p in papers}
        papers = papers + [p for p in existing_papers if p.id not in known]

    if not args.no_translate:
        # Simplified Chinese -> Traditional offline; English / Japanese / Korean via Claude when available.
        llm = None
        if not args.no_llm and llm_available():
            llm = llm_translator(Summarizer(list(config.settings.categories), model=args.model, background=config.market_context))
        translate_records(items, papers, llm)

    md_path, json_path = write_reports(items, config, digest, args.out, generated, papers, metals)
    if not args.papers_only:
        store.mark(items)
    if papers:
        store.mark(papers)
    store.close()
    paper_note = f", {len(papers)} papers" if papers is not None else ""
    print(f"{len(items)} items{paper_note} -> {md_path} , {json_path}")
    return 0


def _keep(new: Digest, old: Digest) -> Digest:
    """Use the new overview unless the LLM call produced none."""
    return new if new.overview else old


def _report_path(arg: str | None, reports_dir: str) -> Path:
    if arg:
        return Path(arg)
    paths = sorted(Path(reports_dir).glob("????-??-??.json"))
    if not paths:
        raise SystemExit(f"{reports_dir} 沒有報告")
    return paths[-1]


def _pending(args, config) -> int:
    """Write what still needs a Claude summary / translation, for a scheduled Claude task to fill in."""
    path = _report_path(args.report, args.reports_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    digest = Digest(**(data.get("digest") or {}))
    news = [
        {"id": d["id"], "title": d["title"], "snippet": (d.get("snippet") or "")[:400], "source": d.get("source", ""),
         "date": (d.get("published") or "")[:10], "companies": d.get("entities", []), "rule_categories": d.get("categories", []),
         "needs_title_zh": needs_translation(d["title"]) and not d.get("title_zh"),
         "needs_snippet_zh": needs_translation(d.get("snippet") or "") and not d.get("snippet_zh")}
        for d in data.get("items", []) if args.force or not d.get("summary")
    ]
    papers = [
        {"id": d["id"], "title": d["title"], "abstract": (d.get("abstract") or "")[:1500], "venue": d.get("venue", ""),
         "needs_title_zh": needs_translation(d["title"]) and not d.get("title_zh")}
        for d in data.get("papers") or [] if args.force or not d.get("summary")
    ]
    out = {
        "report": str(path),
        "need_overview": args.force or not (digest.overview and digest.source != "auto"),
        "categories": list(config.settings.categories),
        "topics": list(config.research.topics) if config.research else [],
        "instructions": {"news": news_system_prompt(list(config.settings.categories), config.market_context),
                         "papers": PAPER_PROMPT.format(topics="、".join(config.research.topics if config.research else [])),
                         "translation": TRANSLATE_PROMPT},
        "news": news,
        "papers": papers,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{path}: {len(news)} news, {len(papers)} papers pending, overview {'needed' if out['need_overview'] else 'done'} -> {args.out}")
    return 0


def _apply(args, config) -> int:
    """Merge summaries written by a scheduled Claude task (JSON file) into a report, then rewrite it."""
    path = _report_path(args.report, args.reports_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    given = json.loads(Path(args.file).read_text(encoding="utf-8"))
    items = [NewsItem.from_dict(d) for d in data.get("items", [])]
    papers = [Paper.from_dict(d) for d in data["papers"]] if "papers" in data else None
    cats = list(config.settings.categories)
    topics = list(config.research.topics) if config.research else []

    def fill(records, entries, valid, cat_attr):
        by_id, n = {r.id: r for r in records}, 0
        for e in entries:
            r = by_id.get(str(e.get("id", "")))
            if r is None:
                continue
            if (e.get("summary") or "").strip():
                r.summary, n = e["summary"].strip(), n + 1
            if e.get("importance"):
                r.importance = min(max(int(e["importance"]), 1), 5)
            for key in ("title_zh", "snippet_zh", "abstract_zh"):
                if (e.get(key) or "").strip() and hasattr(r, key):
                    setattr(r, key, e[key].strip())
            cat = e.get("category")
            if cat in valid:
                setattr(r, cat_attr, [cat] + [c for c in getattr(r, cat_attr) if c != cat])
        return n

    n_news = fill(items, given.get("news") or [], cats, "categories")
    n_papers = fill(papers or [], given.get("papers") or [], topics, "topics")
    digest = Digest(**(data.get("digest") or {}))
    if (given.get("overview") or "").strip():
        digest = Digest(overview=given["overview"].strip(), key_trends=[str(t) for t in given.get("key_trends") or []],
                        watch_points=[str(t) for t in given.get("watch_points") or []], source="claude")
    digest = finalize_digest(digest, items, config)
    translate_records(items, papers, None)  # Simplified -> Traditional for anything new
    if data.get("days"):
        config.settings.days = data["days"]
    if config.research and data.get("research_days"):
        config.research.days = data["research_days"]
    write_reports(items, config, digest, path.parent, datetime.fromisoformat(data["generated"]), papers, data.get("metals"))
    print(f"{path}: {n_news} news, {n_papers} papers summarized; overview: {digest.source or 'none'}; "
          f"{sum(bool(i.summary) for i in items)}/{len(items)} news now have summaries")
    return 0


def _resummarize(args, config, p: argparse.ArgumentParser) -> int:
    """Add Claude summaries, importance and translations to reports written without them."""
    if not llm_available():
        p.error("resummarize 需要 ANTHROPIC_API_KEY")
    paths = [Path(r) for r in args.reports] or sorted(Path(args.reports_dir).glob("????-??-??.json"))
    summarizer = Summarizer(list(config.settings.categories), model=args.model, background=config.market_context)
    topics = list(config.research.topics) if config.research else []
    done = 0
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = [NewsItem.from_dict(d) for d in data.get("items", [])]
        papers = [Paper.from_dict(d) for d in data["papers"]] if "papers" in data else None
        if not args.force and all(i.summary for i in items) and all(x.summary for x in papers or []):
            continue
        digest = Digest(**(data.get("digest") or {}))
        if items and (args.force or not all(i.summary for i in items)):
            digest = _keep(summarizer.digest(items), digest)
        digest = finalize_digest(digest, items, config)
        missing = [x for x in papers or [] if args.force or not x.summary]
        if missing:
            summarizer.digest_papers(missing, topics)
        translate_records(items, papers, llm_translator(summarizer))
        if data.get("days"):
            config.settings.days = data["days"]
        write_reports(items, config, digest, path.parent, datetime.fromisoformat(data["generated"]), papers, data.get("metals"))
        done += 1
        print(f"{path}: {sum(bool(i.summary) for i in items)}/{len(items)} items, "
              f"{sum(bool(x.summary) for x in papers or [])}/{len(papers or [])} papers summarized")
    print(f"{done} reports updated")
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
        if args.only in ("all", "companies"):
            db_id = notion.create_database(call, args.parent, args.companies_title, notion.COMPANY_PROPERTIES)
            print(f"Created companies database {db_id}\nSet NOTION_COMPANIES_DATABASE_ID={db_id}")
        return 0

    news_db = os.environ.get("NOTION_DATABASE_ID")
    papers_db = os.environ.get("NOTION_PAPERS_DATABASE_ID")
    companies_db = os.environ.get("NOTION_COMPANIES_DATABASE_ID")
    parent = os.environ.get("NOTION_PARENT_PAGE_ID")
    if parent:
        # Only a parent page is needed: find the three databases under it, creating any that are missing.
        news_db = news_db or notion.find_or_create_database(call, parent, notion.NEWS_TITLE, notion.PROPERTIES)
        papers_db = papers_db or notion.find_or_create_database(call, parent, notion.PAPERS_TITLE, notion.PAPER_PROPERTIES)
        companies_db = companies_db or notion.find_or_create_database(
            call, parent, notion.COMPANIES_TITLE, notion.COMPANY_PROPERTIES
        )
    if not news_db and not papers_db and not companies_db:
        p.error("set NOTION_PARENT_PAGE_ID, or NOTION_DATABASE_ID / NOTION_PAPERS_DATABASE_ID (run `notion-setup` first)")
    if args.report:
        path = Path(args.report)
    else:
        reports = sorted(Path(args.reports_dir).glob("????-??-??.json"))
        if not reports:
            p.error(f"no report JSON found in {args.reports_dir}")
        path = reports[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    config = load_config(args.config)
    items = [NewsItem.from_dict(d) for d in data.get("items", [])]
    if news_db:
        added = notion.ensure_properties(call, news_db, notion.PROPERTIES)
        if added:
            print(f"Added columns to the news database: {', '.join(added)}")
        roles = {e.name: e.role_label for e in config.entities}
        created = notion.sync(items, call, news_db, properties=lambda i: notion.item_properties(i, roles))
        print(f"{path}: {len(items)} news items, {created} new rows in Notion")
    if papers_db:
        papers = [Paper.from_dict(d) for d in data.get("papers", [])]
        created = notion.sync(papers, call, papers_db, properties=notion.paper_properties)
        print(f"{path}: {len(papers)} papers, {created} new rows in Notion")
    if companies_db:
        counts: dict[str, int] = {}
        last: dict[str, str] = {}
        for i in items:
            for e in i.entities:
                counts[e] = counts.get(e, 0) + 1
                if i.published:
                    last[e] = max(last.get(e, ""), i.published.date().isoformat())
        created, updated = notion.sync_companies(call, companies_db, config.entities, counts, last)
        print(f"{path}: companies database: {created} added, {updated} updated")
    return 0

if __name__ == "__main__":
    sys.exit(main())

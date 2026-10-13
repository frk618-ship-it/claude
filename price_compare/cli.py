"""Command line entry point: `python -m price_compare search "鈦靶 4N5 Φ300x6.35mm"`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import load_config
from .models import Quote
from .report import render_markdown, write_reports
from .search import run_search
from .sources import append_quote_book

log = logging.getLogger("price_compare")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="price_compare", description="特定物料比價:輸入品名規格,找出市場流通報價")
    p.add_argument("-c", "--config", default="config/price_compare.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="比價(未提供品名規格時會互動詢問)")
    s.add_argument("query", nargs="*", help='品名規格,例如 "鈦靶 4N5 Φ300x6.35mm"')
    s.add_argument("-q", "--quantity", default="", help="需求數量(提供給網路搜尋參考),例如 '10 片'")
    s.add_argument("-o", "--out", default="reports/prices", help="報告輸出資料夾")
    s.add_argument("--no-web", action="store_true", help="不使用 Claude 網路搜尋,只查報價紀錄")
    s.add_argument("--no-book", action="store_true", help="不查報價紀錄 CSV")
    s.add_argument("--pchome", action="store_true", help="同時搜尋 PChome 24h(一般消費性商品)")
    s.add_argument("--save-web", action="store_true", help="把網路找到且規格符合的報價存入報價紀錄 CSV")
    s.add_argument("--model", help="Claude 模型 ID(預設 claude-opus-5-5 或 PRICE_COMPARE_MODEL)")
    s.add_argument("--tz", default="Asia/Taipei")

    a = sub.add_parser("add", help="新增一筆報價到報價紀錄 CSV(供應商報價單、歷史採購價)")
    a.add_argument("--vendor", required=True)
    a.add_argument("--title", required=True, help="品名,例如 '鈦靶'")
    a.add_argument("--spec", default="", help="規格,例如 '4N5 Φ300x6.35mm'")
    a.add_argument("--price", type=float, required=True)
    a.add_argument("--currency", default="TWD")
    a.add_argument("--unit", default="pcs", help="pcs / kg / g / lb / oz / ozt / set")
    a.add_argument("--pack-qty", type=float, default=1.0)
    a.add_argument("--date", default=datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat())
    a.add_argument("--moq", default="")
    a.add_argument("--lead-time", default="")
    a.add_argument("--region", default="")
    a.add_argument("--url", default="")
    a.add_argument("--note", default="")

    w = sub.add_parser("serve", help="啟動本機網頁介面")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("-o", "--out", default="reports/prices")

    b = sub.add_parser("site", help="把 reports/prices 的比價報告產生成靜態網站(GitHub Pages)")
    b.add_argument("-i", "--input", default="reports/prices", help="比價報告資料夾")
    b.add_argument("-o", "--out", default="site", help="網站輸出資料夾")
    b.add_argument("--new-query-url", default="", help="首頁「新增比價」按鈕連結(例如 Actions workflow 頁面)")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    settings = load_config(args.config)

    if args.command == "add":
        q = Quote(source="報價紀錄", vendor=args.vendor, title=args.title, spec_text=args.spec, price=args.price,
                  currency=args.currency.upper(), unit=args.unit, pack_qty=args.pack_qty, quote_date=args.date,
                  moq=args.moq, lead_time=args.lead_time, region=args.region, url=args.url, note=args.note,
                  price_type="quote")
        append_quote_book(settings.quote_book, [q])
        print(f"已新增到 {settings.quote_book}")
        return 0

    if args.command == "site":
        from .site import build_site

        n = build_site(args.input, args.out, args.new_query_url)
        print(f"{n} 份比價報告 -> {args.out}/index.html")
        return 0

    if args.command == "serve":
        from .web import serve

        serve(settings, args.host, args.port, args.out)
        return 0

    query = " ".join(args.query).strip()
    if not query:
        try:
            query = input("請輸入物料品名規格(例如:鈦靶 4N5 Φ300x6.35mm):").strip()
        except EOFError:
            query = ""
        if not query:
            p.error("需要品名規格")

    comp = run_search(
        query, settings, quantity=args.quantity, use_web=not args.no_web, use_book=not args.no_book,
        use_pchome=True if args.pchome else None, model=args.model,
    )
    generated = datetime.now(ZoneInfo(args.tz))
    print(render_markdown(comp, generated))
    md, js, cs = write_reports(comp, args.out, generated)
    print(f"報告:{md} , {js} , {cs}", file=sys.stderr)

    if args.save_web:
        keep = [q for q in comp.quotes if q.source != "報價紀錄" and q.match_level == "符合" and q.price_type != "reference"]
        append_quote_book(settings.quote_book, keep)
        print(f"已存入 {len(keep)} 筆網路報價到 {settings.quote_book}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

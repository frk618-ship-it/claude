"""Command line entry point: `python -m metal_alert run | serve | site`."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .config import load_config
from .engine import load_snapshot, run_once
from .page import write_site

log = logging.getLogger("metal_alert")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="metal_alert", description="貴金屬價格預警:技術指標判斷相對低點(進場)與高點(出場)")
    p.add_argument("-c", "--config", default="config/metal_alert.yaml")
    p.add_argument("-d", "--data-dir", default="data/metal_alert", help="狀態、預警紀錄與最新資料的資料夾")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="執行一次檢查(排程用):抓資料、計算指標、發出預警")
    r.add_argument("--no-notify", action="store_true", help="不發送通知(仍會記錄預警)")
    r.add_argument("--site", help="同時輸出網頁到此資料夾,例如 site/metals")

    w = sub.add_parser("serve", help="啟動網頁模組,背景每小時自動檢查")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8770)

    s = sub.add_parser("site", help="用最近一次的檢查結果產生靜態網頁")
    s.add_argument("-o", "--out", default="site/metals")
    s.add_argument("--fetch", action="store_true", help="先抓最新資料(唯讀:不發通知、不更新狀態與預警紀錄)")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    data_dir = Path(args.data_dir)

    if args.command == "run":
        snap = run_once(cfg, data_dir, notify=not args.no_notify)
        if args.site:
            write_site(snap, Path(args.site))
        _summary(snap)
        return 0 if snap["metals"] else 1

    if args.command == "serve":
        from .web import serve

        serve(cfg, data_dir, args.host, args.port)
        return 0

    if args.command == "site":
        snap = run_once(cfg, data_dir, notify=False, record=False) if args.fetch else load_snapshot(data_dir)
        if not snap:
            log.error("沒有資料:請先執行 `python -m metal_alert run`,或加上 --fetch")
            return 1
        print(write_site(snap, Path(args.out)))
        return 0
    return 2


def _summary(snap: dict) -> None:
    """Print a table; also write it to the GitHub Actions job summary when running there."""
    lines = ["| 品項 | 價格 | 24h | 分數 | 訊號 |", "|---|---:|---:|---:|---|"]
    for m in snap["metals"]:
        ch = "—" if m["change_24h"] is None else f"{m['change_24h']:+.2f}%"
        lines.append(f"| {m['name']} {m['code']} | {m['price']:,.2f} | {ch} | {m['score']:+.0f} | {m['level_name']} |")
    if snap["new_alerts"]:
        lines += ["", "**新預警**", ""] + [f"- {a['title']}" for a in snap["new_alerts"]]
    lines += [f"- ⚠️ {e}" for e in snap["errors"]]
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 貴金屬價格預警\n\n" + text + "\n")

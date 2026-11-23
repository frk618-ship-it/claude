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

    sub.add_parser("test-email", help="寄一封測試信到 ALERT_EMAIL_TO,確認寄件人與 SMTP 設定(不抓資料、不發預警)")

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

    if args.command == "test-email":
        return _test_email(os.environ)

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


def _test_email(env) -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .alerts import _send_email

    missing = [k for k in ("SMTP_HOST", "ALERT_EMAIL_TO") if not env.get(k, "").strip()]
    if missing:
        log.error("缺少設定:%s(請在 GitHub Secrets 設定)", "、".join(missing))
        return 1
    now = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")
    sender = env.get("ALERT_EMAIL_FROM", "").strip() or env.get("SMTP_USER", "").strip()
    body = (f"這是貴金屬價格預警的測試信({now} 台北時間)。\n\n"
            f"收到這封信,代表寄件人與 SMTP 設定正確,之後的預警通知與週報都會由此寄出。\n"
            f"寄件人:{sender}\nSMTP:{env['SMTP_HOST'].strip()}:{env.get('SMTP_PORT', '').strip() or 465}")
    try:
        _send_email(env, f"【測試】貴金屬價格預警 寄件設定確認 {now}", body)
    except Exception as exc:  # report the SMTP error (never the password) and fail the run
        log.error("測試信寄送失敗:%s: %s", type(exc).__name__, exc)
        return 1
    n = len([a for a in env["ALERT_EMAIL_TO"].replace(";", ",").split(",") if a.strip()])
    print(f"測試信已寄出:寄件人 {sender},收件人 {n} 位")
    for line in _sender_checks(env):
        print(line)
    return 0


def _sender_checks(env) -> list[str]:
    """Spam-relevant facts about the sender setup, without printing any address (Actions logs are public)."""
    from email.utils import parseaddr

    def domain(a: str) -> str:
        return (parseaddr(a)[1] or a).rpartition("@")[2].lower().strip()

    user = env.get("SMTP_USER", "").strip()
    sender = env.get("ALERT_EMAIL_FROM", "").strip() or user
    host = env.get("SMTP_HOST", "").strip().lower()
    out = []
    if user and domain(sender) and domain(user):
        same = domain(sender) == domain(user)
        out.append(f"檢查:寄件人網域與 SMTP 帳號網域{'一致' if same else '不一致(DMARC 會判定冒名,最常見的進垃圾信原因)'}")
    free = ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.com.tw")
    if domain(sender) in free and not any(k in host for k in ("gmail", "google", "outlook", "office365", "live", "yahoo")):
        out.append("檢查:寄件人是免費信箱網域,但不是透過該信箱的 SMTP 寄出,會被判定冒名")
    kind = next((k for k in ("gmail", "google", "office365", "outlook", "yahoo", "sendgrid", "mailgun", "ses") if k in host), "其他")
    out.append(f"檢查:SMTP 伺服器類型 {kind}")
    return out


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

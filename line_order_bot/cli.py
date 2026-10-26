"""Command line entry point: `python -m line_order_bot serve | simulate | list | remind`."""

from __future__ import annotations

import argparse
import logging
import sys

from .bot import OrderBot
from .config import load_config
from .line_api import LineClient, RecordingLine
from .parser import ClaudeExtractor, llm_available
from .store import OPEN_STATUSES, STATUS_LABELS, Store

log = logging.getLogger("line_order_bot")

SIM_CUSTOMER = "U_sim_customer"
SIM_STAFF = "U_sim_staff"
SIM_GROUP = "C_sim_staff_group"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="line_order_bot", description="LINE 訂單詢價機器人:客戶提出品項與重量需求,內部窗口回覆報價與交期")
    p.add_argument("-c", "--config", default="config/line_order_bot.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--model", help="Claude 模型 ID(預設 claude-opus-5-5 或 LINE_BOT_MODEL)")
    p.add_argument("--no-claude", action="store_true", help="不使用 Claude,只用規則解析客戶訊息")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="啟動 LINE webhook 伺服器")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--path", default="/callback")

    sub.add_parser("simulate", help="在終端機模擬客戶與窗口對話(不需要 LINE 帳號,資料存在記憶體)")

    ls = sub.add_parser("list", help="列出詢價")
    ls.add_argument("--all", action="store_true", help="包含已結案")

    sub.add_parser("remind", help="提醒窗口逾時未報價的詢價(可排程每小時執行)")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    settings = load_config(args.config)
    extractor = None
    if settings.use_claude and not args.no_claude and llm_available():
        extractor = ClaudeExtractor(model=args.model)

    if args.command == "simulate":
        return simulate(settings, extractor)

    store = Store(settings.db)
    if args.command == "list":
        rows = store.list(None if args.all else OPEN_STATUSES, limit=200)
        for r in rows:
            print(f"{r['id']}\t{STATUS_LABELS.get(r['status'], r['status'])}\t{r['customer_name']}\t{r['product']}\t"
                  f"{r['weight_kg'] or ''} kg\t{r['due_date'] or r['due_text']}\t{r['assignee']}")
        print(f"共 {len(rows)} 筆", file=sys.stderr)
        return 0

    if not settings.channel_access_token:
        p.error("請設定環境變數 LINE_CHANNEL_ACCESS_TOKEN")
    bot = OrderBot(settings, store, LineClient(settings.channel_access_token), extractor)

    if args.command == "remind":
        print(f"提醒 {bot.remind()} 筆詢價")
        return 0

    if not settings.channel_secret:
        p.error("請設定環境變數 LINE_CHANNEL_SECRET")
    if not settings.staff_targets:
        log.warning("尚未設定 staff_group_id / staff_user_ids:把機器人加入內部群組,輸入 /id 取得群組 ID")
    from .server import serve

    print("客戶訊息解析:" + ("Claude " + extractor.model if extractor else "規則解析"))
    serve(bot, settings.channel_secret, args.host, args.port, args.path)
    return 0


def simulate(settings, extractor) -> int:
    """Interactive console: plain lines are the customer, `s:` lines are staff in the staff group,
    `p:` lines send a postback (button press) as the customer."""
    settings.staff_group_id, settings.staff_user_ids = SIM_GROUP, []

    def echo(kind, target, messages):
        who = {SIM_CUSTOMER: "→客戶", SIM_GROUP: "→窗口群組"}.get(target, "↩回覆")
        for m in messages:
            if m["type"] == "text":
                print(f"[{who}] {m['text']}")
            else:
                t = m["template"]
                print(f"[{who}] {t['text']}")
            actions = (m.get("template", {}).get("actions")
                       or [i["action"] for i in m.get("quickReply", {}).get("items", [])])
            for a in actions:
                hint = f"p:{a['data']}" if a["type"] == "postback" else a["text"]
                print(f"    [{a['label']}] 輸入 {hint}")
        print()

    line = RecordingLine(names={SIM_CUSTOMER: "王小明", SIM_STAFF: "業務小陳"}, echo=echo)
    bot = OrderBot(settings, Store(":memory:"), line, extractor)
    print("模擬模式:直接輸入 = 客戶訊息;s:/指令 = 窗口群組;p:action=… = 客戶按按鈕;q 離開。")
    print("客戶訊息解析:" + ("Claude " + extractor.model if extractor else "規則解析") + "\n")
    bot.handle({"type": "follow", "replyToken": "r", "source": {"type": "user", "userId": SIM_CUSTOMER}})
    while True:
        try:
            line_in = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line_in in ("q", "quit", "exit"):
            return 0
        if not line_in:
            continue
        if line_in.startswith(("s:", "s:")):
            src = {"type": "group", "groupId": SIM_GROUP, "userId": SIM_STAFF}
            event = {"type": "message", "message": {"type": "text", "text": line_in[2:].strip()}}
        elif line_in.startswith(("p:", "p:")):
            src = {"type": "user", "userId": SIM_CUSTOMER}
            event = {"type": "postback", "postback": {"data": line_in[2:].strip()}}
            if "action=claim" in line_in:
                src = {"type": "group", "groupId": SIM_GROUP, "userId": SIM_STAFF}
        else:
            src = {"type": "user", "userId": SIM_CUSTOMER}
            event = {"type": "message", "message": {"type": "text", "text": line_in}}
        bot.handle({**event, "replyToken": "r", "source": src})


if __name__ == "__main__":
    sys.exit(main())

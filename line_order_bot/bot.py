"""Conversation logic: customers send order requests, staff reply with quotes and lead times.

Customer side (1-on-1 chat with the official account)
    free text   -> material/spec + weight (+ due date, destination) collected into a draft;
                   the bot asks for whatever is missing, then shows a summary with 送出 / 修改 buttons
    送出         -> becomes an inquiry (Q20261026-001) and is pushed to the staff group
    quote push  -> 接受報價 / 婉拒 buttons; acceptance is pushed back to staff

Staff side (the configured staff group, or staff user IDs in 1-on-1 chat), commands start with "/":
    /清單  /查 ID  /認領 ID  /報價 ID 單價 幣別/kg 交期 …  /詢問 ID 問題  /婉拒 ID 原因  /成交 ID  /備註 ID 內容
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from . import line_api as msg
from .config import Settings
from .parser import ClaudeExtractor, OrderFields, extract_order, format_kg, parse_due
from .store import OPEN_STATUSES, STATUS_LABELS, Store

log = logging.getLogger(__name__)

ID_RE = re.compile(r"Q\d{8}-\d{3,}", re.I)
HELP_WORDS = {"說明", "说明", "幫助", "帮助", "help", "?", "?", "選單", "菜单", "menu"}
LIST_WORDS = {"我的詢價", "我的询价", "查詢", "查询", "詢價紀錄", "询价纪录", "進度", "进度", "查詢進度", "查询进度"}
SUBMIT_WORDS = {"送出", "確認", "确认", "確認送出", "确认送出"}
ID_COMMANDS = {"查", "show", "認領", "认领", "claim", "報價", "报价", "quote", "詢問", "询问", "ask", "婉拒", "reject",
               "成交", "confirm", "備註", "备注", "note"}
DISCARD_WORDS = {"取消", "重來", "重来", "重新開始", "重新开始", "清除"}

STAFF_HELP = """內部窗口指令(ID 例如 Q20261026-001):
/清單 — 未結案詢價(/清單 全部:最近 20 筆)
/查 ID — 詢價明細與處理紀錄
/認領 ID — 由你負責這筆詢價
/報價 ID 單價 幣別/單位 交期 日期或天數 [有效 N天] [備註 文字]
  例:/報價 Q20261026-001 1250 TWD/kg 交期 2026-11-20 有效 7天 備註 含運
  單位可用 /kg /g /t,或寫「總價」;交期可寫日期或「14天」
/詢問 ID 問題 — 轉問客戶,客戶回覆會轉回這裡
/婉拒 ID 原因 — 通知客戶無法承接
/成交 ID — 客戶接受報價後,確認訂單成立並通知客戶
/備註 ID 內容 — 內部備註(不會通知客戶)
/id — 顯示此聊天室 ID(設定 staff_group_id 用)"""


class OrderBot:
    def __init__(self, settings: Settings, store: Store, line, extractor: ClaudeExtractor | None = None, clock=None):
        self.s = settings
        self.store = store
        self.line = line
        self.extractor = extractor
        self.tz = ZoneInfo(settings.timezone)
        self.clock = clock or (lambda: datetime.now(self.tz))

    # ------------------------------------------------------------ time helpers

    def now(self) -> datetime:
        return self.clock()

    def stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ------------------------------------------------------------ entry point

    def handle(self, event: dict) -> None:
        etype = event.get("type")
        src = event.get("source", {}) or {}
        token = event.get("replyToken", "")
        if etype == "follow":
            self.line.reply(token, [self._welcome()])
        elif etype == "join":
            self.line.reply(token, [msg.text("大家好!內部窗口群組請輸入 /id 取得群組 ID,填入設定檔的 staff_group_id;輸入 /說明 查看指令。")])
        elif etype == "message":
            m = event.get("message", {}) or {}
            if m.get("type") == "text":
                self._on_text(src, token, str(m.get("text", "")))
            elif src.get("type") == "user" and not self._is_staff(src):
                self.line.reply(token, [msg.text("目前只能讀取文字訊息,請用文字描述品項規格與需求重量。")])
        elif etype == "postback":
            data = {k: v[0] for k, v in parse_qs((event.get("postback", {}) or {}).get("data", "")).items()}
            self._on_postback(src, token, data)

    def _is_staff(self, src: dict) -> bool:
        if src.get("type") == "group":
            return bool(self.s.staff_group_id) and src.get("groupId") == self.s.staff_group_id
        return src.get("type") == "user" and src.get("userId") in self.s.staff_user_ids

    def _on_text(self, src: dict, token: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.lower() in ("/id", "/id"):
            ids = [f"{k}: {v}" for k, v in src.items() if k.endswith("Id")]
            self.line.reply(token, [msg.text("此聊天室:\n" + "\n".join(ids))])
            return
        if self._is_staff(src) and text.startswith(("/", "/")):
            self._staff(src, token, text[1:].strip())
            return
        if src.get("type") != "user":
            return  # other groups / rooms: stay quiet
        self._customer_text(src["userId"], token, text)

    def _on_postback(self, src: dict, token: str, data: dict) -> None:
        action, inquiry_id = data.get("action", ""), data.get("id", "").upper()
        if action == "claim" and self._is_staff(src):
            self.line.reply(token, [self._claim(inquiry_id, self._actor(src))])
            return
        user_id = src.get("userId", "")
        if src.get("type") != "user" or not user_id:
            return
        if action == "submit":
            self._submit(user_id, token)
        elif action == "edit":
            self.line.reply(token, [msg.text("請直接輸入要修改的內容,例如「重量改 80 公斤」「交期 12/15」「品項改 銅靶 6N」,"
                                             "或輸入「取消」放棄這筆需求。")])
        elif action == "discard":
            self.store.clear_draft(user_id)
            self.line.reply(token, [msg.text("已取消這筆需求。有新的需求隨時傳訊息給我。")])
        elif action in ("accept", "decline"):
            self._customer_answer(user_id, token, inquiry_id, action == "accept")

    # ------------------------------------------------------------ customer side

    def _welcome(self) -> dict:
        return msg.text(
            f"您好,這裡是{self.s.company_name}訂單詢價服務。\n"
            "請直接傳送品項規格與需求重量,例如:\n「鈦靶 4N5 Φ300x6.35mm 50公斤,11/20前交貨,送新竹」\n"
            "窗口會回覆報價與交期。輸入「我的詢價」可查詢進度。",
            quick=[("我的詢價", "我的詢價"), ("說明", "說明")],
        )

    def _customer_text(self, user_id: str, token: str, text: str) -> None:
        compact = text.replace(" ", "")
        if compact.lower() in HELP_WORDS:
            self.line.reply(token, [self._welcome()])
            return
        if compact in LIST_WORDS:
            self.line.reply(token, [self._customer_list(user_id)])
            return
        m = re.match(r"^取消\s*(Q\d{8}-\d{3,})$", text, re.I)
        if m:
            self.line.reply(token, [self._customer_cancel(user_id, m.group(1).upper())])
            return
        if compact in DISCARD_WORDS:
            self.store.clear_draft(user_id)
            self.line.reply(token, [msg.text("已清除尚未送出的需求。")])
            return
        if compact in SUBMIT_WORDS:
            self._submit(user_id, token)
            return

        # A staff question is waiting on this customer: treat the message as the answer.
        waiting = self.store.list(("need_info",), user_id=user_id, limit=1)
        if waiting:
            self._forward_answer(waiting[0], token, text)
            return

        draft = self.store.get_draft(user_id) or OrderFields()
        found, is_order = extract_order(text, draft, self.now().date(), self.extractor)
        if not is_order:
            if draft.is_empty():
                self.line.reply(token, [self._welcome()])
            else:
                self.line.reply(token, self._draft_prompt(draft))
            return
        merged = draft.merged(found)
        self.store.save_draft(user_id, merged, self.stamp())
        self.line.reply(token, self._draft_prompt(merged))

    def _draft_prompt(self, f: OrderFields) -> list[dict]:
        missing = f.missing()
        if missing:
            known = _fields_text(f)
            body = (("目前記錄:\n" + known + "\n\n") if known else "") + "還需要:" + "、".join(missing) + "。\n"
            if "品項規格" in missing:
                body += "品項請寫材質、純度、型態與尺寸,例如「鈦靶 4N5 Φ300x6.35mm」。\n"
            if "需求重量" in missing:
                body += "重量請加單位,例如「50 公斤」「2 噸」「500 g」。"
            return [msg.text(body.strip(), quick=[("取消", "取消")])]
        summary = "請確認以下詢價內容:\n" + _fields_text(f)
        if self.s.min_order_kg and f.weight_kg and f.weight_kg < self.s.min_order_kg:
            summary += f"\n\n※ 最小訂購量為 {format_kg(self.s.min_order_kg)},窗口會另行與您確認。"
        if not f.due_text:
            summary += "\n\n(可補充希望交期,例如「交期 11/30」,未提供則由窗口回覆最快交期。)"
        return [
            msg.text(summary),
            msg.confirm("請確認詢價內容", "內容正確請按「送出」,要修改請按「修改」。",
                        ("送出", "action=submit"), ("修改", "action=edit")),
        ]

    def _submit(self, user_id: str, token: str) -> None:
        draft = self.store.get_draft(user_id)
        if not draft or draft.is_empty():
            self.line.reply(token, [msg.text("目前沒有待送出的需求,請先傳送品項規格與需求重量。")])
            return
        if draft.missing():
            self.line.reply(token, self._draft_prompt(draft))
            return
        name = self.line.display_name(user_id) or "客戶"
        now = self.now()
        inquiry_id = self.store.create_inquiry(user_id, name, draft, now.strftime("%Y%m%d"), self.stamp())
        self.store.clear_draft(user_id)
        self.line.reply(token, [msg.text(
            f"已收到您的詢價,編號 {inquiry_id}。\n窗口確認後會在這裡回覆報價與交期。\n"
            "輸入「我的詢價」可查詢進度,輸入「取消 " + inquiry_id + "」可取消。",
            quick=[("我的詢價", "我的詢價")],
        )])
        row = self.store.get(inquiry_id)
        self._notify_staff([msg.text(
            "🆕 新詢價 " + _inquiry_text(row) + f"\n\n回覆報價:/報價 {inquiry_id} 單價 {self.s.default_currency}/kg 交期 日期",
            quick=[("認領", f"action=claim&id={inquiry_id}"), ("明細", f"/查 {inquiry_id}")],
        )])

    def _customer_list(self, user_id: str) -> dict:
        rows = self.store.list(user_id=user_id, limit=10)
        if not rows:
            return msg.text("目前沒有詢價紀錄。請傳送品項規格與需求重量開始詢價。")
        lines = ["您最近的詢價:"]
        for r in rows:
            line = f"\n{r['id']} {r['product']} {format_kg(r['weight_kg'])}\n狀態:{STATUS_LABELS.get(r['status'], r['status'])}"
            if r["status"] in ("quoted", "accepted", "confirmed") and r["price"] is not None:
                line += f"\n報價:{_price_text(r)};交期:{r['lead_time']}"
            lines.append(line)
        return msg.text("\n".join(lines))

    def _customer_cancel(self, user_id: str, inquiry_id: str) -> dict:
        row = self.store.get(inquiry_id)
        if not row or row["user_id"] != user_id:
            return msg.text(f"找不到詢價 {inquiry_id}。")
        if row["status"] not in ("new", "claimed", "need_info", "quoted"):
            return msg.text(f"{inquiry_id} 目前狀態為「{STATUS_LABELS[row['status']]}」,無法線上取消,請與窗口聯繫。")
        self.store.update(inquiry_id, self.stamp(), user_id, "cancelled", status="cancelled")
        self._notify_staff([msg.text(f"⚪ 客戶取消詢價 {inquiry_id}({row['customer_name']},{row['product']})")])
        return msg.text(f"已取消詢價 {inquiry_id}。")

    def _forward_answer(self, row: dict, token: str, text: str) -> None:
        status = "claimed" if row["assignee"] else "new"
        note = (row["note"] + ";" if row["note"] else "") + "客戶補充:" + text[:500]
        self.store.update(row["id"], self.stamp(), row["user_id"], "customer_reply", text[:500], status=status, note=note)
        self.line.reply(token, [msg.text(f"已將您的回覆轉交窗口({row['id']}),謝謝!")])
        self._notify_staff([msg.text(f"💬 {row['id']} 客戶回覆({row['customer_name']}):\n{text[:1000]}",
                                     quick=[("明細", f"/查 {row['id']}")])])

    def _customer_answer(self, user_id: str, token: str, inquiry_id: str, accept: bool) -> None:
        row = self.store.get(inquiry_id)
        if not row or row["user_id"] != user_id:
            self.line.reply(token, [msg.text(f"找不到詢價 {inquiry_id}。")])
            return
        if row["status"] != "quoted":
            self.line.reply(token, [msg.text(f"{inquiry_id} 目前狀態為「{STATUS_LABELS[row['status']]}」。")])
            return
        today = self.now().date().isoformat()
        if accept and row["valid_until"] and row["valid_until"] < today:
            self.store.update(inquiry_id, self.stamp(), user_id, "expired", status="expired")
            self.line.reply(token, [msg.text(f"{inquiry_id} 的報價已於 {row['valid_until']} 到期,已請窗口重新報價。")])
            self._notify_staff([msg.text(f"⏰ {inquiry_id} 客戶想接受報價但報價已逾期,請重新報價:/報價 {inquiry_id} …")])
            return
        if accept:
            self.store.update(inquiry_id, self.stamp(), user_id, "accepted", status="accepted")
            self.line.reply(token, [msg.text(f"感謝您接受報價({inquiry_id})!窗口會再與您確認訂單、付款與出貨細節。")])
            self._notify_staff([msg.text(
                f"✅ 客戶接受報價 {inquiry_id}({row['customer_name']},{row['product']} {format_kg(row['weight_kg'])},"
                f"{_price_text(row)})\n確認訂單後請輸入:/成交 {inquiry_id}")])
        else:
            self.store.update(inquiry_id, self.stamp(), user_id, "declined", status="declined")
            self.line.reply(token, [msg.text("已收到,謝謝您。若需要調整重量或交期,直接傳新的需求給我即可重新詢價。")])
            self._notify_staff([msg.text(f"❌ 客戶婉拒報價 {inquiry_id}({row['customer_name']},{_price_text(row)})")])

    # ------------------------------------------------------------ staff side

    def _actor(self, src: dict) -> str:
        uid = src.get("userId", "")
        return (self.line.display_name(uid, src.get("groupId", "")) if uid else "") or (uid[-6:] if uid else "窗口")

    def _staff(self, src: dict, token: str, command: str) -> None:
        cmd, _, rest = command.partition(" ")
        cmd, rest = cmd.strip().lower(), rest.strip()
        m = ID_RE.match(rest)
        inquiry_id = m.group(0).upper() if m else ""
        args = rest[m.end():].strip() if m else rest
        actor = self._actor(src)

        if cmd in ("說明", "说明", "help", "指令"):
            out = msg.text(STAFF_HELP)
        elif cmd in ("清單", "清单", "list"):
            out = self._staff_list(all_recent=rest in ("全部", "all"))
        elif cmd not in ID_COMMANDS:
            out = msg.text(f"不認得的指令「/{cmd}」。輸入 /說明 查看所有指令。")
        elif not inquiry_id:
            out = msg.text("請在指令後加上詢價編號,例如 /查 Q20261026-001。輸入 /說明 查看所有指令。")
        elif not self.store.get(inquiry_id):
            out = msg.text(f"找不到詢價 {inquiry_id}。")
        elif cmd in ("查", "show"):
            out = self._staff_detail(inquiry_id)
        elif cmd in ("認領", "认领", "claim"):
            out = self._claim(inquiry_id, actor)
        elif cmd in ("報價", "报价", "quote"):
            out = self._quote(inquiry_id, args, actor)
        elif cmd in ("詢問", "询问", "ask"):
            out = self._ask(inquiry_id, args, actor)
        elif cmd in ("婉拒", "reject"):
            out = self._reject(inquiry_id, args, actor)
        elif cmd in ("成交", "confirm"):
            out = self._confirm(inquiry_id, actor)
        elif cmd in ("備註", "备注", "note"):
            if not args:
                out = msg.text("請輸入備註內容。")
            else:
                self.store.update(inquiry_id, self.stamp(), actor, "note", args)
                out = msg.text(f"已記錄 {inquiry_id} 內部備註。")
        self.line.reply(token, [out])

    def _staff_list(self, all_recent: bool = False) -> dict:
        rows = self.store.list(None if all_recent else OPEN_STATUSES, limit=20)
        if not rows:
            return msg.text("目前沒有未結案的詢價。" if not all_recent else "目前沒有詢價紀錄。")
        lines = ["最近 20 筆詢價:" if all_recent else "未結案詢價:"]
        for r in rows:
            who = f"[{r['assignee']}]" if r["assignee"] else ""
            lines.append(f"{r['id']} {STATUS_LABELS.get(r['status'], r['status'])}{who} {r['customer_name']}|"
                         f"{r['product']} {format_kg(r['weight_kg'])}|交期 {r['due_date'] or r['due_text'] or '未指定'}")
        return msg.text("\n".join(lines))

    def _staff_detail(self, inquiry_id: str) -> dict:
        row = self.store.get(inquiry_id)
        body = _inquiry_text(row) + f"\n狀態:{STATUS_LABELS.get(row['status'], row['status'])}"
        if row["assignee"]:
            body += f"\n負責窗口:{row['assignee']}"
        if row["price"] is not None:
            body += f"\n報價:{_price_text(row)};交期:{_lead_text(row)};有效至 {row['valid_until']}"
        events = self.store.events(inquiry_id)[-8:]
        if events:
            body += "\n\n紀錄:\n" + "\n".join(f"{e['at'][5:16].replace('T', ' ')} {e['kind']} {e['detail'][:60]}".rstrip()
                                             for e in events)
        return msg.text(body)

    def _claim(self, inquiry_id: str, actor: str) -> dict:
        row = self.store.get(inquiry_id)
        if not row:
            return msg.text(f"找不到詢價 {inquiry_id}。")
        if row["status"] not in OPEN_STATUSES:
            return msg.text(f"{inquiry_id} 已結案({STATUS_LABELS[row['status']]})。")
        status = "claimed" if row["status"] == "new" else row["status"]
        self.store.update(inquiry_id, self.stamp(), actor, "claimed", actor, assignee=actor, status=status)
        return msg.text(f"{inquiry_id} 由 {actor} 負責。")

    def _quote(self, inquiry_id: str, args: str, actor: str) -> dict:
        row = self.store.get(inquiry_id)
        if row["status"] in ("accepted", "confirmed", "cancelled"):
            return msg.text(f"{inquiry_id} 目前為「{STATUS_LABELS[row['status']]}」,不能再報價。")
        today = self.now().date()
        q = parse_quote(args, self.s.default_currency, self.s.quote_valid_days, today)
        if isinstance(q, str):
            return msg.text(q + "\n格式:/報價 ID 單價 幣別/kg 交期 日期或天數 [有效 N天] [備註 文字]")
        total = _total(q["price"], q["price_unit"], row["weight_kg"])
        self.store.update(
            inquiry_id, self.stamp(), actor, "quoted", args, status="quoted", price=q["price"], currency=q["currency"],
            price_unit=q["price_unit"], total=total, lead_time=q["lead_time"], lead_date=q["lead_date"],
            valid_until=q["valid_until"], quote_note=q["note"], quoted_at=self.stamp(),
            assignee=row["assignee"] or actor,
        )
        row = self.store.get(inquiry_id)
        lines = [
            f"【報價通知】{inquiry_id}",
            f"品項:{row['product']}",
            f"重量:{format_kg(row['weight_kg'])}",
            f"報價:{_price_text(row)}",
        ]
        if row["total"] is not None and row["price_unit"] != "total":
            lines.append(f"預估總價:{row['currency']} {row['total']:,.0f}")
        lines.append(f"交期:{_lead_text(row)}")
        if row["due_date"] and row["lead_date"] and row["lead_date"] > row["due_date"]:
            lines.append(f"(晚於您希望的 {row['due_date']})")
        lines.append(f"報價有效至:{row['valid_until']}")
        if row["quote_note"]:
            lines.append(f"備註:{row['quote_note']}")
        self.line.push(row["user_id"], [
            msg.text("\n".join(lines)),
            msg.confirm(f"報價 {inquiry_id}", f"是否接受 {inquiry_id} 的報價?",
                        ("接受報價", f"action=accept&id={inquiry_id}"), ("婉拒", f"action=decline&id={inquiry_id}")),
        ])
        late = ""
        if row["due_date"] and row["lead_date"] and row["lead_date"] > row["due_date"]:
            late = f"\n⚠ 交期 {row['lead_date']} 晚於客戶希望的 {row['due_date']}"
        return msg.text(f"已將報價送給客戶:{inquiry_id} {_price_text(row)},交期 {_lead_text(row)},"
                        f"有效至 {row['valid_until']}{late}")

    def _ask(self, inquiry_id: str, question: str, actor: str) -> dict:
        row = self.store.get(inquiry_id)
        if not question:
            return msg.text("請輸入要詢問客戶的內容,例如 /詢問 " + inquiry_id + " 請問是否需要背板?")
        if row["status"] not in OPEN_STATUSES:
            return msg.text(f"{inquiry_id} 已結案({STATUS_LABELS[row['status']]})。")
        self.store.update(inquiry_id, self.stamp(), actor, "asked", question, status="need_info",
                          assignee=row["assignee"] or actor)
        self.line.push(row["user_id"], [msg.text(f"【窗口詢問】{inquiry_id}({row['product']})\n{question}\n\n請直接在此回覆,我們會轉交窗口。")])
        return msg.text(f"已轉問客戶({inquiry_id}),客戶回覆會轉到這裡。")

    def _reject(self, inquiry_id: str, reason: str, actor: str) -> dict:
        row = self.store.get(inquiry_id)
        if row["status"] not in OPEN_STATUSES:
            return msg.text(f"{inquiry_id} 已結案({STATUS_LABELS[row['status']]})。")
        self.store.update(inquiry_id, self.stamp(), actor, "rejected", reason, status="rejected", reason=reason)
        body = f"很抱歉,您的詢價 {inquiry_id}({row['product']} {format_kg(row['weight_kg'])})目前無法承接。"
        if reason:
            body += f"\n原因:{reason}"
        self.line.push(row["user_id"], [msg.text(body + "\n如有其他需求歡迎再與我們聯繫。")])
        return msg.text(f"已通知客戶婉拒 {inquiry_id}。")

    def _confirm(self, inquiry_id: str, actor: str) -> dict:
        row = self.store.get(inquiry_id)
        if row["status"] != "accepted":
            return msg.text(f"{inquiry_id} 目前為「{STATUS_LABELS[row['status']]}」,客戶接受報價後才能成交。")
        self.store.update(inquiry_id, self.stamp(), actor, "confirmed", status="confirmed")
        amount = _price_text(row)
        if row["total"] is not None and row["price_unit"] != "total":
            amount += f",總價 {row['currency']} {row['total']:,.0f}"
        self.line.push(row["user_id"], [msg.text(
            f"🎉 訂單成立:{inquiry_id}\n品項:{row['product']}\n重量:{format_kg(row['weight_kg'])}\n"
            f"金額:{amount}\n交期:{_lead_text(row)}\n窗口 {actor} 會與您聯繫出貨事宜,感謝您的訂購!")])
        return msg.text(f"{inquiry_id} 訂單成立,已通知客戶。")

    # ------------------------------------------------------------ staff notifications

    def _notify_staff(self, messages: list[dict]) -> None:
        targets = self.s.staff_targets
        if not targets:
            log.warning("no staff_group_id / staff_user_ids configured; staff notification dropped")
        for t in targets:
            self.line.push(t, messages)

    def remind(self) -> int:
        """Push staff a list of inquiries waiting too long for a quote; mark lapsed quotes expired."""
        now = self.now()
        cutoff = (now - timedelta(hours=self.s.remind_after_hours)).isoformat(timespec="seconds")
        today = now.date().isoformat()
        for r in self.store.list(("quoted",), limit=500):
            if r["valid_until"] and r["valid_until"] < today:
                self.store.update(r["id"], self.stamp(), "system", "expired", status="expired")
        due = [r for r in self.store.list(("new", "claimed"), limit=500)
               if r["created_at"] <= cutoff and (not r["reminded_at"] or r["reminded_at"] <= cutoff)]
        if not due:
            return 0
        lines = [f"⏰ 以下詢價超過 {self.s.remind_after_hours:g} 小時尚未報價:"]
        for r in sorted(due, key=lambda r: r["created_at"]):
            who = f"[{r['assignee']}]" if r["assignee"] else "[未認領]"
            lines.append(f"{r['id']} {who} {r['customer_name']}|{r['product']} {format_kg(r['weight_kg'])}")
            self.store.update(r["id"], self.stamp(), reminded_at=self.stamp())
        self._notify_staff([msg.text("\n".join(lines))])
        return len(due)


# ---------------------------------------------------------------- formatting / parsing helpers

UNIT_LABELS = {"kg": "/kg", "g": "/g", "t": "/公噸", "total": "(總價)"}


def _fields_text(f: OrderFields) -> str:
    rows = []
    if f.product:
        rows.append(f"品項:{f.product}")
    if f.weight_kg:
        rows.append(f"重量:{format_kg(f.weight_kg)}")
    if f.due_text or f.due_date:
        rows.append("交期:" + (f.due_date + (f"({f.due_text})" if f.due_text else "") if f.due_date else f.due_text))
    if f.destination:
        rows.append(f"交貨地:{f.destination}")
    if f.company:
        rows.append(f"公司:{f.company}")
    if f.note:
        rows.append(f"備註:{f.note}")
    return "\n".join(rows)


def _inquiry_text(row: dict) -> str:
    f = OrderFields(product=row["product"], weight_kg=row["weight_kg"], due_date=row["due_date"],
                    due_text=row["due_text"], destination=row["destination"], company=row["company"], note=row["note"])
    return f"{row['id']}\n客戶:{row['customer_name']}\n" + _fields_text(f)


def _price_text(row: dict) -> str:
    if row.get("price") is None:
        return "-"
    return f"{row['currency']} {row['price']:,.6g}{UNIT_LABELS.get(row['price_unit'], '')}"


def _lead_text(row: dict) -> str:
    lead, d = row["lead_time"] or "-", row["lead_date"]
    return f"{lead}(約 {d})" if d and d not in lead else lead


def _total(price: float, unit: str, weight_kg: float | None) -> float | None:
    if unit == "total":
        return price
    if not weight_kg:
        return None
    return round(price * weight_kg * {"kg": 1.0, "g": 1000.0, "t": 0.001}[unit], 2)


_Q_PRICE = re.compile(
    r"^(?:單價|单价|價格|价格)?\s*([A-Za-z]{3})?\s*([\d,]+(?:\.\d+)?)\s*([A-Za-z]{3})?\s*"
    r"(?:(?:/|/|每)\s*(kg|公斤|g|公克|克|t|噸|公噸)|(總價|总价|total))?",
    re.I,
)
_Q_KEYS = re.compile(r"(交期|有效(?:期)?|備註|备注)\s*[::]?\s*")
_UNIT_NORM = {"kg": "kg", "公斤": "kg", "g": "g", "公克": "g", "克": "g", "t": "t", "噸": "t", "公噸": "t"}


def parse_quote(args: str, default_currency: str, valid_days: int, today) -> dict | str:
    """Parse the text after `/報價 ID`. Returns the quote fields or an error message."""
    m = _Q_PRICE.match(args.strip())
    if not m or not m.group(2):
        return "找不到單價。"
    price = float(m.group(2).replace(",", ""))
    if price <= 0:
        return "單價必須大於 0。"
    currency = (m.group(1) or m.group(3) or default_currency).upper()
    unit = "total" if m.group(5) else _UNIT_NORM.get((m.group(4) or "kg").lower(), "kg")

    parts: dict[str, str] = {}
    keys = list(_Q_KEYS.finditer(args))
    for i, k in enumerate(keys):
        end = keys[i + 1].start() if i + 1 < len(keys) else len(args)
        name = {"交期": "lead", "備註": "note", "备注": "note"}.get(k.group(1), "valid")
        parts[name] = args[k.end():end].strip()
    lead = parts.get("lead", "")
    if not lead:
        return "缺少交期(例如「交期 2026-11-20」或「交期 14天」)。"
    lead_date = ""
    if re.fullmatch(r"\d+\s*(?:個)?(?:工作)?天", lead):  # "14天" = within 14 days of order
        lead_date, _, _ = parse_due(lead + "內", today)
    else:
        lead_date, _, _ = parse_due(lead, today)
    valid = parts.get("valid", "")
    vm = re.match(r"(\d+)\s*天", valid)
    if vm:
        valid_until = (today + timedelta(days=int(vm.group(1)))).isoformat()
    else:
        valid_until = parse_due(valid, today)[0] if valid else ""
        valid_until = valid_until or (today + timedelta(days=valid_days)).isoformat()
    return {"price": price, "currency": currency, "price_unit": unit, "lead_time": lead, "lead_date": lead_date,
            "valid_until": valid_until, "note": parts.get("note", "")}

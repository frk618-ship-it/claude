import base64
import hashlib
import hmac
import json
import queue
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from line_order_bot.bot import OrderBot, parse_quote
from line_order_bot.config import Settings
from line_order_bot.line_api import RecordingLine, verify_signature
from line_order_bot.parser import ClaudeExtractor, OrderExtract, OrderFields, extract_order, parse_rules
from line_order_bot.server import make_handler, worker
from line_order_bot.store import Store

TZ = ZoneInfo("Asia/Taipei")
TODAY = date(2026, 10, 26)
CUSTOMER = "U_customer"
STAFF = "U_staff"
GROUP = "C_staff"
QID = "Q20261026-001"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 26, 9, 0, tzinfo=TZ)

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    clock = Clock()
    line = RecordingLine(names={CUSTOMER: "王小明", STAFF: "小陳"})
    settings = Settings(staff_group_id=GROUP, company_name="測試材料")
    bot = OrderBot(settings, Store(":memory:"), line, clock=clock)
    return NS(bot=bot, line=line, store=bot.store, clock=clock)


def customer_says(bot, text):
    bot.handle({"type": "message", "replyToken": "r", "source": {"type": "user", "userId": CUSTOMER},
                "message": {"type": "text", "text": text}})


def customer_presses(bot, data):
    bot.handle({"type": "postback", "replyToken": "r", "source": {"type": "user", "userId": CUSTOMER},
                "postback": {"data": data}})


def staff_says(bot, text):
    bot.handle({"type": "message", "replyToken": "r", "source": {"type": "group", "groupId": GROUP, "userId": STAFF},
                "message": {"type": "text", "text": text}})


def last_reply(line):
    return [m.get("text") or m["template"]["text"] for kind, _, msgs in line.sent if kind == "reply" for m in msgs][-1]


def submit_order(env, text="鈦靶 4N5 Φ300x6.35mm 50公斤 11/20前交貨,送到新竹"):
    customer_says(env.bot, text)
    customer_presses(env.bot, "action=submit")
    return env.store.get(QID)


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "text, product, kg, due",
    [
        ("您好,我要訂鈦靶 4N5 Φ300x6.35mm 50公斤 11/20前交貨,送到新竹", "鈦靶 4N5 Φ300x6.35mm", 50, "2026-11-20"),
        ("鎳粉 3N 200 kg 兩週內", "鎳粉 3N", 200, "2026-11-09"),
        ("ITO靶材 99.99% 127x406x6mm 1.5噸 交期 2026/12/31", "ITO靶材 99.99% 127x406x6mm", 1500, "2026-12-31"),
        ("急件 銅靶 6N 300g", "銅靶 6N", 0.3, ""),
        ("鉭粉 3N5 325目 500 lb 10個工作天內", "鉭粉 3N5 325目", 226.796185, "2026-11-09"),
        ("Titanium target 99.995% 1,200 kg by 2027-01-15", "Titanium target 99.995% by", 1200, "2027-01-15"),
        ("重量改 80kg", "", 80, ""),
        ("你好", "", None, ""),
        ("1/5 前要 金靶 5N 2kg", "金靶 5N", 2, "2027-01-05"),
    ],
)
def test_parse_rules(text, product, kg, due):
    f = parse_rules(text, TODAY)
    assert f.product == product
    assert f.weight_kg == kg
    assert f.due_date == due


def test_parse_rules_extra_fields():
    f = parse_rules("鋁靶 5N 30kg 送到台南 公司:光明科技 備註 需附背板", TODAY)
    assert (f.destination, f.company, f.note) == ("台南", "光明科技", "需附背板")
    assert f.product == "鋁靶 5N"


def test_dimensions_are_not_weights():
    f = parse_rules("鈦靶 Φ300x6.35mm", TODAY)
    assert f.weight_kg is None and f.product == "鈦靶 Φ300x6.35mm"


def test_draft_merge_keeps_earlier_fields():
    draft = OrderFields(product="鈦靶 4N5", weight_kg=50, due_text="急件")
    merged = draft.merged(OrderFields(weight_kg=80, weight_text="80 kg"))
    assert (merged.product, merged.weight_kg, merged.due_text) == ("鈦靶 4N5", 80, "急件")
    assert merged.missing() == []


@pytest.mark.parametrize(
    "args, expected",
    [
        ("1250 TWD/kg 交期 2026-11-20", dict(price=1250, currency="TWD", price_unit="kg", lead_date="2026-11-20",
                                              valid_until="2026-11-02")),
        ("USD 38.5/g 交期 14天 有效 3天 備註 含背板", dict(price=38.5, currency="USD", price_unit="g",
                                                       lead_date="2026-11-09", valid_until="2026-10-29", note="含背板")),
        ("單價 1,250,000 總價 交期 12/15", dict(price=1250000, currency="TWD", price_unit="total", lead_date="2026-12-15")),
    ],
)
def test_parse_quote(args, expected):
    q = parse_quote(args, "TWD", 7, TODAY)
    assert isinstance(q, dict)
    for k, v in expected.items():
        assert q[k] == v


@pytest.mark.parametrize("args", ["交期 14天", "1250 TWD/kg", "0 TWD/kg 交期 3天"])
def test_parse_quote_errors(args):
    assert isinstance(parse_quote(args, "TWD", 7, TODAY), str)


# ---------------------------------------------------------------- conversation


def test_full_order_flow(env):
    customer_says(env.bot, "我要詢價鈦靶 4N5 Φ300x6.35mm")
    assert "還需要:需求重量" in last_reply(env.line)
    customer_says(env.bot, "50 公斤,11/20前交貨")
    assert "送出" in last_reply(env.line)
    customer_presses(env.bot, "action=submit")
    assert QID in last_reply(env.line)
    row = env.store.get(QID)
    assert (row["product"], row["weight_kg"], row["due_date"], row["customer_name"]) == \
        ("鈦靶 4N5 Φ300x6.35mm", 50, "2026-11-20", "王小明")
    assert env.store.get_draft(CUSTOMER) is None
    assert any("新詢價" in t for t in env.line.texts(GROUP))

    staff_says(env.bot, f"/認領 {QID}")
    assert env.store.get(QID)["assignee"] == "小陳"
    staff_says(env.bot, f"/報價 {QID} 1250 TWD/kg 交期 2026-11-25 備註 含運")
    row = env.store.get(QID)
    assert (row["status"], row["total"], row["valid_until"]) == ("quoted", 62500, "2026-11-02")
    to_customer = "\n".join(env.line.texts(CUSTOMER))
    assert "TWD 1,250/kg" in to_customer and "62,500" in to_customer and "晚於您希望的 2026-11-20" in to_customer
    assert "晚於客戶希望" in last_reply(env.line)

    customer_presses(env.bot, f"action=accept&id={QID}")
    assert env.store.get(QID)["status"] == "accepted"
    assert any("客戶接受報價" in t for t in env.line.texts(GROUP))
    staff_says(env.bot, f"/成交 {QID}")
    assert env.store.get(QID)["status"] == "confirmed"
    assert "訂單成立" in env.line.texts(CUSTOMER)[-1]
    assert [e["kind"] for e in env.store.events(QID)] == ["created", "claimed", "quoted", "accepted", "confirmed"]


def test_staff_question_routes_customer_answer(env):
    submit_order(env)
    staff_says(env.bot, f"/詢問 {QID} 是否需要銅背板?")
    assert env.store.get(QID)["status"] == "need_info"
    assert "是否需要銅背板" in env.line.texts(CUSTOMER)[-1]
    customer_says(env.bot, "需要,謝謝")
    row = env.store.get(QID)
    assert row["status"] == "claimed" and "需要,謝謝" in row["note"]
    assert "需要,謝謝" in env.line.texts(GROUP)[-1]


def test_expired_quote_cannot_be_accepted(env):
    submit_order(env)
    staff_says(env.bot, f"/報價 {QID} 1250 TWD/kg 交期 14天 有效 2天")
    env.clock.t += timedelta(days=3)
    customer_presses(env.bot, f"action=accept&id={QID}")
    assert env.store.get(QID)["status"] == "expired"
    assert "重新報價" in env.line.texts(GROUP)[-1]
    staff_says(env.bot, f"/報價 {QID} 1300 TWD/kg 交期 14天")
    assert env.store.get(QID)["status"] == "quoted"


def test_customer_cancel_and_decline(env):
    submit_order(env)
    customer_says(env.bot, f"取消 {QID}")
    assert env.store.get(QID)["status"] == "cancelled"
    staff_says(env.bot, f"/報價 {QID} 1250 TWD/kg 交期 14天")
    assert "不能再報價" in last_reply(env.line)

    submit_order(env, "銅靶 6N 20kg")
    qid2 = "Q20261026-002"
    staff_says(env.bot, f"/報價 {qid2} 900 TWD/kg 交期 7天")
    customer_presses(env.bot, f"action=decline&id={qid2}")
    assert env.store.get(qid2)["status"] == "declined"


def test_customers_cannot_touch_other_inquiries(env):
    submit_order(env)
    staff_says(env.bot, f"/報價 {QID} 1250 TWD/kg 交期 14天")
    env.bot.handle({"type": "postback", "replyToken": "r", "source": {"type": "user", "userId": "U_other"},
                    "postback": {"data": f"action=accept&id={QID}"}})
    assert env.store.get(QID)["status"] == "quoted"
    # Staff commands from outside the staff group are not accepted.
    env.bot.handle({"type": "message", "replyToken": "r", "source": {"type": "user", "userId": "U_other"},
                    "message": {"type": "text", "text": f"/成交 {QID}"}})
    assert env.store.get(QID)["status"] == "quoted"


def test_staff_reject_and_unknown_commands(env):
    submit_order(env)
    staff_says(env.bot, "/xyz")
    assert "不認得" in last_reply(env.line)
    staff_says(env.bot, "/查 Q20990101-001")
    assert "找不到" in last_reply(env.line)
    staff_says(env.bot, f"/婉拒 {QID} 此尺寸目前無法生產")
    assert env.store.get(QID)["status"] == "rejected"
    assert "此尺寸目前無法生產" in env.line.texts(CUSTOMER)[-1]


def test_remind_lists_stale_inquiries_once(env):
    submit_order(env)
    assert env.bot.remind() == 0
    env.clock.t += timedelta(hours=5)
    assert env.bot.remind() == 1
    assert QID in env.line.texts(GROUP)[-1]
    assert env.bot.remind() == 0  # already reminded within the window
    env.clock.t += timedelta(hours=5)
    assert env.bot.remind() == 1


def test_greeting_without_order_gets_welcome(env):
    customer_says(env.bot, "你好")
    assert "測試材料" in last_reply(env.line)
    assert env.store.get_draft(CUSTOMER) is None


# ---------------------------------------------------------------- Claude extraction


class FakeMessages:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return NS(stop_reason="end_turn", parsed_output=self.parsed)


def test_claude_extraction_wins_and_rules_fill_gaps():
    parsed = OrderExtract(is_order_request=True, product="高純鈦靶 4N5 Φ300x6.35mm", weight_kg=50.0,
                          weight_text="五十公斤", due_date="", due_text="")
    fake = NS(beta=NS(messages=FakeMessages(parsed)))
    extractor = ClaudeExtractor(model="claude-opus-5-5", client=fake)
    fields, is_order = extract_order("想訂高純鈦靶 4N5 Φ300x6.35mm 五十公斤,11/20前", OrderFields(), TODAY, extractor)
    assert is_order
    assert fields.product == "高純鈦靶 4N5 Φ300x6.35mm" and fields.weight_kg == 50
    assert fields.due_date == "2026-11-20"  # rules caught the date Claude left out
    call = fake.beta.messages.calls[0]
    assert call["output_format"] is OrderExtract and call["model"] == "claude-opus-5-5"
    assert "2026-10-26" in call["messages"][0]["content"]


# ---------------------------------------------------------------- webhook


def sign(secret, body):
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_verify_signature():
    body = b'{"events":[]}'
    assert verify_signature("s3cret", body, sign("s3cret", body))
    assert not verify_signature("s3cret", body, sign("other", body))
    assert not verify_signature("", body, "")


def test_webhook_server(env):
    jobs = queue.Queue()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(env.bot, "s3cret", jobs))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=worker, args=(env.bot, jobs), daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/callback"
    event = {"type": "message", "replyToken": "r", "source": {"type": "user", "userId": CUSTOMER},
             "message": {"type": "text", "text": "銅靶 6N 20kg"}}
    body = json.dumps({"events": [event]}).encode()
    try:
        bad = urllib.request.Request(url, data=body, headers={"X-Line-Signature": "nope"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad)
        assert exc.value.code == 401
        good = urllib.request.Request(url, data=body, headers={"X-Line-Signature": sign("s3cret", body)})
        assert urllib.request.urlopen(good).status == 200
        jobs.join()
        assert env.store.get_draft(CUSTOMER).weight_kg == 20
    finally:
        httpd.shutdown()
        jobs.put(None)

"""Email subscriptions kept in the sending mailbox itself.

The site's "訂閱通知" button opens the visitor's mail program with a message to the alert mailbox
(the SMTP account), subject "[TTMC-NOTIFY] SUBSCRIBE" and the address to add in the body. Sending
"[TTMC-NOTIFY] UNSUBSCRIBE" takes it off again. Nothing is stored anywhere else: the mailbox is the
list. Every notification reads it over IMAP (same account and password as SMTP), replays the requests
in the order they arrived, and Bcc's the subscribers next to ALERT_EMAIL_TO, so subscribers never see
each other's addresses. The hourly `python -m metal_alert subscriptions` job answers each new request
with a confirmation; which ones were answered is remembered in a small state file (Actions cache), so
reading the mailbox in a mail program changes nothing.

IMAP_HOST is optional: smtp.gmail.com -> imap.gmail.com, smtp.office365.com -> outlook.office365.com,
otherwise "smtp." is swapped for "imap.". IMAP_PORT defaults to 993 (SSL). Reading the list is switched
on with SUBSCRIPTIONS=on (the workflows set it), so local runs and tests never touch the mailbox.
"""

from __future__ import annotations

import email
import imaplib
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from email.policy import default as default_policy
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

log = logging.getLogger(__name__)

TOKEN = "[TTMC-NOTIFY]"
SUBSCRIBE, UNSUBSCRIBE = "SUBSCRIBE", "UNSUBSCRIBE"
ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FIELD_RE = re.compile(r"(?:訂閱信箱|取消信箱|email)\s*[::]\s*(" + ADDRESS_RE.pattern + ")", re.I)
IMAP_HOSTS = {"smtp.gmail.com": "imap.gmail.com", "smtp.office365.com": "outlook.office365.com",
              "smtp-mail.outlook.com": "outlook.office365.com"}


def mailbox_address(env) -> str:
    """The address people write to: the SMTP login (the inbox this module reads), else ALERT_EMAIL_FROM's address."""
    for value in (env.get("SMTP_USER", "").strip(), env.get("ALERT_EMAIL_FROM", "").strip()):
        addr = parseaddr(value)[1]
        m = ADDRESS_RE.search(addr if ADDRESS_RE.fullmatch(addr or "") else value)
        if m:
            return m.group(0)
    return ""


def imap_host(env) -> str:
    if env.get("IMAP_HOST", "").strip():
        return env["IMAP_HOST"].strip()
    smtp = env.get("SMTP_HOST", "").strip().lower()
    return IMAP_HOSTS.get(smtp) or (smtp.replace("smtp.", "imap.", 1) if smtp.startswith("smtp.") else smtp)


def reading_on(env) -> bool:
    return env.get("SUBSCRIPTIONS", "").strip().lower() in ("1", "on", "true")


def enabled(env) -> bool:
    return bool(env.get("SMTP_HOST", "").strip() and env.get("SMTP_USER", "").strip() and env.get("SMTP_PASSWORD", ""))


def parse_request(raw: bytes, own: str = "") -> tuple[str, str] | None:
    """(action, address) from one request mail, or None when it is not a valid request."""
    msg = email.message_from_bytes(raw, policy=default_policy)
    subject = str(msg.get("Subject", "")).upper()
    if TOKEN not in subject:
        return None
    action = UNSUBSCRIBE if UNSUBSCRIBE in subject else SUBSCRIBE if SUBSCRIBE in subject else ""
    if not action:
        return None
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    m = FIELD_RE.search(text)
    addr = m.group(1) if m else ""
    if not addr:
        senders = [a for _, a in getaddresses([str(msg.get("Reply-To", "")), str(msg.get("From", ""))]) if a]
        addr = senders[0] if senders else ""
    addr = addr.strip().lower()
    if not ADDRESS_RE.fullmatch(addr) or len(addr) > 254 or addr == own.lower():
        return None
    return action, addr


def _search(box, criteria: str) -> list[bytes]:
    status, data = box.uid("SEARCH", None, criteria)
    return data[0].split() if status == "OK" and data and data[0] else []


def _fetch(box, uid: bytes) -> bytes:
    status, data = box.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: reading does not mark it read
    for part in data or []:
        if isinstance(part, tuple) and len(part) > 1:
            return part[1]
    return b""


LOOKBACK_DAYS = 400   # requests older than this are no longer read (re-subscribing renews them)
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_UID_RE = re.compile(rb"UID (\d+)")


def _uidvalidity(box) -> str:
    try:
        typ, data = box.response("UIDVALIDITY")
        return (data[0] or b"").decode() if data and data[0] else ""
    except Exception:
        return ""


def _candidates(box, since: date) -> list[bytes]:
    """UIDs whose subject carries TOKEN. Subjects are matched here, not by the server's SEARCH,
    which (Gmail especially) tokenises "[TTMC-NOTIFY]" and misses it."""
    uids = _search(box, f"SINCE {since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}")
    hits = []
    for i in range(0, len(uids), 300):
        status, data = box.uid("FETCH", b",".join(uids[i:i + 300]), "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
        for part in data or []:
            if not (isinstance(part, tuple) and len(part) > 1):
                continue
            m = _UID_RE.search(part[0] or b"")
            subject = str(email.message_from_bytes(part[1] or b"", policy=default_policy).get("Subject", ""))
            if m and TOKEN in subject.upper():
                hits.append(m.group(1))
    return sorted(set(hits), key=int)


_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) "?(?P<sep>[^" ]*)"? (?P<name>.+)$')


def _folders(box) -> list[str]:
    """INBOX, plus the "all mail" and "junk" folders when the server marks them (RFC 6154; Gmail does):
    a request sent from the mailbox to itself may sit only in Sent / All Mail, and filters may file it as spam."""
    found = {"all": "", "junk": ""}
    try:
        status, rows = box.list()
    except Exception:
        rows = []
    for row in rows or []:
        m = _LIST_RE.match(row if isinstance(row, bytes) else b"")
        if not m:
            continue
        flags, name = m.group("flags").lower(), m.group("name").strip()
        for key, flag in (("all", b"\\all"), ("junk", b"\\junk")):
            if flag in flags and not found[key]:
                found[key] = name.decode("utf-8", "replace")
    names = [found["all"] or "INBOX"] + ([found["junk"]] if found["junk"] else [])
    return [n if n.startswith('"') else f'"{n}"' if " " in n else n for n in names]


def read_requests(env, imap_factory=imaplib.IMAP4_SSL, today: date | None = None) -> list[tuple[str, str, str]]:
    """[(key, action, address)] in the order the requests were sent. key identifies the message
    (its Message-ID, else folder + UIDVALIDITY + UID), so one mail seen in two folders counts once."""
    box = imap_factory(imap_host(env), int(env.get("IMAP_PORT", "").strip() or 993))
    box.login(env["SMTP_USER"].strip(), env["SMTP_PASSWORD"])
    try:
        own = mailbox_address(env)
        since = (today or date.today()) - timedelta(days=LOOKBACK_DAYS)
        found: dict[str, tuple] = {}
        for folder in _folders(box):
            try:
                status, _ = box.select(folder, readonly=True)
            except Exception as exc:
                log.warning("cannot open folder %s: %s", folder, exc)
                continue
            if status != "OK":
                continue
            validity = _uidvalidity(box)
            uids = _candidates(box, since)
            log.info("folder %s: %d message(s) with %s in the subject", folder, len(uids), TOKEN)
            for uid in uids:
                raw = _fetch(box, uid)
                req = parse_request(raw, own)
                if not req:
                    continue
                msg = email.message_from_bytes(raw, policy=default_policy)
                key = str(msg.get("Message-ID", "")).strip() or f"{folder}:{validity}:{uid.decode()}"
                try:
                    when = parsedate_to_datetime(str(msg.get("Date", ""))).timestamp()
                except Exception:
                    when = 0.0
                found.setdefault(key, (when, len(found), key, *req))
        return [(key, action, addr) for _, _, key, action, addr in sorted(found.values())]
    finally:
        try:
            box.logout()
        except Exception:
            pass


def current(requests: list[tuple[bytes, str, str]]) -> tuple[set[str], set[str]]:
    """(subscribed, unsubscribed) after replaying the requests in order; the last word on an address wins."""
    state: dict[str, str] = {}
    for _, action, addr in requests:
        state[addr] = action
    return ({a for a, s in state.items() if s == SUBSCRIBE}, {a for a, s in state.items() if s == UNSUBSCRIBE})


def recipients(env, imap_factory=imaplib.IMAP4_SSL) -> tuple[list[str], list[str]]:
    """(to, bcc): ALERT_EMAIL_TO minus anyone who unsubscribed, and the subscribers. IMAP failures fall back to the group."""
    base = [a.strip() for a in env.get("ALERT_EMAIL_TO", "").replace(";", ",").split(",") if a.strip()]
    if not enabled(env) or not reading_on(env):
        return base, []
    try:
        subscribed, unsubscribed = current(read_requests(env, imap_factory))
    except Exception as exc:
        log.warning("reading subscriptions failed, sending to the group only: %s", exc)
        return base, []
    to = [a for a in base if a.lower() not in unsubscribed]
    known = {a.lower() for a in to}
    return to, sorted(a for a in subscribed if a not in known)


def unsubscribe_note(env) -> str:
    addr = mailbox_address(env)
    return f"取消訂閱:寄信到 {addr},主旨填「{TOKEN} {UNSUBSCRIBE}」。" if addr else ""


CONFIRM = {
    SUBSCRIBE: ("訂閱成功:靶材產業週報與貴金屬價格預警",
                "您好,\n\n{addr} 已加入通知名單,之後會收到:\n・貴金屬價格預警(價格出現進出場訊號時)\n・靶材產業週報(每週一上午)\n\n{note}\n"),
    UNSUBSCRIBE: ("已取消訂閱:靶材產業週報與貴金屬價格預警",
                  "您好,\n\n{addr} 已從通知名單移除,不會再收到週報與貴金屬預警。\n若要重新訂閱,請寄信到 {box},主旨填「" + TOKEN + " " + SUBSCRIBE + "」。\n"),
}


def _load_answered(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def process(env, send_one, imap_factory=imaplib.IMAP4_SSL, state: Path | None = None) -> dict:
    """Answer every request not answered before with a confirmation. Which ones were answered is kept in
    `state` (a JSON list of message keys, carried between runs in the Actions cache), not in the mail's
    read flag, so opening the mailbox in a mail program does not hide a request."""
    state = state or Path(env.get("SUBSCRIPTIONS_STATE", "").strip() or "data/subscriptions/answered.json")
    answered = _load_answered(state)
    requests = read_requests(env, imap_factory)
    new = [r for r in requests if r[0] not in answered]
    for key, action, addr in new:
        subject, body = CONFIRM[action]
        try:
            send_one(env, addr, subject, body.format(addr=addr, note=unsubscribe_note(env), box=mailbox_address(env)))
            answered.add(key)
            log.info("%s %s", action, _mask(addr))
        except Exception as exc:
            log.warning("confirmation to %s failed (retried next run): %s", _mask(addr), exc)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(sorted(answered)), encoding="utf-8")
    subscribed, _ = current(requests)
    return {"requests": len(requests), "answered": sum(1 for r in new if r[0] in answered), "subscribers": len(subscribed)}


def _mask(addr: str) -> str:
    """a***@example.com: enough to tell requests apart in the public Actions log."""
    name, _, domain = addr.partition("@")
    return f"{name[:1]}***@{domain}"

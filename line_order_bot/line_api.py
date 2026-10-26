"""LINE Messaging API: webhook signature check, reply / push, and message builders (standard library only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from urllib.parse import quote

log = logging.getLogger(__name__)

API = "https://api.line.me/v2/bot"
MAX_TEXT = 5000
MAX_MESSAGES = 5  # per reply / push request


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), signature)


# ---------------------------------------------------------------- message builders


def text(body: str, quick: list[tuple[str, str]] | None = None) -> dict:
    """Text message; `quick` is [(label, text-or-postback)] where postback data starts with 'action='."""
    msg: dict = {"type": "text", "text": body[:MAX_TEXT]}
    if quick:
        msg["quickReply"] = {"items": [{"type": "action", "action": _action(label, value)} for label, value in quick[:13]]}
    return msg


def confirm(alt: str, body: str, yes: tuple[str, str], no: tuple[str, str]) -> dict:
    """Two-button confirm template; each button is (label, postback data)."""
    return {
        "type": "template",
        "altText": alt[:400],
        "template": {"type": "confirm", "text": body[:240], "actions": [_action(*yes), _action(*no)]},
    }


def _action(label: str, value: str) -> dict:
    label = label[:20]
    if value.startswith("action="):
        return {"type": "postback", "label": label, "data": value, "displayText": label}
    return {"type": "message", "label": label, "text": value}


# ---------------------------------------------------------------- client


class LineClient:
    def __init__(self, access_token: str, timeout: float = 10.0):
        self.token = access_token
        self.timeout = timeout

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"{API}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            log.warning("LINE API %s %s failed: %s %s", method, path, exc.code, exc.read()[:300])
            return {}
        except urllib.error.URLError as exc:
            log.warning("LINE API %s %s failed: %s", method, path, exc.reason)
            return {}
        return json.loads(raw) if raw else {}

    def reply(self, reply_token: str, messages: list[dict]) -> None:
        if reply_token and messages:
            self._call("POST", "/message/reply", {"replyToken": reply_token, "messages": messages[:MAX_MESSAGES]})

    def push(self, to: str, messages: list[dict]) -> None:
        if to and messages:
            self._call("POST", "/message/push", {"to": to, "messages": messages[:MAX_MESSAGES]})

    def display_name(self, user_id: str, group_id: str = "") -> str:
        path = f"/group/{quote(group_id)}/member/{quote(user_id)}" if group_id else f"/profile/{quote(user_id)}"
        return str(self._call("GET", path).get("displayName", ""))


class RecordingLine:
    """Stand-in client for tests and the local simulator: keeps every outgoing message."""

    def __init__(self, names: dict[str, str] | None = None, echo=None):
        self.sent: list[tuple[str, str, list[dict]]] = []  # (kind, target, messages)
        self.names = names or {}
        self.echo = echo

    def reply(self, reply_token: str, messages: list[dict]) -> None:
        self._record("reply", reply_token, messages)

    def push(self, to: str, messages: list[dict]) -> None:
        self._record("push", to, messages)

    def display_name(self, user_id: str, group_id: str = "") -> str:
        return self.names.get(user_id, "")

    def _record(self, kind, target, messages):
        self.sent.append((kind, target, messages))
        if self.echo:
            self.echo(kind, target, messages)

    def texts(self, target: str | None = None) -> list[str]:
        """Plain text of everything sent (to `target` when given), template bodies included."""
        out = []
        for _, t, msgs in self.sent:
            if target is None or t == target:
                out.extend(m.get("text") or m.get("template", {}).get("text", "") for m in msgs)
        return out

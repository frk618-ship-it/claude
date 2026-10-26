"""Webhook server (standard library only). LINE posts events to /callback; they are handled on a worker thread
so the webhook answers 200 right away even while Claude reads a message."""

from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .line_api import verify_signature

log = logging.getLogger(__name__)


def make_handler(bot, channel_secret: str, jobs: "queue.Queue[dict]", path: str = "/callback"):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes = b"OK"):
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (http.server API)
            self._send(200 if self.path in ("/", "/health") else 404, b"OK" if self.path in ("/", "/health") else b"")

        def do_POST(self):  # noqa: N802
            if self.path.split("?")[0] != path:
                return self._send(404, b"")
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, 1_000_000))
            if not verify_signature(channel_secret, body, self.headers.get("X-Line-Signature", "")):
                log.warning("rejected webhook with bad signature from %s", self.address_string())
                return self._send(401, b"bad signature")
            try:
                events = json.loads(body).get("events", [])
            except (ValueError, AttributeError):
                return self._send(400, b"bad json")
            for event in events:
                jobs.put(event)
            self._send(200)

        def log_message(self, fmt, *args):
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def worker(bot, jobs: "queue.Queue[dict]") -> None:
    while True:
        event = jobs.get()
        if event is None:
            return
        try:
            bot.handle(event)
        except Exception:  # keep serving after one bad event
            log.exception("failed to handle event %s", event.get("type"))
        finally:
            jobs.task_done()


def serve(bot, channel_secret: str, host: str, port: int, path: str = "/callback") -> None:
    jobs: queue.Queue = queue.Queue()
    threading.Thread(target=worker, args=(bot, jobs), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(bot, channel_secret, jobs, path))
    print(f"LINE webhook:http://{host}:{port}{path}  (Ctrl+C 結束)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

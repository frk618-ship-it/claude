"""Local web module: dashboard + JSON API, with a background thread that re-checks every interval.

    python -m metal_alert serve            # http://127.0.0.1:8770/
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import Config
from .engine import load_snapshot, run_once
from .page import render_page

log = logging.getLogger(__name__)


class Monitor:
    """Holds the latest snapshot and runs checks (scheduled or on demand) one at a time."""

    def __init__(self, cfg: Config, data_dir: Path, runner=run_once):
        self.cfg, self.data_dir, self.runner = cfg, data_dir, runner
        self.lock = threading.Lock()
        self.snapshot = load_snapshot(data_dir)
        self._stop = threading.Event()

    def check(self) -> dict | None:
        with self.lock:
            try:
                self.snapshot = self.runner(self.cfg, self.data_dir)
            except Exception:
                log.exception("check failed")
            return self.snapshot

    def due_in(self) -> float:
        last = (self.snapshot or {}).get("generated_at") or 0
        return max(0.0, last + self.cfg.interval_minutes * 60 - time.time())

    def loop(self) -> None:
        while not self._stop.is_set():
            if self.due_in() <= 0:
                self.check()
            self._stop.wait(min(60.0, max(1.0, self.due_in())))

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.loop, name="metal-alert-scheduler", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()


def make_handler(monitor: Monitor):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

        def do_GET(self):  # noqa: N802 (http.server API)
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(render_page(monitor.snapshot, live=True).encode(), "text/html; charset=utf-8")
            if path == "/api/latest":
                return self._json(monitor.snapshot or {"error": "尚無資料"}, 200 if monitor.snapshot else 503)
            if path == "/api/alerts":
                return self._json((monitor.snapshot or {}).get("alerts", []))
            if path == "/healthz":
                return self._json({"ok": True, "generated_at": (monitor.snapshot or {}).get("generated_at")})
            self._send(b"not found", "text/plain", 404)

        def do_POST(self):  # noqa: N802
            if urlparse(self.path).path == "/api/refresh":
                snap = monitor.check()
                return self._json(snap or {"error": "更新失敗"}, 200 if snap else 503)
            self._send(b"not found", "text/plain", 404)

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(cfg: Config, data_dir: Path, host: str = "127.0.0.1", port: int = 8770) -> None:
    monitor = Monitor(cfg, data_dir)
    monitor.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(monitor))
    log.info("貴金屬價格預警:http://%s:%d/(每 %d 分鐘檢查一次,Ctrl+C 結束)", host, port, cfg.interval_minutes)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        httpd.server_close()

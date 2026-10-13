"""Minimal local web UI (standard library only): a form to enter the material spec and a result table."""

from __future__ import annotations

import html
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import site
from .compare import Comparison
from .config import Settings
from .report import render_json, write_reports
from .search import run_search
from .sources import llm_available

log = logging.getLogger(__name__)

def _e(v) -> str:
    return html.escape(str(v or ""))


def form_html(query: str = "", qty: str = "", web: bool = True, book: bool = True) -> str:
    llm = "" if llm_available() else "<div class='note'>未設定 ANTHROPIC_API_KEY:網路搜尋停用,只查報價紀錄。</div>"
    return (
        "<h1>物料比價</h1><div class='muted'>輸入物料品名與規格(材質、純度、尺寸、型態),彙整網路公開報價與報價紀錄。</div>"
        "<form method='get' action='/search'>"
        f"<input type='text' name='q' required placeholder='例如:鈦靶 4N5 Φ300x6.35mm、ITO 靶材 99.99% 127x406x6mm、金蒸鍍料 5N 顆粒' value='{_e(query)}'>"
        "<div class='row'>"
        f"<label>需求數量 <input type='text' name='qty' value='{_e(qty)}' style='width:120px'></label>"
        f"<label><input type='checkbox' name='web' value='1' {'checked' if web else ''}> 網路搜尋(Claude)</label>"
        f"<label><input type='checkbox' name='book' value='1' {'checked' if book else ''}> 報價紀錄</label>"
        "<button type='submit'>比價</button></div>"
        "<div class='note'>網路搜尋約需 1–3 分鐘。</div>" + llm + "</form>"
    )


def result_html(comp: Comparison, files: list[Path]) -> str:
    data = json.loads(render_json(comp, datetime.now()))
    return site.result_html(data, [(f.suffix[1:].upper(), f"/download?f={quote(f.name)}") for f in files])


def make_handler(settings: Settings, out_dir: str):
    out = Path(out_dir).resolve()
    lock = threading.Lock()  # one search at a time keeps API usage predictable

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8", extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (http.server API)
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                return self._send(site.page(form_html()))
            if url.path == "/search":
                query = params.get("q", "").strip()[:200]
                qty = params.get("qty", "").strip()[:50]
                web, book = params.get("web") == "1", params.get("book") == "1"
                if not query:
                    return self._send(site.page(form_html()))
                with lock:
                    comp = run_search(query, settings, quantity=qty, use_web=web, use_book=book)
                    files = list(write_reports(comp, out, datetime.now()))
                return self._send(site.page(form_html(query, qty, web, book) + result_html(comp, files), f"比價:{query}"))
            if url.path == "/download":
                name = Path(params.get("f", "")).name
                target = out / name
                if name and target.suffix in (".csv", ".json", ".md") and target.is_file():
                    ctype = {".csv": "text/csv", ".json": "application/json", ".md": "text/markdown"}[target.suffix]
                    return self._send(target.read_bytes(), ctype=f"{ctype}; charset=utf-8",
                                      extra={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
            return self._send(site.page("<p>找不到頁面。<a href='/'>回首頁</a></p>"), 404)

        def log_message(self, fmt, *args):
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(settings: Settings, host: str, port: int, out_dir: str) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(settings, out_dir))
    print(f"物料比價網頁:http://{host}:{port}/  (Ctrl+C 結束)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

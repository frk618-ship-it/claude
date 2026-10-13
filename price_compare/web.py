"""Minimal local web UI (standard library only): a form to enter the material spec and a result table."""

from __future__ import annotations

import html
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .compare import Comparison
from .config import Settings
from .report import PRICE_TYPES, money, write_reports
from .search import run_search
from .sources import llm_available

log = logging.getLogger(__name__)

STYLE = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e2e2dc;--accent:#2f5d8a;--ok:#2e7d4f;--warn:#a86b00;--bad:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#161615;--fg:#ececea;--muted:#a3a39d;--card:#1f1f1e;--line:#353532;--accent:#7fb0e0;--ok:#6cc596;--warn:#e0b050;--bad:#f08a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.muted{color:var(--muted)}form{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;display:grid;gap:10px;margin:16px 0}
input[type=text]{width:100%;padding:10px 12px;font-size:16px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}button{padding:10px 18px;font-size:15px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
.table{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-weight:600;color:var(--muted);white-space:nowrap}
td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.符合{color:var(--ok)}.近似{color:var(--warn)}.差異大{color:var(--bad)}
.note{font-size:12px;color:var(--muted)}a{color:var(--accent)}.spec{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;display:inline-block}
"""


def _e(v) -> str:
    return html.escape(str(v or ""))


def page(body: str, title: str = "物料比價") -> bytes:
    return (
        "<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>"
    ).encode("utf-8")


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
    base = comp.base_currency
    out = [f"<div class='spec'>解析規格:<b>{_e(comp.spec.summary())}</b></div> "
           f"<span class='muted'>報價 {len(comp.quotes)} 筆,可比 {len(comp.usable)} 筆,排除規格不符 {comp.excluded} 筆</span>"]
    if comp.stats:
        out.append("<h2>比價摘要</h2><div class='table'><table><tr><th>計價基準</th><th>筆數</th><th>最低</th><th>中位數</th><th>最高</th><th>建議優先詢價</th></tr>")
        for s in comp.stats:
            best = f"<a href='{_e(s.best.url)}' target='_blank' rel='noopener'>{_e(s.best.vendor)}</a>" if s.best.url else _e(s.best.vendor)
            out.append(f"<tr><td>{_e(s.basis)}</td><td class='num'>{s.count}</td><td class='num'>{money(s.low, base)}</td>"
                       f"<td class='num'>{money(s.median, base)}</td><td class='num'>{money(s.high, base)}</td>"
                       f"<td>{best}({money(s.best.unit_price_twd, base)})</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<p>沒有找到規格相符、可換算比較的報價。可放寬規格,或用 <code>python -m price_compare add</code> 補上詢價結果。</p>")
    if comp.market_summary:
        out.append(f"<h2>市場概況</h2><p>{_e(comp.market_summary)}</p>")
    if comp.quotes:
        out.append(f"<h2>報價明細</h2><div class='table'><table><tr><th>相符度</th><th>供應商 / 品名</th><th>解析規格</th>"
                   f"<th>原始報價</th><th>換算單價({_e(base)})</th><th>類型</th><th>地區 / 日期 / MOQ</th><th>備註</th></tr>")
        for q in comp.quotes:
            vendor = f"<a href='{_e(q.url)}' target='_blank' rel='noopener'>{_e(q.vendor or '?')}</a>" if q.url else _e(q.vendor or "?")
            pack = f"{q.pack_qty:g} {q.unit}" if q.pack_qty != 1 else q.unit
            notes = [*q.match_notes] + ([f"⚠ {q.outlier}"] if q.outlier else []) + ([q.note] if q.note else [])
            out.append(
                f"<tr><td class='{_e(q.match_level)}'>{_e(q.match_level)} {q.match_score}</td>"
                f"<td>{vendor}<div class='note'>{_e(q.title)} · {_e(q.source)}</div></td>"
                f"<td>{_e(q.spec.summary() if q.spec else '')}</td>"
                f"<td class='num'>{money(q.price, q.currency.upper())} / {_e(pack)}</td>"
                f"<td class='num'>{money(q.unit_price_twd, base)}<div class='note'>{_e(q.basis)}</div></td>"
                f"<td>{_e(PRICE_TYPES.get(q.price_type, q.price_type))}</td>"
                f"<td>{_e(' '.join(x for x in (q.region, q.quote_date, q.moq and 'MOQ ' + q.moq) if x))}</td>"
                f"<td class='note'>{_e(';'.join(notes))}</td></tr>"
            )
        out.append("</table></div>")
    if files:
        out.append("<p class='note'>下載:" + " · ".join(
            f"<a href='/download?f={quote(f.name)}'>{_e(f.suffix[1:].upper())}</a>" for f in files) + "</p>")
    out.append("<p class='note'>網路公開標價常未含稅、運費、綁靶/背板與加工費;正式採購請向供應商確認報價。</p>")
    return "".join(out)


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
                return self._send(page(form_html()))
            if url.path == "/search":
                query = params.get("q", "").strip()[:200]
                qty = params.get("qty", "").strip()[:50]
                web, book = params.get("web") == "1", params.get("book") == "1"
                if not query:
                    return self._send(page(form_html()))
                with lock:
                    comp = run_search(query, settings, quantity=qty, use_web=web, use_book=book)
                    files = list(write_reports(comp, out, datetime.now()))
                return self._send(page(form_html(query, qty, web, book) + result_html(comp, files), f"比價:{query}"))
            if url.path == "/download":
                name = Path(params.get("f", "")).name
                target = out / name
                if name and target.suffix in (".csv", ".json", ".md") and target.is_file():
                    ctype = {".csv": "text/csv", ".json": "application/json", ".md": "text/markdown"}[target.suffix]
                    return self._send(target.read_bytes(), ctype=f"{ctype}; charset=utf-8",
                                      extra={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
            return self._send(page("<p>找不到頁面。<a href='/'>回首頁</a></p>"), 404)

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

"""Command line: build the static page, or serve it locally for use on this computer / the LAN."""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import tempfile
from pathlib import Path

from .page import write_site


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m card_scanner", description="名片 OCR 辨識資料庫")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="產生單一檔案的網頁(index.html)")
    b.add_argument("-o", "--out", default="site/cards", help="輸出資料夾(預設 site/cards)")
    b.add_argument("--no-nav", action="store_true", help="不加網站的頁面切換選單(單獨使用時)")
    s = sub.add_parser("serve", help="在本機啟動網頁伺服器")
    s.add_argument("--host", default="127.0.0.1", help="要讓同網段的手機使用請設 0.0.0.0")
    s.add_argument("--port", type=int, default=8780)
    args = ap.parse_args(argv)

    if args.cmd == "build":
        page = write_site(args.out, nav=not args.no_nav)
        print(f"wrote {page}")
        return 0

    out = Path(tempfile.mkdtemp(prefix="card_scanner_"))
    write_site(out, nav=False)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as httpd:
        print(f"名片庫: http://{'127.0.0.1' if args.host in ('127.0.0.1', 'localhost') else args.host}:{args.port}/")
        if args.host == "0.0.0.0" and (ip := _lan_ip()):
            print(f"同網段的手機: http://{ip}:{args.port}/ (資料存在各自的瀏覽器裡,手機與電腦不共用)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0

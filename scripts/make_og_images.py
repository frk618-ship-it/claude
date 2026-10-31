"""Render the 1200x630 share-preview images (og:image) in the sites' flat style.

    python scripts/make_og_images.py

Writes fin_analyzer/assets/og.png (financial dashboard) and target_news_agent/assets/og.png
(news site). Needs Playwright with Chromium; set CHROMIUM to a browser binary if Playwright
cannot find one. Needs a CJK font installed (e.g. Noto Sans CJK) for the Chinese text.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

# Flat: solid fills only, no gradients, shadows or transparency. Colours from the site's data-viz
# palette from the 鑫科材料 (TTMC) logo, as on the site: cool-grey page, charcoal ink, navy accent,
# an orange brand rule, the target rings in steps of a navy ramp.
CARD = """<!doctype html><html><head><meta charset=utf-8><style>
*{box-sizing:border-box;margin:0}
body{width:1200px;height:630px;overflow:hidden;position:relative;background:#f3f4f6;color:#131415;
font-family:"Noto Sans CJK TC","Noto Sans TC","PingFang TC",sans-serif;padding:72px 80px}
.rings{position:absolute;right:-150px;top:50%;transform:translateY(-50%)}
.eyebrow{font:600 24px/1 ui-monospace,Menlo,monospace;letter-spacing:.12em;color:#b35a10;text-transform:uppercase}
h1{font-size:76px;font-weight:700;line-height:1.15;margin-top:24px;position:relative}
p{font-size:31px;color:#4d4e4f;margin-top:22px;line-height:1.45;max-width:700px;position:relative}
.bar{position:absolute;left:80px;bottom:150px;width:96px;height:10px;background:#f39043}
.row{position:absolute;left:80px;bottom:72px;display:flex;gap:14px}
.chip{background:#fbfcfd;color:#131415;border:2px solid #dfe1e5;border-radius:6px;padding:10px 20px;font-size:26px;font-weight:700}
.logo{position:absolute;left:80px;top:72px;width:0;height:0}
.tile{position:absolute;right:80px;top:72px;width:112px;height:112px;border-radius:10px;background:#173c66;display:grid;place-items:center}
</style></head><body>
<svg class=rings width=620 height=620 viewBox="0 0 620 620" aria-hidden=true>
<circle cx=310 cy=310 r=300 fill=#e4eaf2 /><circle cx=310 cy=310 r=230 fill=#c9d6e6 /><circle cx=310 cy=310 r=160 fill=#a9c0dc />
<circle cx=310 cy=310 r=90 fill=#7f9fc6 /><circle cx=310 cy=310 r=30 fill=#173c66 /></svg>
<div class=tile><svg width=68 height=68 viewBox="0 0 24 24" fill=none stroke=#ffffff stroke-width=1.8 stroke-linecap=round>
<circle cx=12 cy=12 r=9 /><circle cx=12 cy=12 r=5.5 /><circle cx=12 cy=12 r=1.8 fill=#ffffff /></svg></div>
<div class=eyebrow>EYEBROW</div><h1>TITLE</h1><p>SUB</p><div class=bar></div><div class=row>CHIPS</div></body></html>"""

CARDS = [
    (ROOT / "fin_analyzer" / "assets" / "og.png", "Sputtering Target · Financial Dashboard", "濺鍍靶材製造商<br>財務指標儀表板",
     "台日美中 13 家靶材廠的獲利、成長、財務結構與評價比較", ["營收與成長", "毛利率 / ROE", "現金流", "本益比 / EV"]),
    (ROOT / "target_news_agent" / "assets" / "og.png", "Sputtering Target · Industry Bulletin", "靶材產業新聞摘要",
     "靶材生產商、濺鍍設備、半導體與封裝廠動態,每日自動整理", ["每日新聞", "AI 繁中摘要", "商情預測", "知識小遊戲"]),
]


def main() -> None:
    with sync_playwright() as pw:
        exe = os.environ.get("CHROMIUM")
        browser = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        for out, eyebrow, title, sub, chips in CARDS:
            html = (CARD.replace("EYEBROW", eyebrow).replace("TITLE", title).replace("SUB", sub)
                    .replace("CHIPS", "".join(f"<span class=chip>{c}</span>" for c in chips)))
            page.set_content(html)
            page.screenshot(path=str(out))
            print(out)
        browser.close()


if __name__ == "__main__":
    main()

"""The business report as a page of the radar site (<site>/leads/report/), so it can be read with
the site's password alone — no Claude login. Source: reports/leads/business_report.md, exported
from the business-report document (claude.ai) whenever it is updated.

A small Markdown subset is rendered (what the exported report uses): headings, paragraphs,
bullet / numbered / task lists, pipe tables, **bold**, `code` and [links](https://…).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _inline(text: str) -> str:
    out, pos = [], 0
    for m in _LINK.finditer(text):
        out.append(_fmt(text[pos:m.start()]))
        out.append(f'<a href="{html.escape(m.group(2))}" target="_blank" rel="noopener">{_fmt(m.group(1))}</a>')
        pos = m.end()
    out.append(_fmt(text[pos:]))
    return "".join(out)


def _fmt(text: str) -> str:
    t = html.escape(text.replace("\\*", "*"))
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", t)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"(#{1,4})\s+(.*)", line)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"\s*\|?\s*:?-{3,}", lines[i + 1]):
            head = _cells(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append('<div class="tw"><table><thead><tr>' + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table></div>")
            continue
        if re.match(r"\s*([-*]|\d+\.)\s+", line):
            ordered = bool(re.match(r"\s*\d+\.", line))
            items = []
            while i < len(lines) and re.match(r"\s*([-*]|\d+\.)\s+", lines[i]):
                text = re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i])
                task = re.match(r"\[( |x|X)\]\s+(.*)", text)
                if task:
                    box = "☑" if task.group(1).lower() == "x" else "☐"
                    items.append(f'<li class="task"><span aria-hidden="true">{box}</span> {_inline(task.group(2))}</li>')
                else:
                    items.append(f"<li>{_inline(text)}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"(#{1,4}\s|\s*\||\s*([-*]|\d+\.)\s)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(out)


PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{title}</title>
<style>
/* Hallmark · restyle · long document · brand: 鑫科材料 (TTMC) navy and orange, kept · posture: restrained */
:root{{--font:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;--mono:ui-monospace,Menlo,monospace;--focus:#1f5fa8;--bg:#f3f4f6;--surface:#fbfcfd;--fg:#131415;--muted:#4d4e4f;--line:#dfe1e5;--accent:#173c66;--accent-2:#005a7f;--accent-soft:#e4eaf2;--brand-orange:#f39043;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1319;--surface:#161c24;--fg:#eef0f3;--muted:#b4b6ba;--line:#29313c;--accent:#7fa6d6;--accent-2:#4fb3cf;--accent-soft:#1a2a40;color-scheme:dark}}}}
*{{box-sizing:border-box}}html,body{{overflow-x:clip}}body{{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font);font-size:16px;line-height:1.65;font-synthesis:none}}
:where(a,button,summary,[tabindex]):focus-visible{{outline:2px solid var(--focus);outline-offset:2px;border-radius:4px}}
.wrap{{max-width:980px;margin:0 auto;padding:28px 16px 80px}}
.back{{display:inline-flex;gap:6px;align-items:center;text-decoration:none;border:1px solid var(--line);background:var(--surface);color:var(--fg);border-radius:999px;padding:6px 14px;font-size:.9rem}}
.back:hover{{border-color:var(--accent)}}
article{{margin-top:18px;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:24px clamp(16px,4vw,40px)}}
h1{{font-size:clamp(1.6rem,4vw,2.2rem);line-height:1.15;letter-spacing:-.02em;text-wrap:balance;overflow-wrap:break-word;margin:.2em 0 .4em;padding-bottom:12px;border-bottom:3px solid var(--accent);position:relative}}
h1::after{{content:"";position:absolute;left:0;bottom:-3px;width:96px;height:3px;background:var(--brand-orange)}}
h2{{font-size:1.375rem;line-height:1.25;margin:1.8em 0 .6em;text-wrap:balance}}h3{{font-size:1.125rem;margin:1.4em 0 .5em;text-wrap:balance}}
p{{margin:.6em 0;max-width:70ch;text-wrap:pretty}}a{{color:var(--accent);text-underline-offset:3px}}
ul,ol{{padding-left:1.4em;margin:.6em 0}}li{{margin:.25em 0}}li.task{{list-style:none;margin-left:-1.2em}}
.tw{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:.8em 0;border:1px solid var(--line);border-radius:8px}}
table{{border-collapse:collapse;width:100%;font-size:.88rem;min-width:640px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{background:var(--accent-soft);font-weight:600;white-space:nowrap}}
code{{font-family:var(--mono);font-size:.88em;background:var(--accent-soft);padding:1px 5px;border-radius:4px}}
.note{{color:var(--muted);font-size:.84rem;margin-top:10px}}
@media print{{.back,.fabnav,.fab-scrim{{display:none}}article{{border:0;padding:0}}}}
</style></head><body><div class="wrap">
<a class="back" href="{back}">← 商機雷達</a>
<article>
{body}
</article>
<p class="note">本頁為業務報告文件的匯出版本,只需網站密碼即可閱讀;報告更新時會重新匯出。</p>
</div></body></html>"""


def render_report_page(md: str, back: str = "../index.html") -> str:
    first = next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), "業務報告")
    return PAGE.format(title=html.escape(first), back=html.escape(back), body=markdown_to_html(md))


def write_report_page(md_path: str | Path, out_dir: str | Path, nav: bool = True) -> Path | None:
    """<out_dir>/index.html from the report Markdown; None when there is no report yet."""
    src = Path(md_path)
    if not src.exists():
        return None
    page = render_report_page(src.read_text(encoding="utf-8"))
    if nav:
        from target_news_agent.fabnav import fab_nav

        page = page.replace("</body>", fab_nav("leads", "../../") + "\n</body>", 1)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return path

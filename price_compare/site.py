"""HTML rendering shared by the local web UI and the static site published to GitHub Pages.

The static site is built from the JSON reports in reports/prices/: an index page listing every
comparison (newest first) plus one page per comparison with download links to its MD/JSON/CSV.
"""

from __future__ import annotations

import html
import json
import logging
import shutil
from pathlib import Path
from urllib.parse import quote

from .report import PRICE_TYPES, money

log = logging.getLogger(__name__)

STYLE = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e2e2dc;--accent:#2f5d8a;--ok:#2e7d4f;--warn:#a86b00;--bad:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#161615;--fg:#ececea;--muted:#a3a39d;--card:#1f1f1e;--line:#353532;--accent:#7fb0e0;--ok:#6cc596;--warn:#e0b050;--bad:#f08a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.muted{color:var(--muted)}form{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;display:grid;gap:10px;margin:16px 0}
input[type=text],input[type=search]{width:100%;padding:10px 12px;font-size:16px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}button,.btn{padding:10px 18px;font-size:15px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
.table{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-weight:600;color:var(--muted);white-space:nowrap}
td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.符合{color:var(--ok)}.近似{color:var(--warn)}.差異大{color:var(--bad)}
.note{font-size:12px;color:var(--muted)}a{color:var(--accent)}.spec{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;display:inline-block}
.bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin:16px 0}
"""


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def page(body: str, title: str = "物料比價") -> bytes:
    return (
        "<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>"
    ).encode("utf-8")


def _link(text: str, url: str) -> str:
    if url and url.startswith(("http://", "https://")):
        return f"<a href='{esc(url)}' target='_blank' rel='noopener'>{esc(text)}</a>"
    return esc(text)


def result_html(data: dict, downloads: list[tuple[str, str]] | None = None) -> str:
    """Comparison body from a report JSON dict (see report.render_json)."""
    base = data.get("base_currency", "TWD")
    quotes = data.get("quotes", [])
    usable = sum(s["count"] for s in data.get("summary", []))
    out = [f"<div class='spec'>解析規格:<b>{esc(data['parsed_spec']['summary'])}</b></div> "
           f"<span class='muted'>報價 {len(quotes)} 筆,可比 {usable} 筆,排除規格不符 {data.get('excluded', 0)} 筆</span>"]
    if data.get("summary"):
        out.append("<h2>比價摘要</h2><div class='table'><table><tr><th>計價基準</th><th>筆數</th><th>最低</th>"
                   "<th>中位數</th><th>最高</th><th>建議優先詢價</th></tr>")
        for s in data["summary"]:
            out.append(f"<tr><td>{esc(s['basis'])}</td><td class='num'>{s['count']}</td><td class='num'>{money(s['low'], base)}</td>"
                       f"<td class='num'>{money(s['median'], base)}</td><td class='num'>{money(s['high'], base)}</td>"
                       f"<td>{_link(s['best_vendor'], s.get('best_url', ''))}"
                       + (f"({money(s['best_unit_price'], base)},{esc(s.get('best_level', ''))})" if s.get('best_unit_price') else "")
                       + "</td></tr>")
        out.append("</table></div>")
    else:
        out.append("<p>沒有找到規格相符、可換算比較的報價。可放寬規格,或在報價紀錄補上詢價結果。</p>")
    if data.get("market_summary"):
        out.append(f"<h2>市場概況</h2><p>{esc(data['market_summary'])}</p>")
    if quotes:
        out.append(f"<h2>報價明細</h2><div class='table'><table><tr><th>相符度</th><th>供應商 / 品名</th><th>解析規格</th>"
                   f"<th>原始報價</th><th>換算單價({esc(base)})</th><th>類型</th><th>地區 / 日期 / MOQ</th><th>備註</th></tr>")
        for q in quotes:
            pack = f"{q['pack_qty']:g} {q['unit']}" if q.get("pack_qty", 1) != 1 else q["unit"]
            notes = [n for n in (q.get("match_notes") or "").split(";") if n]
            notes += [f"⚠ {q['outlier']}"] if q.get("outlier") else []
            notes += [q["note"]] if q.get("note") else []
            where = " ".join(x for x in (q.get("region"), q.get("quote_date"), q.get("moq") and "MOQ " + q["moq"]) if x)
            out.append(
                f"<tr><td class='{esc(q['match_level'])}'>{esc(q['match_level'])} {q['match_score']}</td>"
                f"<td>{_link(q['vendor'] or '?', q.get('url', ''))}<div class='note'>{esc(q['title'])} · {esc(q['source'])}</div></td>"
                f"<td>{esc(q.get('spec'))}</td>"
                f"<td class='num'>{money(q['price'], str(q['currency']).upper())} / {esc(pack)}</td>"
                f"<td class='num'>{money(q.get('unit_price_twd'), base)}<div class='note'>{esc(q.get('basis'))}</div></td>"
                f"<td>{esc(PRICE_TYPES.get(q['price_type'], q['price_type']))}</td>"
                f"<td>{esc(where)}</td><td class='note'>{esc(';'.join(notes))}</td></tr>"
            )
        out.append("</table></div>")
    if downloads:
        out.append("<p class='note'>下載:" + " · ".join(f"<a href='{esc(href)}'>{esc(label)}</a>" for label, href in downloads) + "</p>")
    out.append("<p class='note'>網路公開標價常未含稅、運費、綁靶/背板與加工費;正式採購請向供應商確認報價。</p>")
    return "".join(out)


def _load_reports(src: Path) -> list[tuple[Path, dict]]:
    reports = []
    for path in src.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["parsed_spec"]["summary"], data["query"]  # noqa: B018 — validate shape
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("skip %s: %s", path.name, exc)
            continue
        reports.append((path, data))
    return sorted(reports, key=lambda r: r[1].get("generated", ""), reverse=True)


def index_html(reports: list[tuple[Path, dict]], new_query_url: str = "") -> str:
    out = ["<h1>物料比價紀錄</h1><div class='muted'>每次比價的結果(新到舊)。點品名規格看報價明細。</div>"]
    out.append("<div class='bar'><input type='search' id='filter' placeholder='篩選品名規格…' style='max-width:360px'>")
    if new_query_url:
        out.append(f"<a class='btn' href='{esc(new_query_url)}' target='_blank' rel='noopener'>新增比價(GitHub Actions)</a>")
    out.append("</div>")
    if not reports:
        out.append("<p>還沒有比價紀錄。</p>")
        return "".join(out)
    out.append("<div class='table'><table id='list'><tr><th>時間</th><th>品名規格</th><th>解析規格</th><th>報價筆數</th>"
               "<th>最低 / 中位數</th><th>建議優先詢價</th></tr>")
    for path, d in reports:
        base = d.get("base_currency", "TWD")
        summary = d.get("summary") or []
        price = "<br>".join(f"{esc(s['basis'])} {money(s['low'], base)} / {money(s['median'], base)}" for s in summary) or "—"
        best = "<br>".join(esc(s["best_vendor"]) for s in summary) or "—"
        out.append(
            f"<tr data-q='{esc((d['query'] + ' ' + d['parsed_spec']['summary']).lower())}'>"
            f"<td class='note'>{esc(d.get('generated', '')[:16].replace('T', ' '))}</td>"
            f"<td><a href='{quote(path.stem)}.html'>{esc(d['query'])}</a></td>"
            f"<td>{esc(d['parsed_spec']['summary'])}</td><td class='num'>{len(d.get('quotes', []))}</td>"
            f"<td class='num'>{price}</td><td>{best}</td></tr>"
        )
    out.append("</table></div>")
    out.append(
        "<script>document.getElementById('filter').addEventListener('input',e=>{const v=e.target.value.trim().toLowerCase();"
        "document.querySelectorAll('#list tr[data-q]').forEach(r=>{r.style.display=!v||r.dataset.q.includes(v)?'':'none'})})</script>"
    )
    return "".join(out)


def build_site(src_dir: str | Path, out_dir: str | Path, new_query_url: str = "") -> int:
    """Write index.html and one page per report; returns the number of reports published."""
    src, out = Path(src_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    reports = _load_reports(src)
    for path, data in reports:
        downloads = []
        for ext in (".csv", ".md", ".json"):
            f = path.with_suffix(ext)
            if f.exists():
                shutil.copyfile(f, out / f.name)
                downloads.append((ext[1:].upper(), quote(f.name)))
        body = ("<p class='note'><a href='./'>← 比價紀錄</a></p>"
                f"<h1>物料比價:{esc(data['query'])}</h1>"
                f"<div class='muted'>產生時間 {esc(data.get('generated', '')[:16].replace('T', ' '))}</div><br>"
                + result_html(data, downloads))
        (out / f"{path.stem}.html").write_bytes(page(body, f"比價:{data['query']}"))
    (out / "index.html").write_bytes(page(index_html(reports, new_query_url), "物料比價紀錄"))
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is, no Jekyll processing
    return len(reports)

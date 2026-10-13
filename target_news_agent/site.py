"""Static news site for GitHub Pages, built from the JSON reports in reports/.

index.html shows the latest report; YYYY-MM-DD.html keeps every earlier one, and
data/ holds the raw MD/JSON for download. Each item gets a "kind" label (news,
filing, market data, forum, research, profile page) so readers can tell real
news from the market-data and forum noise that news search also returns.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from collections import Counter, OrderedDict
from pathlib import Path

from .config import Config

REPORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")

KINDS = OrderedDict([
    ("news", "新聞"),
    ("filing", "公司公告"),
    ("market", "行情數據"),
    ("forum", "論壇與自媒體"),
    ("research", "市場報告"),
    ("profile", "資料頁"),
])
SIGNAL_KINDS = ("news", "filing")

_MARKET_RE = re.compile(r"主力资金|主力資金|股东户数|股東戶數|开盘|開盤|低开|低開|賣超|买超|買超|周KD|週KD|大阳线|跌近|涨停|漲停|Decline|shares? (?:rose|fell)|stock (?:rose|fell)", re.I)
_RESEARCH_RE = re.compile(r"市場|市场|Market|レポート|予測|CAGR", re.I)
_FILING_RE = re.compile(r"\(\d{6}\)[::]|公告")


def item_kind(item: dict) -> str:
    title, source = item.get("title", ""), item.get("source", "")
    if "企業情報" in title or "個股概覽" in title:
        return "profile"
    if "股市爆料同學會" in title or source in ("财富号", "財富號", "PTT", "Mobile01"):
        return "forum"
    if any(e.startswith("產業綜合") for e in item.get("entities", [])) and _RESEARCH_RE.search(title):
        return "research"
    if source == "中财网" or _FILING_RE.search(title):
        return "filing"
    if _MARKET_RE.search(title):
        return "market"
    return "news"


def short_name(entity: str) -> str:
    return re.sub(r"\s*\(.*\)$", "", entity)


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


STYLE = """
:root{--bg:#f3f4f5;--surface:#fff;--fg:#1c2126;--muted:#5b6570;--line:#d6dadf;--accent:#8a4f24;--accent-soft:#f3e6dc;--ok:#2f6b4f;--ok-soft:#e1efe7;--none:#6b7480;--none-soft:#eceef1;
--font-display:"Noto Serif TC","Songti TC",serif;--font-body:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;--font-data:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#14181c;--surface:#1b2026;--fg:#e6e9ec;--muted:#9aa4ae;--line:#2c333b;--accent:#e0a070;--accent-soft:#3a2a1e;--ok:#7fc8a2;--ok-soft:#1d3329;--none:#8d96a0;--none-soft:#232930;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font-body);font-size:16px;line-height:1.7}
.wrap{max-width:860px;margin:0 auto;padding-inline:20px;padding-block:40px 64px;display:grid;gap:44px}
a{color:var(--accent);text-underline-offset:3px}a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
h1,h2{font-family:var(--font-display);text-wrap:balance;margin:0}h1{font-size:clamp(1.9rem,5vw,2.6rem);font-weight:800;line-height:1.25}
h2{font-size:1.35rem;font-weight:600;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px}h2 .count{font-family:var(--font-data);font-size:.85rem;color:var(--muted);font-weight:400}
.eyebrow{font-family:var(--font-data);font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.mono{font-family:var(--font-data);font-variant-numeric:tabular-nums}p{margin:0;max-width:65ch}section{display:grid;gap:16px;min-width:0}
header{display:grid;gap:14px;border-bottom:2px solid var(--fg);padding-bottom:24px}.meta{display:flex;flex-wrap:wrap;gap:8px 20px;color:var(--muted);font-size:.9rem}
.stats{display:flex;flex-wrap:wrap;gap:28px;margin-top:6px}.stat{display:grid}.stat b{font-family:var(--font-data);font-size:1.6rem;font-weight:500;line-height:1.2}.stat span{font-size:.82rem;color:var(--muted)}
.lead{font-size:1.05rem}ul.plain{margin:0;padding-left:1.2em;display:grid;gap:4px}
.filters{display:grid;gap:10px}.frow{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.frow .lbl{font-size:.8rem;color:var(--muted);margin-right:4px;min-width:3em}
button.f{font:inherit;font-size:.85rem;border:1px solid var(--line);background:var(--surface);color:var(--fg);padding:3px 10px;border-radius:4px;cursor:pointer;display:inline-flex;gap:6px;align-items:baseline}
button.f .mono{font-size:.74rem;color:var(--muted)}button.f[aria-pressed=true]{background:var(--fg);color:var(--bg);border-color:var(--fg)}button.f[aria-pressed=true] .mono{color:var(--bg)}
.shown{font-size:.85rem;color:var(--muted)}button.reset{font:inherit;font-size:.85rem;background:none;border:0;color:var(--accent);cursor:pointer;text-decoration:underline;padding:0}
.news{display:grid;border-top:1px solid var(--line)}.item{display:grid;grid-template-columns:56px 1fr;gap:4px 16px;padding:12px 0;border-bottom:1px solid var(--line)}
.item time{font-family:var(--font-data);font-size:.82rem;color:var(--muted);padding-top:2px}.item .body{display:grid;gap:4px;min-width:0}
.item h3{margin:0;font-size:.97rem;font-weight:500;line-height:1.55;overflow-wrap:anywhere}.item h3 a{color:var(--fg);text-decoration-color:var(--line)}.item h3 a:hover{text-decoration-color:var(--accent)}
.item .sum{font-size:.9rem;color:var(--muted)}.item .co{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:.8rem;color:var(--muted);align-items:baseline}
.kind{font-size:.72rem;padding:0 7px;border-radius:3px;font-weight:500}.k-news,.k-filing{background:var(--accent-soft);color:var(--accent)}.k-market,.k-forum,.k-research,.k-profile{background:var(--none-soft);color:var(--none)}
.item.low h3 a{color:var(--muted)}.top .item h3{font-weight:700}
.ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:24px 32px}.region h3{margin:0 0 6px;font-size:.82rem;letter-spacing:.06em;color:var(--muted);font-weight:500}
.region ul{list-style:none;margin:0;padding:0}.region li{display:grid;grid-template-columns:1fr auto;align-items:baseline;gap:10px;padding:7px 0;border-bottom:1px dashed var(--line);font-size:.93rem}
.tk{font-family:var(--font-data);font-size:.74rem;color:var(--muted);margin-left:6px}.pill{font-size:.72rem;padding:1px 8px;border-radius:99px;white-space:nowrap}
.pill.yes{background:var(--ok-soft);color:var(--ok);font-weight:700}.pill.no{background:var(--none-soft);color:var(--none)}
.archive{display:flex;flex-wrap:wrap;gap:8px}.archive a{font-family:var(--font-data);font-size:.85rem;border:1px solid var(--line);background:var(--surface);padding:3px 10px;border-radius:4px;text-decoration:none}
.archive a[aria-current=page]{background:var(--fg);color:var(--bg);border-color:var(--fg)}
h3.topic{margin:10px 0 0;font-size:1rem;font-weight:700;display:flex;gap:8px;align-items:baseline}h3.topic .mono{font-size:.8rem;color:var(--muted);font-weight:400}
.note{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:18px 20px;display:grid;gap:10px;font-size:.9rem;color:var(--muted)}.note strong{color:var(--fg)}
footer{font-size:.82rem;color:var(--muted);border-top:1px solid var(--line);padding-top:18px}
@media (max-width:520px){.item{grid-template-columns:1fr}}
"""

SCRIPT = """
(function(){var items=[].slice.call(document.querySelectorAll('#list .item')),shown=document.getElementById('shown'),reset=document.getElementById('reset'),
buttons=[].slice.call(document.querySelectorAll('button.f')),active={kind:null,ent:null};
function apply(){var n=0;items.forEach(function(el){var ok=(!active.kind||el.dataset.kind===active.kind)&&(!active.ent||el.dataset.ent.split('|').indexOf(active.ent)!==-1);el.hidden=!ok;if(ok)n++;});
buttons.forEach(function(b){b.setAttribute('aria-pressed',String(active[b.dataset.filter]===b.dataset.value));});shown.textContent='顯示 '+n+' / '+items.length+' 則';reset.hidden=!active.kind&&!active.ent;}
buttons.forEach(function(b){b.addEventListener('click',function(){var k=b.dataset.filter;active[k]=active[k]===b.dataset.value?null:b.dataset.value;apply();});});
reset.addEventListener('click',function(){active={kind:null,ent:null};apply();});})();
"""


def _title_link(item: dict) -> str:
    link = item.get("link", "")
    if link.startswith(("https://", "http://")):
        return f'<a href="{esc(link)}" target="_blank" rel="noopener">{esc(item["title"])}</a>'
    return esc(item["title"])  # never turn javascript:/data: links from feeds into clickable links


def _item_html(item: dict, extra_class: str = "") -> str:
    kind = item["kind"]
    date = item["published"][5:10].replace("-", "/") if item.get("published") else "--/--"
    ents = [short_name(e) for e in item.get("entities", [])]
    stars = f" {'★' * item['importance']}" if item.get("importance") else ""
    summary = f'<p class="sum">{esc(item["summary"])}</p>' if item.get("summary") else ""
    low = "" if kind in SIGNAL_KINDS else " low"
    return (
        f'<article class="item{low}{extra_class}" data-kind="{kind}" data-ent="{esc("|".join(ents))}">'
        f'<time datetime="{esc((item.get("published") or "")[:10])}">{date}</time>'
        f'<div class="body"><h3>{_title_link(item)}{stars}</h3>{summary}'
        f'<div class="co"><span class="kind k-{kind}">{KINDS[kind]}</span>'
        + "".join(f"<span>{esc(e)}</span>" for e in ents)
        + f'<span>{esc(item.get("source", ""))}</span></div></div></article>'
    )


def _paper_html(p: dict) -> str:
    date = (p.get("published") or "")[:10].replace("-", "/") or "日期不明"
    authors = p.get("authors") or []
    author_text = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")
    stars = f" {'★' * p['importance']}" if p.get("importance") else ""
    if p.get("summary"):
        body = f'<p class="sum">{esc(p["summary"])}</p>'
    elif p.get("abstract"):
        abstract = p["abstract"]
        body = f'<p class="sum">{esc(abstract[:260])}{"…" if len(abstract) > 260 else ""}</p>'
    else:
        body = ""
    meta = "".join(f"<span>{esc(x)}</span>" for x in (p.get("venue"), author_text) if x)
    return (
        f'<article class="item"><time datetime="{esc((p.get("published") or "")[:10])}">{esc(date[5:] if len(date) == 10 else date)}</time>'
        f'<div class="body"><h3>{_title_link(p)}{stars}</h3>{body}<div class="co">{meta}</div></div></article>'
    )


def _papers_section(report: dict) -> str:
    if "papers" not in report:
        return ""
    papers = report["papers"]
    days = report.get("research_days") or 30
    if not papers:
        inner = "<p>本期沒有新的靶材製造技術文獻。</p>"
    else:
        groups: "OrderedDict[str, list]" = OrderedDict()
        ranked = sorted(papers, key=lambda p: (p.get("importance", 0), p.get("published") or ""), reverse=True)
        for p in ranked:
            groups.setdefault((p.get("topics") or ["其他靶材研究"])[0], []).append(p)
        inner = "".join(
            f'<h3 class="topic">{esc(t)} <span class="mono">{len(ps)}</span></h3><div class="news">{"".join(_paper_html(p) for p in ps)}</div>'
            for t, ps in groups.items()
        )
    return (
        f'<section aria-labelledby="papers"><h2 id="papers">技術文獻:靶材製造技術 <span class="count">{len(papers)} 篇 · 最近 {esc(days)} 天 · 依製程分類</span></h2>'
        f'<p class="sum" style="color:var(--muted)">來源:OpenAlex、arXiv 與期刊 RSS(Thin Solid Films、Surface and Coatings Technology、Vacuum 等);只收錄標題或摘要明確談到濺鍍靶材的研究。</p>'
        f"{inner}</section>"
    )


def _ledger(config: Config, counts: Counter) -> str:
    groups: "OrderedDict[str, list]" = OrderedDict((k, []) for k in ("日本", "中國大陸", "台灣", "韓國", "歐美", "銷售商", "其他"))
    western = {"美國", "奧地利", "德國", "比利時", "英國", "法國", "瑞士"}
    for e in config.entities:
        if e.is_topic:
            continue
        key = "銷售商" if e.role == "distributor" else ("歐美" if e.region in western else e.region)
        groups.setdefault(key if key in groups else "其他", []).append(e)
    out = []
    for key, ents in groups.items():
        if not ents:
            continue
        lis = []
        for e in ents:
            n = counts.get(short_name(e.name), 0)
            tk = f'<span class="tk">{esc(e.ticker)}</span>' if e.ticker else ""
            pill = f'<span class="pill yes">{n} 則</span>' if n else '<span class="pill no">無報導</span>'
            lis.append(f"<li><span>{esc(short_name(e.name))}{tk}</span>{pill}</li>")
        label = key if key == "銷售商" else f"生產商 · {key}"
        out.append(f'<div class="region"><h3>{label}</h3><ul>{"".join(lis)}</ul></div>')
    return "\n".join(out)


def render_page(report: dict, config: Config, archive: list[tuple[str, int]], current: str, home: str = "") -> str:
    items = report.get("items", [])
    for it in items:
        it["kind"] = item_kind(it)
    digest = report.get("digest") or {}
    kinds = Counter(it["kind"] for it in items)
    ent_counts = Counter(short_name(e) for it in items for e in it.get("entities", []))
    companies = [e for e in config.entities if not e.is_topic]
    covered = [e for e in companies if ent_counts.get(short_name(e.name))]
    signal = [it for it in items if it["kind"] in SIGNAL_KINDS]
    generated = (report.get("generated") or "")[:16].replace("T", " ")

    if digest.get("overview"):
        summary = f'<p class="lead">{esc(digest["overview"])}</p>'
        if digest.get("key_trends"):
            summary += "<p><strong>產業趨勢</strong></p><ul class='plain'>" + "".join(f"<li>{esc(t)}</li>" for t in digest["key_trends"]) + "</ul>"
        if digest.get("watch_points"):
            summary += "<p><strong>後續追蹤</strong></p><ul class='plain'>" + "".join(f"<li>{esc(t)}</li>" for t in digest["watch_points"]) + "</ul>"
    elif items:
        top_names = "、".join(f"{n}({c})" for n, c in ent_counts.most_common(4) if not n.startswith("產業綜合"))
        summary = (
            f'<p class="lead">本期蒐集 {len(items)} 則,其中新聞與公司公告 {len(signal)} 則;'
            f"{len(covered)} / {len(companies)} 家追蹤公司有報導,報導最多的是 {esc(top_names)}。</p>"
            '<p class="sum" style="color:var(--muted)">本期未產生 AI 摘要(設定 ANTHROPIC_API_KEY 後,這裡會顯示 Claude 撰寫的本期總覽與趨勢)。</p>'
        )
    else:
        summary = '<p class="lead">本期沒有新的相關新聞。</p>'

    # Highest-rated news/filings, at most 2 per company so one busy company doesn't fill the list.
    top, per_company = [], Counter()
    for it in sorted(signal, key=lambda it: (it.get("importance", 0), it.get("score", 0)), reverse=True):
        key = (it.get("entities") or [""])[0]
        if per_company[key] < 2:
            top.append(it)
            per_company[key] += 1
        if len(top) == 8:
            break
    top_html = "\n".join(_item_html(it) for it in top) or "<p>本期沒有新聞或公司公告。</p>"
    kind_chips = "".join(
        f'<button type="button" class="f" data-filter="kind" data-value="{k}" aria-pressed="false">{v}<span class="mono">{kinds[k]}</span></button>'
        for k, v in KINDS.items() if kinds[k]
    )
    ent_chips = "".join(
        f'<button type="button" class="f" data-filter="ent" data-value="{esc(e)}" aria-pressed="false">{esc(e)}<span class="mono">{n}</span></button>'
        for e, n in ent_counts.most_common()
    )
    paper_stat = (
        f'<div class="stat"><b>{len(report["papers"])}</b><span>技術文獻(篇)</span></div>' if "papers" in report else ""
    )
    archive_html = "".join(
        f'<a href="{home}{"index.html" if i == 0 else d + ".html"}"{" aria-current=page" if d == current else ""}>{d} · {n}</a>'
        for i, (d, n) in enumerate(archive)
    )
    data_links = f'<a href="{home}data/{current}.md">Markdown</a> · <a href="{home}data/{current}.json">JSON</a>'

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>靶材產業新聞摘要 {esc(current)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@600;800&family=Noto+Sans+TC:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{STYLE}</style></head><body><div class="wrap">
<header><div class="eyebrow">Sputtering Target · Industry Bulletin</div><h1>靶材產業新聞摘要</h1>
<div class="meta"><span>{esc(current)} · 最近 {esc(report.get("days", ""))} 天 · 產生於 <span class="mono">{esc(generated)}</span></span><span>原始資料:{data_links}</span></div>
<div class="stats"><div class="stat"><b>{len(items)}</b><span>蒐集到的新聞(則)</span></div>
<div class="stat"><b>{len(signal)}</b><span>其中新聞與公司公告</span></div>
<div class="stat"><b>{len(covered)} / {len(companies)}</b><span>有報導的公司</span></div>{paper_stat}</div></header>
<section aria-labelledby="sum"><h2 id="sum">本期總結</h2>{summary}</section>
<section aria-labelledby="top" class="top"><h2 id="top">重點新聞 <span class="count">新聞與公告中評分最高的 {len(top)} 則,每家最多 2 則</span></h2><div class="news">{top_html}</div></section>
{_papers_section(report)}
<section aria-labelledby="all"><h2 id="all">全部新聞 <span class="count">{len(items)} 則</span></h2>
<div class="filters" role="group" aria-label="篩選新聞"><div class="frow"><span class="lbl">類型</span>{kind_chips}</div>
<div class="frow"><span class="lbl">公司</span>{ent_chips}</div>
<div class="frow"><span class="shown" id="shown" aria-live="polite">顯示 {len(items)} / {len(items)} 則</span><button type="button" class="reset" id="reset" hidden>清除篩選</button></div></div>
<div class="news" id="list">{"".join(_item_html(it) for it in items)}</div></section>
<section aria-labelledby="ledger"><h2 id="ledger">追蹤清單 <span class="count">{len(companies)} 家 · 本期報導則數</span></h2><div class="ledger">{_ledger(config, ent_counts)}</div></section>
<section aria-labelledby="arc"><h2 id="arc">歷次報告</h2><div class="archive">{archive_html}</div></section>
<div class="note"><p><strong>資料來源</strong>:Google News RSS(繁中、簡中、英、日、韓),由靶材新聞 Agent 每日自動蒐集。</p>
<p><strong>技術文獻</strong>:OpenAlex 學術索引、arXiv 預印本與期刊 RSS,每篇只出現一次。</p>
<p><strong>類型標籤</strong>依標題與來源自動判斷;灰色標籤(行情數據、論壇、市場報告、資料頁)參考價值較低。</p></div>
<footer>資料來源為公開新聞,摘要為改寫整理,不構成投資建議。</footer>
</div><script>{SCRIPT}</script></body></html>"""


def build_site(reports_dir: str | Path, out_dir: str | Path, config: Config) -> list[Path]:
    reports_dir, out = Path(reports_dir), Path(out_dir)
    paths = sorted((p for p in reports_dir.iterdir() if REPORT_RE.match(p.name)), reverse=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    reports = [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    archive = [(d, len(r.get("items", []))) for d, r in reports]
    written = []
    for i, (date, report) in enumerate(reports):
        page = render_page(report, config, archive, date)
        targets = [out / f"{date}.html"] + ([out / "index.html"] if i == 0 else [])
        for t in targets:
            t.write_text(page, encoding="utf-8")
            written.append(t)
        for ext in (".json", ".md"):
            src = reports_dir / f"{date}{ext}"
            if src.exists():
                shutil.copyfile(src, out / "data" / src.name)
    if not reports:
        empty = {"items": [], "days": config.settings.days}
        (out / "index.html").write_text(render_page(empty, config, [], "尚無報告"), encoding="utf-8")
        written.append(out / "index.html")
    return written


def write_redirect(root_dir: str | Path, target_dir: str | Path) -> Path:
    """index.html in root_dir that forwards to the news site in target_dir (a subfolder of root_dir)."""
    root, target = Path(root_dir), Path(target_dir)
    rel = target.resolve().relative_to(root.resolve()).as_posix().rstrip("/") + "/"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "index.html"
    path.write_text(
        "<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
        f"<meta http-equiv='refresh' content='0; url={esc(rel)}'><link rel='canonical' href='{esc(rel)}'>"
        f"<title>靶材產業新聞摘要</title></head><body><p><a href='{esc(rel)}'>前往靶材產業新聞摘要</a></p></body></html>",
        encoding="utf-8",
    )
    return path


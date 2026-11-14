"""Company tracking pages and site-wide search.

- companies.html lists every tracked company with its report count over the last COMPANY_DAYS days,
  and co-<slug>.html shows one company's stories over those days as a timeline (reprints folded into
  one event, low-value items collapsed), with the topic mix and the most recent dates.
- search.html searches every report kept in reports/ (a year by default). Its index is a separate
  file, search-index.json, loaded when the page opens. On the protected site protect.py replaces it
  with search-index.bin, gzip-compressed and encrypted with the same key as the pages, and the page
  decrypts it with the key the unlock page remembered.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, timedelta

from .config import Config, Entity
from .site import (
    KINDS,
    SCRIPT,
    SIGNAL_KINDS,
    _fab,
    _figures,
    _h2,
    _head,
    _item_html,
    _ledger,
    _mast,
    esc,
    hbar,
    icon,
    is_low,
    item_kind,
    pick_events,
    short_name,
    source_name,
)
from .translate import to_traditional

COMPANY_DAYS = 30
SEARCH_INDEX = "search-index.json"
SUMMARY_CHARS = 140


def slug(entity: Entity | str) -> str:
    """File-name-safe key: the Latin name or the name in brackets, else a short hash."""
    name = entity.name if isinstance(entity, Entity) else entity
    for part in (re.search(r"\(([^)]*)\)", name), name):
        text = part.group(1) if isinstance(part, re.Match) else part
        if text and re.fullmatch(r"[A-Za-z0-9 .&'-]+", text.strip()):
            s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            if s:
                return s
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


def company_href(name: str, config: Config) -> str:
    return f"co-{slug(name)}.html" if any(e.name == name and not e.is_topic for e in config.entities) else ""


def recent_items(reports: list[tuple[str, dict]], days: int) -> list[dict]:
    """Items from the reports of the last `days` days (newest report first), each story once."""
    if not reports:
        return []
    latest = date.fromisoformat(reports[0][0])
    seen, out = set(), []
    for d, r in reports:
        if (latest - date.fromisoformat(d)).days >= days:
            break
        for it in r.get("items", []):
            if it.get("id") not in seen:
                seen.add(it.get("id"))
                out.append(dict(it))
    return out


def _by_company(items: list[dict], config: Config) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {e.name: [] for e in config.entities if not e.is_topic}
    for it in items:
        for e in it.get("entities", []):
            if e in out:
                out[e].append(it)
    return out


def _sorted_new(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda it: it.get("published") or "", reverse=True)


def render_company(entity: Entity, items: list[dict], config: Config, generated: str, days: int = COMPANY_DAYS) -> str:
    for it in items:
        it["kind"] = item_kind(it)
    items = _sorted_new(items)
    events = pick_events(items, config)
    main = [ev for ev in events if not is_low(ev)]
    low = [ev for ev in events if is_low(ev)]
    key = sum(1 for ev in main if (ev.get("importance") or 0) >= 4)
    last = (items[0].get("published") or "")[:10] if items else ""
    name = short_name(entity.name)

    timeline = []
    day = None
    for ev in main:
        d = (ev.get("published") or "")[:10] or "日期不明"
        if d != day:
            if day is not None:
                timeline.append("</div>")
            timeline.append(f'<h3 class="tday"><time datetime="{esc(d)}">{esc(d.replace("-", "/"))}</time></h3><div class="cards rows">')
            day = d
        timeline.append(_item_html(ev))
    if day is not None:
        timeline.append("</div>")
    body = "".join(timeline) or f"<p>近 {days} 天沒有 {esc(name)} 的新聞或公告。</p>"
    low_html = (
        f'<details class="lowgrp"><summary>{icon("layers")}其他 {len(low)} 則參考價值較低'
        f'<span class="sub">行情與股價、論壇、市場報告、資料頁,以及 AI 評為 1 分的報導</span></summary>'
        f'<div class="cards rows">{"".join(_item_html(ev) for ev in low)}</div></details>' if low else ""
    )
    cats = Counter((ev.get("categories") or ["未分類"])[0] for ev in main if ev["kind"] in SIGNAL_KINDS)
    cat_html = (f'<section aria-labelledby="cats">{_h2("cats", "bars", "主題分布", f"近 {days} 天的新聞事件")}'
                f'<div class="ch">{hbar(cats.most_common(), "件", total=sum(cats.values()))}</div></section>' if cats else "")
    facts = [entity.region, entity.ticker and f"股票代號 {entity.ticker}"]
    lede = f"近 {days} 天 {len(items)} 則報導" + (f",合併為 {len(events)} 個事件" if len(events) < len(items) else "") + "。"
    return (
        _head(f"{name}:公司追蹤", f"{name} 近 {days} 天的靶材產業新聞時間軸。", config.settings.site_url, f"co-{slug(entity)}.html")
        + f"""<style>{SEARCH_STYLE}</style><div class="wrap">
{_mast("", [("#timeline", "時間軸"), ("#cats", "主題"), ("companies.html", "全部公司")])}
<section class="hero hero-plain" data-enter aria-labelledby="h1"><div class="hero-text">
<p class="dateline">{icon("building")}<span>{esc("、".join(f for f in facts if f) or "追蹤公司")}</span></p>
<h1 id="h1">{esc(name)}</h1>
<p class="lede">{esc(lede)}</p>
<div class="actions"><a class="btn primary" href="#timeline">看時間軸</a><a class="btn" href="search.html#q={esc(name)}">搜尋一年內的報導</a></div></div></section>
{_figures([(str(len(items)), f"近 {days} 天報導(則)"), (str(len(main)), "新聞事件"), (str(key), "重要事件(4 分以上)"), (esc(last.replace("-", "/")) or "—", "最近一次報導")])}
<section aria-labelledby="timeline">{_h2("timeline", "calendar", "時間軸", "新到舊;同一事件的多家報導合併")}
<div class="timeline">{body}{low_html}</div></section>
{cat_html}
<footer>資料來源為公開新聞,摘要為改寫整理,不構成投資建議。更新於 <span class="mono">{esc(generated)}</span>。</footer>
</div>{_fab("companies", config)}</body></html>"""
    )


def render_companies(config: Config, by_company: dict[str, list[dict]], generated: str, days: int = COMPANY_DAYS) -> str:
    counts = Counter({short_name(n): len(v) for n, v in by_company.items()})
    links = {short_name(n): f"co-{slug(n)}.html" for n in by_company}
    active = sum(1 for v in by_company.values() if v)
    busiest = sorted(((short_name(n), len(v)) for n, v in by_company.items() if v), key=lambda r: -r[1])[:10]
    return (
        _head("公司追蹤", f"追蹤清單上每家公司近 {days} 天的靶材產業新聞。", config.settings.site_url, "companies.html")
        + f"""<div class="wrap">
{_mast("", [("#list", "全部公司"), ("#busy", "報導最多")])}
<section class="hero hero-plain" data-enter aria-labelledby="h1"><div class="hero-text">
<p class="dateline">{icon("calendar")}<span>近 {days} 天,更新於 <span class="mono">{esc(generated)}</span></span></p>
<h1 id="h1">公司追蹤</h1>
<p class="lede">點公司名稱,看它近 {days} 天的新聞時間軸;拜訪客戶前快速掌握近況。</p></div></section>
{_figures([(str(len(by_company)), "追蹤公司"), (str(active), f"近 {days} 天有報導")])}
<section aria-labelledby="busy">{_h2("busy", "bars", "報導最多", f"近 {days} 天前 {len(busiest)} 家")}<div class="ch">{hbar(busiest, "則")}</div></section>
<section aria-labelledby="list">{_h2("list", "globe", "全部公司", f"近 {days} 天報導則數")}<div class="ledger">{_ledger(config, counts, links)}</div></section>
<footer>資料來源為公開新聞,摘要為改寫整理,不構成投資建議。</footer>
</div>{_fab("companies", config)}</body></html>"""
    )


def search_index(reports: list[tuple[str, dict]]) -> list[dict]:
    """Every story kept in reports/, newest first, in the short form the search page reads."""
    seen, out = set(), []
    for _, r in reports:  # newest report first: the latest copy carries the summary
        for it in r.get("items", []):
            if it.get("id") in seen:
                continue
            seen.add(it.get("id"))
            kind = item_kind(it)
            title = it.get("title_zh") or to_traditional(it.get("title") or "")
            row = {"d": (it.get("published") or "")[:10], "t": title, "u": it.get("link", ""),
                   "r": source_name(it.get("source")), "e": "|".join(dict.fromkeys(short_name(e) for e in it.get("entities", []))),
                   "k": kind}
            if it.get("title") and it["title"] != title:
                row["o"] = it["title"]
            summary = it.get("summary") or it.get("snippet_zh") or ""
            if summary:
                row["s"] = summary[:SUMMARY_CHARS]
            if it.get("importance"):
                row["i"] = it["importance"]
            if kind not in SIGNAL_KINDS or it.get("importance") == 1:
                row["l"] = 1
            out.append(row)
    out.sort(key=lambda r: r["d"], reverse=True)
    return out


SEARCH_STYLE = """
.sbox{display:flex;gap:var(--space-xs);flex-wrap:wrap}.sbox input{flex:1 1 260px;font:inherit;font-size:1.0625rem;height:48px;padding:0 var(--space-sm);border:1px solid var(--axis);border-radius:var(--radius);background:var(--surface);color:var(--fg)}
.sbox select{font:inherit;height:48px;border:1px solid var(--axis);border-radius:var(--radius);background:var(--surface);color:var(--fg);padding:0 var(--space-xs)}
.hits{display:grid;gap:var(--space-xs);margin:0;padding:0;list-style:none}.hit{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:var(--space-sm) var(--space-md);display:grid;gap:var(--space-2xs)}
.hit .m{display:flex;flex-wrap:wrap;gap:var(--space-2xs) var(--space-md);font-size:.78rem;color:var(--muted)}.hit .m time{font-family:var(--font-data)}
.hit a{color:var(--fg);font-weight:600;text-decoration:none}.hit a:hover{color:var(--accent);text-decoration:underline}.hit p{margin:0;font-size:.9rem;color:var(--muted)}
.hit.low{background:transparent;border-color:transparent}.hit.low a{color:var(--muted);font-weight:500}.hit mark{background:var(--accent-soft);color:inherit;border-radius:2px}
.tday{font-family:var(--font-data);font-size:.95rem;color:var(--muted);margin:var(--space-lg) 0 var(--space-xs)}.timeline>.tday:first-child{margin-top:0}
"""

SEARCH_SCRIPT = """
(function(){
  var KINDS = %(kinds)s, LIMIT = 200;
  var q = document.getElementById('q'), kind = document.getElementById('kind'), low = document.getElementById('low'),
      out = document.getElementById('hits'), status = document.getElementById('status'), rows = null;
  function b64(s){ return Uint8Array.from(atob(s), function(c){ return c.charCodeAt(0); }); }
  function siteKey(){
    // the unlock page remembers the derived key as site-key-<salt> in session or local storage
    var stores = [sessionStorage, localStorage];
    for (var s = 0; s < stores.length; s++) { try { for (var i = 0; i < stores[s].length; i++) {
      var k = stores[s].key(i); if (k && k.indexOf('site-key-') === 0) return stores[s].getItem(k);
    } } catch (e) {} }
    return null;
  }
  async function load(){
    try {
      var r = await fetch('search-index.bin');
      if (r.ok) {
        var key = siteKey(); if (!key) throw new Error('nokey');
        var buf = new Uint8Array(await r.arrayBuffer());
        var k = await crypto.subtle.importKey('raw', b64(key), 'AES-GCM', false, ['decrypt']);
        var plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv: buf.slice(0, 12)}, k, buf.slice(12));
        var text = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
        return JSON.parse(text);
      }
    } catch (e) { if (e.message === 'nokey') throw e; }
    var p = await fetch('search-index.json'); if (!p.ok) throw new Error('index');
    return p.json();
  }
  function esc(s){ return String(s || '').replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function mark(text, terms){
    var h = esc(text);
    terms.forEach(function(t){ if (!t) return; var re = new RegExp(t.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'gi'); h = h.replace(re, function(m){ return '<mark>' + m + '</mark>'; }); });
    return h;
  }
  function run(){
    if (!rows) return;
    var terms = q.value.trim().toLowerCase().split(/\\s+/).filter(Boolean).map(function(t){ return esc(t); });
    var raw = q.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
    try { history.replaceState(null, '', q.value.trim() ? '#q=' + encodeURIComponent(q.value.trim()) : location.pathname); } catch (e) {}
    if (!raw.length) { out.innerHTML = ''; status.textContent = '共 ' + rows.length + ' 則報導可搜尋,輸入關鍵字開始。'; return; }
    var hits = rows.filter(function(r){
      if (kind.value && r.k !== kind.value) return false;
      if (!low.checked && r.l) return false;
      var hay = (r.t + ' ' + (r.o || '') + ' ' + (r.s || '') + ' ' + r.e + ' ' + r.r).toLowerCase();
      return raw.every(function(t){ return hay.indexOf(t) !== -1; });
    });
    status.textContent = '找到 ' + hits.length + ' 則' + (hits.length > LIMIT ? ',顯示最新 ' + LIMIT + ' 則' : '') + '。';
    out.innerHTML = hits.slice(0, LIMIT).map(function(r){
      var link = /^https?:\\/\\//.test(r.u) ? '<a href="' + esc(r.u) + '" target="_blank" rel="noopener"' + (r.o ? ' title="原文:' + esc(r.o) + '"' : '') + '>' + mark(r.t, terms) + '</a>' : mark(r.t, terms);
      return '<li class="hit' + (r.l ? ' low' : '') + '"><div class="m"><time>' + esc(r.d.replace(/-/g, '/')) + '</time><span>' + esc(KINDS[r.k] || '') + '</span><span>' + esc(r.r) + '</span>'
        + (r.i ? '<span>' + '★'.repeat(r.i) + '</span>' : '') + '</div>' + link
        + (r.s ? '<p>' + mark(r.s, terms) + '</p>' : '') + (r.e ? '<div class="m"><span>' + mark(r.e.split('|').join('、'), terms) + '</span></div>' : '') + '</li>';
    }).join('');
  }
  var timer; [q, kind, low].forEach(function(el){ el.addEventListener(el === q ? 'input' : 'change', function(){ clearTimeout(timer); timer = setTimeout(run, 120); }); });
  var m = location.hash.match(/^#q=(.*)$/); if (m) { try { q.value = decodeURIComponent(m[1]); } catch (e) { q.value = m[1]; } }
  status.textContent = '載入索引中…';
  load().then(function(r){ rows = r; run(); }).catch(function(e){
    status.textContent = e.message === 'nokey' ? '請先從網站首頁輸入密碼開啟,再回到這一頁搜尋。' : '索引載入失敗,請稍後再試。';
  });
})();
"""


def render_search(config: Config, count: int, first: str, last: str, generated: str) -> str:
    kinds = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in KINDS.items())
    span = f"{first.replace('-', '/')} 至 {last.replace('-', '/')}" if first and last else ""
    script = SEARCH_SCRIPT % {"kinds": json.dumps(dict(KINDS), ensure_ascii=False)}
    return (
        _head("全站搜尋", "搜尋一年內的靶材產業新聞:標題、摘要、公司與來源。", config.settings.site_url, "search.html")
        + f"""<style>{SEARCH_STYLE}</style><div class="wrap">
{_mast("", [])}
<section class="hero hero-plain" data-enter aria-labelledby="h1"><div class="hero-text">
<p class="dateline">{icon("calendar")}<span>{esc(span)},共 {count} 則</span></p>
<h1 id="h1">全站搜尋</h1>
<p class="lede">搜尋保存中的所有每日報告:標題(含原文)、摘要、公司與來源。多個關鍵字以空白分隔,須全部符合。</p></div></section>
<section aria-labelledby="s">{_h2("s", "filter", "搜尋")}
<form class="sbox" role="search" onsubmit="return false"><input id="q" type="search" placeholder="例如:江豐 港交所、ITO、Materion" aria-label="關鍵字" autocomplete="off">
<select id="kind" aria-label="類型"><option value="">全部類型</option>{kinds}</select></form>
<p class="sub"><label><input type="checkbox" id="low"> 包含參考價值較低的項目(行情、論壇、資料頁等)</label></p>
<p class="sub" id="status" aria-live="polite"></p>
<ol class="hits" id="hits"></ol></section>
<footer>資料來源為公開新聞,摘要為改寫整理,不構成投資建議。更新於 <span class="mono">{esc(generated)}</span>。</footer>
</div><script>{script}</script>{_fab("search", config)}</body></html>"""
    )


def build_explore(reports: list[tuple[str, dict]], config: Config, out, generated: str) -> list:
    """Write companies.html, co-<slug>.html, search.html and the search index into `out`."""
    written = []
    by_company = _by_company(recent_items(reports, COMPANY_DAYS), config)
    entities = {e.name: e for e in config.entities if not e.is_topic}
    for name, items in by_company.items():
        path = out / f"co-{slug(entities[name])}.html"
        path.write_text(render_company(entities[name], items, config, generated), encoding="utf-8")
        written.append(path)
    (out / "companies.html").write_text(render_companies(config, by_company, generated), encoding="utf-8")
    written.append(out / "companies.html")
    rows = search_index(reports)
    (out / SEARCH_INDEX).write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dates = [r["d"] for r in rows if r["d"]]
    (out / "search.html").write_text(
        render_search(config, len(rows), min(dates, default=""), max(dates, default=""), generated), encoding="utf-8")
    written += [out / SEARCH_INDEX, out / "search.html"]
    return written

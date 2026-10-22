"""Static news site for GitHub Pages, built from the JSON reports in reports/.

index.html shows the latest report and YYYY-MM-DD.html keeps every earlier one.
Titles and abstracts are shown in Traditional Chinese (see translate.py). Each item gets a "kind" label (news,
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
from .icons import SPRITE, favicon_href, icon, topic_icon
from .research import GENERAL_TOPIC
from .thumbs import pick as pick_thumb
from .translate import to_traditional

OG_IMAGE = Path(__file__).with_name("assets") / "og.png"  # 1200x630 share-preview image
HOME_PAGE = "home"  # `current` value for the combined homepage
REPORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")

KINDS = OrderedDict([
    ("news", "新聞"),
    ("filing", "公司公告"),
    ("recycling", "回收循環"),
    ("market", "行情數據"),
    ("forum", "論壇與自媒體"),
    ("research", "市場報告"),
    ("profile", "資料頁"),
    ("metals", "貴金屬行情"),
])
SIGNAL_KINDS = ("news", "filing", "recycling")
RECYCLING_CATEGORY = "回收與循環"

_MARKET_RE = re.compile(r"主力资金|主力資金|股东户数|股東戶數|开盘|開盤|低开|低開|賣超|买超|買超|周KD|週KD|大阳线|跌近|涨停|漲停|Decline|shares? (?:rose|fell)|stock (?:rose|fell)", re.I)
_RESEARCH_RE = re.compile(r"市場|市场|Market|レポート|予測|CAGR", re.I)
_FILING_RE = re.compile(r"\(\d{6}\)[::]|公告")


def item_kind(item: dict) -> str:
    title, source = item.get("title", ""), item.get("source", "")
    if item.get("entities") and all(e.startswith(METAL_PREFIX) for e in item["entities"]):
        return "metals"
    if "企業情報" in title or "個股概覽" in title:
        return "profile"
    if "股市爆料同學會" in title or source in ("财富号", "財富號", "PTT", "Mobile01"):
        return "forum"
    if any(e.startswith(TOPIC_PREFIXES + (RECYCLE_PREFIX,)) for e in item.get("entities", [])) and _RESEARCH_RE.search(title):
        return "research"
    if source == "中财网" or _FILING_RE.search(title):
        return "filing"
    if _MARKET_RE.search(title):
        return "market"
    if item.get("entities") and all(e.startswith(RECYCLE_PREFIX) for e in item["entities"]):
        return "recycling"
    return "news"


# Industry-wide searches are named after their search phrase; show which language edition they cover instead.
TOPIC_PREFIXES = ("產業綜合", "設備綜合")
METAL_PREFIX = "行情綜合"
RECYCLE_PREFIX = "回收綜合"
TOPIC_LABELS = {
    "回收綜合:靶材回收": "靶材回收循環(繁中)", "回收綜合:靶材回收(简)": "靶材回收循環(簡中)",
    "回收綜合:Target Recycling": "靶材回收循環(英文)", "回收綜合:ターゲットリサイクル": "靶材回收循環(日文)",
    "行情綜合:貴金屬": "貴金屬行情(繁中)", "行情綜合:贵金属": "貴金屬行情(簡中)", "行情綜合:Precious Metals": "貴金屬行情(英文)",
    "行情綜合:貴金属相場": "貴金屬行情(日文)", "行情綜合:귀금속 시세": "貴金屬行情(韓文)",
    "設備綜合:濺鍍設備": "濺鍍設備綜合(繁中)", "設備綜合:溅射设备": "濺鍍設備綜合(簡中)",
    "設備綜合:Sputtering Equipment": "濺鍍設備綜合(英文)", "設備綜合:スパッタ装置": "濺鍍設備綜合(日文)",
    "設備綜合:스퍼터 장비": "濺鍍設備綜合(韓文)",
    "產業綜合:濺鍍靶材": "產業綜合(繁中)", "產業綜合:溅射靶材": "產業綜合(簡中)", "產業綜合:Sputtering Target": "產業綜合(英文)",
    "產業綜合:スパッタリングターゲット": "產業綜合(日文)", "產業綜合:스퍼터링 타겟": "產業綜合(韓文)",
}


def short_name(entity: str) -> str:
    return TOPIC_LABELS.get(entity) or re.sub(r"\s*\(.*\)$", "", entity)


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


STYLE = """
:root{--bg:#f4f6fb;--surface:#fff;--fg:#162033;--muted:#55607a;--line:#dfe4ef;--accent:#3b5bdb;--accent-2:#f76707;--accent-soft:#e7ecff;--hero:#e4e9ff;
--ok:#2b8a3e;--ok-soft:#e3f6e7;--none:#6b7480;--none-soft:#eceef3;
--k-news:#3b5bdb;--k-filing:#7048e8;--k-market:#0c8f6a;--k-forum:#d6336c;--k-research:#e8590c;--k-profile:#6b7480;--k-metals:#b7791f;--k-recycling:#2f9e44;--up:#e03131;--down:#2b8a3e;
--shadow:0 1px 2px rgba(20,24,28,.06),0 2px 8px rgba(20,24,28,.05);
--font-display:"Noto Serif TC","Songti TC",serif;--font-body:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;--font-data:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#0f1424;--surface:#171e33;--fg:#e8ecf6;--muted:#9aa5c0;--line:#283252;--accent:#748ffc;--accent-2:#ffa94d;--accent-soft:#1f2a52;--hero:#1a2350;
--ok:#69db7c;--ok-soft:#173322;--none:#8d96a8;--none-soft:#1f2639;
--k-news:#748ffc;--k-filing:#b197fc;--k-market:#38d9a9;--k-forum:#f783ac;--k-research:#ffa94d;--k-profile:#adb5bd;--k-metals:#ffd43b;--k-recycling:#69db7c;--up:#ff8787;--down:#69db7c;
--shadow:none;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,var(--hero) 0,var(--bg) 420px) no-repeat,var(--bg);color:var(--fg);font-family:var(--font-body);font-size:16px;line-height:1.7}
.wrap{max-width:900px;margin:0 auto;padding-inline:20px;padding-block:36px 64px;display:grid;gap:44px}
a{color:var(--accent);text-underline-offset:3px}a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.ic{width:1.15em;height:1.15em;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round;flex:none;vertical-align:-.18em}
h1,h2{font-family:var(--font-display);text-wrap:balance;margin:0}h1{font-size:clamp(1.8rem,5vw,2.5rem);font-weight:800;line-height:1.2}
h2{font-size:1.3rem;font-weight:600;display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}
h2 .hic{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:10px;background:var(--accent-soft);color:var(--accent)}
h2 .hic .ic{width:19px;height:19px}h2 .count{font-family:var(--font-data);font-size:.82rem;color:var(--muted);font-weight:400}
.eyebrow{font-family:var(--font-data);font-size:.76rem;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.mono{font-family:var(--font-data);font-variant-numeric:tabular-nums}p{margin:0;max-width:68ch}section{display:grid;gap:16px;min-width:0}
header{display:grid;gap:18px;padding-bottom:26px;border-bottom:1px solid var(--line)}
.brand{display:flex;gap:16px;align-items:center}.brand .logo{width:56px;height:56px;border-radius:16px;display:grid;place-items:center;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;flex:none;box-shadow:0 6px 18px color-mix(in srgb,var(--accent) 35%,transparent)}
.brand .logo .ic{width:34px;height:34px;stroke-width:1.6}.brand>div{display:grid;gap:2px;min-width:0}
.meta{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:.88rem;align-items:center}.meta>*{display:inline-flex;gap:6px;align-items:center}
.btn{display:inline-flex;gap:6px;align-items:center;padding:5px 12px;border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--fg);text-decoration:none;font-size:.85rem}
.btn:hover{border-color:var(--accent);color:var(--accent)}.btn.primary{background:linear-gradient(135deg,var(--accent),color-mix(in srgb,var(--accent) 70%,var(--accent-2)));border-color:transparent;color:#fff}.btn.primary:hover{color:#fff;opacity:.92}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{display:flex;gap:12px;align-items:center;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow)}
.stat .sic{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;flex:none;background:var(--accent-soft);color:var(--accent)}
.stat .sic .ic{width:22px;height:22px}.stat b{display:block;font-family:var(--font-data);font-size:1.45rem;font-weight:500;line-height:1.15}.stat span{font-size:.8rem;color:var(--muted)}
.fc-stats .stat>div:last-child{min-width:0}.fc-stats .stat b{font-size:1.15rem;white-space:normal;word-break:keep-all}.fc-stats .stat span{display:block}
.fc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:12px;margin-top:14px}.card.fc h3{display:flex;align-items:center;gap:8px;margin:0;font-size:1rem}.card.fc h3 .ic{width:20px;height:20px;color:var(--accent)}.card.fc li{line-height:1.6}
.lead{font-size:1.04rem}ul.plain{margin:0;padding-left:1.2em;display:grid;gap:4px}
.filters{display:grid;gap:10px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.frow{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.frow .lbl{font-size:.8rem;color:var(--muted);margin-right:4px;min-width:3.2em;display:inline-flex;gap:4px;align-items:center}
button.f{font:inherit;font-size:.84rem;border:1px solid var(--line);background:var(--bg);color:var(--fg);padding:4px 11px;border-radius:999px;cursor:pointer;display:inline-flex;gap:6px;align-items:center}
button.f .mono{font-size:.72rem;color:var(--muted)}button.f:hover{border-color:var(--accent)}
button.f[aria-pressed=true]{background:var(--fg);color:var(--bg);border-color:var(--fg)}button.f[aria-pressed=true] .mono{color:var(--bg)}
.shown{font-size:.84rem;color:var(--muted)}button.reset{font:inherit;font-size:.84rem;background:none;border:0;color:var(--accent);cursor:pointer;text-decoration:underline;padding:0}
.co{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.78rem;color:var(--muted);align-items:center}.co>span{display:inline-flex;gap:4px;align-items:center}.co .ic{width:13px;height:13px}
.kind{font-size:.72rem;padding:1px 8px;border-radius:999px;font-weight:500;color:var(--k);background:color-mix(in srgb,var(--k) 13%,transparent)}
.k-news{--k:var(--k-news)}.k-filing{--k:var(--k-filing)}.k-market{--k:var(--k-market)}.k-forum{--k:var(--k-forum)}.k-research{--k:var(--k-research)}.k-profile{--k:var(--k-profile)}.k-metals{--k:var(--k-metals)}.k-recycling{--k:var(--k-recycling)}
.quotes{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.quote{background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--k-metals);border-radius:12px;padding:12px 16px;display:grid;gap:2px;box-shadow:var(--shadow)}
.quote .qn{font-size:.85rem;color:var(--muted);display:flex;gap:6px;align-items:center}.quote .qn .mono{font-family:var(--font-data)}
.quote b{font-family:var(--font-data);font-size:1.3rem;font-weight:500}.quote .chg{font-family:var(--font-data);font-size:.85rem}.quote .chg.up{color:var(--up)}.quote .chg.down{color:var(--down)}
.quote .qd{font-size:.75rem;color:var(--muted)}
[hidden]{display:none!important}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.card{--k:var(--accent);display:grid;gap:10px;align-content:start;background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--k);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow);min-width:0}
.card .head{display:flex;gap:8px;align-items:center;justify-content:space-between}.card .head time{font-family:var(--font-data);font-size:.76rem;color:var(--muted)}
.card h3{margin:0;font-size:1rem;font-weight:700;line-height:1.5;overflow-wrap:anywhere}
.card h3 a{color:var(--fg);text-decoration:none}.card h3 a:hover{color:var(--accent);text-decoration:underline}.card .sum{margin:0;font-size:.9rem;line-height:1.7;color:var(--muted)}
.kind{display:inline-flex;gap:4px;align-items:center}.kind .ic{width:13px;height:13px}
.card.low{background:transparent;box-shadow:none}.card.low h3{font-weight:500}.card.low h3 a{color:var(--muted)}
.card.has-thumb{grid-template-columns:76px minmax(0,1fr);column-gap:14px}.card.has-thumb>*{grid-column:2}
.card.has-thumb>.thumb{grid-column:1;grid-row:1/span 4;align-self:start}
.thumb{position:relative;width:76px;height:76px;border-radius:12px;display:grid;place-items:center;align-content:center;gap:3px;color:var(--k);background:linear-gradient(140deg,color-mix(in srgb,var(--k) 24%,var(--surface)),color-mix(in srgb,var(--k) 7%,var(--surface)));border:1px solid color-mix(in srgb,var(--k) 28%,transparent)}
.thumb .ic{width:32px;height:32px;stroke-width:1.5;vertical-align:0}.thumb .cap{font-size:.66rem;line-height:1;color:var(--muted);white-space:nowrap}
.thumb .sym{position:absolute;right:-5px;bottom:-5px;min-width:24px;height:22px;padding:0 5px;border-radius:7px;background:var(--k);color:var(--surface);font-family:var(--font-data);font-size:.7rem;font-weight:500;display:grid;place-items:center;box-shadow:0 0 0 2px var(--surface)}
.card.low .thumb{filter:saturate(.55);opacity:.85}
.stats .stat:nth-child(1){--c:var(--k-news)}.stats .stat:nth-child(2){--c:var(--k-market)}.stats .stat:nth-child(3){--c:var(--k-filing)}.stats .stat:nth-child(4){--c:var(--k-research)}
.stats .stat .sic{background:color-mix(in srgb,var(--c,var(--accent)) 15%,transparent);color:var(--c,var(--accent))}.stats .stat{border-top:3px solid var(--c,var(--accent))}
/* per-type colour wins over the .card default */
.card.k-news{--k:var(--k-news)}.card.k-filing{--k:var(--k-filing)}.card.k-market{--k:var(--k-market)}.card.k-forum{--k:var(--k-forum)}.card.k-research{--k:var(--k-research)}.card.k-profile{--k:var(--k-profile)}.card.k-metals{--k:var(--k-metals)}.card.k-recycling{--k:var(--k-recycling)}
.ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.region{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 16px}
.region h3{margin:0 0 4px;font-size:.85rem;color:var(--muted);font-weight:500;display:flex;gap:8px;align-items:center}
.rb{display:inline-grid;place-items:center;min-width:30px;height:24px;padding:0 6px;border-radius:7px;background:var(--accent-soft);color:var(--accent);font-family:var(--font-data);font-size:.72rem;font-weight:500;letter-spacing:.04em}
.region ul{list-style:none;margin:0;padding:0}.region li{display:grid;grid-template-columns:1fr auto;align-items:center;gap:10px;padding:6px 0;border-bottom:1px dashed var(--line);font-size:.92rem}
.region li:last-child{border-bottom:0}
.tk{font-family:var(--font-data);font-size:.72rem;color:var(--muted);margin-left:6px}.pill{font-size:.72rem;padding:1px 9px;border-radius:99px;white-space:nowrap}
.pill.yes{background:var(--ok-soft);color:var(--ok);font-weight:700}.pill.no{background:var(--none-soft);color:var(--none)}
.archive{display:flex;flex-wrap:wrap;gap:8px}.archive a{display:inline-flex;gap:6px;align-items:center;font-family:var(--font-data);font-size:.84rem;border:1px solid var(--line);background:var(--surface);color:var(--fg);padding:4px 12px;border-radius:999px;text-decoration:none}
.archive a:hover{border-color:var(--accent)}.archive a[aria-current=page]{background:var(--fg);color:var(--bg);border-color:var(--fg)}
h3.topic{margin:8px 0 0;font-size:.98rem;font-weight:700;display:flex;gap:10px;align-items:center}
h3.topic .tic{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent)}h3.topic .tic .ic{width:17px;height:17px}
h3.topic .mono{font-size:.8rem;color:var(--muted);font-weight:400}
.note{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px;display:grid;gap:10px;font-size:.88rem;color:var(--muted)}
.note p{display:flex;gap:10px;align-items:flex-start}.note .ic{margin-top:.25em;color:var(--accent)}.note strong{color:var(--fg)}
footer{font-size:.8rem;color:var(--muted);border-top:1px solid var(--line);padding-top:18px}
.sub{font-size:.88rem;color:var(--muted)}
@media (max-width:520px){.stat{padding:12px;gap:10px}.stat .sic{width:36px;height:36px}.stat b{font-size:1.25rem}.card{padding:12px 14px}.card.has-thumb{grid-template-columns:56px minmax(0,1fr);column-gap:12px}.thumb{width:56px;height:56px;border-radius:10px}.thumb .ic{width:24px;height:24px}.thumb .cap{display:none}.brand .logo{width:46px;height:46px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

SCRIPT = """
(function(){var items=[].slice.call(document.querySelectorAll('#list > article')),shown=document.getElementById('shown'),reset=document.getElementById('reset'),
buttons=[].slice.call(document.querySelectorAll('button.f')),active={kind:null,ent:null};
function apply(){var n=0;items.forEach(function(el){var ok=(!active.kind||el.dataset.kind===active.kind)&&(!active.ent||el.dataset.ent.split('|').indexOf(active.ent)!==-1);el.hidden=!ok;if(ok)n++;});
buttons.forEach(function(b){b.setAttribute('aria-pressed',String(active[b.dataset.filter]===b.dataset.value));});shown.textContent='顯示 '+n+' / '+items.length+' 則';reset.hidden=!active.kind&&!active.ent;}
buttons.forEach(function(b){b.addEventListener('click',function(){var k=b.dataset.filter;active[k]=active[k]===b.dataset.value?null:b.dataset.value;apply();});});
reset.addEventListener('click',function(){active={kind:null,ent:null};apply();});})();
"""


def _title_link(item: dict) -> str:
    original = item["title"]
    shown = item.get("title_zh") or to_traditional(original)
    tip = f' title="原文:{esc(original)}"' if shown != original else ""
    link = item.get("link", "")
    if link.startswith(("https://", "http://")):
        return f'<a href="{esc(link)}" target="_blank" rel="noopener"{tip}>{esc(shown)}</a>'
    return f"<span{tip}>{esc(shown)}</span>"  # never turn javascript:/data: links from feeds into clickable links


KIND_ICONS = {"news": "news", "filing": "filing", "market": "market", "forum": "forum", "research": "research", "profile": "profile", "metals": "gem", "recycling": "recycle"}


def _date(published: str | None, full: bool = False) -> str:
    if not published:
        return "日期不明"
    d = published[:10].replace("-", "/")
    return d if full else d[5:]


def _news_summary(item: dict) -> str:
    """Card summary: Claude's summary, else the feed's snippet when it adds something beyond the title."""
    if item.get("summary"):
        return item["summary"]
    snippet = item.get("snippet_zh") or to_traditional(item.get("snippet") or "")
    titles = (item.get("title_zh") or "", to_traditional(item.get("title") or ""))
    return snippet if snippet and not any(snippet in t for t in titles) else ""


def _thumb(motif: str, caption: str, symbol: str = "") -> str:
    """Content thumbnail: a picture of what the story is about, plus the material when named."""
    sym = f'<span class="sym">{esc(symbol)}</span>' if symbol else ""
    return (
        f'<div class="thumb" role="img" aria-label="{esc(caption + (" · " + symbol if symbol else ""))}">'
        f'{icon(motif)}<span class="cap">{esc(caption)}</span>{sym}</div>'
    )


def _card(kind_class: str, label: str, label_icon: str, published: str | None, title: str, summary: str,
          meta: str, attrs: str = "", extra_class: str = "", full_date: bool = False, thumb: str = "") -> str:
    """One card: thumbnail, then label + date, title, summary and source details."""
    date = esc((published or "")[:10])
    sum_html = f'<p class="sum">{esc(summary)}</p>' if summary else ""
    return (
        f'<article class="card {kind_class}{extra_class}{" has-thumb" if thumb else ""}"{attrs}>{thumb}'
        f'<div class="head"><span class="kind {kind_class}">{icon(label_icon)}{esc(label)}</span>'
        f'<time datetime="{date}">{_date(published, full=full_date)}</time></div>'
        f"<h3>{title}</h3>{sum_html}<div class=\"co\">{meta}</div></article>"
    )


# Japanese / Korean outlet names shown in Traditional Chinese (Latin brand names stay as they are).
SOURCE_ZH = {
    "Yahoo!ファイナンス": "Yahoo!財經(日本)", "日本経済新聞": "日本經濟新聞", "日経会社情報DIGITAL": "日經公司資訊",
    "ドリームニュース": "Dream News", "アットプレス": "@Press", "株探": "股探 Kabutan", "みんかぶ": "Minkabu",
    "日刊工業新聞": "日刊工業新聞", "化学工業日報": "化學工業日報", "電子デバイス産業新聞": "電子元件產業新聞",
    "연합뉴스": "韓聯社", "전자신문": "電子新聞(韓國)", "조선비즈": "朝鮮Biz", "한국경제": "韓國經濟新聞", "매일경제": "每日經濟新聞",
}


def source_name(source: str | None) -> str:
    return SOURCE_ZH.get(source or "", to_traditional(source or ""))


def _news_meta(item: dict) -> str:
    ents = [short_name(e) for e in item.get("entities", [])]
    return "".join(f'<span>{icon("building")}{esc(e)}</span>' for e in ents) + (
        f'<span>{icon("news")}{esc(source_name(item.get("source")))}</span>' if item.get("source") else ""
    )


def _item_html(item: dict, extra_class: str = "") -> str:
    """News card for the filterable list."""
    kind = item["kind"]
    ents = [short_name(e) for e in item.get("entities", [])]
    stars = f" {'★' * item['importance']}" if item.get("importance") else ""
    low = "" if kind in SIGNAL_KINDS else " low"
    attrs = f' data-kind="{kind}" data-ent="{esc("|".join(ents))}"'
    return _card(f"k-{kind}", KINDS[kind], KIND_ICONS[kind], item.get("published"), _title_link(item) + stars,
                 _news_summary(item), _news_meta(item), attrs, low + extra_class, thumb=_thumb(*pick_thumb(item)))


def _card_html(item: dict) -> str:
    """Top-news card."""
    return _item_html(item, " top")


SOURCE_ICONS = {"OpenAlex": "database", "arXiv": "filing"}


def _paper_html(p: dict, filterable: bool = False) -> str:
    authors = p.get("authors") or []
    author_text = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")
    stars = f" {'★' * p['importance']}" if p.get("importance") else ""
    if p.get("summary"):
        summary = p["summary"]
    elif p.get("abstract"):
        abstract = p.get("abstract_zh") or to_traditional(p["abstract"])
        summary = abstract[:200] + ("…" if len(abstract) > 200 else "")
    else:
        summary = ""
    topic = (p.get("topics") or [GENERAL_TOPIC])[0]
    meta = ""
    if p.get("venue"):
        meta += f'<span>{icon(SOURCE_ICONS.get(p.get("source", ""), "book"))}{esc(p["venue"])}</span>'
    if author_text:
        meta += f'<span>{icon("user")}{esc(author_text)}</span>'
    attrs = ""
    if filterable:
        attrs = f' data-kind="{esc(p.get("source", ""))}" data-ent="{esc("|".join(p.get("topics") or [GENERAL_TOPIC]))}"'
    from .thumbs import material

    thumb = _thumb(topic_icon(topic), "技術文獻", material(f"{p.get('title', '')} {p.get('title_zh', '')}"))
    return _card("k-research", topic, topic_icon(topic), p.get("published"), _title_link(p) + stars,
                 summary, meta, attrs, full_date=filterable, thumb=thumb)


def _metals_section(metals: list[dict], kinds: Counter) -> str:
    if not metals:
        return ""
    cards = []
    for m in metals:
        chg = m.get("change_pct")
        if chg is None:
            chg_html = '<span class="chg">—</span>'
        else:
            cls = "up" if chg > 0 else "down" if chg < 0 else ""
            arrow = "▲" if chg > 0 else "▼" if chg < 0 else ""
            chg_html = f'<span class="chg {cls}">{arrow} {chg:+.2f}%</span>'
        cards.append(
            f'<div class="quote"><span class="qn">{icon("gem")}{esc(m["name"])} <span class="mono">{esc(m["code"])}</span></span>'
            f'<b>{m["price"]:,.2f}</b>{chg_html}<span class="qd">{esc(m["unit"])} · {esc(m["date"])} 收盤</span></div>'
        )
    sources = "、".join(dict.fromkeys(m.get("source", "") for m in metals if m.get("source")))
    news = f";相關行情新聞 {kinds['metals']} 則,可在下方「全部新聞」選擇「貴金屬行情」查看" if kinds.get("metals") else ""
    return (
        f'<section aria-labelledby="metals">{_h2("metals", "gem", "貴金屬行情", "日收盤價與漲跌(漲紅跌綠)")}'
        f'<div class="quotes">{"".join(cards)}</div><p class="sub">報價來源:{esc(sources)}{news}。</p></section>'
    )


def _recycling_section(items: list[dict], config: Config) -> str:
    """News from the target-recycling searches plus company news classified as 回收與循環."""
    if not any(e.role == "recycling" for e in config.entities):
        return ""
    picked = [it for it in items if it["kind"] in SIGNAL_KINDS
              and (it["kind"] == "recycling" or RECYCLING_CATEGORY in (it.get("categories") or []))]
    picked.sort(key=lambda it: (it.get("importance", 0), it.get("score", 0)), reverse=True)
    shown = picked[:8]
    body = (f'<div class="cards">{"".join(_card_html(it) for it in shown)}</div>' if shown
            else "<p>本期沒有靶材回收循環相關新聞。</p>")
    more = (f'<p class="sub">共 {len(picked)} 則,僅列評分最高的 {len(shown)} 則;其餘可在下方「全部新聞」選擇「回收循環」查看。</p>'
            if len(picked) > len(shown) else "")
    return (
        f'<section aria-labelledby="recycle">{_h2("recycle", "recycle", "靶材回收循環", "廢靶回收、稀有/貴金屬精煉再生、循環經濟")}'
        f"{body}{more}</section>"
    )


def _h2(id_: str, icon_name: str, title: str, count: str = "") -> str:
    count_html = f' <span class="count">{count}</span>' if count else ""
    return f'<h2 id="{id_}"><span class="hic">{icon(icon_name)}</span>{title}{count_html}</h2>'


PAPER_SOURCES_NOTE = (
    "來源:OpenAlex、arXiv 與期刊 RSS(Thin Solid Films、Surface and Coatings Technology、Vacuum 等);"
    "只收錄標題或摘要明確談到濺鍍靶材的研究。"
)


def _grouped_papers(papers: list[dict]) -> str:
    groups: "OrderedDict[str, list]" = OrderedDict()
    ranked = sorted(papers, key=lambda p: (p.get("importance", 0), p.get("published") or ""), reverse=True)
    for p in ranked:
        groups.setdefault((p.get("topics") or [GENERAL_TOPIC])[0], []).append(p)
    return "".join(
        f'<h3 class="topic"><span class="tic">{icon(topic_icon(t))}</span>{esc(t)} <span class="mono">{len(ps)}</span></h3>'
        f'<div class="cards">{"".join(_paper_html(p) for p in ps)}</div>'
        for t, ps in groups.items()
    )


def _papers_section(report: dict, library: list[dict], is_latest: bool, home: str = "") -> str:
    papers = report.get("papers")
    link = (
        f'<p><a class="btn" href="{home}papers.html">{icon("book")}查看技術文獻庫:累積收錄的全部 {len(library)} 篇,可依製程主題篩選 {icon("arrow-right")}</a></p>'
        if library else ""
    )
    if papers:
        days = report.get("research_days") or 30
        span = report.get("span") or []
        when = f"最近 {len(span)} 天新收錄" if len(span) > 1 else "本期新收錄"
        count = f"{when} {len(papers)} 篇 · 依製程分類"
        inner = _grouped_papers(papers)
    elif is_latest and library:
        # Homepage: no new papers today, so show the most recently collected ones.
        recent = library[:10]
        count = f"本期無新收錄 · 以下為最近收錄的 {len(recent)} 篇"
        inner = '<div class="cards">' + "".join(_paper_html(p) for p in recent) + "</div>"
    elif "papers" in report or is_latest:
        count = "本期沒有新收錄的文獻"
        inner = "<p>本期沒有新的靶材製造技術文獻。</p>"
    else:
        return ""  # older reports from before papers were collected
    return (
        f'<section aria-labelledby="papers">{_h2("papers", "book", "技術文獻:靶材製造技術", count)}'
        f'<p class="sub">{PAPER_SOURCES_NOTE}</p>{inner}{link}</section>'
    )


def paper_library(reports: list[tuple[str, dict]]) -> list[dict]:
    """Every paper across all reports, one entry per paper ID, newest publication first."""
    seen: dict[str, dict] = {}
    for date, report in sorted(reports):  # oldest report first, so "collected" is the first sighting
        for p in report.get("papers") or []:
            if p.get("id") and p["id"] not in seen:
                seen[p["id"]] = {**p, "collected": date}
    return sorted(seen.values(), key=lambda p: (p.get("published") or "", p["collected"]), reverse=True)


SITE_DESCRIPTION = "每日彙整全球濺鍍靶材產業新聞、公司公告、貴金屬行情與技術文獻,附 AI 繁體中文摘要。"


def _share_meta(title: str, description: str, site_url: str, page: str) -> str:
    """Description + Open Graph / Twitter tags so shared links get a preview card (LINE, Slack, Teams)."""
    tags = [f'<meta name="description" content="{esc(description)}">',
            '<meta property="og:type" content="website">', '<meta property="og:site_name" content="靶材產業新聞摘要">',
            '<meta property="og:locale" content="zh_TW">',
            f'<meta property="og:title" content="{esc(title)}">', f'<meta property="og:description" content="{esc(description)}">',
            '<meta name="twitter:card" content="summary_large_image">', f'<meta name="twitter:title" content="{esc(title)}">',
            f'<meta name="twitter:description" content="{esc(description)}">']
    if site_url:  # crawlers need absolute URLs
        base = site_url.rstrip("/") + "/"
        image = base + OG_IMAGE.name
        tags += [f'<meta property="og:url" content="{esc(base + page)}">', f'<meta property="og:image" content="{esc(image)}">',
                 '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">',
                 f'<meta name="twitter:image" content="{esc(image)}">']
    return "".join(tags)


def _head(title: str, description: str = SITE_DESCRIPTION, site_url: str = "", page: str = "") -> str:
    return (
        '<!doctype html>\n<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)}</title>{_share_meta(title, description, site_url, page)}<link rel="icon" href="{favicon_href()}">'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@600;800&family=Noto+Sans+TC:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">'
        f"<style>{STYLE}</style></head><body>{SPRITE}"
    )


def _brand(eyebrow: str, title: str) -> str:
    return (
        f'<div class="brand"><div class="logo">{icon("target")}</div>'
        f'<div><div class="eyebrow">{esc(eyebrow)}</div><h1>{esc(title)}</h1></div></div>'
    )


def _stat(icon_name: str, value: str, label: str) -> str:
    return f'<div class="stat"><div class="sic">{icon(icon_name)}</div><div><b>{value}</b><span>{label}</span></div></div>'


FORECAST_ICONS = {"市場概況": "market", "用途與材料": "layers", "技術路線": "chip", "價格": "tag", "主要廠商": "building"}


def _figure(x: dict) -> str:
    note = f'<span>{esc(x["note"])}</span>' if x.get("note") else ""
    return (f'<div class="stat"><div class="sic">{icon("bars")}</div><div><b>{esc(x.get("value", ""))}</b>'
            f'<span>{esc(x.get("label", ""))}</span>{note}</div></div>')


def render_forecasts(config: Config, generated: str) -> str:
    """商情預測: summaries of market-forecast reports (config/forecasts.yaml), with attribution."""
    sections = []
    for n, f in enumerate(config.forecasts):
        figures = "".join(_figure(x) for x in f.get("figures") or [])
        groups = "".join(
            f'<article class="card fc"><h3>{icon(FORECAST_ICONS.get(g.get("title", ""), "info"))}{esc(g.get("title", ""))}</h3>'
            f'<ul class="plain">{"".join(f"<li>{esc(i)}</li>" for i in g.get("items") or [])}</ul></article>'
            for g in f.get("points") or []
        )
        url = f.get("url", "") if str(f.get("url", "")).startswith(("https://", "http://")) else ""
        source = f'<a href="{esc(url)}" rel="noopener" target="_blank">{esc(f.get("source", ""))}</a>' if url else esc(f.get("source", ""))
        sections.append(
            f'<section aria-labelledby="fc{n}">{_h2(f"fc{n}", "research", esc(f.get("section") or f.get("report", "")))}'
            f'<p class="sub">{icon("book")}{source}《{esc(f.get("report", ""))}》· 發刊 <span class="mono">{esc(f.get("published", ""))}</span></p>'
            f'<p class="sub">範圍:{esc(f.get("scope", ""))}</p>'
            f'<div class="stats fc-stats">{figures}</div><div class="fc-grid">{groups}</div></section>'
        )
    body = "".join(sections) or "<p>尚未加入商情預測資料(config/forecasts.yaml)。</p>"
    return (
        _head("靶材商情預測", "濺鍍靶材市場規模預測、用途與材料趨勢、技術路線與主要廠商動向的重點摘要。",
              config.settings.site_url, "forecast.html")
        + f"""<div class="wrap">
<header>{_brand("Sputtering Target · Market Outlook", "靶材商情預測")}
<nav class="meta" aria-label="網站導覽"><a class="btn" href="index.html">{icon("arrow-left")}新聞摘要</a><a class="btn" href="papers.html">{icon("book")}技術文獻庫</a><span>{icon("calendar")}更新於 <span class="mono">{esc(generated)}</span></span></nav></header>
{body}
<footer>本頁為市場調查報告的重點摘要,經改寫整理並註明出處;完整數據請參閱原報告。預測不構成投資建議。</footer>
</div></body></html>"""
    )


def render_library(library: list[dict], config: Config, generated: str) -> str:
    topics = Counter(t for p in library for t in (p.get("topics") or [GENERAL_TOPIC]))
    sources = Counter(p.get("source", "") for p in library)
    order = list(config.research.topics) + [GENERAL_TOPIC] if config.research else list(topics)
    topic_chips = "".join(
        f'<button type="button" class="f" data-filter="ent" data-value="{esc(t)}" aria-pressed="false">{icon(topic_icon(t))}{esc(t)}<span class="mono">{topics[t]}</span></button>'
        for t in order if topics.get(t)
    )
    source_chips = "".join(
        f'<button type="button" class="f" data-filter="kind" data-value="{esc(s)}" aria-pressed="false">{icon(SOURCE_ICONS.get(s, "book"))}{esc(s)}<span class="mono">{n}</span></button>'
        for s, n in sources.most_common() if s
    )
    body = (
        "".join(_paper_html(p, filterable=True) for p in library)
        if library else "<p>尚未收錄任何文獻;每日執行後會累積在這裡。</p>"
    )
    return (
        _head("靶材技術文獻庫", "濺鍍靶材製造技術文獻庫:粉末冶金、熔煉、接合、純化、靶材回收等期刊與預印本,附繁體中文摘要。",
              config.settings.site_url, "papers.html")
        + f"""<div class="wrap">
<header>{_brand("Sputtering Target · Research Library", "靶材技術文獻庫")}
<nav class="meta" aria-label="網站導覽"><a class="btn" href="index.html">{icon("arrow-left")}新聞摘要</a><span>{icon("calendar")}更新於 <span class="mono">{esc(generated)}</span></span></nav>
<div class="stats">{_stat("book", str(len(library)), "累積收錄文獻(篇)")}{_stat("layers", str(len([t for t in topics if t != GENERAL_TOPIC])), "涵蓋製程主題")}{_stat("database", str(len([s for s in sources if s])), "資料來源")}</div></header>
<section aria-labelledby="lib">{_h2("lib", "list", "全部文獻", "依發表日期,新到舊")}
<p class="sub">{PAPER_SOURCES_NOTE}</p>
<div class="filters" role="group" aria-label="篩選文獻"><div class="frow"><span class="lbl">{icon("filter")}主題</span>{topic_chips}</div>
<div class="frow"><span class="lbl">{icon("database")}來源</span>{source_chips}</div>
<div class="frow"><span class="shown" id="shown" aria-live="polite">顯示 {len(library)} / {len(library)} 篇</span><button type="button" class="reset" id="reset" hidden>清除篩選</button></div></div>
<div class="cards" id="list">{body}</div></section>
<footer>文獻摘要為改寫整理;引用請以原文為準。</footer>
</div><script>{SCRIPT.replace("則", "篇")}</script></body></html>"""
    )


REGION_CODES = {"日本": "JP", "中國大陸": "CN", "台灣": "TW", "韓國": "KR", "歐美": "EU·US", "其他": "··"}


NON_PRODUCER_GROUPS = {"distributor": "銷售商", "equipment": "濺鍍設備商", "process": "製程相關 · PVD 半導體廠", "packaging": "封裝廠 · PVD"}
GROUP_ICONS = {"銷售商": "store", "濺鍍設備商": "cube", "製程相關 · PVD 半導體廠": "building", "封裝廠 · PVD": "layers"}


def _ledger(config: Config, counts: Counter) -> str:
    groups: "OrderedDict[str, list]" = OrderedDict(
        (k, []) for k in ("日本", "中國大陸", "台灣", "韓國", "歐美", "銷售商", "濺鍍設備商", "製程相關 · PVD 半導體廠", "封裝廠 · PVD", "其他")
    )
    western = {"美國", "奧地利", "德國", "比利時", "英國", "法國", "瑞士", "荷蘭"}
    for e in config.entities:
        if e.is_topic:
            continue
        key = NON_PRODUCER_GROUPS.get(e.role) or ("歐美" if e.region in western else e.region)
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
        group_icon = GROUP_ICONS.get(key)
        label = key if group_icon else f"靶材生產商 · {key}"
        badge = icon(group_icon) if group_icon else REGION_CODES.get(key, "··")
        out.append(f'<div class="region"><h3><span class="rb">{badge}</span>{label}</h3><ul>{"".join(lis)}</ul></div>')
    return "\n".join(out)


def render_page(
    report: dict,
    config: Config,
    archive: list[tuple[str, int]],
    current: str,
    home: str = "",
    library: list[dict] | None = None,
    is_latest: bool = False,
) -> str:
    library = library or []
    items = report.get("items", [])
    span = report.get("span") or []
    if current == HOME_PAGE and span:
        label = f"最近 {len(span)} 天 · {span[-1]} ~ {span[0]}" if len(span) > 1 else span[0]
    else:
        label = current
    role_of = {e.name: e.role for e in config.entities}
    for it in items:
        it["kind"] = item_kind(it)
        it["roles"] = [role_of.get(e, "") for e in it.get("entities", [])]
    digest = report.get("digest") or {}
    kinds = Counter(it["kind"] for it in items)
    ent_counts = Counter(short_name(e) for it in items for e in it.get("entities", []))
    companies = [e for e in config.entities if not e.is_topic]
    topic_names = {short_name(e.name) for e in config.entities if e.is_topic}
    covered = [e for e in companies if ent_counts.get(short_name(e.name))]
    signal = [it for it in items if it["kind"] in SIGNAL_KINDS]
    generated = (report.get("generated") or "")[:16].replace("T", " ")

    if digest.get("overview"):
        auto = digest.get("source") == "auto"  # rule-based fallback; Claude's digest replaces it later
        summary = (
            '<p class="sub">AI 摘要尚未產生,以下為依類別與評分自動整理的重點;Claude 摘要完成後會自動更新。</p>' if auto else ""
        ) + f'<p class="lead">{esc(digest["overview"])}</p>'
        if digest.get("key_trends"):
            summary += f"<p><strong>{'主要主題' if auto else '產業趨勢'}</strong></p><ul class='plain'>" + "".join(f"<li>{esc(t)}</li>" for t in digest["key_trends"]) + "</ul>"
        if digest.get("watch_points"):
            summary += f"<p><strong>{'評分最高的新聞' if auto else '後續追蹤'}</strong></p><ul class='plain'>" + "".join(f"<li>{esc(t)}</li>" for t in digest["watch_points"]) + "</ul>"
    elif items:
        top_names = "、".join(f"{n}({c})" for n, c in ent_counts.most_common(4) if not n.startswith("產業綜合"))
        summary = (
            f'<p class="lead">本期蒐集 {len(items)} 則,其中新聞與公司公告 {len(signal)} 則;'
            f"{len(covered)} / {len(companies)} 家追蹤公司有報導,報導最多的是 {esc(top_names)}。</p>"
            '<p class="sub">本期未產生 AI 摘要(設定 ANTHROPIC_API_KEY 後,這裡會顯示 Claude 撰寫的本期總覽與趨勢)。</p>'
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
    span_note = (
        f'<p class="sub">總結為最新一期({esc(span[0])})的 AI 整理;以下重點新聞、技術文獻與全部新聞為最近 {len(span)} 天合併,'
        f'各日完整內容請見「歷次報告」。</p>' if len(span) > 1 else ""
    )
    top_html = "".join(_card_html(it) for it in top) or "<p>本期沒有新聞或公司公告。</p>"
    kind_chips = "".join(
        f'<button type="button" class="f" data-filter="kind" data-value="{k}" aria-pressed="false">{icon(KIND_ICONS[k])}{v}<span class="mono">{kinds[k]}</span></button>'
        for k, v in KINDS.items() if kinds[k]
    )
    ent_chips = "".join(
        f'<button type="button" class="f" data-filter="ent" data-value="{esc(e)}" aria-pressed="false">{esc(e)}<span class="mono">{n}</span></button>'
        for e, n in ent_counts.most_common()
    )
    paper_stat = _stat("book", str(len(report["papers"])), "技術文獻(篇)") if "papers" in report else ""
    archive_html = (
        f'<a href="{home}index.html"{" aria-current=page" if current == HOME_PAGE else ""}>{icon("star")}最近 {HOME_DAYS} 天</a>'
        if archive else ""
    ) + "".join(
        f'<a href="{home}{d}.html"{" aria-current=page" if d == current else ""}>{icon("calendar")}{d} · {n}</a>'
        for d, n in archive
    )

    return (
        _head(f"靶材產業新聞摘要 {label}", SITE_DESCRIPTION, config.settings.site_url,
              "" if current == HOME_PAGE else f"{current}.html")
        + f"""<div class="wrap">
<header>{_brand("Sputtering Target · Industry Bulletin", "靶材產業新聞摘要")}
<div class="meta"><span>{icon("calendar")}{esc(label)}{" 合併" if len(span) > 1 else ""} · 每期蒐集最近 {esc(report.get("days", ""))} 天新聞 · 更新於 <span class="mono">{esc(generated)}</span></span></div>
<nav class="meta" aria-label="網站導覽"><a class="btn primary" href="{home}papers.html">{icon("book")}技術文獻庫{icon("arrow-right")}</a>{f'<a class="btn" href="{home}forecast.html">{icon("research")}商情預測{icon("arrow-right")}</a>' if config.forecasts else ""}<a class="btn" href="{home}../financials/">{icon("market")}靶材製造商財務儀表板{icon("arrow-right")}</a></nav>
<div class="stats">{_stat("news", str(len(items)), "蒐集到的新聞(則)")}{_stat("check", str(len(signal)), "其中新聞與公司公告")}{_stat("building", f"{len(covered)} / {len(companies)}", "有報導的公司")}{paper_stat}</div></header>
<section aria-labelledby="sum">{_h2("sum", "sparkle", "本期總結", span[0] if len(span) > 1 else "")}{span_note}{summary}</section>
{_metals_section(report.get("metals") or [], kinds)}
<section aria-labelledby="top" class="top">{_h2("top", "star", "重點新聞", f"新聞與公告中評分最高的 {len(top)} 則,每家最多 2 則")}<div class="cards">{top_html}</div></section>
{_recycling_section(items, config)}
{_papers_section(report, library, is_latest, home)}
<section aria-labelledby="all">{_h2("all", "list", "全部新聞", f"{len(items)} 則")}
<div class="filters" role="group" aria-label="篩選新聞"><div class="frow"><span class="lbl">{icon("filter")}類型</span>{kind_chips}</div>
<div class="frow"><span class="lbl">{icon("building")}公司</span>{ent_chips}</div>
<div class="frow"><span class="shown" id="shown" aria-live="polite">顯示 {len(items)} / {len(items)} 則</span><button type="button" class="reset" id="reset" hidden>清除篩選</button></div></div>
<div class="cards" id="list">{"".join(_item_html(it) for it in items)}</div></section>
<section aria-labelledby="ledger">{_h2("ledger", "globe", "追蹤清單", f"{len(companies)} 家 · 本期報導則數")}<div class="ledger">{_ledger(config, ent_counts)}</div></section>
<section aria-labelledby="arc">{_h2("arc", "calendar", "歷次報告")}<div class="archive">{archive_html}</div></section>
<div class="note"><p>{icon("news")}<span><strong>資料來源</strong>:Google News RSS(繁中、簡中、英、日、韓),由靶材新聞 Agent 每日自動蒐集。</span></p>
<p>{icon("book")}<span><strong>技術文獻</strong>:OpenAlex 學術索引、arXiv 預印本與期刊 RSS,每篇只出現一次。</span></p>
<p>{icon("info")}<span><strong>類型標籤</strong>依標題與來源自動判斷;淡色項目(行情數據、論壇、市場報告、資料頁)參考價值較低。</span></p></div>
<footer>資料來源為公開新聞,摘要為改寫整理,不構成投資建議。</footer>
</div><script>{SCRIPT}</script></body></html>"""
    )


HOME_DAYS = 3  # the homepage combines this many of the latest daily reports


def merge_recent(reports: list[tuple[str, dict]], n: int = HOME_DAYS) -> dict:
    """One report out of the latest n daily reports (newest first): items and papers
    de-duplicated by ID, the newest day's digest and metal quotes."""
    recent = reports[:n]
    items, papers, seen_items, seen_papers = [], [], set(), set()
    for date, r in recent:
        for it in r.get("items", []):
            if it.get("id") not in seen_items:
                seen_items.add(it.get("id"))
                items.append(it)
        for p in r.get("papers") or []:
            if p.get("id") not in seen_papers:
                seen_papers.add(p.get("id"))
                papers.append(p)
    latest = recent[0][1] if recent else {}
    merged = {
        "generated": latest.get("generated"),
        "days": latest.get("days"),
        "digest": latest.get("digest") or {},
        "items": items,
        "metals": next((r["metals"] for _, r in recent if r.get("metals")), []),
        "span": [d for d, _ in recent],
    }
    if any("papers" in r for _, r in recent):
        merged["papers"] = papers
        merged["research_days"] = latest.get("research_days")
    return merged


def build_site(reports_dir: str | Path, out_dir: str | Path, config: Config) -> list[Path]:
    reports_dir, out = Path(reports_dir), Path(out_dir)
    paths = sorted((p for p in reports_dir.iterdir() if REPORT_RE.match(p.name)), reverse=True)
    out.mkdir(parents=True, exist_ok=True)
    if OG_IMAGE.exists():
        shutil.copyfile(OG_IMAGE, out / OG_IMAGE.name)
    reports = [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    archive = [(d, len(r.get("items", []))) for d, r in reports]
    library = paper_library(reports)
    written = []
    generated = (reports[0][1].get("generated") or "")[:16].replace("T", " ") if reports else ""
    (out / "papers.html").write_text(render_library(library, config, generated), encoding="utf-8")
    written.append(out / "papers.html")
    if config.forecasts:
        (out / "forecast.html").write_text(render_forecasts(config, generated), encoding="utf-8")
        written.append(out / "forecast.html")
    for date, report in reports:
        page = render_page(report, config, archive, date, library=library)
        (out / f"{date}.html").write_text(page, encoding="utf-8")
        written.append(out / f"{date}.html")
    if reports:
        home = merge_recent(reports)
        page = render_page(home, config, archive, HOME_PAGE, library=library, is_latest=True)
        (out / "index.html").write_text(page, encoding="utf-8")
        written.append(out / "index.html")
    if not reports:
        empty = {"items": [], "days": config.settings.days}
        (out / "index.html").write_text(render_page(empty, config, [], "尚無報告", is_latest=True), encoding="utf-8")
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


"""靶材產業島: the site's knowledge game (quiz.html).

An explorable map of hexagonal islands, one per theme. Each building on an island is a
question; answering it lights the building, and an island whose buildings are all lit is
conquered. Dashed lines link islands that share a concept, one island hosts the element
matching game, and a mascot hides on a different island every day.

Questions: fixed ones from config/quiz.yaml; the rest are generated from the site's own
material: listed target makers (market, target share) from target_makers.yaml, element
symbols, and the latest days' top news ("which company?", name masked).
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import re
from pathlib import Path

from .config import Config
from .site import SIGNAL_KINDS, item_kind, short_name
from .thumbs import pick as pick_thumb
from .translate import to_traditional

# Symbol -> (name, atomic number or 0 for compounds, colour) for the element questions,
# the matching game's tiles and the film layers deposited in the game's chamber.
ELEMENT_INFO = {
    "Al": ("鋁", 13, "#8ea3b8"), "Ti": ("鈦", 22, "#6c7a89"), "Cu": ("銅", 29, "#d9773a"), "Ta": ("鉭", 73, "#5c6bc0"),
    "W": ("鎢", 74, "#455a64"), "Co": ("鈷", 27, "#3d5afe"), "Ru": ("釕", 44, "#7e57c2"), "Mo": ("鉬", 42, "#78909c"),
    "Ni": ("鎳", 28, "#9e9d24"), "In": ("銦", 49, "#26a69a"), "Ag": ("銀", 47, "#b0bec5"), "Au": ("金", 79, "#f2b705"),
    "Pt": ("鉑", 78, "#a1a9b3"), "Pd": ("鈀", 46, "#8d99ae"), "Cr": ("鉻", 24, "#00897b"), "Si": ("矽", 14, "#5d6d7e"),
    "ITO": ("氧化銦錫", 0, "#29b6f6"), "IGZO": ("銦鎵鋅氧化物", 0, "#ab47bc"),
}
ELEMENTS = {sym: info[0] for sym, info in ELEMENT_INFO.items()}
LOGO_DIR = Path(__file__).resolve().parents[1] / "fin_analyzer" / "assets" / "logos"
LOGO_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}
MARKETS = {"TW": "台灣", "JP": "日本", "US": "美國", "CN": "中國大陸"}
FOCUS = {"pure": "靶材專業廠", "major": "靶材為主要事業之一", "diversified": "多角化(靶材占比較低)"}
FOCUS_NOTE = {
    "pure": "營收以靶材為主,財務指標大致反映靶材事業。",
    "major": "靶材是主要事業之一,但還有其他金屬或材料業務。",
    "diversified": "靶材只占營收一小部分,整體獲利多來自其他事業。",
}
MAX_NEWS = 15


# id, name, icon, colour, axial hex position (q, r), description
ISLANDS = [
    ("wiring", "導線材料島", "layers", "#3b5bdb", (0, 0), "銅、鋁導線與阻障層、種子層:半導體靶材的核心用途。"),
    ("process", "製程工藝島", "flame", "#e8590c", (1, -1), "PVD、純度、綁定、旋轉靶、熱均壓:靶材怎麼做、怎麼用。"),
    ("elements", "元素島", "gem", "#7048e8", (0, -1), "靶材常見的金屬與化合物符號。"),
    ("display", "顯示與封裝島", "chip", "#1098ad", (-1, 0), "ITO、IGZO 與先進封裝的濺鍍應用。"),
    ("recycle", "回收島", "recycle", "#2f9e44", (-1, 1), "殘靶回收與稀有金屬再生。"),
    ("market", "市場預測島", "market", "#d6336c", (0, 1), "市場規模、成長率與價格:取自商情預測頁。"),
    ("news", "新聞港", "news", "#f08c00", (1, 0), "最近 3 天重點新聞的主角是誰?每天換題。"),
    ("jp", "日本廠商島", "building", "#c92a2a", (1, -2), "JX金屬、東曹、三井金屬、住友化學、ULVAC 等。"),
    ("tw", "台灣廠商島", "building", "#0c8f6a", (2, -1), "光洋科、鑫科。"),
    ("cn", "中國廠商島", "building", "#e03131", (-2, 1), "江豐電子、有研新材、阿石創、歐萊新材。"),
    ("us", "美國廠商島", "building", "#364fc7", (-1, 2), "Materion、Solstice Advanced Materials。"),
    ("play", "配對遊樂島", "star", "#f76707", (2, 0), "元素符號配中文名的翻牌遊戲。"),
]
# Collaboration lines: islands that share a concept (label shown when either island is focused).
LINKS = [
    ("wiring", "elements", "Cu、Ta、Ti、Co、Ru"), ("wiring", "process", "6N 高純度"), ("wiring", "jp", "JX金屬:Cu 靶第一"),
    ("wiring", "cn", "江豐電子:Al 靶第一"), ("display", "recycle", "ITO 的銦回收"), ("process", "recycle", "旋轉靶與殘靶"),
    ("market", "cn", "前兩大廠合計約七成"), ("market", "jp", "前兩大廠合計約七成"), ("market", "us", "Solstice 分拆"),
    ("elements", "play", "元素配對"), ("news", "market", "市場訊號"), ("display", "news", "封裝擴產"),
]
MARKET_ISLAND = {"TW": "tw", "JP": "jp", "US": "us", "CN": "cn"}
PER_ISLAND = 9  # buildings drawn per island; larger pools are sampled (seeded by report date)


def _q(topic: str, q: str, answer: str, wrong: list[str], explain: str = "", island: str = "", **extra) -> dict:
    return {"id": hashlib.sha1(q.encode("utf-8")).hexdigest()[:10], "topic": topic, "island": island, "q": q,
            "answer": answer, "wrong": wrong[:3], "explain": explain, **extra}


def fixed_questions(config: Config) -> list[dict]:
    out = []
    for x in config.quiz:
        wrong = [str(w) for w in x.get("wrong") or []]
        if x.get("q") and x.get("answer") and len(wrong) >= 3:
            topic = str(x.get("topic", "materials"))
            island = str(x.get("island") or ("wiring" if topic == "materials" else "market"))
            out.append(_q(topic, str(x["q"]), str(x["answer"]), wrong, str(x.get("explain", "")), island,
                          visual={"kind": "icon", "icon": str(x.get("icon") or ("flask" if topic == "materials" else "market"))}))
    return out


def element_questions(rng: random.Random) -> list[dict]:
    out = []
    names = list(ELEMENTS.values())
    for sym, name in ELEMENTS.items():
        wrong = rng.sample([n for n in names if n != name], 3)
        num = ELEMENT_INFO[sym][1]
        out.append(_q("materials", f"靶材上的「{sym}」指的是哪一種材料?", name, wrong,
                      f"{sym} = {name}" + (f",原子序 {num}。" if num else "。"), "elements", visual={"kind": "element", "sym": sym}))
    return out


def logo_uri(ticker: str, logo_dir: Path = LOGO_DIR) -> str:
    """The financial dashboard's company logo as a data URI ("" when there is none)."""
    for ext, mime in LOGO_MIME.items():
        path = logo_dir / f"{ticker}{ext}"
        if path.exists():
            return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return ""


def maker_questions(config: Config, rng: random.Random) -> list[dict]:
    out = []
    makers = [m for m in config.makers if m.get("name")]
    for m in makers:
        name, market, focus = str(m["name"]), str(m.get("market", "")).upper(), str(m.get("target_focus", ""))
        segment, ticker = str(m.get("segment", "")), str(m.get("ticker", ""))
        visual = {"kind": "logo", "src": logo_uri(ticker), "label": name, "sub": ticker}
        if market in MARKETS:
            out.append(_q("market", f"{name}({ticker})在哪個市場上市?", MARKETS[market],
                          [v for k, v in MARKETS.items() if k != market],
                          f"{name}在{MARKETS[market]}上市;主要業務:{segment}。", MARKET_ISLAND[market], visual=visual))
        if focus in FOCUS:
            out.append(_q("market", f"以靶材占營收的比重來看,{name}屬於哪一類?", FOCUS[focus],
                          [v for k, v in FOCUS.items() if k != focus] + ["不生產靶材"],
                          f"{FOCUS_NOTE[focus]}主要業務:{segment}。", MARKET_ISLAND.get(market, "market"), visual=visual))
    return out


def _mask(title: str, words: list[str]) -> str:
    for w in sorted({w for w in words if len(w) >= 2}, key=len, reverse=True):
        title = re.sub(re.escape(w), "○○", title, flags=re.I)
    return title


def news_questions(items: list[dict], config: Config, rng: random.Random) -> list[dict]:
    """"Which company is this headline about?" from the latest days' signal news, company name masked."""
    companies = {e.name: e for e in config.entities if not e.is_topic}
    roles = {e.name: e.role for e in config.entities}
    names = sorted({short_name(n) for n in companies})
    picked, seen = [], set()
    ranked = sorted(items, key=lambda it: (it.get("importance", 0), it.get("score", 0)), reverse=True)
    for it in ranked:
        ents = [e for e in it.get("entities", []) if e in companies]
        if len(ents) != 1 or item_kind(it) not in SIGNAL_KINDS or ents[0] in seen:
            continue
        ent = companies[ents[0]]
        answer = short_name(ent.name)
        title = it.get("title_zh") or to_traditional(it.get("title", ""))
        masked = _mask(title, [answer, ent.name, *ent.aliases, *(to_traditional(a) for a in ent.aliases)])
        if "○○" not in masked and answer in title:
            continue
        pool = [n for n in names if n != answer and n.lower() not in title.lower()]  # no second right answer
        if len(pool) < 3:
            break
        seen.add(ents[0])
        motif, caption, sym = pick_thumb(it, roles)
        picked.append(_q("news", f"這則近期新聞的主角是哪家公司?「{masked}」", answer, rng.sample(pool, 3),
                         it.get("summary") or "", "news", link=it.get("link", ""), date=(it.get("published") or "")[:10],
                         visual={"kind": "icon", "icon": motif, "caption": caption, "sym": sym}))
        if len(picked) >= MAX_NEWS:
            break
    return picked


def build_bank(config: Config, items: list[dict], seed: str = "") -> dict:
    """The island map: islands with their questions (buildings), links, element pairs and the mascot's island."""
    rng = random.Random(seed)
    questions = fixed_questions(config) + element_questions(rng) + maker_questions(config, rng) + news_questions(items, config, rng)
    by_island: dict[str, list[dict]] = {}
    for q in questions:
        by_island.setdefault(q["island"], []).append(q)
    islands = []
    for iid, name, icon_name, color, (hq, hr), desc in ISLANDS:
        qs = by_island.get(iid, [])
        if len(qs) > PER_ISLAND:
            qs = rng.sample(qs, PER_ISLAND)
        if qs or iid == "play":
            islands.append({"id": iid, "name": name, "icon": icon_name, "color": color, "hex": [hq, hr], "desc": desc, "questions": qs})
    ids = {i["id"] for i in islands}
    links = [[a, b, label] for a, b, label in LINKS if a in ids and b in ids]
    elements = [{"sym": s, "name": n, "num": z, "color": c} for s, (n, z, c) in ELEMENT_INFO.items()]
    mascot = rng.choice([i["id"] for i in islands if i["id"] != "play"]) if islands else ""
    return {"islands": islands, "links": links, "elements": elements, "mascot": mascot}






PAIRS = 6

GAME_STYLE = """
.isle{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:16px;align-items:start}
.stage{position:relative;background:#cfe8f7;border-radius:8px;overflow:hidden;min-height:420px}
@media (prefers-color-scheme:dark){.stage{background:#10283d}}
.stage>svg{display:block;width:100%;height:auto;touch-action:manipulation}
.hud2{position:absolute;left:12px;top:12px;display:flex;flex-wrap:wrap;gap:8px;pointer-events:none}
.chip2{background:var(--surface);border-radius:6px;padding:6px 10px;font-size:.8rem;color:var(--muted);display:inline-flex;gap:6px;align-items:center}.chip2 b{font-family:var(--font-data);color:var(--fg)}
.tools{position:absolute;right:12px;top:12px;display:flex;gap:8px}
.g-btn{font:inherit;font-size:.9rem;border:0;border-radius:6px;padding:9px 16px;cursor:pointer;background:var(--accent-soft);color:var(--accent);font-weight:600;display:inline-flex;gap:8px;align-items:center}
.g-btn:hover{background:var(--accent);color:#fff}.g-btn.primary{background:var(--accent);color:#fff}.g-btn.primary:hover{background:var(--accent-2)}.g-btn.sm{padding:6px 12px;font-size:.82rem}
.tools .g-btn{background:var(--surface)}
.is-land{cursor:pointer;outline:none}.is-land .top{transition:filter .15s}.is-land:hover .top,.is-land:focus-visible .top{filter:brightness(1.12)}
.is-land:focus-visible .top{stroke:var(--fg);stroke-width:3}
.is-name{font:700 13px var(--font-body);fill:var(--fg);paint-order:stroke;stroke:#fff;stroke-width:4px;stroke-linejoin:round;pointer-events:none}
.is-sub{font:500 10px var(--font-data);fill:var(--muted);paint-order:stroke;stroke:#fff;stroke-width:3px;pointer-events:none}
@media (prefers-color-scheme:dark){.is-name,.is-sub{stroke:#10283d}}
.link{fill:none;stroke:#fff;stroke-width:2.5;stroke-dasharray:6 6;opacity:.75}.link.on{stroke:var(--accent-2);opacity:1;stroke-width:3.5}
.link-label{font:600 11px var(--font-body);fill:var(--accent-2);paint-order:stroke;stroke:#fff;stroke-width:4px}
.mascot{cursor:pointer}.mascot:hover{transform:scale(1.15)}
.side{background:var(--surface);border-top:4px solid var(--c,var(--accent));border-radius:6px;padding:18px;display:grid;gap:14px;align-content:start}
.side h3{margin:0;display:flex;gap:10px;align-items:center;font-size:1.15rem}.side h3 .ic{width:26px;height:26px;color:var(--c,var(--accent))}
.side p{margin:0;font-size:.9rem;color:var(--muted);line-height:1.7}
.pbar{height:8px;background:var(--line);border-radius:4px;overflow:hidden}.pbar i{display:block;height:100%;background:var(--c,var(--accent))}
.blist{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.bld{font:inherit;border:0;border-radius:6px;padding:10px 6px;cursor:pointer;background:var(--bg);color:var(--fg);display:grid;gap:4px;justify-items:center;font-size:.8rem}
.bld .ic{width:22px;height:22px;color:var(--muted)}.bld b{font-family:var(--font-data)}
.bld.lit{background:color-mix(in srgb,var(--c) 18%,var(--surface))}.bld.lit .ic{color:var(--c)}.bld:hover{outline:2px solid var(--c)}
.ilist{display:grid;gap:6px}.irow{font:inherit;border:0;border-radius:6px;padding:8px 10px;cursor:pointer;background:var(--bg);color:var(--fg);display:grid;grid-template-columns:28px 1fr auto;gap:10px;align-items:center;text-align:left;font-size:.88rem}
.irow .dot{width:28px;height:28px;border-radius:6px;display:grid;place-items:center;color:#fff}.irow .dot .ic{width:16px;height:16px}.irow span.mono{font-size:.78rem;color:var(--muted)}
.irow:hover{background:var(--accent-soft)}
.g-q{font-size:1.05rem;font-weight:700;line-height:1.6;margin:0}
.qhead{display:grid;grid-template-columns:72px minmax(0,1fr);gap:12px;align-items:center}
.vis{width:72px;height:72px;border-radius:8px;display:grid;place-items:center;position:relative;align-content:center;gap:2px;text-align:center}
.vis.icon{background:color-mix(in srgb,var(--c,var(--accent)) 15%,var(--surface));color:var(--c,var(--accent))}.vis.icon .ic{width:34px;height:34px;stroke-width:1.5}
.vis .vcap{font-size:.66rem;color:var(--muted)}.vis .vsym{position:absolute;right:-6px;bottom:-6px;background:var(--accent);color:#fff;border-radius:4px;font:600 .7rem var(--font-data);padding:2px 5px}
.ptile{background:color-mix(in srgb,var(--pc) 22%,var(--surface));border:3px solid var(--pc);color:var(--fg)}.ptile .z{position:absolute;left:6px;top:3px;font:500 .66rem var(--font-data);color:var(--muted)}
.ptile .s{font:700 1.6rem/1 var(--font-data)}.ptile .s.long{font-size:.95rem}
.vis.logo{background:#fff;border:2px solid var(--line)}.vis.logo img{max-width:56px;max-height:44px;object-fit:contain}
.opts{display:grid;gap:8px}
.opt{font:inherit;text-align:left;border:0;background:var(--bg);color:var(--fg);border-radius:6px;padding:10px 12px;cursor:pointer;line-height:1.5;display:grid;grid-template-columns:28px minmax(0,1fr);gap:10px;align-items:center}
.opt .k{width:28px;height:28px;border-radius:5px;display:grid;place-items:center;background:var(--c,var(--accent));color:#fff;font:700 .85rem var(--font-data)}
.opt:hover:not(:disabled){background:var(--accent-soft)}.opt:disabled{cursor:default}
.opt.right{background:var(--ok-soft);color:var(--ok);font-weight:700}.opt.right .k{background:var(--ok)}
.opt.wrong{background:color-mix(in srgb,var(--up) 13%,var(--surface));color:var(--up)}.opt.wrong .k{background:var(--up)}.opt.dim{opacity:.45}
.fb{border-radius:6px;padding:12px 14px;background:var(--bg);font-size:.9rem;line-height:1.7;border-left:5px solid var(--line)}.fb b{display:block;margin-bottom:2px}
.fb.ok{border-color:var(--ok)}.fb.ok b{color:var(--ok)}.fb.no{border-color:var(--up)}.fb.no b{color:var(--up)}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--fg);color:var(--bg);border-radius:8px;padding:12px 18px;font-size:.92rem;z-index:20;max-width:min(92vw,520px);text-align:center}
.board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;perspective:800px}
.mc{font:inherit;border:0;padding:0;background:none;cursor:pointer;aspect-ratio:1;position:relative}
.mc .in{position:absolute;inset:0;transition:transform .45s;transform-style:preserve-3d}.mc.flip .in{transform:rotateY(180deg)}
.mc .f,.mc .b{position:absolute;inset:0;border-radius:6px;backface-visibility:hidden;display:grid;place-items:center;align-content:center;gap:2px}
.mc .f{background:var(--c,var(--accent));color:#fff;font:700 1.3rem var(--font-data)}
.mc .b{transform:rotateY(180deg);background:color-mix(in srgb,var(--pc) 22%,var(--surface));border:3px solid var(--pc);color:var(--fg)}
.mc .b .z{position:absolute;left:6px;top:3px;font:500 .66rem var(--font-data);color:var(--muted)}.mc .b .s{font:700 1.5rem/1 var(--font-data)}.mc .b .s.long{font-size:.9rem}.mc .b .n{font-size:1.05rem;font-weight:700;text-align:center;line-height:1.3;padding:0 4px}
.mc .b.name{background:var(--accent-soft);border-color:var(--accent)}.mc.done .b.name{background:color-mix(in srgb,var(--pc) 22%,var(--surface))}
.mc.done .b{border-color:var(--ok)}.mc.miss .b{border-color:var(--up)}
@media (max-width:860px){.isle{grid-template-columns:1fr}.stage{min-height:0}}
@media (max-width:520px){.hud2{top:auto;bottom:10px}.hud2 .chip2:nth-child(n+3){display:none}}
@media (prefers-reduced-motion:reduce){.mc .in{transition:none}}
"""

GAME_SCRIPT = r"""
(function(){
const B = JSON.parse(document.getElementById('bank').textContent);
const PAIRS = __PAIRS__, SVGNS = 'http://www.w3.org/2000/svg';
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = id => document.getElementById(id);
const el = (t, cls, txt) => { const n = document.createElement(t); if (cls) n.className = cls; if (txt !== undefined) n.textContent = txt; return n; };
const sv = (t, a) => { const n = document.createElementNS(SVGNS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };
const icon = (name, tag) => { const s = sv('svg', {class: 'ic', 'aria-hidden': 'true'}); s.append(sv('use', {href: '#i-' + name})); return s; };
const btn = (cls, txt, fn) => { const b = el('button', cls, txt); b.type = 'button'; b.onclick = fn; return b; };
const shuffle = a => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
                set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };
const ISL = Object.fromEntries(B.islands.map(i => [i.id, i]));
const EL = Object.fromEntries(B.elements.map(e => [e.sym, e]));
let solved = new Set(store.get('isle-solved', [])), mascotFound = store.get('isle-mascot', false), matchDone = store.get('isle-match', false);
const save = () => { store.set('isle-solved', [...solved]); store.set('isle-mascot', mascotFound); store.set('isle-match', matchDone); };
const lit = i => i.questions.filter(q => solved.has(q.id)).length;
const conquered = i => i.id === 'play' ? matchDone : i.questions.length > 0 && lit(i) === i.questions.length;

// ---------- map geometry: pointy-top hexes, flattened for a 2.5D look ----------
const R = 58, GAP = 1.42, SQ = 0.62, DEPTH = 14;  // GAP spreads the islands apart so there is sea between them
const center = i => [R * GAP * Math.sqrt(3) * (i.hex[0] + i.hex[1] / 2), R * GAP * 1.5 * i.hex[1] * SQ];
const hexPts = (cx, cy, r, dy) => [...Array(6)].map((_, k) => { const a = Math.PI / 180 * (60 * k - 30); return [cx + r * Math.cos(a), cy + r * Math.sin(a) * SQ + (dy || 0)]; });
const pts = p => p.map(([x, y]) => x.toFixed(1) + ',' + y.toFixed(1)).join(' ');
const shade = (hex, f) => { const n = parseInt(hex.slice(1), 16), c = [n >> 16, n >> 8 & 255, n & 255].map(v => Math.round(v * f)); return 'rgb(' + c.join(',') + ')'; };
const xs = B.islands.map(i => center(i)[0]), ys = B.islands.map(i => center(i)[1]);
const FULL = [Math.min(...xs) - R - 40, Math.min(...ys) - R - 40, Math.max(...xs) - Math.min(...xs) + 2 * R + 80, Math.max(...ys) - Math.min(...ys) + 2 * R + 90];

let focus = null, view = FULL.slice(), svg, gLinks, gIslands, gTop;
function drawMap() {
  svg = sv('svg', {viewBox: view.join(' '), role: 'application', 'aria-label': '靶材產業島地圖:點選島嶼查看題目'});
  const sea = sv('rect', {x: -2000, y: -2000, width: 4000, height: 4000, fill: 'transparent'}); sea.onclick = () => go(null); svg.append(sea);
  gLinks = sv('g', {}); gIslands = sv('g', {}); gTop = sv('g', {}); svg.append(gLinks, gIslands, gTop);
  $('stage').prepend(svg); render();
}
function render() {
  gLinks.replaceChildren(); gIslands.replaceChildren(); gTop.replaceChildren();
  for (const [a, b, label] of B.links) {
    const [x1, y1] = center(ISL[a]), [x2, y2] = center(ISL[b]), mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - 18;
    const on = focus && (focus === a || focus === b);
    gLinks.append(sv('path', {class: 'link' + (on ? ' on' : ''), d: `M${x1},${y1} Q${mx},${my} ${x2},${y2}`}));
    if (on) {  // label near the other island's end, so labels of one focused island don't pile up in the middle
      const [ox, oy] = focus === a ? [x2, y2] : [x1, y1], [fx, fy] = focus === a ? [x1, y1] : [x2, y2];
      const t = sv('text', {class: 'link-label', x: fx + (ox - fx) * .7, y: fy + (oy - fy) * .7 - 22, 'text-anchor': 'middle'});
      t.textContent = label; gTop.append(t);
    }
  }
  // back-to-front so nearer islands overlap the ones behind
  for (const i of B.islands.slice().sort((p, q) => center(p)[1] - center(q)[1])) {
    const [cx, cy] = center(i), done = conquered(i), n = i.questions.length, k = lit(i);
    const g = sv('g', {class: 'is-land', tabindex: 0, role: 'button', 'aria-label': i.name + (i.id === 'play' ? '' : `,已點亮 ${k} / ${n}`)});
    g.append(sv('polygon', {points: pts(hexPts(cx, cy, R - 3, DEPTH)), fill: shade(i.color, .55)}),
      sv('polygon', {class: 'top', points: pts(hexPts(cx, cy, R - 3)), fill: done ? i.color : shade(i.color, 1) , opacity: done ? 1 : .55}),
      sv('polygon', {points: pts(hexPts(cx, cy, R - 14)), fill: '#fff', opacity: done ? .18 : .32}));
    // buildings: one small block per question, lit when answered
    const cells = i.id === 'play' ? 4 : n, cols = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(cells))));
    for (let j = 0; j < cells; j++) {
      const row = Math.floor(j / cols), col = j % cols, rows = Math.ceil(cells / cols);
      const bx = cx + (col - (cols - 1) / 2) * 18 - 5, by = cy + (row - (rows - 1) / 2) * 11 - 4;
      const on = i.id === 'play' ? matchDone : solved.has(i.questions[j].id), h = 6 + (j * 7 % 8);
      const front = on ? i.color : '#a3adbf', topc = on ? '#ffe066' : '#d5dbe5';
      g.append(sv('rect', {x: bx, y: by - h, width: 10, height: h + 5, fill: shade(front.startsWith('#') ? front : '#a3adbf', .8)}),
        sv('rect', {x: bx, y: by - h - 4, width: 10, height: 4, fill: topc}));
    }
    if (done) { g.append(sv('line', {x1: cx + 30, y1: cy - 30, x2: cx + 30, y2: cy - 58, stroke: 'var(--fg)', 'stroke-width': 2}),
      sv('polygon', {points: `${cx + 30},${cy - 58} ${cx + 50},${cy - 52} ${cx + 30},${cy - 46}`, fill: '#ffd43b'})); }
    const nm = sv('text', {class: 'is-name', x: cx, y: cy + R * SQ + DEPTH + 14, 'text-anchor': 'middle'}); nm.textContent = i.name;
    const sub = sv('text', {class: 'is-sub', x: cx, y: cy + R * SQ + DEPTH + 27, 'text-anchor': 'middle'});
    sub.textContent = i.id === 'play' ? (matchDone ? '已完成' : '小遊戲') : `${k} / ${n}`;
    gTop.append(nm, sub);
    g.onclick = e => { e.stopPropagation(); go(i.id); };
    g.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(i.id); } };
    gIslands.append(g);
    if (i.id === B.mascot && !mascotFound) {
      const m = sv('g', {class: 'mascot', role: 'button', tabindex: 0, 'aria-label': '某個小東西'});
      const mx = cx - 34, my = cy + 4;
      m.append(sv('circle', {cx: mx, cy: my, r: 6.5, fill: '#fff', stroke: '#e03131', 'stroke-width': 2.5}), sv('circle', {cx: mx, cy: my, r: 2.5, fill: '#e03131'}),
        sv('circle', {cx: mx - 2.5, cy: my - 9, r: 1.3, fill: 'var(--fg)'}), sv('circle', {cx: mx + 2.5, cy: my - 9, r: 1.3, fill: 'var(--fg)'}));
      m.onclick = e => { e.stopPropagation(); mascotFound = true; save(); render(); hud();
        toast('🎯 找到靶材小精靈了!小知識:濺鍍時靶材接在負極,電漿中的氬離子被吸過去撞擊靶面,把原子撞出來沉積到晶圓上。'); };
      gTop.append(m);
    }
  }
}
function camera(target) {
  const from = view.slice(), t0 = performance.now(), dur = RM ? 0 : 450;
  const step = now => { const p = dur ? Math.min(1, (now - t0) / dur) : 1, e = 1 - Math.pow(1 - p, 3);
    view = from.map((v, k) => v + (target[k] - v) * e); svg.setAttribute('viewBox', view.join(' ')); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
function go(id) {
  focus = id && ISL[id] ? id : null;
  const url = new URL(location.href); focus ? url.searchParams.set('island', focus) : url.searchParams.delete('island');
  try { history.replaceState(null, '', url); } catch (e) {}
  if (focus) { const [cx, cy] = center(ISL[focus]), w = Math.min(FULL[2], R * GAP * 6.5), h = w * FULL[3] / FULL[2]; camera([cx - w / 2, cy - h / 2 + 10, w, h]); }
  else camera(FULL);
  render(); panel();
}
function hud() {
  const box = $('hud2'); box.replaceChildren();
  const total = B.islands.reduce((n, i) => n + i.questions.length, 0), litAll = B.islands.reduce((n, i) => n + lit(i), 0);
  const c1 = el('span', 'chip2', '征服島嶼 '); c1.append(el('b', '', B.islands.filter(conquered).length + ' / ' + B.islands.length));
  const c2 = el('span', 'chip2', '點亮建築 '); c2.append(el('b', '', litAll + ' / ' + total));
  const c3 = el('span', 'chip2', mascotFound ? '🎯 已找到小精靈' : '🔍 島上藏著小精靈');
  box.append(c1, c2, c3);
}
function toast(msg) { const t = el('div', 'toast', msg); t.setAttribute('role', 'status'); document.body.append(t); setTimeout(() => t.remove(), 4200); }

// ---------- side panel ----------
function panel(qid) {
  const side = $('side'); side.replaceChildren(); hud();
  const i = focus && ISL[focus];
  side.style.setProperty('--c', i ? i.color : 'var(--accent)');
  if (!i) {
    side.append(el('h3', '', '歡迎來到靶材產業島'), el('p', '', '每座島是一個主題,島上每棟建築是一道題。點選島嶼、答對題目就能點亮建築;整座島都點亮就算征服。虛線是島與島之間的關聯,地圖上還藏著一隻靶材小精靈。'));
    const list = el('div', 'ilist');
    for (const x of B.islands) {
      const r = btn('irow', '', () => go(x.id)), d = el('span', 'dot'); d.style.background = x.color; d.append(icon(x.icon));
      r.append(d, el('span', '', x.name + (conquered(x) ? ' 🚩' : '')), el('span', 'mono', x.id === 'play' ? (matchDone ? '完成' : '遊戲') : lit(x) + '/' + x.questions.length));
      list.append(r);
    }
    side.append(list); return;
  }
  const h = el('h3'); h.append(icon(i.icon), document.createTextNode(i.name)); side.append(h, el('p', '', i.desc));
  if (i.id === 'play') { side.append(matchGame(i)); return side.append(nav(i)); }
  const pb = el('div', 'pbar'), f = el('i'); f.style.width = (lit(i) / i.questions.length * 100) + '%'; pb.append(f);
  side.append(pb);
  if (qid) return side.append(question(i, i.questions.find(q => q.id === qid)), nav(i));
  const list = el('div', 'blist');
  i.questions.forEach((q, n) => { const b = btn('bld' + (solved.has(q.id) ? ' lit' : ''), '', () => panel(q.id));
    b.append(icon(solved.has(q.id) ? 'check' : 'building'), el('b', '', '#' + (n + 1)), el('span', '', solved.has(q.id) ? '已點亮' : '未作答')); list.append(b); });
  side.append(list, nav(i));
}
function nav(i) {
  const row = el('div', 'g-meta'); row.style.display = 'flex'; row.style.gap = '8px'; row.style.flexWrap = 'wrap';
  const ids = B.islands.map(x => x.id), k = ids.indexOf(i.id);
  row.append(btn('g-btn sm', '← 上一座島', () => go(ids[(k - 1 + ids.length) % ids.length])), btn('g-btn sm', '下一座島 →', () => go(ids[(k + 1) % ids.length])),
    btn('g-btn sm', '回到全島地圖', () => go(null)));
  return row;
}
function visual(q, i) {
  const v = q.visual || {};
  if (v.kind === 'element' && EL[v.sym]) { const e = EL[v.sym], d = el('div', 'vis ptile'); d.style.setProperty('--pc', e.color);
    if (e.num) d.append(el('span', 'z', e.num)); d.append(el('span', 's' + (e.sym.length > 2 ? ' long' : ''), e.sym)); return d; }
  if (v.kind === 'logo') { const d = el('div', 'vis logo'); if (v.src) { const img = el('img'); img.src = v.src; img.alt = v.label + ' logo'; d.append(img); } else d.append(el('b', '', [...(v.label || '?')][0])); return d; }
  const d = el('div', 'vis icon'); d.append(icon(v.icon || i.icon)); if (v.caption) d.append(el('span', 'vcap', v.caption)); if (v.sym) d.append(el('span', 'vsym', v.sym)); return d;
}
function question(i, q) {
  const box = el('div'); box.style.display = 'grid'; box.style.gap = '12px';
  const n = i.questions.indexOf(q), head = el('div', 'qhead'); head.append(visual(q, i), el('p', 'g-q', q.q));
  const opts = el('div', 'opts'), fb = el('div', 'fb'); fb.hidden = true; fb.setAttribute('aria-live', 'polite');
  const after = el('div', 'g-row'); after.style.display = 'flex'; after.style.gap = '8px'; after.hidden = true;
  const buttons = [];
  shuffle([q.answer, ...q.wrong]).forEach((text, k) => {
    const b = el('button', 'opt'); b.type = 'button'; b.append(el('span', 'k', 'ABCD'[k]), el('span', '', text));
    b.onclick = () => {
      const ok = text === q.answer;
      buttons.forEach(o => { o.disabled = true; if (o.lastChild.textContent === q.answer) o.classList.add('right'); else if (o !== b) o.classList.add('dim'); });
      if (!ok) b.classList.add('wrong');
      if (ok && !solved.has(q.id)) { solved.add(q.id); save(); render(); hud(); if (conquered(i)) toast('🚩 征服「' + i.name + '」!'); }
      fb.className = 'fb ' + (ok ? 'ok' : 'no');
      fb.replaceChildren(el('b', '', ok ? '答對了!點亮一棟建築' : '答錯了,正解是「' + q.answer + '」(可以再挑戰)'));
      if (q.explain) fb.append(document.createTextNode(q.explain));
      if (q.link) { const a = el('a', '', ' 閱讀原文 →'); a.href = q.link; a.target = '_blank'; a.rel = 'noopener'; fb.append(a); }
      fb.hidden = false; after.hidden = false;
    };
    buttons.push(b); opts.append(b);
  });
  const nextQ = i.questions.slice(n + 1).concat(i.questions.slice(0, n)).find(x => !solved.has(x.id));
  after.append(nextQ ? btn('g-btn primary sm', '下一棟 →', () => panel(nextQ.id)) : btn('g-btn primary sm', '本島完成 🚩', () => panel()),
    btn('g-btn sm', '島上建築一覽', () => panel()));
  box.append(el('div', 'chip2', '建築 #' + (n + 1) + ' / ' + i.questions.length), head, opts, fb, after);
  return box;
}

// ---------- 配對遊樂島 ----------
function matchGame(i) {
  const box = el('div'); box.style.display = 'grid'; box.style.gap = '12px';
  const pick = shuffle(B.elements).slice(0, PAIRS), cards = shuffle(pick.flatMap(e => [{e, sym: true}, {e, sym: false}]));
  let open = [], moves = 0, left = PAIRS;
  const info = el('div', 'chip2', '翻兩張:元素符號配中文名 · 步數 '), mv = el('b', '', '0'); info.append(mv);
  const board = el('div', 'board');
  for (const c of cards) {
    const card = el('button', 'mc'); card.type = 'button'; card.setAttribute('aria-label', '翻開卡片');
    const inner = el('div', 'in'), back = el('div', c.sym ? 'b' : 'b name'); back.style.setProperty('--pc', c.e.color);
    if (c.sym) { if (c.e.num) back.append(el('span', 'z', c.e.num)); back.append(el('span', 's' + (c.e.sym.length > 2 ? ' long' : ''), c.e.sym)); }
    else back.append(el('span', 'n', c.e.name));
    inner.append(el('div', 'f', '?'), back); card.append(inner);
    card.onclick = () => {
      if (card.classList.contains('flip') || open.length === 2) return;
      card.classList.add('flip'); card.setAttribute('aria-label', c.sym ? c.e.sym : c.e.name); open.push([card, c]);
      if (open.length < 2) return;
      mv.textContent = ++moves;
      const [[k1, c1], [k2, c2]] = open;
      if (c1.e.sym === c2.e.sym && c1.sym !== c2.sym) {
        [k1, k2].forEach(k => { k.classList.add('done'); k.disabled = true; }); open = [];
        if (!--left) { const first = !matchDone; matchDone = true; save(); render(); hud(); toast((first ? '🚩 征服「配對遊樂島」!' : '完成!') + moves + ' 步'); }
      } else { [k1, k2].forEach(k => k.classList.add('miss'));
        setTimeout(() => { [k1, k2].forEach(k => { k.classList.remove('flip', 'miss'); k.setAttribute('aria-label', '翻開卡片'); }); open = []; }, 900); }
    };
    board.append(card);
  }
  box.append(info, board, btn('g-btn sm', '重新洗牌', () => panel()));
  return box;
}

$('reset').onclick = () => { if (confirm('清除所有進度,重新探索?')) { solved = new Set(); mascotFound = false; matchDone = false; save(); render(); panel(); } };
$('home').onclick = () => go(null);
drawMap();
const start = new URLSearchParams(location.search).get('island');
start && ISL[start] ? go(start) : panel();
})();
"""


def render_quiz(config: Config, items: list[dict], generated: str, seed: str = "") -> str:
    from .site import _brand, _head, esc, icon

    bank = build_bank(config, items, seed)
    payload = json.dumps(bank, ensure_ascii=False).replace("</", "<\\/")
    buildings = sum(len(i["questions"]) for i in bank["islands"])
    script = GAME_SCRIPT.replace("__PAIRS__", str(PAIRS))
    return (
        _head("靶材產業島", "探索靶材產業島:每座島是一個主題、每棟建築是一道題,答對點亮建築、征服島嶼。題目取自本站新聞、商情預測與財報素材。",
              config.settings.site_url, "quiz.html")
        + f"""<style>{GAME_STYLE}</style><div class="wrap" style="max-width:1180px">
<header>{_brand("Sputtering Target · Knowledge Islands", "靶材產業島")}
<nav class="meta" aria-label="網站導覽"><a class="btn" href="index.html">{icon("arrow-left")}新聞摘要</a><a class="btn" href="forecast.html">{icon("research")}商情預測</a><span>{icon("calendar")}題庫更新於 <span class="mono">{esc(generated)}</span></span></nav>
<p class="sub">{len(bank["islands"])} 座島、{buildings} 棟建築(題目)。點選島嶼探索,答對點亮建築;新聞港每天隨新聞換題。進度只存在你的瀏覽器。</p></header>
<section class="isle" aria-label="靶材產業島"><div class="stage" id="stage"><div class="hud2" id="hud2"></div>
<div class="tools"><button type="button" class="g-btn sm" id="home">{icon("globe")}全島</button><button type="button" class="g-btn sm" id="reset">重來</button></div></div>
<aside class="side" id="side" aria-live="polite"></aside></section>
<footer>題目與解說由網站素材整理;市場數字出自富士キメラ総研報告摘要,以原報告為準。公司 logo 取自財務儀表板。</footer>
</div><script type="application/json" id="bank">{payload}</script><script>{script}</script></body></html>"""
    )

"""靶材產業島: the site's knowledge game (quiz.html).

An explorable map of six hexagonal islands around a plaza, one per theme. Each building on an
island is a question; answering it lights the building, and an island whose buildings are all
lit is conquered. Dashed lines link islands that share a concept, one island hosts the element
matching game, a guide character (靶靶) walks to the island you open and reacts to your
answers, and a golden target hides on a different island every day.

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


# id, name, icon, colour, axial hex position (q, r), description. The six sit in a ring
# around the plaza at (0, 0), where the guide character waits.
ISLANDS = [
    ("materials", "材料島", "layers", "#3b5bdb", (0, -1), "導線、阻障層、種子層用的金屬,以及靶材常見的元素符號。"),
    ("process", "製程島", "flame", "#e8590c", (1, -1), "PVD、純度、綁定、旋轉靶、熱均壓,顯示與封裝應用,以及殘靶回收。"),
    ("news", "新聞港", "news", "#f08c00", (1, 0), "最近 3 天重點新聞的主角是誰?每天換題。"),
    ("market", "市場島", "market", "#d6336c", (0, 1), "市場規模與成長率(商情預測)與各家公司的靶材比重。"),
    ("makers", "廠商島", "building", "#0c8f6a", (-1, 1), "台日美中靶材廠:在哪上市、各自強項。"),
    ("play", "配對遊樂島", "star", "#7048e8", (-1, 0), "元素符號配中文名的翻牌遊戲。"),
]
# Collaboration lines: islands that share a concept (label shown when either island is focused).
LINKS = [
    ("materials", "process", "高純度金屬 → 靶材製程"), ("materials", "makers", "JX金屬 Cu 靶、江豐電子 Al 靶"),
    ("market", "makers", "前兩大廠合計約七成"), ("news", "market", "每日市場訊號"),
    ("process", "news", "封裝擴產帶動 PVD"), ("materials", "play", "元素配對"),
]
PER_ISLAND = 12  # buildings per island; fixed questions first, generated ones sampled (seeded by report date)
GUIDE_TIPS = [
    "點一座島,我就帶你過去!答對題目會點亮島上的建築。",
    "島和島之間的虛線代表它們有關聯,點開島嶼就看得到說明。",
    "聽說有一座島上藏著金色靶材,每天換地方喔。",
    "新聞港的題目每天跟著最新新聞更新。",
    "答錯沒關係,建築可以再挑戰,直到點亮為止。",
    "濺鍍時,電漿裡的氬離子撞擊靶材,把原子撞出來沉積到晶圓上。",
    "旋轉靶的材料利用率比平面靶高,常用在面板與太陽能大面積鍍膜。",
]


def _q(topic: str, q: str, answer: str, wrong: list[str], explain: str = "", island: str = "", **extra) -> dict:
    return {"id": hashlib.sha1(q.encode("utf-8")).hexdigest()[:10], "topic": topic, "island": island, "q": q,
            "answer": answer, "wrong": wrong[:3], "explain": explain, **extra}


def fixed_questions(config: Config) -> list[dict]:
    out = []
    for x in config.quiz:
        wrong = [str(w) for w in x.get("wrong") or []]
        if x.get("q") and x.get("answer") and len(wrong) >= 3:
            topic = str(x.get("topic", "materials"))
            island = str(x.get("island") or ("materials" if topic == "materials" else "market"))
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
                      f"{sym} = {name}" + (f",原子序 {num}。" if num else "。"), "materials", visual={"kind": "element", "sym": sym}))
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
                          f"{name}在{MARKETS[market]}上市;主要業務:{segment}。", "makers", visual=visual))
        if focus in FOCUS:
            out.append(_q("market", f"以靶材占營收的比重來看,{name}屬於哪一類?", FOCUS[focus],
                          [v for k, v in FOCUS.items() if k != focus] + ["不生產靶材"],
                          f"{FOCUS_NOTE[focus]}主要業務:{segment}。", "market", visual=visual))
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
            continue  # this headline names too many companies; later ones may still work
        seen.add(ents[0])
        motif, caption, sym = pick_thumb(it, roles)
        picked.append(_q("news", f"這則近期新聞的主角是哪家公司?「{masked}」", answer, rng.sample(pool, 3),
                         it.get("summary") or "", "news", link=it.get("link", ""), date=(it.get("published") or "")[:10],
                         visual={"kind": "icon", "icon": motif, "caption": caption, "sym": sym}))
        if len(picked) >= MAX_NEWS:
            break
    return picked


def build_bank(config: Config, items: list[dict], seed: str = "") -> dict:
    """The island map: islands with their questions (buildings), links, element pairs, the treasure's island and guide tips."""
    rng = random.Random(seed)
    fixed = fixed_questions(config)
    generated = element_questions(rng) + maker_questions(config, rng) + news_questions(items, config, rng)
    islands = []
    for iid, name, icon_name, color, (hq, hr), desc in ISLANDS:
        qs = [q for q in fixed if q["island"] == iid][:PER_ISLAND]
        pool = [q for q in generated if q["island"] == iid]
        qs += rng.sample(pool, min(len(pool), PER_ISLAND - len(qs)))
        if qs or iid == "play":
            islands.append({"id": iid, "name": name, "icon": icon_name, "color": color, "hex": [hq, hr], "desc": desc, "questions": qs})
    ids = {i["id"] for i in islands}
    links = [[a, b, label] for a, b, label in LINKS if a in ids and b in ids]
    elements = [{"sym": s, "name": n, "num": z, "color": c} for s, (n, z, c) in ELEMENT_INFO.items()]
    treasure = rng.choice([i["id"] for i in islands if i["id"] != "play"]) if islands else ""
    return {"islands": islands, "links": links, "elements": elements, "treasure": treasure, "tips": GUIDE_TIPS}






PAIRS = 6

GAME_STYLE = """
.isle{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:16px;align-items:start}
.stage{position:relative;background:#cfe8f7;border-radius:8px;overflow:hidden;min-height:420px}
@media (prefers-color-scheme:dark){.stage{background:#10283d}}
.hud2{position:absolute;left:12px;top:12px;display:flex;flex-wrap:wrap;gap:8px;pointer-events:none;z-index:2}
.chip2{background:var(--surface);border-radius:6px;padding:6px 10px;font-size:.8rem;color:var(--muted);display:inline-flex;gap:6px;align-items:center}.chip2 b{font-family:var(--font-data);color:var(--fg)}
.tools{position:absolute;right:12px;top:12px;display:flex;gap:8px;z-index:2}
.g-btn{font:inherit;font-size:.9rem;border:0;border-radius:6px;padding:9px 16px;cursor:pointer;background:var(--accent-soft);color:var(--accent);font-weight:600;display:inline-flex;gap:8px;align-items:center}
.g-btn:hover{background:var(--accent);color:#fff}.g-btn.primary{background:var(--accent);color:#fff}.g-btn.primary:hover{background:var(--accent-2)}.g-btn.sm{padding:6px 12px;font-size:.82rem}
.tools .g-btn{background:var(--surface)}
.stage canvas.gl{display:block;width:100%;cursor:grab;touch-action:pan-y}.stage canvas.gl.grab{cursor:grabbing}
.ov{position:absolute;inset:0;pointer-events:none;overflow:hidden}
.ov>*{position:absolute;left:0;top:0;will-change:transform}
.lbl{display:grid;justify-items:center;text-align:center;white-space:nowrap;line-height:1.25}
.lbl b{font:700 13px var(--font-body);color:var(--fg);text-shadow:0 0 3px #fff,0 0 3px #fff,0 0 6px #fff}
.lbl small{font:500 10.5px var(--font-data);color:var(--muted);text-shadow:0 0 3px #fff,0 0 3px #fff}
.lbl.on b{color:var(--accent)}
@media (prefers-color-scheme:dark){.lbl b,.lbl small{text-shadow:0 0 3px #10283d,0 0 3px #10283d,0 0 6px #10283d}}
.link-tag{font:600 11.5px var(--font-body);color:var(--accent-2);background:var(--surface);border-radius:4px;padding:2px 7px;white-space:nowrap}
.gbubble{background:var(--surface);border:1.5px solid var(--accent);border-radius:8px;padding:7px 11px;font:600 12.5px/1.5 var(--font-body);color:var(--fg);white-space:nowrap;transition:opacity .25s;margin-top:-10px}
.gbubble::after{content:"";position:absolute;left:50%;bottom:-7px;width:10px;height:10px;background:var(--surface);border:solid var(--accent);border-width:0 1.5px 1.5px 0;transform:translateX(-50%) rotate(45deg)}
.gbubble.off{opacity:0}.gbubble.corner{position:absolute;left:12px;bottom:12px}.gbubble.corner::after{display:none}
.nomap{position:absolute;inset:auto 12px 12px 12px;background:var(--surface);border-radius:6px;padding:12px 14px;font-size:.88rem;color:var(--muted)}
.stage{min-height:380px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
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
@media (prefers-reduced-motion:reduce){.mc .in{transition:none}.gbubble{transition:none}}
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
let solved = new Set(store.get('isle-solved', [])), treasureFound = store.get('isle-treasure', ''), matchDone = store.get('isle-match', false);
const found = () => treasureFound === B.treasure;  // the treasure moves daily; finding today's counts
const save = () => { store.set('isle-solved', [...solved]); store.set('isle-treasure', treasureFound); store.set('isle-match', matchDone); };
const lit = i => i.questions.filter(q => solved.has(q.id)).length;
const conquered = i => i.id === 'play' ? matchDone : i.questions.length > 0 && lit(i) === i.questions.length;

// ---------- world: target-shaped islands floating over the sea (three.js) ----------
// three.js is loaded on demand; until it arrives (or if WebGL is unavailable) the side panel
// still lists every island, so the game stays playable. Labels and the guide's speech bubble
// are HTML overlays positioned from the 3D scene, so Chinese text stays crisp.
const THREE_URL = 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.min.js';
const S = 10, R = 3.2, TH = .9, PL = .4, D2R = Math.PI / 180;  // island spacing, target radius / thickness, backing plate
const PLAZA = {id: 'plaza', plaza: true, name: '靶材廣場', hex: [0, 0]};
const ALL = B.islands.concat(PLAZA);
const PHASE = Object.fromEntries(ALL.map((i, k) => [i.id, k * 1.7]));
const worldXZ = i => [S * (i.hex[0] + i.hex[1] / 2), S * i.hex[1] * Math.sqrt(3) / 2];
// target designs: shape of the target face and the material colour
const DESIGN = {
  materials: {shape: 'disc', metal: '#c3ccd8', ring: '#8d99ae'},
  process: {shape: 'rect', metal: '#aab4c3', ring: '#6c7a89', w: 7.2, d: 4.6},
  news: {shape: 'disc', metal: '#b9c4d2', ring: '#7c8ba1'},
  market: {shape: 'disc', metal: '#f2c94c', ring: '#c99a06'},
  makers: {shape: 'disc', metal: '#e0915a', ring: '#a85a24'},
  play: {shape: 'tiles', metal: '#66d9e8', ring: '#1098ad', w: 6.2},
  plaza: {shape: 'disc', metal: '#efe1bf', ring: '#c9b48a'},
};
const OVERVIEW = {x: 0, z: 1.5, r: 46, pitch: 48}, CLOSE = {r: 21, pitch: 40};

let focus = null, T = null, W3 = null;

// Bubble lines of at most `per` characters: break after punctuation when one is near the end,
// and never start a line with punctuation.
const PUNCT = '。!?,、;:)」~!?,';
function wrap(text, per) {
  const out = []; let rest = [...text];
  while (rest.length) {
    let cut = Math.min(per, rest.length);
    if (cut < rest.length) { for (let k = cut; k > cut - 5 && k > 1; k--) if (PUNCT.includes(rest[k - 1])) { cut = k; break; } }
    while (cut < rest.length && PUNCT.includes(rest[cut])) cut++;
    out.push(rest.slice(0, cut).join('')); rest = rest.slice(cut);
  }
  return out;
}

function boot() {
  import(THREE_URL).then(mod => {
    try { init3d(mod); }
    catch (e) { console.error(e); noMap('這個瀏覽器無法顯示 3D 地圖(需要 WebGL),請用右側清單選擇島嶼。'); }
  }).catch(() => noMap('3D 地圖載入失敗,請檢查網路後重新整理;也可以直接用右側清單選擇島嶼。'));
}
function noMap(msg) { const n = el('div', 'nomap', msg); $('stage').append(n); }

function init3d(THREE) {
  T = THREE;
  const stage = $('stage'), dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const renderer = new T.WebGLRenderer({antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = T.PCFSoftShadowMap;
  const canvas = renderer.domElement;
  canvas.className = 'gl'; canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', '靶材產業島 3D 地圖:點選島嶼查看題目,拖曳可以轉動視角;也可以用右側清單選擇島嶼');
  const overlay = el('div', 'ov');
  stage.prepend(canvas, overlay);

  const scene = new T.Scene(), sky = new T.Color(dark ? '#10283d' : '#cfe8f7');
  scene.background = sky; scene.fog = new T.Fog(sky, 60, 140);
  const camera = new T.PerspectiveCamera(32, 1.6, .1, 400);
  scene.add(new T.HemisphereLight('#ffffff', dark ? '#1b3550' : '#8fbfdd', dark ? 1.1 : 1.6));
  const sun = new T.DirectionalLight('#fff6e8', dark ? 1.6 : 2.4);
  sun.position.set(16, 34, 22); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {left: -32, right: 32, top: 32, bottom: -32, near: 1, far: 100}); sun.shadow.bias = -.0006;
  scene.add(sun);

  // ---- helpers: flat-shaded materials (cached), meshes, canvas-text textures ----
  const mats = {};
  const mat = (c, o) => { const k = c + JSON.stringify(o || {}); return mats[k] || (mats[k] = new T.MeshStandardMaterial(Object.assign({color: c, flatShading: true, roughness: .55, metalness: .1}, o))); };
  const mesh = (geo, m, shadow = true) => { const x = new T.Mesh(geo, m); x.castShadow = shadow; x.receiveShadow = true; return x; };
  const at = (o, x, y, z) => { o.position.set(x, y, z); return o; };
  const textTex = (text, bg, fg, w = 128, h = 128, size = 64) => {
    const c = document.createElement('canvas'); c.width = w; c.height = h; const g = c.getContext('2d');
    g.fillStyle = bg; g.fillRect(0, 0, w, h); g.fillStyle = fg; g.font = `700 ${size}px "Noto Sans TC", system-ui, sans-serif`;
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, w / 2, h / 2 + 2);
    const t = new T.CanvasTexture(c); t.colorSpace = T.SRGBColorSpace; return t;
  };
  const anim = [];  // per-frame animations of decorations: fn(seconds)

  // ---- sea with gentle low-poly waves, and a few drifting clouds ----
  const seaGeo = new T.PlaneGeometry(260, 260, 64, 64); seaGeo.rotateX(-Math.PI / 2);
  const sea = mesh(seaGeo, mat(dark ? '#1d4e73' : '#7cc4ec', {roughness: .85, metalness: 0}), false);
  sea.position.y = -8; scene.add(sea);
  const seaPos = seaGeo.attributes.position, seaBase = Float32Array.from(seaPos.array);
  const clouds = [];
  for (let k = 0; k < 6; k++) {
    const c = new T.Group(), m = mat('#ffffff', {roughness: 1, transparent: true, opacity: dark ? .35 : .9});
    [[0, 0, 0, 1.6], [1.5, -.2, .3, 1.2], [-1.4, -.3, -.2, 1.1], [.4, .5, -.4, 1.1]].forEach(([x, y, z, r]) => c.add(at(mesh(new T.IcosahedronGeometry(r, 0), m, false), x, y, z)));
    const a = k / 6 * Math.PI * 2 + .4, rr = 30 + (k % 3) * 9;
    c.position.set(Math.cos(a) * rr, 7 + (k % 2) * 3, Math.sin(a) * rr - 6); c.scale.setScalar(1 + (k % 3) * .3);
    clouds.push({c, a, rr}); scene.add(c);
  }

  // ---- decorations (base at y = 0), built from primitives ----
  const deco = {
    cube(sym, color) { const tex = textTex(sym, color, '#ffffff', 128, 128, 58), b = mesh(new T.BoxGeometry(.75, .75, .75), new T.MeshStandardMaterial({map: tex, roughness: .5}));
      b.position.y = .375; b.rotation.y = .5; const g = new T.Group(); g.add(b); return g; },
    ingot(color) { const geo = new T.CylinderGeometry(.42, .6, .38, 4); geo.rotateY(Math.PI / 4); geo.scale(1.7, 1, .9);
      const g = new T.Group(); g.add(at(mesh(geo, mat(color, {metalness: .4})), 0, .19, 0)); return g; },
    chamber() { const g = new T.Group();
      g.add(at(mesh(new T.BoxGeometry(1.6, .8, 1.1), mat('#6c7a89')), 0, .4, 0));
      g.add(at(mesh(new T.SphereGeometry(.6, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2), mat('#d0ebff', {transparent: true, opacity: .55, roughness: .1})), 0, .8, 0));
      const plasma = at(mesh(new T.SphereGeometry(.28, 12, 8), mat('#9775fa', {emissive: '#7048e8', emissiveIntensity: 1.2}), false), 0, 1.0, 0);
      g.add(plasma, at(mesh(new T.BoxGeometry(1.2, .12, .02), mat('#ffd43b')), 0, .5, .56));
      anim.push(t => { const s = RM ? 1 : .85 + .25 * Math.sin(t * 4.4); plasma.scale.setScalar(s); });
      return g; },
    magnet() { const g = new T.Group();
      g.add(at(mesh(new T.BoxGeometry(.42, .75, .5), mat('#e03131')), -.22, .375, 0), at(mesh(new T.BoxGeometry(.42, .75, .5), mat('#3b5bdb')), .22, .375, 0));
      return g; },
    tube() { const g = new T.Group();
      g.add(at(mesh(new T.CylinderGeometry(.32, .32, 2.6, 16), mat('#9aa5b1', {metalness: .3})), 0, 1.3, 0), at(mesh(new T.CylinderGeometry(.34, .34, .18, 16), mat('#f76707')), 0, 1.7, 0));
      const beamGeo = new T.ConeGeometry(.55, 2.4, 16, 1, true); beamGeo.rotateZ(Math.PI / 2); beamGeo.translate(1.4, 0, 0);
      const beam = at(mesh(beamGeo, mat('#ffe066', {transparent: true, opacity: .5, emissive: '#ffd43b', emissiveIntensity: .8, side: T.DoubleSide}), false), 0, 2.45, 0);
      g.add(beam); anim.push(t => { if (!RM) { beam.rotation.y = t * 1.2; beam.material.opacity = .3 + .25 * (1 + Math.sin(t * 3)) / 2; } });
      return g; },
    boat() { const g = new T.Group(), hull = new T.CylinderGeometry(.55, .35, .32, 4); hull.rotateY(Math.PI / 4); hull.scale(1.7, 1, .7);
      g.add(at(mesh(hull, mat('#f08c00')), 0, .16, 0), at(mesh(new T.CylinderGeometry(.03, .03, 1.3), mat('#495057')), 0, .95, 0));
      const sail = new T.Shape(); sail.moveTo(0, 0); sail.lineTo(.75, 0); sail.lineTo(0, 1.05); sail.closePath();
      g.add(at(mesh(new T.ShapeGeometry(sail), mat('#ffffff', {side: T.DoubleSide})), .04, .4, 0));
      anim.push(t => { if (!RM) g.rotation.z = .08 * Math.sin(t * 2.2); });
      return g; },
    bars() { const g = new T.Group();
      [[-.5, .5, '#d6336c'], [0, .9, '#f76707'], [.5, 1.35, '#2f9e44']].forEach(([x, h, c]) => g.add(at(mesh(new T.BoxGeometry(.38, h, .38), mat(c)), x, h / 2, 0)));
      return g; },
    coins() { const g = new T.Group();
      for (let k = 0; k < 5; k++) g.add(at(mesh(new T.CylinderGeometry(.38, .38, .12, 20), mat(k % 2 ? '#f2b705' : '#ffd43b', {metalness: .5})), (k % 2) * .04, .06 + k * .13, 0));
      return g; },
    factory() { const g = new T.Group();
      g.add(at(mesh(new T.BoxGeometry(1.4, .7, .9), mat('#e9ecef')), 0, .35, 0));
      for (let k = 0; k < 3; k++) { const roof = new T.CylinderGeometry(.01, .32, .9, 3); roof.rotateX(Math.PI / 2); g.add(at(mesh(roof, mat('#868e96')), -.45 + k * .45, .85, 0)); }
      g.add(at(mesh(new T.CylinderGeometry(.13, .16, 1.3, 10), mat('#868e96')), .55, .95, -.25));
      const puffs = [0, 1, 2].map(k => { const p = mesh(new T.IcosahedronGeometry(.18, 0), mat('#dee2e6', {transparent: true, opacity: .8}), false); g.add(p); return p; });
      anim.push(t => puffs.forEach((p, k) => { const f = RM ? .4 + k * .2 : (t * .45 + k / 3) % 1; p.position.set(.55 + f * .2, 1.65 + f * 1.2, -.25); p.scale.setScalar(.7 + f * 1.3); p.material.opacity = .85 * (1 - f); }));
      return g; },
    pennant(label, color) { const g = new T.Group();
      g.add(at(mesh(new T.CylinderGeometry(.03, .03, 1.5), mat('#495057')), 0, .75, 0));
      const flag = at(new T.Mesh(new T.PlaneGeometry(.75, .45), new T.MeshStandardMaterial({map: textTex(label, color, '#ffffff', 128, 80, 44), side: T.DoubleSide})), .38, 1.25, 0);
      g.add(flag); const ph = Math.random() * 6; anim.push(t => { if (!RM) flag.rotation.y = .25 * Math.sin(t * 2.6 + ph); });
      return g; },
    cards() { const g = new T.Group();
      const c1 = at(mesh(new T.BoxGeometry(.55, .78, .05), mat('#7048e8')), -.2, .39, 0), c2 = at(mesh(new T.BoxGeometry(.55, .78, .05), mat('#f76707')), .22, .39, .05);
      c1.rotation.z = .2; c2.rotation.z = -.18; g.add(c1, c2); return g; },
    fountain() { const g = new T.Group();
      g.add(at(mesh(new T.CylinderGeometry(1.2, 1.3, .35, 24), mat('#c9b48a')), 0, .17, 0), at(mesh(new T.CylinderGeometry(1.0, 1.0, .05, 24), mat('#74c0fc', {roughness: .2})), 0, .36, 0),
        at(mesh(new T.CylinderGeometry(.16, .2, .8, 10), mat('#e9dcc0')), 0, .7, 0));
      const drops = [...Array(10)].map(() => { const d = mesh(new T.SphereGeometry(.07, 6, 4), mat('#a5d8ff', {emissive: '#74c0fc', emissiveIntensity: .3}), false); g.add(d); return d; });
      anim.push(t => drops.forEach((d, k) => { const a = k / drops.length * Math.PI * 2, f = RM ? .5 : (t * .8 + k * .13) % 1, rr = f * .85;
        d.position.set(Math.cos(a) * rr, 1.1 + 1.1 * f - 1.6 * f * f, Math.sin(a) * rr); }));
      return g; },
    flag() { const g = new T.Group();
      g.add(at(mesh(new T.CylinderGeometry(.04, .04, 2.4), mat('#162033')), 0, 1.2, 0));
      const f = at(new T.Mesh(new T.PlaneGeometry(1, .62, 6, 1), mat('#ffd43b', {side: T.DoubleSide})), .5, 2.05, 0);
      g.add(f); const base = Float32Array.from(f.geometry.attributes.position.array);
      anim.push(t => { if (RM) return; const p = f.geometry.attributes.position;
        for (let k = 0; k < p.count; k++) { const x = base[k * 3] + .5; p.setZ(k, Math.sin(t * 5 - x * 4) * .09 * x); } p.needsUpdate = true; });
      return g; },
  };
  const DECOR = {
    materials: () => [[30, .55, deco.cube('Cu', '#d9773a')], [100, .6, deco.cube('Ta', '#5c6bc0')], [170, .55, deco.cube('Ti', '#6c7a89')], [250, .5, deco.ingot('#c3ccd8')]],
    process: () => [[200, .35, deco.chamber()], [20, .5, deco.magnet()]],
    news: () => [[120, .4, deco.tube()], [330, .65, deco.boat()]],
    market: () => [[200, .4, deco.bars()], [40, .5, deco.coins()]],
    makers: () => [[150, .45, deco.factory()], [20, .58, deco.pennant('TW', '#0c8f6a')], [70, .62, deco.pennant('JP', '#c92a2a')],
      [250, .62, deco.pennant('US', '#364fc7')], [300, .58, deco.pennant('CN', '#e03131')]],
    play: () => [[200, .3, deco.cards()]],
    plaza: () => [[0, 0, deco.fountain()]],
  };

  // ---- islands: rock cone, copper backing plate, the target itself, decorations; studs = questions ----
  const nodes = {};
  for (const i of ALL) {
    const d = DESIGN[i.id] || DESIGN.materials, plaza = !!i.plaza, [x, z] = worldXZ(i);
    const root = new T.Group(), top = new T.Group(); root.add(top); root.position.set(x, 0, z);
    const reach = d.shape === 'disc' ? R : d.shape === 'rect' ? Math.max(d.w, d.d) / 2 : d.w / 2;
    const rock = mesh(new T.ConeGeometry(reach * .92, 4.6, 7), mat(plaza ? '#b08d57' : '#7a6a5a', {roughness: .9}));
    rock.rotation.x = Math.PI; rock.position.y = -PL - 2.3; top.add(rock);
    let topY = TH;
    if (d.shape === 'disc') {
      if (!plaza) top.add(at(mesh(new T.CylinderGeometry(R + .35, R + .35, PL, 48), mat('#c0773f', {metalness: .3})), 0, -PL / 2, 0));
      const h = plaza ? TH * 1.3 : TH; topY = plaza ? h - PL : TH;
      top.add(at(mesh(new T.CylinderGeometry(plaza ? R * 1.05 : R, plaza ? R * 1.05 : R, h, 48), mat(d.metal, {metalness: plaza ? 0 : .35})), 0, topY - h / 2, 0));
      if (!plaza) {
        const ring = new T.TorusGeometry(R * .62, .2, 6, 48); ring.rotateX(Math.PI / 2); ring.scale(1, .25, 1);
        top.add(at(mesh(ring, mat(d.ring, {metalness: .3}), false), 0, TH, 0), at(mesh(new T.CylinderGeometry(R * .18, R * .18, .06, 24), mat(d.metal, {metalness: .5, roughness: .3}), false), 0, TH + .03, 0));
      }
    } else if (d.shape === 'rect') {
      top.add(at(mesh(new T.BoxGeometry(d.w + .6, PL, d.d + .6), mat('#c0773f', {metalness: .3})), 0, -PL / 2, 0),
        at(mesh(new T.BoxGeometry(d.w, TH, d.d), mat(d.metal, {metalness: .35})), 0, TH / 2, 0));
      const race = new T.TorusGeometry(1, .09, 6, 48); race.rotateX(Math.PI / 2); race.scale(d.w * .36, .5, d.d * .28);
      top.add(at(mesh(race, mat(d.ring, {metalness: .3}), false), 0, TH, 0));
    } else {
      top.add(at(mesh(new T.BoxGeometry(d.w + .6, PL, d.w + .6), mat('#c0773f', {metalness: .3})), 0, -PL / 2, 0));
      const t3 = d.w / 3;
      for (let r0 = 0; r0 < 3; r0++) for (let c0 = 0; c0 < 3; c0++)
        top.add(at(mesh(new T.BoxGeometry(t3 - .14, TH, t3 - .14), mat((r0 + c0) % 2 ? d.metal : d.ring, {metalness: .2})), -d.w / 2 + t3 * (c0 + .5), TH / 2, -d.w / 2 + t3 * (r0 + .5)));
    }
    const decoR = d.shape === 'disc' ? R : d.shape === 'rect' ? d.w / 2 : d.w / 2;
    for (const [deg, rr, g] of (DECOR[i.id] || (() => []))()) { g.position.set(rr * decoR * Math.cos(deg * D2R), topY, rr * decoR * Math.sin(deg * D2R)); top.add(g); }
    const dyn = new T.Group(); top.add(dyn);
    root.traverse(o => { o.userData.pick = i.id; });
    scene.add(root);
    const lbl = el('div', 'lbl'), nm = el('b', '', i.name), sub = el('small', ''); lbl.append(nm, sub); overlay.append(lbl);
    const rim = d.shape === 'disc' ? [R * .86, R * .86] : d.shape === 'rect' ? [d.w * .44, d.d * .4] : [d.w * .5, d.w * .5];
    nodes[i.id] = {i, d, root, top, dyn, topY, rim, x, z, lbl, sub, hover: 0, reach};
  }

  // ---- links between related islands: dashed arcs, highlighted (with a label) around the focused island ----
  const links = B.links.map(([a, b, label]) => {
    const geo = new T.BufferGeometry(), line = new T.Line(geo, new T.LineDashedMaterial({color: '#ffffff', dashSize: .7, gapSize: .5, transparent: true, opacity: .8}));
    scene.add(line); const tag = el('div', 'link-tag', label); tag.hidden = true; tag.dataset.anchor = '-50%,-50%'; overlay.append(tag);
    return {a, b, line, tag};
  });

  // ---- treasure: a spinning gold target with a sparkle ----
  const treasure = new T.Group();
  const coin = mesh(new T.CylinderGeometry(.5, .5, .14, 28), mat('#f2b705', {metalness: .7, roughness: .25, emissive: '#b7791f', emissiveIntensity: .25}));
  coin.rotation.x = Math.PI / 2; coin.position.y = .8;
  const star = mesh(new T.OctahedronGeometry(.16), mat('#ffffff', {emissive: '#ffffff', emissiveIntensity: 1}), false); star.position.set(.45, 1.35, 0);
  treasure.add(coin, star); treasure.traverse(o => { o.userData.pick = 'treasure'; });

  // ---- 靶靶: the guide, a little figure with a target for a head ----
  const G = new T.Group(), gBody = new T.Group(); G.add(gBody);
  const navy = mat('#1e2a4a'), blue = mat('#3b5bdb'), ink = mat('#162033', {roughness: .3});
  gBody.add(at(mesh(new T.CylinderGeometry(.11, .11, .5, 8), navy), -.17, .25, 0), at(mesh(new T.CylinderGeometry(.11, .11, .5, 8), navy), .17, .25, 0),
    at(mesh(new T.CylinderGeometry(.36, .42, .75, 14), blue), 0, .85, 0), at(mesh(new T.CylinderGeometry(.43, .43, .12, 14), mat('#f76707')), 0, .7, 0));
  const armL = at(mesh(new T.CylinderGeometry(.08, .08, .55, 8), blue), -.48, .95, 0), armR = at(mesh(new T.CylinderGeometry(.08, .08, .55, 8), blue), .5, 1.05, 0);
  armL.rotation.z = -.5; armR.rotation.z = .9; gBody.add(armL, armR);
  gBody.add(at(mesh(new T.CylinderGeometry(.025, .025, 1.3, 6), ink), .72, 1.5, 0));
  const pen = new T.Shape(); pen.moveTo(0, 0); pen.lineTo(.5, -.15); pen.lineTo(0, -.3); pen.closePath();
  gBody.add(at(mesh(new T.ShapeGeometry(pen), mat('#f76707', {side: T.DoubleSide})), .73, 2.15, 0));
  const head = new T.Group(); head.position.y = 1.75; gBody.add(head);
  const face = new T.CylinderGeometry(.58, .58, .26, 32); face.rotateX(Math.PI / 2);
  head.add(mesh(face, mat('#dde3ec', {metalness: .4, roughness: .35})));
  const hr = new T.TorusGeometry(.4, .025, 6, 32); head.add(at(mesh(hr, mat('#b5bdcb'), false), 0, 0, .135));
  head.add(at(mesh(new T.SphereGeometry(.07, 10, 8), ink, false), -.18, .08, .14), at(mesh(new T.SphereGeometry(.07, 10, 8), ink, false), .18, .08, .14));
  const blush = [-.33, .33].map(x => at(mesh(new T.CircleGeometry(.07, 12), mat('#ff8787', {transparent: true, opacity: .8}), false), x, -.08, .135));
  head.add(...blush);
  const mouthGeo = new T.TorusGeometry(.12, .025, 6, 14, Math.PI);
  const smile = at(mesh(mouthGeo, ink, false), 0, -.08, .14), frown = at(mesh(mouthGeo, ink, false), 0, -.2, .14);
  smile.rotation.z = Math.PI; head.add(smile, frown); frown.visible = false;
  const sweat = at(mesh(new T.SphereGeometry(.07, 8, 6), mat('#74c0fc'), false), .5, .38, .1); sweat.visible = false; head.add(sweat);
  const shadowDisc = at(new T.Mesh(new T.CircleGeometry(.45, 20), new T.MeshBasicMaterial({color: '#000000', transparent: true, opacity: .18, depthWrite: false})), 0, .02, 0);
  shadowDisc.rotation.x = -Math.PI / 2; G.add(shadowDisc);
  G.traverse(o => { o.castShadow = o.castShadow && o !== shadowDisc; o.userData.pick = 'guide'; });
  scene.add(G);
  const bubble = el('div', 'gbubble off'); bubble.dataset.anchor = '-50%,-100%'; overlay.append(bubble);
  const early = $('bubble0'); if (early) early.remove();

  // ---- camera: orbit around a target, eased toward the overview or the focused island; drag to turn ----
  const cam = {x: OVERVIEW.x, z: OVERVIEW.z, r: OVERVIEW.r, pitch: OVERVIEW.pitch, yaw: 0};
  let goal = Object.assign({}, OVERVIEW), dragYaw = 0, dragPitch = 0, drag = null, dragMoved = false, hovered = null;
  const ray = new T.Raycaster(), ndc = new T.Vector2();
  const pickAt = e => { const b = canvas.getBoundingClientRect(); ndc.set((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1);
    ray.setFromCamera(ndc, camera); const hit = ray.intersectObjects(scene.children, true).find(h => h.object.userData.pick && h.object.visible);
    return hit ? hit.object.userData.pick : null; };
  canvas.addEventListener('pointerdown', e => { drag = {x: e.clientX, y: e.clientY, yaw: dragYaw, pitch: dragPitch}; dragMoved = false; });
  canvas.addEventListener('pointermove', e => {
    if (drag) { const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!dragMoved && Math.hypot(dx, dy) > 5) { dragMoved = true; canvas.setPointerCapture && canvas.setPointerCapture(e.pointerId); canvas.classList.add('grab'); }
      if (dragMoved) { dragYaw = Math.max(-70, Math.min(70, drag.yaw - dx * .3)); dragPitch = Math.max(-20, Math.min(22, drag.pitch + dy * .2)); }
      return; }
    const id = e.pointerType === 'mouse' ? pickAt(e) : null; hovered = id && id !== 'plaza' && ISL[id] ? id : null;
    canvas.style.cursor = id ? 'pointer' : 'grab';
  });
  const end = e => {
    canvas.classList.remove('grab');
    if (drag && !dragMoved && e.type === 'pointerup') {
      const id = pickAt(e);
      if (id === 'guide') guide.poke();
      else if (id === 'treasure') findTreasure();
      else go(id && ISL[id] ? id : null);
    }
    drag = null;
  };
  canvas.addEventListener('pointerup', end); canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('pointerleave', () => { hovered = null; });

  const resize = () => { const w = stage.clientWidth, h = Math.round(Math.max(380, Math.min(640, w * .68)));
    renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe(stage); resize();

  const v3 = new T.Vector3();
  const screen = p => { v3.copy(p).project(camera); return [(v3.x + 1) / 2 * canvas.clientWidth, (1 - v3.y) / 2 * canvas.clientHeight, v3.z < 1]; };
  // overlays: each div is anchored by its data-anchor (default: top centre)
  const place = (div, p) => { const [sx, sy, ok] = screen(p);
    div.style.transform = `translate(${sx.toFixed(1)}px,${sy.toFixed(1)}px) translate(${div.dataset.anchor || '-50%,0'})`; div.style.visibility = ok ? '' : 'hidden'; };

  W3 = {
    nodes, links, treasure, deco, mesh, mat, at, T,
    G, gBody, head, smile, frown, sweat, blush, bubble,
    focusOn(id) { const n = id && nodes[id]; goal = n ? {x: n.x, z: n.z + 1, r: CLOSE.r, pitch: CLOSE.pitch} : Object.assign({}, OVERVIEW); },
    yaw: () => cam.yaw,
  };

  let t = 0, last = performance.now();
  const loop = now => {
    const dt = Math.min(.05, (now - last) / 1000); last = now; t += dt;
    // islands bob, sway on their own axis and lift a little under the mouse
    for (const id in nodes) {
      const n = nodes[id], ph = PHASE[id];
      n.root.position.y = RM ? 0 : .35 * Math.sin(t * 2 * Math.PI / 3.2 + ph);
      n.top.rotation.y = RM ? 0 : 9 * D2R * Math.sin(t * 2 * Math.PI / 7 + ph);
      n.top.rotation.z = RM ? 0 : .025 * Math.sin(t * 2 * Math.PI / 5 + ph * 1.3);
      n.hover += ((hovered === id || focus === id ? 1 : 0) - n.hover) * Math.min(1, dt * 8);
      n.root.scale.setScalar(1 + .05 * n.hover);
      // name tag just below the island's rim on the side facing the camera
      place(n.lbl, new T.Vector3(n.x + Math.sin(cam.yaw) * (n.reach + .3), n.root.position.y - PL - .6, n.z + Math.cos(cam.yaw) * (n.reach + .3)));
    }
    // links follow the islands
    for (const L of links) {
      const A = nodes[L.a].root.position, Bp = nodes[L.b].root.position, mid = A.clone().add(Bp).multiplyScalar(.5); mid.y += 2.2;
      const pts = new T.QuadraticBezierCurve3(A.clone().setY(A.y + TH), mid, Bp.clone().setY(Bp.y + TH)).getPoints(24);
      L.line.geometry.setFromPoints(pts); L.line.computeLineDistances();
      if (!L.tag.hidden) { const f = focus === L.a ? pts : pts.slice().reverse(); place(L.tag, f[15].clone().setY(f[15].y + .8)); }
    }
    if (treasure.parent) { coin.rotation.z = RM ? .5 : t * 2; star.scale.setScalar(RM ? 1 : .6 + .5 * Math.abs(Math.sin(t * 3))); }
    for (const f of anim) f(t);
    // sea waves and clouds
    if (!RM) {
      for (let k = 0; k < seaPos.count; k++) { const x = seaBase[k * 3], z = seaBase[k * 3 + 2];
        seaPos.setY(k, .35 * Math.sin(x * .18 + t * .9) + .25 * Math.cos(z * .21 + t * .7)); }
      seaPos.needsUpdate = true; seaGeo.computeVertexNormals();
      clouds.forEach((c, k) => { const a = c.a + t * .01 * (k % 2 ? 1 : -1); c.c.position.x = Math.cos(a) * c.rr; c.c.position.z = Math.sin(a) * c.rr - 6; });
    }
    // camera eases toward its goal; a slow idle sway when nobody is dragging
    if (!drag) { dragYaw *= RM ? 1 : .995; dragPitch *= RM ? 1 : .99; }
    const k = Math.min(1, dt * 3.2);
    cam.x += (goal.x - cam.x) * k; cam.z += (goal.z - cam.z) * k; cam.r += (goal.r * Math.max(1, 1.45 / camera.aspect) - cam.r) * k;  // narrow screens: step back so the ring fits cam.pitch += (goal.pitch - cam.pitch) * k;
    cam.yaw = (dragYaw + (RM ? 0 : 6 * Math.sin(t * 2 * Math.PI / 24))) * D2R;
    const p = Math.max(18, Math.min(72, cam.pitch + dragPitch)) * D2R;
    camera.position.set(cam.x + cam.r * Math.cos(p) * Math.sin(cam.yaw), cam.r * Math.sin(p), cam.z + cam.r * Math.cos(p) * Math.cos(cam.yaw));
    camera.lookAt(cam.x, 0, cam.z);
    guide.tick(dt, t);
    if (!bubble.classList.contains('off')) place(bubble, G.position.clone().setY(G.position.y + 2.7));
    renderer.render(scene, camera);
    requestAnimationFrame(loop);
  };
  render(); guide.home();
  if (focus) { W3.focusOn(focus); guide.place(focus, -1.3, 2.0); }  // ?island= was opened before three.js arrived
  requestAnimationFrame(loop);
}

// Rebuild what depends on progress: studs (one per question, lit when answered), conquest flags,
// label text, the link highlight and the treasure.
function render() {
  if (!W3) return;
  const {nodes, links, treasure, deco, mesh, mat, at, T} = W3;
  const STUD = W3.stud || (W3.stud = {post: new T.CylinderGeometry(.14, .14, .4, 8), cap: new T.SphereGeometry(.15, 10, 6)});  // shared by every rebuild
  for (const id in nodes) {
    const n = nodes[id], i = n.i, plaza = !!i.plaza, done = !plaza && conquered(i);
    const cnt = plaza ? 0 : i.id === 'play' ? 6 : i.questions.length, k = plaza ? 0 : lit(i);
    n.dyn.clear();
    for (let j = 0; j < cnt; j++) {
      const on = i.id === 'play' ? matchDone : solved.has(i.questions[j].id), a = j / cnt * Math.PI * 2;
      const s = new T.Group(); s.position.set(n.rim[0] * Math.cos(a), n.topY, n.rim[1] * Math.sin(a));
      s.add(at(mesh(STUD.post, mat(on ? '#f08c00' : '#868e96')), 0, .2, 0),
        at(mesh(STUD.cap, on ? mat('#ffe066', {emissive: '#ffd43b', emissiveIntensity: 1.1}) : mat('#ced4da'), false), 0, .45, 0));
      n.dyn.add(s);
    }
    if (done) n.dyn.add(deco.flag());
    n.dyn.traverse(o => { o.userData.pick = id; });
    n.sub.textContent = plaza ? '' : i.id === 'play' ? (matchDone ? '已完成' : '小遊戲') : `${k} / ${cnt}`;
    n.lbl.classList.toggle('on', focus === id);
  }
  for (const L of links) {
    const on = !!focus && (focus === L.a || focus === L.b);
    L.line.material.color.set(on ? '#f76707' : '#ffffff'); L.line.material.opacity = on ? 1 : .75; L.tag.hidden = !on;
  }
  if (treasure.parent) treasure.parent.remove(treasure);
  if (B.treasure && !found() && nodes[B.treasure]) { const n = nodes[B.treasure]; treasure.position.set(-n.rim[0] * .55, n.topY, n.rim[1] * .4); n.top.add(treasure); }
}
function findTreasure() {
  treasureFound = B.treasure; save(); render(); hud();
  guide.goTo(B.treasure, -1.2, .9, () => { guide.react('jump', 'happy'); guide.say('哇!找到今天的金色靶材了!明天它會躲到別座島喔。'); });
  toast('✨ 找到金色靶材!');
}

// ---------- 靶靶: the guide character ----------
// Hops between islands, then rides along on the island it stands on (a spot in that island's own
// frame, so it bobs and turns with it). Without the 3D map only the speech bubble is shown.
const guide = (() => {
  let tip = 0, hideT = null, anchor = null, hop = null, react = null, standOn = 'plaza';
  const spot = (id, ox, oz) => () => { const n = W3.nodes[id]; return n.top.localToWorld(new W3.T.Vector3(ox, n.topY, oz)); };
  const mood = m => { if (!W3) return; W3.smile.visible = m !== 'sad'; W3.frown.visible = m === 'sad'; W3.sweat.visible = m === 'sad';
    W3.blush.forEach(b => { b.visible = m !== 'sad'; }); W3.smile.scale.setScalar(m === 'happy' ? 1.3 : 1); };
  const api = {
    home() { api.place('plaza', 1.8, 1.1); },
    place(id, ox, oz) { anchor = spot(id, ox, oz); standOn = id; hop = null; W3.G.position.copy(anchor()); },
    goTo(id, ox, oz, done) {
      if (!W3) { done && done(); return; }
      const to = spot(id, ox, oz), from = W3.G.position.clone(), dist = from.distanceTo(to());
      anchor = null; standOn = id;
      hop = {to, from, t0: performance.now(), dur: RM || dist < .05 ? 0 : Math.min(1100, 380 + dist * 45), h: Math.min(5, 1 + dist * .25), done};
    },
    tick(dt, t) {
      const G = W3.G, body = W3.gBody;
      if (hop) {
        const p = hop.dur ? Math.min(1, (performance.now() - hop.t0) / hop.dur) : 1, e = p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
        G.position.lerpVectors(hop.from, hop.to(), e); G.position.y += Math.sin(p * Math.PI) * hop.h;
        if (p >= 1) { const d = hop.done; anchor = hop.to; hop = null; d && d(); }
      } else if (anchor) G.position.copy(anchor());
      // face the camera; idle breathing, jump for joy, shake when sad
      G.rotation.y = W3.yaw();
      let y = RM ? 0 : .05 * Math.abs(Math.sin(t * 2.2)), rz = 0, sc = 1;
      if (hop && !RM) y = .12 * Math.abs(Math.sin(t * 16));
      if (react) { const p = (performance.now() - react.t0) / 1300;
        if (p >= 1) { react = null; mood('calm'); }
        else if (!RM && react.kind === 'jump') { const q = (p * 2) % 1; y = Math.sin(q * Math.PI) * .9; sc = 1 + .05 * Math.sin(q * Math.PI); }
        else if (!RM) rz = .25 * Math.sin(p * Math.PI * 8) * (1 - p); }
      body.position.y = y; body.rotation.z = rz; body.scale.set(sc, 2 - sc, sc);
    },
    react(kind, m) { react = {kind, t0: performance.now()}; mood(m); },
    say(text) {
      const live = $('guide-live'); if (live) live.textContent = text;
      // before the 3D map is ready (or without it) the bubble sits in the corner of the stage
      let b = W3 ? W3.bubble : $('bubble0');
      if (!b) { b = el('div', 'gbubble corner off'); b.id = 'bubble0'; $('stage').append(b); }
      clearTimeout(hideT); b.replaceChildren(...wrap(text, 13).slice(0, 5).map(l => el('div', '', l)));
      b.classList.remove('off'); hideT = setTimeout(() => b.classList.add('off'), Math.max(3500, text.length * 140));
    },
    poke() { api.react('jump', 'happy'); api.say(B.tips[tip++ % B.tips.length]); },
    on: () => standOn,
  };
  return api;
})();
const praise = ['答對了!又點亮一棟!', '太厲害了!', '正確!這棟亮起來了!', '靶材達人就是你!'];
const cheer = ['差一點!看完解說再挑戰一次~', '沒關係,建築可以重新挑戰!', '再想想看,答案就在解說裡。'];
const pickOne = a => a[Math.floor(Math.random() * a.length)];
function guideTo(id) {
  const i = id && ISL[id];
  if (!i) { guide.goTo('plaza', 1.8, 1.1, () => guide.say('這裡是靶材廣場。點一座島,我帶你去探險!')); return; }
  const left = i.questions.length - lit(i);
  guide.goTo(i.id, -1.3, 2.0, () => guide.say(
    i.id === 'play' ? '翻牌配對元素符號和中文名,記住位置喔!' :
    conquered(i) ? `${i.name}已經征服了 🚩 去下一座島吧!` : `${i.name}到了!還有 ${left} 棟建築等你點亮。`));
}
function go(id) {
  focus = id && ISL[id] ? id : null;
  const url = new URL(location.href); focus ? url.searchParams.set('island', focus) : url.searchParams.delete('island');
  try { history.replaceState(null, '', url); } catch (e) {}
  if (W3) W3.focusOn(focus);
  render(); panel(); guideTo(focus);
}
function hud() {
  const box = $('hud2'); box.replaceChildren();
  const total = B.islands.reduce((n, i) => n + i.questions.length, 0), litAll = B.islands.reduce((n, i) => n + lit(i), 0);
  const c1 = el('span', 'chip2', '征服島嶼 '); c1.append(el('b', '', B.islands.filter(conquered).length + ' / ' + B.islands.length));
  const c2 = el('span', 'chip2', '點亮建築 '); c2.append(el('b', '', litAll + ' / ' + total));
  const c3 = el('span', 'chip2', found() ? '✨ 已找到今天的金色靶材' : '🔍 某座島上藏著金色靶材');
  box.append(c1, c2, c3);
}
function toast(msg) { const t = el('div', 'toast', msg); t.setAttribute('role', 'status'); document.body.append(t); setTimeout(() => t.remove(), 4200); }

// ---------- side panel ----------
function panel(qid) {
  const side = $('side'); side.replaceChildren(); hud();
  const i = focus && ISL[focus];
  side.style.setProperty('--c', i ? i.color : 'var(--accent)');
  if (!i) {
    side.append(el('h3', '', '歡迎來到靶材產業島'), el('p', '', '六座島各是一個主題,島上每棟建築是一道題。點選島嶼,嚮導靶靶會帶你過去(拖曳地圖可以轉動視角);答對點亮建築,整座島都點亮就算征服。虛線是島與島之間的關聯,某座島上還藏著每天換位置的金色靶材。點靶靶可以聽小提示。'));
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
      if (ok && !solved.has(q.id)) { solved.add(q.id); save(); render(); hud(); }
      if (ok && conquered(i)) { toast('🚩 征服「' + i.name + '」!'); guide.react('jump', 'happy'); guide.say('征服' + i.name + '!🚩 太強了!'); }
      else if (ok) { guide.react('jump', 'happy'); guide.say(pickOne(praise)); }
      else { guide.react('sad', 'sad'); guide.say(pickOne(cheer)); }
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
        if (!--left) { const first = !matchDone; matchDone = true; save(); render(); hud(); toast((first ? '🚩 征服「配對遊樂島」!' : '完成!') + moves + ' 步');
          guide.react('jump', 'happy'); guide.say('全部配對完成,' + moves + ' 步!記憶力真好!'); }
      } else { [k1, k2].forEach(k => k.classList.add('miss'));
        setTimeout(() => { [k1, k2].forEach(k => { k.classList.remove('flip', 'miss'); k.setAttribute('aria-label', '翻開卡片'); }); open = []; }, 900); }
    };
    board.append(card);
  }
  box.append(info, board, btn('g-btn sm', '重新洗牌', () => panel()));
  return box;
}

$('reset').onclick = () => { if (confirm('清除所有進度,重新探索?')) { solved = new Set(); treasureFound = ''; matchDone = false; save(); render(); panel(); guide.say('進度清空了,重新出發吧!'); } };
$('home').onclick = () => go(null);
boot();
const start = new URLSearchParams(location.search).get('island');
if (start && ISL[start]) go(start);
else { panel(); setTimeout(() => guide.say('嗨!我是嚮導靶靶。點一座島,我帶你去!'), 400); }
})();
"""


def render_quiz(config: Config, items: list[dict], generated: str, seed: str = "") -> str:
    from .site import _brand, _fab, _head, esc, icon

    bank = build_bank(config, items, seed)
    payload = json.dumps(bank, ensure_ascii=False).replace("</", "<\\/")
    buildings = sum(len(i["questions"]) for i in bank["islands"])
    script = GAME_SCRIPT.replace("__PAIRS__", str(PAIRS))
    return (
        _head("靶材產業島", "跟著嚮導靶靶探索六座靶材產業島:每座島是一個主題、每棟建築是一道題,答對點亮建築、征服島嶼。題目取自本站新聞、商情預測與財報素材。",
              config.settings.site_url, "quiz.html")
        + f"""<style>{GAME_STYLE}</style><div class="wrap" style="max-width:1180px">
<header>{_brand("Sputtering Target · Knowledge Islands", "靶材產業島")}
<div class="meta"><span>{icon("calendar")}題庫更新於 <span class="mono">{esc(generated)}</span></span></div>
<p class="sub">{len(bank["islands"])} 座島、{buildings} 棟建築(題目)。點選島嶼,嚮導靶靶會帶你過去;答對點亮建築,新聞港每天隨新聞換題。進度只存在你的瀏覽器。</p></header>
<section class="isle" aria-label="靶材產業島"><div class="stage" id="stage"><div class="hud2" id="hud2"></div>
<div class="tools"><button type="button" class="g-btn sm" id="home">{icon("globe")}全島</button><button type="button" class="g-btn sm" id="reset">重來</button></div></div>
<aside class="side" id="side" aria-live="polite"></aside><p id="guide-live" class="sr" aria-live="polite"></p></section>
<footer>題目與解說由網站素材整理;市場數字出自富士キメラ総研報告摘要,以原報告為準。公司 logo 取自財務儀表板。</footer>
</div><script type="application/json" id="bank">{payload}</script><script>{script}</script>{_fab("quiz", config)}</body></html>"""
    )

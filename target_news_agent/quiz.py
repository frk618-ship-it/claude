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
.treasure{cursor:pointer}.treasure .glint{animation:glint 1.2s ease-in-out infinite alternate}@keyframes glint{from{opacity:.25}to{opacity:1}}
.guide{cursor:pointer}.guide .gb{transform-box:fill-box;transform-origin:50% 100%;animation:bob 1.6s ease-in-out infinite alternate}
@keyframes bob{to{transform:translateY(-2.5px)}}
.guide.walk .gb{animation:bob .18s ease-in-out infinite alternate}
.guide.jump .gb{animation:jump .55s ease-out 2}@keyframes jump{0%,100%{transform:translateY(0)}45%{transform:translateY(-16px) scale(1.04,.98)}}
.guide.sad .gb{animation:shake .5s ease-in-out 2}@keyframes shake{25%{transform:translateX(-3px) rotate(-5deg)}75%{transform:translateX(3px) rotate(5deg)}}
.bubble rect{fill:var(--surface);stroke:var(--accent);stroke-width:1.5}.bubble polygon{fill:var(--surface);stroke:var(--accent);stroke-width:1.5}
.bubble text{font:600 11.5px var(--font-body);fill:var(--fg)}.bubble{transition:opacity .25s;pointer-events:none}.bubble.off{opacity:0}
.plaza{cursor:pointer}.stage>svg{cursor:grab}.stage>svg:active{cursor:grabbing}
.plasma{animation:pulse 1.4s ease-in-out infinite alternate}@keyframes pulse{from{opacity:.35}to{opacity:.95}}
.beam{animation:pulse 1.8s ease-in-out infinite alternate}.glow{animation:pulse 1.6s ease-in-out infinite alternate}
.smoke{animation:smoke 2.4s ease-out infinite}@keyframes smoke{from{opacity:.9;transform:translateY(0)}to{opacity:0;transform:translateY(-10px)}}
.jet{animation:pulse 1s ease-in-out infinite alternate}.wave{transform-box:fill-box;transform-origin:0 50%;animation:wave 1.2s ease-in-out infinite alternate}@keyframes wave{to{transform:scaleX(.8) skewY(6deg)}}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
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
@media (prefers-reduced-motion:reduce){.mc .in{transition:none}.guide .gb,.guide.walk .gb,.guide.jump .gb,.guide.sad .gb,.treasure .glint,.plasma,.beam,.glow,.smoke,.jet,.wave{animation:none}}
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

// ---------- world: target-shaped islands floating on the sea ----------
// World coordinates lie in the sea plane; a gentle yaw turns the whole archipelago and each island
// sways on its own axis. Projection flattens y by SQ; islands bob up and down.
const S = 150, SQ = .5, RX = 50, DEPTH = 12, PLATE = 5, D2R = Math.PI / 180;
const world = i => [S * (i.hex[0] + i.hex[1] / 2), S * i.hex[1] * Math.sqrt(3) / 2];
const PLAZA = {id: 'plaza', plaza: true, name: '靶材廣場', hex: [0, 0]};
const ALL = B.islands.concat(PLAZA);
const PHASE = Object.fromEntries(ALL.map((i, k) => [i.id, k * 1.7]));
const RING = Math.max(...B.islands.map(i => Math.hypot(...world(i))));
const FULL = [-(RING + RX + 60), -(RING * SQ + RX * SQ + 110), 2 * (RING + RX + 60), 2 * (RING * SQ + RX * SQ) + 190];
const shade = (hex, f) => { const n = parseInt(hex.slice(1), 16), c = [n >> 16, n >> 8 & 255, n & 255].map(v => Math.min(255, Math.round(v * f))); return 'rgb(' + c.join(',') + ')'; };
const proj = (px, py, a) => [px * Math.cos(a) - py * Math.sin(a), (px * Math.sin(a) + py * Math.cos(a)) * SQ];  // rotate in plane, flatten
const poly = (ptsArr, a, dy) => ptsArr.map(([px, py]) => { const [x, y] = proj(px, py, a); return x.toFixed(1) + ',' + (y + (dy || 0)).toFixed(1); }).join(' ');
const rectPts = (w, h) => [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
const stadium = (w, h, n) => { const r = h / 2, c = w / 2 - r, out = [];
  for (let k = 0; k <= n; k++) { const a = -Math.PI / 2 + Math.PI * k / n; out.push([c + r * Math.cos(a), r * Math.sin(a)]); }
  for (let k = 0; k <= n; k++) { const a = Math.PI / 2 + Math.PI * k / n; out.push([-c + r * Math.cos(a), r * Math.sin(a)]); }
  return out; };

// target designs: shape of the target face and the material colour
const DESIGN = {
  materials: {shape: 'disc', metal: '#c3ccd8', ring: '#8d99ae'},
  process: {shape: 'rect', metal: '#aab4c3', ring: '#6c7a89', w: 104, h: 64},
  news: {shape: 'disc', metal: '#b9c4d2', ring: '#7c8ba1'},
  market: {shape: 'disc', metal: '#f2c94c', ring: '#c99a06'},
  makers: {shape: 'disc', metal: '#e0915a', ring: '#a85a24'},
  play: {shape: 'tiles', metal: '#66d9e8', ring: '#1098ad', w: 96},
  plaza: {shape: 'disc', metal: '#efe1bf', ring: '#c9b48a'},
};

// decorations: drawn at a local origin (base on the island surface), placed by polar angle / radius
const deco = {
  cube(sym, color) { const g = sv('g', {}); g.append(sv('polygon', {points: '-7,-12 0,-16 7,-12 0,-8', fill: shade(color, 1.25)}),
    sv('polygon', {points: '-7,-12 0,-8 0,2 -7,-2', fill: color}), sv('polygon', {points: '0,-8 7,-12 7,-2 0,2', fill: shade(color, .75)}));
    const t = sv('text', {x: -3.5, y: -3, 'text-anchor': 'middle', 'font-size': 5.5, 'font-weight': 700, fill: '#fff'}); t.textContent = sym; g.append(t); return g; },
  ingot(color) { const g = sv('g', {}); g.append(sv('polygon', {points: '-11,0 11,0 8,-7 -8,-7', fill: shade(color, .8)}), sv('polygon', {points: '-8,-7 8,-7 6,-10 -6,-10', fill: shade(color, 1.15)})); return g; },
  chamber() { const g = sv('g', {}); g.append(sv('rect', {x: -13, y: -14, width: 26, height: 14, rx: 2, fill: '#6c7a89'}),
    sv('path', {d: 'M-11,-14 A11,10 0 0 1 11,-14 Z', fill: '#d0ebff', stroke: '#6c7a89', 'stroke-width': 1.5}),
    sv('ellipse', {class: 'plasma', cx: 0, cy: -18, rx: 7, ry: 4, fill: '#9775fa'}), sv('rect', {x: -9, y: -6, width: 18, height: 3, fill: '#ffd43b'})); return g; },
  magnet() { const g = sv('g', {}); g.append(sv('rect', {x: -9, y: -10, width: 8, height: 10, fill: '#e03131'}), sv('rect', {x: 1, y: -10, width: 8, height: 10, fill: '#3b5bdb'}));
    const n = sv('text', {x: -5, y: -3, 'text-anchor': 'middle', 'font-size': 6, 'font-weight': 700, fill: '#fff'}); n.textContent = 'N';
    const s = sv('text', {x: 5, y: -3, 'text-anchor': 'middle', 'font-size': 6, 'font-weight': 700, fill: '#fff'}); s.textContent = 'S'; g.append(n, s); return g; },
  tube() { const g = sv('g', {}); g.append(sv('rect', {x: -6, y: -40, width: 12, height: 40, fill: '#9aa5b1'}), sv('rect', {x: -6, y: -40, width: 4, height: 40, fill: '#c3ccd8'}),
    sv('ellipse', {cx: 0, cy: -40, rx: 6, ry: 3, fill: '#dde3ec'}), sv('rect', {x: -6, y: -26, width: 12, height: 3, fill: '#f76707'}),
    sv('polygon', {class: 'beam', points: '4,-42 32,-50 32,-34', fill: '#ffe066'})); return g; },
  boat() { const g = sv('g', {}); g.append(sv('path', {d: 'M-12,-4 L12,-4 L8,2 L-8,2 Z', fill: '#f08c00'}), sv('line', {x1: 0, y1: -4, x2: 0, y2: -22, stroke: '#495057', 'stroke-width': 1.3}),
    sv('polygon', {points: '1,-21 12,-7 1,-7', fill: '#fff'})); return g; },
  bars() { const g = sv('g', {}); [[-10, 8, '#d6336c'], [-3, 14, '#f76707'], [4, 21, '#2f9e44']].forEach(([x, h, c]) => g.append(sv('rect', {x, y: -h, width: 6, height: h, fill: c})));
    g.append(sv('polyline', {points: '-10,-12 -2,-18 6,-26 11,-30', fill: 'none', stroke: '#162033', 'stroke-width': 1.4})); return g; },
  coins() { const g = sv('g', {}); for (let k = 0; k < 4; k++) g.append(sv('ellipse', {cx: 0, cy: -k * 3, rx: 7, ry: 3, fill: k % 2 ? '#f2b705' : '#ffd43b', stroke: '#b7791f', 'stroke-width': .8})); return g; },
  factory() { const g = sv('g', {}); g.append(sv('rect', {x: -12, y: -12, width: 24, height: 12, fill: '#e9ecef'}), sv('polygon', {points: '-12,-12 -6,-18 -6,-12 0,-18 0,-12 6,-18 6,-12', fill: '#868e96'}),
    sv('rect', {x: 7, y: -24, width: 4, height: 12, fill: '#868e96'}), sv('circle', {class: 'smoke', cx: 9, cy: -28, r: 3, fill: '#dee2e6'})); return g; },
  pennant(label, color) { const g = sv('g', {}); g.append(sv('line', {x1: 0, y1: 0, x2: 0, y2: -22, stroke: '#495057', 'stroke-width': 1.2}), sv('polygon', {points: '0,-22 15,-18 0,-14', fill: color}));
    const t = sv('text', {x: 6, y: -16.5, 'text-anchor': 'middle', 'font-size': 4.5, 'font-weight': 700, fill: '#fff'}); t.textContent = label; g.append(t); return g; },
  cards() { const g = sv('g', {}); g.append(sv('rect', {x: -10, y: -16, width: 11, height: 15, rx: 1.5, fill: '#7048e8', transform: 'rotate(-12)'}),
    sv('rect', {x: -1, y: -17, width: 11, height: 15, rx: 1.5, fill: '#f76707', transform: 'rotate(10)'})); return g; },
  fountain() { const g = sv('g', {}); g.append(sv('ellipse', {cx: 0, cy: 0, rx: 16, ry: 8, fill: '#74c0fc'}), sv('ellipse', {cx: 0, cy: 0, rx: 7, ry: 3.5, fill: '#d0ebff'}),
    sv('path', {class: 'jet', d: 'M0,-2 Q-5,-14 -9,-4 M0,-2 Q5,-14 9,-4', fill: 'none', stroke: '#a5d8ff', 'stroke-width': 1.6})); return g; },
  flag(color) { const g = sv('g', {}); g.append(sv('line', {x1: 0, y1: 0, x2: 0, y2: -34, stroke: '#162033', 'stroke-width': 1.6}), sv('polygon', {class: 'wave', points: '0,-34 18,-29 0,-24', fill: '#ffd43b'})); return g; },
};
const DECOR = {
  materials: () => [[30, .55, deco.cube('Cu', '#d9773a')], [100, .6, deco.cube('Ta', '#5c6bc0')], [170, .55, deco.cube('Ti', '#6c7a89')], [250, .5, deco.ingot('#c3ccd8')]],
  process: () => [[200, .35, deco.chamber()], [20, .5, deco.magnet()]],
  news: () => [[120, .4, deco.tube()], [330, .65, deco.boat()]],
  market: () => [[200, .4, deco.bars()], [40, .5, deco.coins()]],
  makers: () => [[150, .45, deco.factory()], [20, .55, deco.pennant('TW', '#0c8f6a')], [70, .6, deco.pennant('JP', '#c92a2a')],
    [250, .6, deco.pennant('US', '#364fc7')], [300, .55, deco.pennant('CN', '#e03131')]],
  play: () => [[200, .35, deco.cards()]],
  plaza: () => [[0, 0, deco.fountain()]],
};

let focus = null, view = FULL.slice(), svg, gSea, gLinks, gIslands, gTop, guide, nodes = {}, linkEls = [], treasureEl = null;
let dragYaw = 0, dragging = null, dragMoved = false, now = 0;
const yaw = () => (RM ? 0 : 5 * Math.sin(now / 7000 * 2 * Math.PI)) * D2R + dragYaw * D2R;
const spin = i => yaw() + (RM ? 0 : 9 * Math.sin(now / 4300 * 2 * Math.PI + PHASE[i.id]) * D2R);
const bob = i => RM ? 0 : 4 * Math.sin(now / 1900 * 2 * Math.PI + PHASE[i.id]);
function pos(i) { const [wx, wy] = world(i), [x, y] = proj(wx, wy, yaw()); return [x, y - bob(i)]; }
const center = i => pos(i);

function drawMap() {
  svg = sv('svg', {viewBox: view.join(' '), role: 'application', 'aria-label': '靶材產業島地圖:點選島嶼查看題目,拖曳可以轉動地圖'});
  const sea = sv('rect', {x: -3000, y: -3000, width: 6000, height: 6000, fill: 'transparent'}); sea.onclick = () => { if (!dragMoved) go(null); };
  gSea = sv('g', {}); gLinks = sv('g', {}); gIslands = sv('g', {}); gTop = sv('g', {});
  svg.append(sea, gSea, gLinks, gIslands, gTop);
  guide = makeGuide(); svg.append(guide.g);
  // drag to turn the archipelago a little
  svg.addEventListener('pointerdown', e => { dragging = {x: e.clientX, yaw: dragYaw}; dragMoved = false; });
  svg.addEventListener('pointermove', e => { if (!dragging) return; const dx = e.clientX - dragging.x;
    if (Math.abs(dx) > 4) { dragMoved = true; svg.setPointerCapture && svg.setPointerCapture(e.pointerId); }
    dragYaw = Math.max(-30, Math.min(30, dragging.yaw + dx * .25)); if (RM) frame(0); });
  const end = () => { dragging = null; setTimeout(() => dragMoved = false, 0); };
  svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
  $('stage').prepend(svg); render();
  const loop = t => { now = t; if (!dragging && !RM) dragYaw *= .985; frame(); requestAnimationFrame(loop); };
  RM ? frame() : requestAnimationFrame(loop);
}

// Target body and face, built once per island; update(a) turns rectangles and tile targets (discs look the same at any angle).
function buildShape(d, i, done) {
  const top = done || i.plaza ? d.metal : shade(d.metal, .9), side = shade(d.metal, .62), body = [], face = [];
  if (d.shape === 'disc') {
    body.push(sv('ellipse', {cx: 0, cy: DEPTH, rx: RX, ry: RX * SQ, fill: side}), sv('rect', {x: -RX, y: 0, width: 2 * RX, height: DEPTH, fill: side}),
      sv('ellipse', {cx: 0, cy: 0, rx: RX, ry: RX * SQ, fill: top}));
    if (!i.plaza) face.push(sv('ellipse', {cx: 0, cy: 0, rx: RX * .66, ry: RX * .66 * SQ, fill: 'none', stroke: d.ring, 'stroke-width': 7, opacity: .55}),
      sv('ellipse', {cx: 0, cy: 0, rx: RX * .2, ry: RX * .2 * SQ, fill: shade(d.metal, 1.12)}));
    return {body, face, update() {}};
  }
  if (d.shape === 'rect') {
    const r = rectPts(d.w, d.h), race = stadium(d.w * .72, d.h * .5, 8);
    const s1 = sv('polygon', {fill: side}), s2 = sv('polygon', {fill: top}), s3 = sv('polygon', {fill: 'none', stroke: d.ring, 'stroke-width': 6, opacity: .55});
    body.push(s1, s2); face.push(s3);
    return {body, face, update(a) { s1.setAttribute('points', poly(r, a, DEPTH)); s2.setAttribute('points', poly(r, a)); s3.setAttribute('points', poly(race, a)); }};
  }
  const w = d.w, t3 = w / 3, sq = rectPts(w, w), s1 = sv('polygon', {fill: side}), s2 = sv('polygon', {fill: shade(d.ring, .9)}), tiles = [];
  body.push(s1, s2);
  for (let r0 = 0; r0 < 3; r0++) for (let c0 = 0; c0 < 3; c0++) {
    const cx0 = -w / 2 + t3 * (c0 + .5), cy0 = -w / 2 + t3 * (r0 + .5), h = t3 / 2 - 1.5;
    const el = sv('polygon', {fill: (r0 + c0) % 2 ? top : shade(d.metal, 1.08)}); face.push(el);
    tiles.push([el, [[cx0 - h, cy0 - h], [cx0 + h, cy0 - h], [cx0 + h, cy0 + h], [cx0 - h, cy0 + h]]]);
  }
  return {body, face, update(a) { s1.setAttribute('points', poly(sq, a, DEPTH)); s2.setAttribute('points', poly(sq, a)); tiles.forEach(([el, p]) => el.setAttribute('points', poly(p, a))); }};
}

// Build the island DOM (called when progress changes); frame() moves it every animation frame.
function render() {
  gSea.replaceChildren(); gLinks.replaceChildren(); gIslands.replaceChildren(); gTop.replaceChildren(); nodes = {}; linkEls = []; treasureEl = null;
  for (const [a, b, label] of B.links) {
    const on = focus && (focus === a || focus === b), path = sv('path', {class: 'link' + (on ? ' on' : '')});
    gLinks.append(path); let text = null;
    if (on) { text = sv('text', {class: 'link-label', 'text-anchor': 'middle'}); text.textContent = label; gTop.append(text); }
    linkEls.push({a, b, path, text});
  }
  for (const i of ALL) {
    const d = DESIGN[i.id] || DESIGN.materials, plaza = !!i.plaza, done = !plaza && conquered(i);
    const n = plaza ? 0 : i.id === 'play' ? 6 : i.questions.length, k = plaza ? 0 : lit(i);
    const shadow = sv('ellipse', {fill: '#0b3a5b', opacity: .14}); gSea.append(shadow);
    const g = sv('g', {class: plaza ? 'plaza' : 'is-land', tabindex: 0, role: 'button',
      'aria-label': plaza ? '靶材廣場:回到全島地圖' : i.name + (i.id === 'play' ? '' : `,已點亮 ${k} / ${n}`)});
    // backing plate (copper) under the target, then the target body and face
    const plate = sv('g', {}); plate.append(sv('ellipse', {cx: 0, cy: DEPTH + PLATE, rx: RX + 7, ry: (RX + 7) * SQ, fill: '#8f4f22'}),
      sv('rect', {x: -RX - 7, y: DEPTH, width: 2 * RX + 14, height: PLATE, fill: '#8f4f22'}),
      sv('ellipse', {cx: 0, cy: DEPTH, rx: RX + 7, ry: (RX + 7) * SQ, fill: '#c0773f'}));
    if (plaza) plate.setAttribute('opacity', 0);
    const shape = buildShape(d, i, done), body = sv('g', {}), face = sv('g', {class: 'top'}), faceBase = sv('g', {}), parts = sv('g', {});
    body.append(...shape.body); faceBase.append(...shape.face); face.append(faceBase, parts);
    g.append(plate, body, face);
    const studs = [], decos = [];
    if (!plaza) for (let j = 0; j < n; j++) {
      const on = i.id === 'play' ? (matchDone || j < 0) : solved.has(i.questions[j].id), s = sv('g', {});
      s.append(sv('rect', {x: -2.6, y: -6, width: 5.2, height: 6, fill: on ? '#f08c00' : '#868e96'}),
        sv('ellipse', {cx: 0, cy: -6, rx: 2.6, ry: 1.3, fill: on ? '#ffe066' : '#ced4da'}));
      if (on) s.append(sv('circle', {class: 'glow', cx: 0, cy: -8, r: 4.5, fill: '#ffe066', opacity: .35}));
      studs.push({el: s, a: j / n * 2 * Math.PI}); parts.append(s);
    }
    for (const [deg, r, el] of (DECOR[i.id] || (() => []))()) decos.push({el, a: deg * D2R, r});
    if (done) decos.push({el: deco.flag(), a: 0, r: 0});
    decos.forEach(x => parts.append(x.el));
    g.onclick = e => { e.stopPropagation(); if (!dragMoved) go(plaza ? null : i.id); };
    g.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(plaza ? null : i.id); } };
    gIslands.append(g);
    const nm = sv('text', {class: 'is-name', 'text-anchor': 'middle'}); nm.textContent = i.name;
    const sub = sv('text', {class: 'is-sub', 'text-anchor': 'middle'});
    sub.textContent = plaza ? '' : i.id === 'play' ? (matchDone ? '已完成' : '小遊戲') : `${k} / ${n}`;
    gTop.append(nm, sub);
    nodes[i.id] = {i, d, g, shape, parts, shadow, studs, decos, nm, sub, order: ''};
  }
  if (B.treasure && !found() && ISL[B.treasure]) {
    const t = sv('g', {class: 'treasure', role: 'button', tabindex: 0, 'aria-label': '閃閃發亮的東西'});
    t.append(sv('ellipse', {cx: 0, cy: 3, rx: 8, ry: 3, fill: '#000', opacity: .15}), sv('circle', {cx: 0, cy: -4, r: 7, fill: '#f2b705', stroke: '#b7791f', 'stroke-width': 1.5}),
      sv('circle', {cx: 0, cy: -4, r: 3.5, fill: 'none', stroke: '#b7791f', 'stroke-width': 1.2}), sv('path', {class: 'glint', d: 'M8,-14 l2,4 4,2 -4,2 -2,4 -2,-4 -4,-2 4,-2z', fill: '#fff'}));
    t.onclick = e => { e.stopPropagation(); treasureFound = B.treasure; save(); render(); hud();
      guide.goTo(B.treasure, 18, 14, () => { guide.react('jump', 'happy'); guide.say('哇!找到今天的金色靶材了!明天它會躲到別座島喔。'); });
      toast('✨ 找到金色靶材!'); };
    gTop.append(t); treasureEl = t;
  }
  frame();
}
function frame() {
  const order = ALL.map(i => [i.id, pos(i)[1]]).sort((p, q) => p[1] - q[1]).map(x => x[0]);
  if (gIslands.dataset.order !== order.join()) { order.forEach(id => nodes[id] && gIslands.append(nodes[id].g)); gIslands.dataset.order = order.join(); }
  for (const id in nodes) {
    const nd = nodes[id], i = nd.i, d = nd.d, [x, y] = pos(i), a = spin(i), bb = bob(i), [wx, wy] = world(i), [, ys] = proj(wx, wy, yaw());
    nd.g.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)})`);
    const sh = 1 - bb / 30; nd.shadow.setAttribute('cx', x.toFixed(1)); nd.shadow.setAttribute('cy', (ys + DEPTH + PLATE + 18).toFixed(1));
    nd.shadow.setAttribute('rx', ((RX + 10) * sh).toFixed(1)); nd.shadow.setAttribute('ry', ((RX + 10) * SQ * .55 * sh).toFixed(1));
    nd.shape.update(a);
    // studs (one per question) around the rim, and decorations, turning with the island
    const items = [];
    const rimX = d.shape === 'disc' ? RX * .86 : d.shape === 'rect' ? d.w * .42 : d.w * .42, rimY = d.shape === 'disc' ? RX * .86 : d.shape === 'rect' ? d.h * .36 : d.w * .42;
    nd.studs.forEach(s => { const [sx, sy] = proj(rimX * Math.cos(s.a), rimY * Math.sin(s.a), a); items.push([s.el, sx, sy]); });
    nd.decos.forEach(o => { const rr = o.r * (d.shape === 'disc' ? RX : d.w / 2), [dx, dy] = proj(rr * Math.cos(o.a), rr * Math.sin(o.a), a); items.push([o.el, dx, dy]); });
    items.forEach(([el, ex, ey]) => el.setAttribute('transform', `translate(${ex.toFixed(1)},${ey.toFixed(1)})`));
    const ord = items.map((it, k) => [k, it[2]]).sort((p, q) => p[1] - q[1]).map(x => x[0]).join();  // back-to-front, re-append only on change
    if (ord !== nd.order) { ord.split(',').forEach(k => nd.parts.append(items[+k][0])); nd.order = ord; }
    const lab = y + RX * SQ + DEPTH + PLATE + 16;
    nd.nm.setAttribute('x', x.toFixed(1)); nd.nm.setAttribute('y', lab.toFixed(1)); nd.sub.setAttribute('x', x.toFixed(1)); nd.sub.setAttribute('y', (lab + 13).toFixed(1));
  }
  for (const L of linkEls) {
    const [x1, y1] = pos(ISL[L.a]), [x2, y2] = pos(ISL[L.b]), mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - 20;
    L.path.setAttribute('d', `M${x1.toFixed(1)},${y1.toFixed(1)} Q${mx.toFixed(1)},${my.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`);
    if (L.text) { const f = focus === L.a ? [x1, y1, x2, y2] : [x2, y2, x1, y1];
      L.text.setAttribute('x', (f[0] + (f[2] - f[0]) * .62).toFixed(1)); L.text.setAttribute('y', (f[1] + (f[3] - f[1]) * .62 - 24).toFixed(1)); }
  }
  if (treasureEl) { const [x, y] = pos(ISL[B.treasure]), a = spin(ISL[B.treasure]), [dx, dy] = proj(-RX * .45, RX * .3, a);
    treasureEl.setAttribute('transform', `translate(${(x + dx).toFixed(1)},${(y + dy).toFixed(1)})`); }
  guide && guide.tick();
}

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

// ---------- 靶靶: the guide character ----------
function makeGuide() {
  const g = sv('g', {class: 'guide', role: 'button', tabindex: 0, 'aria-label': '嚮導靶靶:點我看小提示'});
  const body = sv('g', {class: 'gb'});
  const mouth = sv('path', {d: 'M-3.5,-30 Q0,-27 3.5,-30', fill: 'none', stroke: '#162033', 'stroke-width': 1.4, 'stroke-linecap': 'round'});
  const blush = sv('g', {}); blush.append(sv('circle', {cx: -6.5, cy: -31.5, r: 1.8, fill: '#ff8787', opacity: .8}), sv('circle', {cx: 6.5, cy: -31.5, r: 1.8, fill: '#ff8787', opacity: .8}));
  const sweat = sv('path', {d: 'M9,-42 q2,3 0,5 q-2,-2 0,-5z', fill: '#74c0fc', opacity: 0});
  body.append(
    sv('rect', {x: -6, y: -9, width: 4.5, height: 9, rx: 2, fill: '#1e2a4a'}), sv('rect', {x: 1.5, y: -9, width: 4.5, height: 9, rx: 2, fill: '#1e2a4a'}),
    sv('rect', {x: -8.5, y: -23, width: 17, height: 16, rx: 5, fill: '#3b5bdb'}), sv('rect', {x: -8.5, y: -13, width: 17, height: 2.5, fill: '#f76707'}),
    sv('line', {x1: 8, y1: -20, x2: 13, y2: -27, stroke: '#3b5bdb', 'stroke-width': 3, 'stroke-linecap': 'round'}),
    sv('line', {x1: 13, y1: -26, x2: 13, y2: -44, stroke: '#162033', 'stroke-width': 1.3}), sv('polygon', {points: '13,-44 23,-41 13,-38', fill: '#f76707'}),
    sv('line', {x1: -8, y1: -20, x2: -12, y2: -13, stroke: '#3b5bdb', 'stroke-width': 3, 'stroke-linecap': 'round'}),
    sv('circle', {cx: 0, cy: -34, r: 11.5, fill: '#dde3ec', stroke: '#8a93a8', 'stroke-width': 1.5}),
    sv('circle', {cx: 0, cy: -34, r: 8, fill: 'none', stroke: '#b5bdcb', 'stroke-width': 1}),
    sv('circle', {cx: -3.6, cy: -35.5, r: 1.7, fill: '#162033'}), sv('circle', {cx: 3.6, cy: -35.5, r: 1.7, fill: '#162033'}),
    mouth, blush, sweat);
  const bubble = sv('g', {class: 'bubble off'});
  g.append(sv('ellipse', {cx: 0, cy: 1, rx: 10, ry: 3, fill: '#000', opacity: .18}), body, bubble);
  let x = 0, y = 0, tip = 0, hideT = null, anchor = null, walking = false;
  const place = () => g.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)})`);
  const mood = m => {
    mouth.setAttribute('d', m === 'sad' ? 'M-3.5,-28.5 Q0,-31.5 3.5,-28.5' : m === 'happy' ? 'M-4,-30.5 Q0,-25.5 4,-30.5' : 'M-3.5,-30 Q0,-27 3.5,-30');
    blush.setAttribute('opacity', m === 'sad' ? 0 : 1); sweat.setAttribute('opacity', m === 'sad' ? 1 : 0);
  };
  const api = {
    g,
    // hop to a spot on an island (offset in the island's own plane), then ride along as it bobs and turns
    goTo(id, ox, oy, done) {
      const isl = id === 'plaza' ? PLAZA : ISL[id], spot = () => { const [cx, cy] = pos(isl), [dx, dy] = proj(ox, oy, spin(isl)); return [cx + dx, cy + dy]; };
      const fx = x, fy = y, [tx0, ty0] = spot(), d = Math.hypot(tx0 - fx, ty0 - fy), dur = RM || d < 1 ? 0 : Math.min(900, 300 + d * 2.2), t0 = performance.now();
      anchor = null; walking = true; g.classList.add('walk');
      const step = t => { const p = dur ? Math.min(1, (t - t0) / dur) : 1, e = p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2, [tx, ty] = spot();
        x = fx + (tx - fx) * e; y = fy + (ty - fy) * e - Math.sin(p * Math.PI) * Math.min(40, d * .25); place();
        if (p < 1) requestAnimationFrame(step); else { walking = false; anchor = spot; g.classList.remove('walk'); done && done(); } };
      requestAnimationFrame(step);
    },
    tick() { if (anchor && !walking) { [x, y] = anchor(); place(); } },
    react(kind, m) { g.classList.remove('jump', 'sad'); void g.getBBox(); g.classList.add(kind); mood(m);
      setTimeout(() => { g.classList.remove(kind); mood('calm'); }, 1300); },
    say(text) {
      bubble.replaceChildren(); clearTimeout(hideT);
      const lines = wrap(text, 13).slice(0, 5);
      const w = Math.max(...lines.map(l => [...l].reduce((n, ch) => n + (ch.charCodeAt(0) > 0x2e80 ? 12 : 6.6), 0))) + 18, h = lines.length * 15 + 12;
      const bx = -w / 2, by = -58 - h;
      bubble.append(sv('rect', {x: bx, y: by, width: w, height: h, rx: 6}), sv('polygon', {points: `-5,${by + h - 1} 5,${by + h - 1} 0,${by + h + 7}`}));
      lines.forEach((l, k) => { const t = sv('text', {x: bx + 9, y: by + 19 + k * 15}); t.textContent = l; bubble.append(t); });
      bubble.classList.remove('off'); hideT = setTimeout(() => bubble.classList.add('off'), Math.max(3500, text.length * 140));
      const live = $('guide-live'); if (live) live.textContent = text;
    },
    at() { return [x, y]; },
  };
  g.onclick = e => { e.stopPropagation(); api.react('jump', 'happy'); api.say(B.tips[tip++ % B.tips.length]); };
  g.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); g.onclick(e); } };
  const [px, py] = pos(PLAZA); x = px + 26; y = py + 8; place();
  anchor = () => { const [cx, cy] = pos(PLAZA); return [cx + 26, cy + 8]; };
  return api;
}
const praise = ['答對了!又點亮一棟!', '太厲害了!', '正確!這棟亮起來了!', '靶材達人就是你!'];
const cheer = ['差一點!看完解說再挑戰一次~', '沒關係,建築可以重新挑戰!', '再想想看,答案就在解說裡。'];
const pickOne = a => a[Math.floor(Math.random() * a.length)];
function guideTo(id) {
  const i = id && ISL[id];
  if (!i) { guide.goTo('plaza', 26, 16, () => guide.say('這裡是靶材廣場。點一座島,我帶你去探險!')); return; }
  const left = i.questions.length - lit(i);
  guide.goTo(i.id, -20, 34, () => guide.say(
    i.id === 'play' ? '翻牌配對元素符號和中文名,記住位置喔!' :
    conquered(i) ? `${i.name}已經征服了 🚩 去下一座島吧!` : `${i.name}到了!還有 ${left} 棟建築等你點亮。`));
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
  if (focus) { const [cx, cy] = pos(ISL[focus]), w = FULL[2] * .7, h = w * FULL[3] / FULL[2]; camera([cx - w / 2, cy - h / 2 - 10, w, h]); }
  else camera(FULL);
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
    side.append(el('h3', '', '歡迎來到靶材產業島'), el('p', '', '六座島各是一個主題,島上每棟建築是一道題。點選島嶼,嚮導靶靶會帶你過去;答對點亮建築,整座島都點亮就算征服。虛線是島與島之間的關聯,某座島上還藏著每天換位置的金色靶材。點靶靶可以聽小提示。'));
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
drawMap();
const start = new URLSearchParams(location.search).get('island');
if (start && ISL[start]) go(start);
else { panel(); setTimeout(() => guide.say('嗨!我是嚮導靶靶。點一座島,我帶你去!'), 400); }
})();
"""


def render_quiz(config: Config, items: list[dict], generated: str, seed: str = "") -> str:
    from .site import _brand, _head, esc, icon

    bank = build_bank(config, items, seed)
    payload = json.dumps(bank, ensure_ascii=False).replace("</", "<\\/")
    buildings = sum(len(i["questions"]) for i in bank["islands"])
    script = GAME_SCRIPT.replace("__PAIRS__", str(PAIRS))
    return (
        _head("靶材產業島", "跟著嚮導靶靶探索六座靶材產業島:每座島是一個主題、每棟建築是一道題,答對點亮建築、征服島嶼。題目取自本站新聞、商情預測與財報素材。",
              config.settings.site_url, "quiz.html")
        + f"""<style>{GAME_STYLE}</style><div class="wrap" style="max-width:1180px">
<header>{_brand("Sputtering Target · Knowledge Islands", "靶材產業島")}
<nav class="meta" aria-label="網站導覽"><a class="btn" href="index.html">{icon("arrow-left")}新聞摘要</a><a class="btn" href="forecast.html">{icon("research")}商情預測</a><span>{icon("calendar")}題庫更新於 <span class="mono">{esc(generated)}</span></span></nav>
<p class="sub">{len(bank["islands"])} 座島、{buildings} 棟建築(題目)。點選島嶼,嚮導靶靶會帶你過去;答對點亮建築,新聞港每天隨新聞換題。進度只存在你的瀏覽器。</p></header>
<section class="isle" aria-label="靶材產業島"><div class="stage" id="stage"><div class="hud2" id="hud2"></div>
<div class="tools"><button type="button" class="g-btn sm" id="home">{icon("globe")}全島</button><button type="button" class="g-btn sm" id="reset">重來</button></div></div>
<aside class="side" id="side" aria-live="polite"></aside><p id="guide-live" class="sr" aria-live="polite"></p></section>
<footer>題目與解說由網站素材整理;市場數字出自富士キメラ総研報告摘要,以原報告為準。公司 logo 取自財務儀表板。</footer>
</div><script type="application/json" id="bank">{payload}</script><script>{script}</script></body></html>"""
    )

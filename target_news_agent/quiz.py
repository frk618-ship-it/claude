"""Question bank for the site's knowledge game (quiz.html).

Fixed questions come from config/quiz.yaml; the rest are generated from the site's own
material, so the game follows the data: listed target makers (market, target share) from
target_makers.yaml, element symbols, and the latest days' top news ("which company?").
"""

from __future__ import annotations

import base64
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


def _q(topic: str, q: str, answer: str, wrong: list[str], explain: str = "", **extra) -> dict:
    return {"topic": topic, "q": q, "answer": answer, "wrong": wrong[:3], "explain": explain, **extra}


def fixed_questions(config: Config) -> list[dict]:
    out = []
    for x in config.quiz:
        wrong = [str(w) for w in x.get("wrong") or []]
        if x.get("q") and x.get("answer") and len(wrong) >= 3:
            topic = str(x.get("topic", "materials"))
            out.append(_q(topic, str(x["q"]), str(x["answer"]), wrong, str(x.get("explain", "")),
                          visual={"kind": "icon", "icon": str(x.get("icon") or ("flask" if topic == "materials" else "market"))}))
    return out


def element_questions(rng: random.Random) -> list[dict]:
    out = []
    names = list(ELEMENTS.values())
    for sym, name in ELEMENTS.items():
        wrong = rng.sample([n for n in names if n != name], 3)
        num = ELEMENT_INFO[sym][1]
        out.append(_q("materials", f"靶材上的「{sym}」指的是哪一種材料?", name, wrong,
                      f"{sym} = {name}" + (f",原子序 {num}。" if num else "。"), visual={"kind": "element", "sym": sym}))
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
                          f"{name}在{MARKETS[market]}上市;主要業務:{segment}。", visual=visual))
        if focus in FOCUS:
            out.append(_q("market", f"以靶材占營收的比重來看,{name}屬於哪一類?", FOCUS[focus],
                          [v for k, v in FOCUS.items() if k != focus] + ["不生產靶材"],
                          f"{FOCUS_NOTE[focus]}主要業務:{segment}。", visual=visual))
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
                         it.get("summary") or "", link=it.get("link", ""), date=(it.get("published") or "")[:10],
                         visual={"kind": "icon", "icon": motif, "caption": caption, "sym": sym}))
        if len(picked) >= MAX_NEWS:
            break
    return picked


def build_bank(config: Config, items: list[dict], seed: str = "") -> dict:
    """All questions plus the element pairs for the matching game."""
    rng = random.Random(seed)
    questions = fixed_questions(config) + element_questions(rng) + maker_questions(config, rng) + news_questions(items, config, rng)
    elements = [{"sym": s, "name": n, "num": z, "color": c} for s, (n, z, c) in ELEMENT_INFO.items()]
    return {"questions": questions, "elements": elements}




TOPICS = [("all", "全部", "sparkle"), ("materials", "材料與製程", "flask"), ("market", "市場與廠商", "market"), ("news", "近期新聞", "news")]
ROUND = 10
PAIRS = 6

GAME_STYLE = """
.g-btn{font:inherit;font-size:.95rem;border:0;border-radius:6px;padding:10px 18px;cursor:pointer;background:var(--accent-soft);color:var(--accent);font-weight:600;display:inline-flex;gap:8px;align-items:center}
.g-btn:hover{background:var(--accent);color:#fff}.g-btn.primary{background:var(--accent);color:#fff}.g-btn.primary:hover{background:var(--accent-2)}
.g-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.tabs{display:flex;gap:8px;flex-wrap:wrap}.tabs button{font:inherit;font-size:.95rem;border:0;border-radius:6px;padding:10px 18px;cursor:pointer;background:var(--accent-soft);color:var(--accent);font-weight:600;display:inline-flex;gap:8px;align-items:center}
.tabs button[aria-selected=true]{background:var(--accent);color:#fff}
.panel{background:var(--surface);border-top:4px solid var(--accent);border-radius:6px;padding:20px;display:grid;gap:18px}
.game{display:grid;grid-template-columns:200px minmax(0,1fr);gap:24px;align-items:start}
.chamber{position:sticky;top:16px;display:grid;gap:8px;justify-items:center}.chamber svg{width:200px;height:auto;display:block;overflow:visible}
.chamber .cap{font-size:.78rem;color:var(--muted);text-align:center;line-height:1.5}
.ch-wall{fill:var(--bg);stroke:var(--line);stroke-width:2}.ch-target{fill:#8a93a8}.ch-magnet{fill:var(--fg);opacity:.75}.ch-wafer{fill:var(--fg);opacity:.8}
.ch-plasma{fill:var(--accent);opacity:.12;transform-box:fill-box;transform-origin:center;animation:glow 2.4s ease-in-out infinite alternate}
.ch-label{font:600 9px var(--font-data);fill:var(--muted);letter-spacing:.06em}
.chamber.hit .ch-plasma{opacity:.35}.chamber.miss .ch-target{fill:var(--up)}
@keyframes glow{from{transform:scale(.92);opacity:.1}to{transform:scale(1.06);opacity:.2}}
.hud{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;font-size:.85rem;color:var(--muted)}.hud b{font-family:var(--font-data);color:var(--fg);font-size:1rem}
.dots{display:flex;gap:5px}.dots i{width:12px;height:12px;border-radius:3px;background:var(--line)}.dots i.ok{background:var(--ok)}.dots i.no{background:var(--up)}.dots i.now{background:var(--accent)}
.streak{color:var(--accent-2);font-weight:700}
.qcard{display:grid;grid-template-columns:96px minmax(0,1fr);gap:16px;align-items:center}
.g-q{font-size:1.15rem;font-weight:700;line-height:1.6;margin:0}
.vis{width:96px;height:96px;border-radius:8px;display:grid;place-items:center;position:relative;align-content:center;gap:2px;text-align:center}
.vis.icon{background:color-mix(in srgb,var(--accent) 14%,var(--surface));color:var(--accent)}.vis.icon .ic{width:44px;height:44px;stroke-width:1.5}
.vis .vcap{font-size:.7rem;color:var(--muted)}.vis .vsym{position:absolute;right:-6px;bottom:-6px;background:var(--accent);color:#fff;border-radius:4px;font:600 .72rem var(--font-data);padding:2px 6px}
.ptile{background:color-mix(in srgb,var(--c) 22%,var(--surface));border:3px solid var(--c);color:var(--fg)}.ptile .z{position:absolute;left:7px;top:4px;font:500 .72rem var(--font-data);color:var(--muted)}
.ptile .s{font:700 2rem/1 var(--font-data)}.ptile .s.long{font-size:1.15rem}
.vis.logo{background:#fff;border:2px solid var(--line)}.vis.logo img{max-width:70px;max-height:56px;object-fit:contain}.vis.logo .mono{position:absolute;bottom:-20px;font-size:.7rem;color:var(--muted);white-space:nowrap}
.opts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.opt{font:inherit;text-align:left;border:0;background:var(--bg);color:var(--fg);border-radius:8px;padding:14px;cursor:pointer;line-height:1.5;display:grid;grid-template-columns:34px minmax(0,1fr);gap:12px;align-items:center;min-height:68px;transition:transform .12s,background .12s}
.opt .k{width:34px;height:34px;border-radius:6px;display:grid;place-items:center;background:var(--accent);color:#fff;font:700 .95rem var(--font-data)}
.opt:hover:not(:disabled){background:var(--accent-soft);transform:translateY(-2px)}.opt:disabled{cursor:default}
.opt.right{background:var(--ok-soft);color:var(--ok);font-weight:700}.opt.right .k{background:var(--ok)}
.opt.wrong{background:color-mix(in srgb,var(--up) 13%,var(--surface));color:var(--up)}.opt.wrong .k{background:var(--up)}.opt.dim{opacity:.45}
.fb{border-radius:8px;padding:14px 16px;background:var(--bg);font-size:.93rem;line-height:1.7;border-left:5px solid var(--line)}.fb b{display:block;margin-bottom:2px;font-size:1rem}
.fb.ok{border-color:var(--ok)}.fb.ok b{color:var(--ok)}.fb.no{border-color:var(--up)}.fb.no b{color:var(--up)}
.topics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.topic{font:inherit;border:0;border-radius:8px;padding:16px;cursor:pointer;background:var(--bg);color:var(--fg);display:grid;gap:6px;justify-items:start;text-align:left}
.topic .ic{width:30px;height:30px;color:var(--accent)}.topic b{font-size:1rem}.topic span{font-size:.8rem;color:var(--muted)}
.topic[aria-pressed=true]{background:var(--accent);color:#fff}.topic[aria-pressed=true] .ic,.topic[aria-pressed=true] span{color:#fff}
.intro{display:grid;grid-template-columns:auto minmax(0,1fr);gap:20px;align-items:center}.intro p{margin:0;line-height:1.8}
.result{display:grid;gap:14px;justify-items:center;text-align:center}.result .big{font:600 3rem/1 var(--font-data)}.result .grade{font-size:1.25rem;font-weight:800;color:var(--accent)}
.stack{width:100%;max-width:560px}.stack svg{width:100%;height:auto;display:block}
.review{display:grid;gap:8px;padding:0;margin:0;list-style:none;text-align:left;width:100%}.review li{background:var(--bg);border-radius:6px;padding:10px 12px;font-size:.88rem;line-height:1.6;border-left:4px solid var(--up)}
.review li .a{color:var(--ok);font-weight:700}
.board{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;perspective:900px;max-width:560px}
.mc{font:inherit;border:0;padding:0;background:none;cursor:pointer;aspect-ratio:1;position:relative}
.mc .in{position:absolute;inset:0;transition:transform .45s;transform-style:preserve-3d}.mc.flip .in{transform:rotateY(180deg)}
.mc .f,.mc .b{position:absolute;inset:0;border-radius:8px;backface-visibility:hidden;display:grid;place-items:center;align-content:center;gap:4px}
.mc .f{background:var(--accent);color:#fff;font:700 1.6rem var(--font-data)}.mc .f::after{content:"";position:absolute;inset:8px;border:2px dashed color-mix(in srgb,#fff 45%,transparent);border-radius:6px}
.mc .b{transform:rotateY(180deg);background:color-mix(in srgb,var(--c) 22%,var(--surface));border:3px solid var(--c);color:var(--fg)}
.mc .b .z{position:absolute;left:8px;top:5px;font:500 .75rem var(--font-data);color:var(--muted)}.mc .b .s{font:700 2rem/1 var(--font-data)}.mc .b .s.long{font-size:1.2rem}.mc .b .n{font-size:1.4rem;font-weight:700;padding:0 6px;text-align:center;line-height:1.3}.mc .b.name{background:var(--accent-soft);border-color:var(--accent)}.mc.done .b.name{background:color-mix(in srgb,var(--c) 22%,var(--surface))}
.mc.done .b{border-color:var(--ok)}.mc.done .b::after{content:"✓";position:absolute;right:8px;top:4px;color:var(--ok);font-weight:800}
.mc.miss .b{border-color:var(--up)}
@media (max-width:640px){.game{grid-template-columns:1fr}.chamber{position:static;grid-template-columns:auto 1fr;justify-items:start;align-items:center;gap:12px}.chamber svg{width:96px}
.qcard{grid-template-columns:72px minmax(0,1fr);gap:12px}.vis{width:72px;height:72px}.vis.icon .ic{width:32px;height:32px}.ptile .s{font-size:1.5rem}.g-q{font-size:1.02rem}
.opts{grid-template-columns:1fr}.board{grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.mc .b .s{font-size:1.5rem}.mc .b .n{font-size:.95rem}.intro{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){.ch-plasma{animation:none}.mc .in,.opt{transition:none}}
"""

GAME_SCRIPT = r"""
(function(){
const B = JSON.parse(document.getElementById('bank').textContent);
const ROUND = __ROUND__, PAIRS = __PAIRS__, LAYER = 9, BASE = 228;
const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const SVGNS = 'http://www.w3.org/2000/svg';
const $ = id => document.getElementById(id);
const el = (t, cls, txt) => { const n = document.createElement(t); if (cls) n.className = cls; if (txt !== undefined) n.textContent = txt; return n; };
const sv = (t, a) => { const n = document.createElementNS(SVGNS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };
const icon = name => { const s = sv('svg', {class: 'ic', 'aria-hidden': 'true'}); s.append(sv('use', {href: '#i-' + name})); return s; };
const btn = (cls, txt, fn) => { const b = el('button', cls, txt); b.type = 'button'; b.onclick = fn; return b; };
const shuffle = a => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
const EL = Object.fromEntries(B.elements.map(e => [e.sym, e]));
const PALETTE = ['#3b5bdb', '#0c8f6a', '#f76707', '#7048e8', '#d6336c', '#1098ad', '#e8590c', '#2f9e44', '#5c7cfa', '#b7791f'];

document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => {
  document.querySelectorAll('[data-tab]').forEach(x => x.setAttribute('aria-selected', x === b));
  $('quiz').hidden = b.dataset.tab !== 'quiz'; $('match').hidden = b.dataset.tab !== 'match';
});

// ---------- sputtering chamber: every right answer deposits one film layer ----------
function chamber(caption) {
  const wrap = el('div', 'chamber');
  const s = sv('svg', {viewBox: '0 0 200 260', role: 'img', 'aria-label': '濺鍍腔體:答對一題就在晶圓上鍍一層薄膜'});
  s.append(sv('rect', {class: 'ch-wall', x: 6, y: 6, width: 188, height: 248, rx: 10}),
    sv('rect', {class: 'ch-magnet', x: 40, y: 14, width: 120, height: 8, rx: 2}),
    sv('rect', {class: 'ch-target', x: 30, y: 24, width: 140, height: 16, rx: 2}),
    sv('ellipse', {class: 'ch-plasma', cx: 100, cy: 70, rx: 70, ry: 22}));
  const t1 = sv('text', {class: 'ch-label', x: 100, y: 35, 'text-anchor': 'middle'}); t1.textContent = 'TARGET';
  const films = sv('g', {}), atoms = sv('g', {});
  const wafer = sv('rect', {class: 'ch-wafer', x: 24, y: BASE, width: 152, height: 10, rx: 2});
  const t2 = sv('text', {class: 'ch-label', x: 100, y: 251, 'text-anchor': 'middle'}); t2.textContent = 'WAFER';
  s.append(t1, films, atoms, wafer, t2);
  wrap.append(s, el('div', 'cap', caption));
  return {wrap, films, atoms, layers: 0};
}
function deposit(ch, color, done) {
  const top = BASE - (ch.layers + 1) * LAYER, n = ch.layers++;
  const lay = () => {
    const r = sv('rect', {x: 24, y: top, width: 152, height: LAYER, fill: color, style: 'transform-box:fill-box;transform-origin:bottom'});
    ch.films.append(r);
    if (!RM) r.animate([{opacity: 0, transform: 'scaleY(0)'}, {opacity: 1, transform: 'scaleY(1)'}], {duration: 250, easing: 'ease-out'});
    done && done();
  };
  ch.wrap.classList.add('hit'); setTimeout(() => ch.wrap.classList.remove('hit'), 700);
  if (RM) return lay();
  for (let i = 0; i < 16; i++) {
    const x0 = 40 + Math.random() * 120, x1 = 30 + Math.random() * 140;
    const c = sv('circle', {cx: 0, cy: 0, r: 3, fill: color}); ch.atoms.append(c);
    c.animate([{transform: `translate(${x0}px,44px)`, opacity: 1}, {transform: `translate(${x1}px,${top}px)`, opacity: .9}],
      {duration: 520 + Math.random() * 260, delay: i * 25, easing: 'cubic-bezier(.3,.6,.4,1)', fill: 'forwards'}).onfinish = () => c.remove();
  }
  setTimeout(lay, 700);
}
function misfire(ch) { ch.wrap.classList.add('miss'); setTimeout(() => ch.wrap.classList.remove('miss'), 600); }

// ---------- question visuals ----------
function visual(q) {
  const v = q.visual || {};
  if (v.kind === 'element' && EL[v.sym]) {
    const e = EL[v.sym], d = el('div', 'vis ptile'); d.style.setProperty('--c', e.color);
    if (e.num) d.append(el('span', 'z', e.num));
    d.append(el('span', 's' + (e.sym.length > 2 ? ' long' : ''), e.sym)); return d;
  }
  if (v.kind === 'logo') {
    const d = el('div', 'vis logo');
    if (v.src) { const img = el('img'); img.src = v.src; img.alt = v.label + ' logo'; d.append(img); }
    else d.append(el('b', '', [...(v.label || '?')][0]));
    d.append(el('span', 'mono', v.sub || '')); return d;
  }
  const d = el('div', 'vis icon'); d.append(icon(v.icon || 'sparkle'));
  if (v.caption) d.append(el('span', 'vcap', v.caption));
  if (v.sym) d.append(el('span', 'vsym', v.sym));
  return d;
}
const layerColor = (q, i) => (q.visual && q.visual.kind === 'element' && EL[q.visual.sym]) ? EL[q.visual.sym].color : PALETTE[i % PALETTE.length];

// ---------- quiz ----------
let topic = 'all', round = [], idx = 0, score = 0, streak = 0, bestStreak = 0, results = [], ch = null;
const pool = t => B.questions.filter(q => t === 'all' || q.topic === t);
function startScreen() {
  const box = $('quiz'); box.replaceChildren(); document.onkeydown = null;
  const intro = el('div', 'intro'), demo = chamber('');
  [0, 1, 2].forEach(i => deposit(demo, PALETTE[i]));
  const p = el('p'); p.append(el('b', '', '把知識鍍上晶圓!'), el('br'),
    document.createTextNode('每回合 ' + ROUND + ' 題,答對一題就在晶圓上濺鍍一層薄膜;答錯會留下缺陷。看看你能鍍出幾層完整的膜。'));
  intro.append(demo.wrap, p);
  const grid = el('div', 'topics');
  for (const [k, label, ic] of B.topics) {
    const n = pool(k).length; if (!n) continue;
    const t = btn('topic', '', () => { topic = k; startScreen(); }); t.setAttribute('aria-pressed', k === topic);
    t.append(icon(ic), el('b', '', label), el('span', '', n + ' 題 · 最佳 ' + (store.get('quiz-best-' + k) || '—'))); grid.append(t);
  }
  const go = btn('g-btn primary', '', start); go.append(icon('target'), document.createTextNode('開始濺鍍'));
  box.append(intro, grid, go);
}
function start() { round = shuffle(pool(topic)).slice(0, ROUND); idx = 0; score = 0; streak = 0; bestStreak = 0; results = []; ch = chamber('答對 = 鍍上一層膜'); show(); }
function show() {
  const q = round[idx], box = $('quiz'); box.replaceChildren();
  const game = el('div', 'game'), main = el('div'); main.style.display = 'grid'; main.style.gap = '18px';
  const hud = el('div', 'hud'), dots = el('div', 'dots');
  round.forEach((_, i) => { const d = el('i'); if (i < results.length) d.className = results[i] ? 'ok' : 'no'; else if (i === idx) d.className = 'now'; dots.append(d); });
  const sc = el('span', '', '膜層 '), scv = el('b', '', score + ' / ' + round.length); sc.append(scv);
  hud.append(dots, sc); if (streak >= 2) hud.append(el('span', 'streak', '🔥 連續答對 ' + streak));
  const card = el('div', 'qcard'); card.append(visual(q), el('p', 'g-q', q.q));
  const opts = el('div', 'opts'), fb = el('div', 'fb'); fb.hidden = true; fb.setAttribute('aria-live', 'polite');
  const next = btn('g-btn primary', idx + 1 < round.length ? '下一題 →' : '看我的晶圓 →', () => { idx++; idx < round.length ? show() : finish(); });
  next.hidden = true;
  const choices = shuffle([q.answer, ...q.wrong]), buttons = [];
  const answer = (text, b) => {
    if (!next.hidden) return;
    const ok = text === q.answer; results.push(ok);
    dots.children[idx].className = ok ? 'ok' : 'no'; scv.textContent = (score + (ok ? 1 : 0)) + ' / ' + round.length;
    if (ok) { score++; streak++; bestStreak = Math.max(bestStreak, streak); deposit(ch, layerColor(q, idx)); }
    else { streak = 0; misfire(ch); }
    buttons.forEach(o => { o.disabled = true; if (o.dataset.t === q.answer) o.classList.add('right'); else if (o !== b) o.classList.add('dim'); });
    if (!ok) b.classList.add('wrong');
    fb.className = 'fb ' + (ok ? 'ok' : 'no');
    fb.replaceChildren(el('b', '', ok ? (streak >= 3 ? '答對了!連續 ' + streak + ' 題 🔥' : '答對了!鍍上一層膜') : '答錯了,正解是「' + q.answer + '」'));
    if (q.explain) fb.append(document.createTextNode(q.explain));
    if (q.link) { const a = el('a', '', ' 閱讀原文 →'); a.href = q.link; a.target = '_blank'; a.rel = 'noopener'; fb.append(a); }
    fb.hidden = false; next.hidden = false; next.focus();
  };
  choices.forEach((text, i) => {
    const b = el('button', 'opt'); b.type = 'button'; b.dataset.t = text;
    b.append(el('span', 'k', 'ABCD'[i]), el('span', '', text)); b.onclick = () => answer(text, b);
    buttons.push(b); opts.append(b);
  });
  document.onkeydown = e => {
    if (e.target.closest && e.target.closest('input,textarea')) return;
    const i = '1234'.indexOf(e.key) >= 0 ? '1234'.indexOf(e.key) : 'abcd'.indexOf(e.key.toLowerCase());
    if (i >= 0 && i < buttons.length && next.hidden) answer(choices[i], buttons[i]);
  };
  main.append(hud, card, opts, fb, next);
  game.append(ch.wrap, main); box.append(game);
}
function finish() {
  const box = $('quiz'); box.replaceChildren(); document.onkeydown = null;
  const key = 'quiz-best-' + topic, best = +(store.get(key) || 0);
  if (score > best) store.set(key, score);
  const n = round.length, grade = score === n ? '完美鍍膜 · 靶材達人' : score >= n * .7 ? '良率優良' : score >= n * .4 ? '製程需要調整' : '重新開機再來一次';
  // wafer cross-section: one band per question, film for right answers, defect for wrong ones
  const W = 520, H = 40 + n * 14, s = sv('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': '你的晶圓膜層剖面'});
  round.forEach((q, i) => {
    const y = H - 30 - (i + 1) * 14;
    if (results[i]) s.append(sv('rect', {x: 20, y, width: W - 40, height: 13, fill: layerColor(q, i), rx: 1}));
    else { s.append(sv('rect', {x: 20, y, width: W - 40, height: 13, fill: 'none', stroke: 'var(--up)', 'stroke-dasharray': '4 3'}));
      const t = sv('text', {x: W / 2, y: y + 10, 'text-anchor': 'middle', fill: 'var(--up)', 'font-size': 10}); t.textContent = '缺陷'; s.append(t); }
  });
  s.append(sv('rect', {x: 10, y: H - 30, width: W - 20, height: 22, rx: 3, fill: 'var(--fg)', opacity: .85}));
  const tw = sv('text', {x: W / 2, y: H - 15, 'text-anchor': 'middle', fill: 'var(--surface)', 'font-size': 11}); tw.textContent = 'Si WAFER'; s.append(tw);
  const stack = el('div', 'stack'); stack.append(s);
  const res = el('div', 'result');
  res.append(el('div', 'grade', grade), el('div', 'big', score + ' / ' + n),
    el('div', 'hud', (score > best ? '🎉 新紀錄!' : '最佳成績 ' + Math.max(best, score) + ' / ' + n) + (bestStreak >= 2 ? ' · 最長連對 ' + bestStreak : '')), stack);
  const missed = round.filter((q, i) => !results[i]);
  if (missed.length) {
    const ul = el('ul', 'review');
    for (const q of missed) { const li = el('li'); li.append(el('div', '', q.q), el('span', 'a', '正解:' + q.answer)); ul.append(li); }
    res.append(el('b', '', '缺陷檢查:答錯的題目'), ul);
  }
  const row = el('div', 'g-row'); row.append(btn('g-btn primary', '再鍍一片', start), btn('g-btn', '換個主題', startScreen));
  res.append(row); box.append(res);
}

// ---------- matching: periodic-table tiles ----------
let open = [], moves = 0, left = 0, t0 = 0, timer = null;
function face(e, sym) {
  // name cards stay neutral until matched, so the colour doesn't give the pair away
  const b = el('div', sym ? 'b' : 'b name'); b.style.setProperty('--c', e.color);
  if (sym) { if (e.num) b.append(el('span', 'z', e.num)); b.append(el('span', 's' + (e.sym.length > 2 ? ' long' : ''), e.sym)); }
  else b.append(el('span', 'n', e.name));
  return b;
}
function newMatch() {
  const box = $('match'); box.replaceChildren(); clearInterval(timer);
  const pick = shuffle(B.elements).slice(0, PAIRS);
  const cards = shuffle(pick.flatMap(e => [{e, sym: true}, {e, sym: false}]));
  open = []; moves = 0; left = PAIRS; t0 = 0;
  const hud = el('div', 'hud'), mv = el('b', '', '0'), tm = el('b', '', '0'), best = store.get('match-best');
  const a = el('span', '', '步數 '), b2 = el('span', '', '秒數 '); a.append(mv); b2.append(tm);
  hud.append(a, b2, el('span', '', best ? '最佳 ' + best + ' 步' : '翻開兩張:元素符號配中文名稱'));
  const board = el('div', 'board');
  for (const c of cards) {
    const card = el('button', 'mc'); card.type = 'button'; card.setAttribute('aria-label', '翻開卡片');
    const inner = el('div', 'in'), front = el('div', 'f', '?');
    inner.append(front, face(c.e, c.sym)); card.append(inner);
    card.onclick = () => {
      if (card.classList.contains('flip') || open.length === 2) return;
      if (!t0) { t0 = Date.now(); timer = setInterval(() => tm.textContent = Math.round((Date.now() - t0) / 1000), 500); }
      card.classList.add('flip'); card.setAttribute('aria-label', c.sym ? c.e.sym : c.e.name); open.push([card, c]);
      if (open.length < 2) return;
      moves++; mv.textContent = moves;
      const [[k1, c1], [k2, c2]] = open;
      if (c1.e.sym === c2.e.sym && c1.sym !== c2.sym) {
        [k1, k2].forEach(k => { k.classList.add('done'); k.disabled = true; }); open = [];
        if (!--left) {
          clearInterval(timer); const prev = +(store.get('match-best') || 0);
          if (!prev || moves < prev) store.set('match-best', moves);
          const fb = el('div', 'fb ok'); fb.append(el('b', '', '完成!' + moves + ' 步、' + tm.textContent + ' 秒' + (!prev || moves < prev ? ' 🎉 新紀錄' : '')),
            document.createTextNode(pick.map(e => e.sym + ' = ' + e.name).join('、')));
          box.insertBefore(fb, row);
        }
      } else {
        [k1, k2].forEach(k => k.classList.add('miss'));
        setTimeout(() => { [k1, k2].forEach(k => { k.classList.remove('flip', 'miss'); k.setAttribute('aria-label', '翻開卡片'); }); open = []; }, 900);
      }
    };
    board.append(card);
  }
  const row = el('div', 'g-row'); row.append(btn('g-btn primary', '重新洗牌', newMatch));
  box.append(hud, board, row);
}
startScreen(); newMatch();
})();
"""


def render_quiz(config: Config, items: list[dict], generated: str, seed: str = "") -> str:
    from .site import _brand, _head, esc, icon

    bank = build_bank(config, items, seed)
    bank["topics"] = TOPICS
    payload = json.dumps(bank, ensure_ascii=False).replace("</", "<\\/")
    counts = {k: sum(q["topic"] == k for q in bank["questions"]) for k, *_ in TOPICS[1:]}
    script = GAME_SCRIPT.replace("__ROUND__", str(ROUND)).replace("__PAIRS__", str(PAIRS))
    return (
        _head("靶材知識小遊戲", "用網站的新聞、商情預測與財報素材出題的靶材產業知識問答與元素配對遊戲。",
              config.settings.site_url, "quiz.html")
        + f"""<style>{GAME_STYLE}</style><div class="wrap">
<header>{_brand("Sputtering Target · Knowledge Game", "靶材知識小遊戲")}
<nav class="meta" aria-label="網站導覽"><a class="btn" href="index.html">{icon("arrow-left")}新聞摘要</a><a class="btn" href="forecast.html">{icon("research")}商情預測</a><span>{icon("calendar")}題庫更新於 <span class="mono">{esc(generated)}</span></span></nav>
<p class="sub">題目取自本站素材:材料與製程 {counts["materials"]} 題、市場與廠商 {counts["market"]} 題、近期新聞 {counts["news"]} 題(每天隨新聞更新)。可用鍵盤 1–4 或 A–D 作答。</p></header>
<section aria-label="遊戲"><div class="tabs" role="tablist"><button type="button" role="tab" data-tab="quiz" aria-selected="true">{icon("target")}濺鍍問答</button><button type="button" role="tab" data-tab="match" aria-selected="false">{icon("layers")}元素配對</button></div>
<div class="panel" id="quiz"></div><div class="panel" id="match" hidden></div></section>
<footer>題目與解說由網站素材整理;市場數字出自富士キメラ総研報告摘要,以原報告為準。公司 logo 取自財務儀表板。</footer>
</div><script type="application/json" id="bank">{payload}</script><script>{script}</script></body></html>"""
    )

"""Question bank for the site's knowledge game (quiz.html).

Fixed questions come from config/quiz.yaml; the rest are generated from the site's own
material, so the game follows the data: listed target makers (market, target share) from
target_makers.yaml, element symbols, and the latest days' top news ("which company?").
"""

from __future__ import annotations

import json
import random
import re

from .config import Config
from .site import SIGNAL_KINDS, item_kind, short_name
from .translate import to_traditional

# Symbol -> name for the element / compound questions and the matching game.
ELEMENTS = {
    "Al": "鋁", "Ti": "鈦", "Cu": "銅", "Ta": "鉭", "W": "鎢", "Co": "鈷", "Ru": "釕", "Mo": "鉬",
    "Ni": "鎳", "In": "銦", "Ag": "銀", "Au": "金", "Pt": "鉑", "Pd": "鈀", "Cr": "鉻", "Si": "矽",
    "ITO": "氧化銦錫", "IGZO": "銦鎵鋅氧化物",
}
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
            out.append(_q(str(x.get("topic", "materials")), str(x["q"]), str(x["answer"]), wrong, str(x.get("explain", ""))))
    return out


def element_questions(rng: random.Random) -> list[dict]:
    out = []
    names = list(ELEMENTS.values())
    for sym, name in ELEMENTS.items():
        wrong = rng.sample([n for n in names if n != name], 3)
        out.append(_q("materials", f"靶材上的「{sym}」指的是哪一種材料?", name, wrong,
                      f"{sym} = {name}。"))
    return out


def maker_questions(config: Config, rng: random.Random) -> list[dict]:
    out = []
    makers = [m for m in config.makers if m.get("name")]
    for m in makers:
        name, market, focus = str(m["name"]), str(m.get("market", "")).upper(), str(m.get("target_focus", ""))
        segment = str(m.get("segment", ""))
        if market in MARKETS:
            out.append(_q("market", f"{name}({m.get('ticker', '')})在哪個市場上市?", MARKETS[market],
                          [v for k, v in MARKETS.items() if k != market],
                          f"{name}在{MARKETS[market]}上市;主要業務:{segment}。"))
        if focus in FOCUS:
            out.append(_q("market", f"以靶材占營收的比重來看,{name}屬於哪一類?", FOCUS[focus],
                          [v for k, v in FOCUS.items() if k != focus] + ["不生產靶材"],
                          f"{FOCUS_NOTE[focus]}主要業務:{segment}。"))
    return out


def _mask(title: str, words: list[str]) -> str:
    for w in sorted({w for w in words if len(w) >= 2}, key=len, reverse=True):
        title = re.sub(re.escape(w), "○○", title, flags=re.I)
    return title


def news_questions(items: list[dict], config: Config, rng: random.Random) -> list[dict]:
    """"Which company is this headline about?" from the latest days' signal news, company name masked."""
    companies = {e.name: e for e in config.entities if not e.is_topic}
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
        picked.append(_q("news", f"這則近期新聞的主角是哪家公司?「{masked}」", answer, rng.sample(pool, 3),
                         it.get("summary") or "", link=it.get("link", ""), date=(it.get("published") or "")[:10]))
        if len(picked) >= MAX_NEWS:
            break
    return picked


def build_bank(config: Config, items: list[dict], seed: str = "") -> dict:
    """All questions plus the element pairs for the matching game."""
    rng = random.Random(seed)
    questions = fixed_questions(config) + element_questions(rng) + maker_questions(config, rng) + news_questions(items, config, rng)
    return {"questions": questions, "elements": [[s, n] for s, n in ELEMENTS.items()]}


TOPICS = [("all", "全部"), ("materials", "材料與製程"), ("market", "市場與廠商"), ("news", "近期新聞")]
ROUND = 10
PAIRS = 6

GAME_STYLE = """
.tabs{display:flex;gap:8px;flex-wrap:wrap}.tabs button,.g-btn{font:inherit;font-size:.92rem;border:0;border-radius:6px;padding:8px 16px;cursor:pointer;background:var(--accent-soft);color:var(--accent);font-weight:500;display:inline-flex;gap:6px;align-items:center}
.tabs button[aria-selected=true],.g-btn.primary{background:var(--accent);color:#fff}.g-btn:hover{background:var(--accent);color:#fff}.g-btn.primary:hover{background:var(--accent-2)}
.panel{background:var(--surface);border-top:4px solid var(--accent);border-radius:6px;padding:20px;display:grid;gap:16px}
.g-meta{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:.85rem;color:var(--muted)}.g-meta b{font-family:var(--font-data);color:var(--fg)}
.g-q{font-size:1.12rem;font-weight:700;line-height:1.6;margin:0}
.opts{display:grid;gap:10px}.opt{font:inherit;text-align:left;border:2px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:10px 14px;cursor:pointer;line-height:1.5}
.opt:hover:not(:disabled){border-color:var(--accent)}.opt:disabled{cursor:default}
.opt.right{border-color:var(--ok);background:var(--ok-soft);color:var(--ok);font-weight:700}.opt.wrong{border-color:var(--up);background:color-mix(in srgb,var(--up) 12%,var(--surface));color:var(--up)}
.fb{border-radius:6px;padding:12px 14px;background:var(--bg);font-size:.92rem;line-height:1.7}.fb b{display:block;margin-bottom:2px}
.fb.ok b{color:var(--ok)}.fb.no b{color:var(--up)}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--accent);transition:width .2s}
.big{font-family:var(--font-data);font-size:2.6rem;font-weight:500;line-height:1}
.review{display:grid;gap:8px;padding:0;margin:0;list-style:none}.review li{background:var(--bg);border-radius:6px;padding:10px 12px;font-size:.88rem;line-height:1.6}
.review li .a{color:var(--ok);font-weight:700}
.cards-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.mc{font:inherit;aspect-ratio:4/3;border:0;border-radius:6px;cursor:pointer;background:var(--accent);color:#fff;font-size:1.1rem;font-weight:700;display:grid;place-items:center;padding:6px;line-height:1.3}
.mc.up{background:var(--accent-soft);color:var(--accent)}.mc.done{background:var(--ok-soft);color:var(--ok);cursor:default}
.mc.sym{font-family:var(--font-data)}.mc.miss{background:color-mix(in srgb,var(--up) 15%,var(--surface));color:var(--up)}
@media (max-width:520px){.cards-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.mc{font-size:.95rem}.big{font-size:2.1rem}}
"""

GAME_SCRIPT = r"""
(function(){
const B = JSON.parse(document.getElementById('bank').textContent);
const ROUND = %(round)d, PAIRS = %(pairs)d;
const $ = id => document.getElementById(id);
const el = (t, cls, txt) => { const n = document.createElement(t); if (cls) n.className = cls; if (txt !== undefined) n.textContent = txt; return n; };
const shuffle = a => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };

// tabs
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.setAttribute('aria-selected', x === b));
  $('quiz').hidden = b.dataset.tab !== 'quiz'; $('match').hidden = b.dataset.tab !== 'match';
});

// ---------- quiz ----------
let topic = 'all', round = [], idx = 0, score = 0, missed = [];
const pool = t => B.questions.filter(q => t === 'all' || q.topic === t);
function startScreen() {
  const box = $('quiz'); box.replaceChildren();
  box.append(el('p', 'g-q', '選一個主題,每回合隨機 ' + ROUND + ' 題,答完立刻看解說。'));
  const chips = el('div', 'tabs');
  for (const [k, label] of B.topics) {
    const n = pool(k).length; if (!n) continue;
    const c = el('button', '', label + ' · ' + n + ' 題'); c.type = 'button'; c.setAttribute('aria-selected', k === topic);
    c.onclick = () => { topic = k; startScreen(); }; chips.append(c);
  }
  const best = store.get('quiz-best-' + topic);
  const go = el('button', 'g-btn primary', '開始挑戰'); go.type = 'button'; go.onclick = start;
  box.append(chips, el('div', 'g-meta', best ? '這個主題的最佳成績:' + best + ' / ' + ROUND : '還沒有紀錄,來挑戰看看!'), go);
}
function start() { round = shuffle(pool(topic)).slice(0, ROUND); idx = 0; score = 0; missed = []; show(); }
function show() {
  const q = round[idx], box = $('quiz'); box.replaceChildren();
  const meta = el('div', 'g-meta'); meta.append(el('span', '', '第 ' + (idx + 1) + ' / ' + round.length + ' 題'), el('span', '', '得分 ' + score));
  const bar = el('div', 'bar'); const fill = el('i'); fill.style.width = (idx / round.length * 100) + '%%'; bar.append(fill);
  const opts = el('div', 'opts'), fb = el('div', 'fb'); fb.hidden = true; fb.setAttribute('aria-live', 'polite');
  const next = el('button', 'g-btn primary', idx + 1 < round.length ? '下一題' : '看結果'); next.type = 'button'; next.hidden = true;
  next.onclick = () => { idx++; idx < round.length ? show() : finish(); };
  for (const text of shuffle([q.answer, ...q.wrong])) {
    const b = el('button', 'opt', text); b.type = 'button';
    b.onclick = () => {
      const ok = text === q.answer; if (ok) score++; else missed.push(q);
      opts.querySelectorAll('.opt').forEach(o => { o.disabled = true; if (o.textContent === q.answer) o.classList.add('right'); });
      if (!ok) b.classList.add('wrong');
      fb.className = 'fb ' + (ok ? 'ok' : 'no'); fb.replaceChildren(el('b', '', ok ? '答對了!' : '答錯了,正解是「' + q.answer + '」'));
      if (q.explain) fb.append(document.createTextNode(q.explain));
      if (q.link) { const a = el('a', '', ' 閱讀原文 →'); a.href = q.link; a.target = '_blank'; a.rel = 'noopener'; fb.append(a); }
      fb.hidden = false; next.hidden = false; next.focus();
    };
    opts.append(b);
  }
  box.append(meta, bar, el('p', 'g-q', q.q), opts, fb, next);
}
function finish() {
  const box = $('quiz'); box.replaceChildren();
  const key = 'quiz-best-' + topic, best = +(store.get(key) || 0);
  if (score > best) store.set(key, score);
  const msg = score === round.length ? '全對!靶材達人 🎉' : score >= round.length * 0.7 ? '很不錯,再接再厲!' : '多看看網站的新聞與商情預測,下次會更好。';
  box.append(el('div', 'big', score + ' / ' + round.length), el('p', 'g-q', msg),
    el('div', 'g-meta', score > best ? '新紀錄!' : '最佳成績:' + Math.max(best, score) + ' / ' + round.length));
  if (missed.length) {
    const ul = el('ul', 'review');
    for (const q of missed) { const li = el('li'); li.append(el('div', '', q.q), el('span', 'a', '正解:' + q.answer)); ul.append(li); }
    box.append(el('b', '', '答錯的題目'), ul);
  }
  const again = el('button', 'g-btn primary', '再玩一回'); again.type = 'button'; again.onclick = start;
  const other = el('button', 'g-btn', '換個主題'); other.type = 'button'; other.onclick = startScreen;
  const row = el('div', 'tabs'); row.append(again, other); box.append(row);
}

// ---------- matching ----------
let open = [], moves = 0, pairsLeft = 0, t0 = 0, timer = null;
function newMatch() {
  const box = $('match'); box.replaceChildren(); clearInterval(timer);
  const pick = shuffle(B.elements).slice(0, PAIRS);
  const cards = shuffle(pick.flatMap(([s, n]) => [{key: s, text: s, sym: true}, {key: s, text: n, sym: false}]));
  open = []; moves = 0; pairsLeft = PAIRS; t0 = 0;
  const meta = el('div', 'g-meta'), mv = el('b', '', '0'), tm = el('b', '', '0'), best = store.get('match-best');
  const s1 = el('span', '', '步數 '), s2 = el('span', '', '秒數 '); s1.append(mv); s2.append(tm);
  meta.append(s1, s2, el('span', '', best ? '最佳:' + best + ' 步' : '把元素符號和中文名稱配成對'));
  const grid = el('div', 'cards-grid');
  for (const c of cards) {
    const b = el('button', 'mc' + (c.sym ? ' sym' : ''), '?'); b.type = 'button'; b.setAttribute('aria-label', '翻開卡片');
    b.onclick = () => {
      if (b.classList.contains('up') || b.classList.contains('done') || open.length === 2) return;
      if (!t0) { t0 = Date.now(); timer = setInterval(() => tm.textContent = Math.round((Date.now() - t0) / 1000), 500); }
      b.classList.add('up'); b.textContent = c.text; open.push([b, c]);
      if (open.length < 2) return;
      moves++; mv.textContent = moves;
      const [[b1, c1], [b2, c2]] = open;
      if (c1.key === c2.key && c1.sym !== c2.sym) {
        [b1, b2].forEach(x => { x.classList.remove('up'); x.classList.add('done'); x.disabled = true; }); open = []; pairsLeft--;
        if (!pairsLeft) {
          clearInterval(timer); const prev = +(store.get('match-best') || 0);
          if (!prev || moves < prev) store.set('match-best', moves);
          const done = el('div', 'fb ok'); done.append(el('b', '', '完成!' + moves + ' 步、' + tm.textContent + ' 秒'),
            document.createTextNode(pick.map(([s, n]) => s + ' = ' + n).join('、')));
          box.append(done);
        }
      } else {
        [b1, b2].forEach(x => x.classList.add('miss'));
        setTimeout(() => { [b1, b2].forEach(x => { x.classList.remove('up', 'miss'); x.textContent = '?'; }); open = []; }, 800);
      }
    };
    grid.append(b);
  }
  const again = el('button', 'g-btn primary', '重新洗牌'); again.type = 'button'; again.onclick = newMatch;
  box.append(meta, grid, again);
}
startScreen(); newMatch();
})();
"""


def render_quiz(config: Config, items: list[dict], generated: str, seed: str = "") -> str:
    from .site import _brand, _head, esc, icon

    bank = build_bank(config, items, seed)
    bank["topics"] = TOPICS
    payload = json.dumps(bank, ensure_ascii=False).replace("</", "<\\/")
    counts = {k: sum(q["topic"] == k for q in bank["questions"]) for k, _ in TOPICS[1:]}
    return (
        _head("靶材知識小遊戲", "用網站的新聞、商情預測與財報素材出題的靶材產業知識問答與元素配對遊戲。",
              config.settings.site_url, "quiz.html")
        + f"""<style>{GAME_STYLE}</style><div class="wrap">
<header>{_brand("Sputtering Target · Knowledge Game", "靶材知識小遊戲")}
<nav class="meta" aria-label="網站導覽"><a class="btn" href="index.html">{icon("arrow-left")}新聞摘要</a><a class="btn" href="forecast.html">{icon("research")}商情預測</a><span>{icon("calendar")}題庫更新於 <span class="mono">{esc(generated)}</span></span></nav>
<p class="sub">題目取自本站素材:材料與製程 {counts["materials"]} 題、市場與廠商 {counts["market"]} 題、近期新聞 {counts["news"]} 題(每天隨新聞更新)。</p></header>
<section aria-label="遊戲"><div class="tabs" role="tablist"><button type="button" role="tab" data-tab="quiz" aria-selected="true">{icon("star")}知識問答</button><button type="button" role="tab" data-tab="match" aria-selected="false">{icon("layers")}元素配對</button></div>
<div class="panel" id="quiz"></div><div class="panel" id="match" hidden></div></section>
<footer>題目與解說由網站素材整理;市場數字出自富士キメラ総研報告摘要,以原報告為準。</footer>
</div><script type="application/json" id="bank">{payload}</script><script>{GAME_SCRIPT % {"round": ROUND, "pairs": PAIRS}}</script></body></html>"""
    )

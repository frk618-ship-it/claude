#!/usr/bin/env node
// 把 daily.mjs 產生的 daily.json 做成「每日預測報告」
//
//   node daily-report.mjs [輸出目錄=output]            → <目錄>/daily.html
//   node daily-report.mjs [目錄] --fragment 檔案.html   → 不含 <html>/<head> 外殼的版本（發布成 Artifact 用）
// 若同目錄的 results.json 有同一天的 volTarget（train.mjs 產生），一併顯示今日部位建議
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragIdx = args.indexOf('--fragment');
const fragmentOut = fragIdx >= 0 ? args[fragIdx + 1] : null;
const dir = path.resolve(HERE, args.find((a, i) => !a.startsWith('--') && !(fragIdx >= 0 && i === fragIdx + 1)) || 'output');
const R = JSON.parse(readFileSync(path.join(dir, 'daily.json'), 'utf8'));
try {
  const res = JSON.parse(readFileSync(path.join(dir, 'results.json'), 'utf8'));
  if (res.volTarget && res.volTarget.current.date === R.forecast.lastDate) R.volTarget = res.volTarget;
} catch (_) { /* 沒有 results.json */ }
const dataJson = JSON.stringify(R).replace(/<\//g, '<\\/');
const PLOTLY = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.24.1/plotly.min.js';

const body = `<title>白銀 每日預測</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* 版面：單欄閱讀流。先回答問題（結論 + 三種目標的總表），再給證據（各期間散點、雜訊對比、單邊預測），最後是實驗設定。
   色彩沿用 Silver AI Trader 的冷調金屬語彙；兩種訓練視窗用藍、橘；基準與買進持有用中性灰；漲跌用 up/down 語意色，基準線用中性灰。 */
:root {
  --ground: #EEF2F4; --surface: #FFFFFF; --surface-2: #E4EAEE; --edge: #CDD6DC;
  --ink: #0F1A21; --ink-2: #3A4A55; --ink-3: #66767F;
  --up: #1F8A3A; --down: #C8461F;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s5: #e87ba4; --base: #8497A3; --grid: rgba(15,26,33,.08);
  --font-body: "IBM Plex Sans TC", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --down: #F0603A;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --base: #5B6C77; --grid: rgba(237,244,248,.08);
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --down: #F0603A;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --base: #5B6C77; --grid: rgba(237,244,248,.08);
  color-scheme: dark; }

body { background: var(--ground); color: var(--ink); font-family: var(--font-body); font-size: 15px; line-height: 1.6; }
.page { max-width: 1040px; margin: 0 auto; padding-block: 28px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }
h1, h2 { text-wrap: balance; margin: 0; }
h1 { font-size: 1.75rem; font-weight: 700; }
h2 { font-size: 1.1rem; font-weight: 600; }
p { margin: 0; max-width: 70ch; }
.eyebrow { font-family: var(--font-mono); font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
.muted { color: var(--ink-3); font-size: .875rem; }
.num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
header { display: grid; gap: 8px; }
.verdict { border-left: 3px solid var(--ink-3); padding: 4px 0 4px 16px; display: grid; gap: 8px; }
section { display: grid; gap: 12px; }
.panel { background: var(--surface); border: 1px solid var(--edge); border-radius: 10px; padding: 16px; min-width: 0; }
.chart { width: 100%; height: 380px; }
.chart.short { height: 300px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: .85rem; color: var(--ink-2); }
.key { display: inline-flex; align-items: center; gap: 6px; }
.key i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.key i.line { width: 16px; height: 0; border-radius: 0; border-top: 2px dashed var(--base); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--edge); white-space: nowrap; }
th { font-weight: 500; color: var(--ink-3); font-size: .8rem; }
td.n, th.n { text-align: right; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
td .sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; vertical-align: 0; }
.good { color: var(--up); } .bad { color: var(--down); }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; align-items: start; }
.cols > * { min-width: 0; }
ul.recs { margin: 0; padding-left: 1.2em; display: grid; gap: 8px; max-width: 72ch; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0; font-size: .9rem; }
dt { color: var(--ink-3); } dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
footer { color: var(--ink-3); font-size: .8rem; border-top: 1px solid var(--edge); padding-top: 16px; }
.pill { display:inline-block; font-size:.75rem; padding:1px 8px; border-radius:999px; background: var(--surface-2); color: var(--ink-2); }
.pill.off { color: var(--down); }
td.wrap { white-space: normal; min-width: 14em; }
a { color: var(--s1); }
.kind { font-size:.75rem; color: var(--ink-3); }
tr.hl td { background: var(--surface-2); }
.stats { display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.stat { background: var(--surface); border: 1px solid var(--edge); border-radius: 10px; padding: 14px 16px; display:grid; gap:4px; min-width:0; }
.stat .v { font-family: var(--font-mono); font-size: 1.5rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.stat .l { font-size: .8rem; color: var(--ink-3); }
.seg { display:inline-flex; border:1px solid var(--edge); border-radius:8px; overflow:hidden; }
.seg button { font: inherit; font-size:.85rem; padding:6px 12px; border:0; background:var(--surface); color:var(--ink-2); cursor:pointer; }
.seg button[aria-pressed="true"] { background:var(--surface-2); color:var(--ink); font-weight:600; }
.chart.mini { height: 220px; }
.grp { font-size:.72rem; color: var(--ink-3); display:block; }
.fcgrid { display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:1px; background: var(--edge); border:1px solid var(--edge); border-radius:10px; overflow:hidden; }
.fc { background: var(--surface); padding: 14px 16px; display:grid; gap:4px; min-width:0; }
.fc .label { font-size:.8rem; color: var(--ink-3); }
.fc .big { font-family: var(--font-mono); font-size:1.45rem; font-weight:600; font-variant-numeric: tabular-nums; }
.chg.up { color: var(--up); } .chg.down { color: var(--down); }
@media (max-width: 480px) { .chart { height: 320px; } h1 { font-size: 1.45rem; } }
</style>
<div class="page">
  <header>
    <div class="eyebrow">Silver AI Trader · 每日預測報告</div>
    <h1>白銀期貨 (SI=F) 隔日預測</h1>
    <p class="muted" id="sub"></p>
  </header>

  <section aria-labelledby="fc-h">
    <h2 id="fc-h">下一個交易日</h2>
    <div class="fcgrid" id="fcCells"></div>
    <p class="muted" id="fcNote"></p>
  </section>

  <div class="verdict" id="verdict"></div>
  <div class="stats" id="stats"></div>

  <section aria-labelledby="sum-h">
    <h2 id="sum-h">2024 年起的每日樣本外準確度</h2>
    <div class="panel table-wrap"><table id="sumTable"></table></div>
    <p class="muted" id="sumNote"></p>
  </section>

  <section aria-labelledby="q-h">
    <h2 id="q-h">各季方向準確率</h2>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="qLegend"></div>
      <div id="qChart" class="chart short" role="img" aria-label="各季方向準確率"></div>
    </div>
  </section>

  <section aria-labelledby="roll-h">
    <h2 id="roll-h">滾動 60 日方向準確率</h2>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="rollLegend"></div>
      <div id="rollChart" class="chart short" role="img" aria-label="滾動 60 日方向準確率"></div>
    </div>
  </section>

  <div class="cols">
    <section aria-labelledby="conf-h">
      <h2 id="conf-h">模型越有把握，越準嗎？</h2>
      <div class="panel table-wrap"><table id="confTable"></table></div>
      <p class="muted">依預測報酬的絕對值分成五組（第 5 組 = 模型預測漲跌幅最大的 20% 日子）。</p>
    </section>
    <section aria-labelledby="reg-h">
      <h2 id="reg-h">不同波動環境下的準確率</h2>
      <div class="panel table-wrap"><table id="regTable"></table></div>
      <p class="muted">依前一天的 EWMA 預測波動分成三組。</p>
    </section>
  </div>

  <section aria-labelledby="bt-h">
    <h2 id="bt-h">照預測交易會怎樣（2024 年起，每日調整）</h2>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="btLegend"></div>
      <div id="btChart" class="chart" role="img" aria-label="依預測交易的淨值"></div>
    </div>
    <div class="panel table-wrap"><table id="btTable"></table></div>
    <p class="muted">每天收盤依隔日預測決定部位，扣 3 bps 換倉成本；「波動縮放」是部位再乘上波動目標化的比例（目標 20%、上限 1 倍）。夏普差區間為 20 日區塊拔靴法的 95% 信賴區間。</p>
  </section>

  <section aria-labelledby="src-h">
    <h2 id="src-h">優勢從哪裡來？</h2>
    <div class="cols">
      <div class="panel table-wrap"><table id="sepTable"></table></div>
      <div class="panel table-wrap"><table id="exTable"></table></div>
    </div>
    <div class="panel table-wrap"><table id="yrTable"></table></div>
    <p class="muted" id="srcNote"></p>
  </section>

  <section aria-labelledby="vt-h" id="vtSec" hidden>
    <h2 id="vt-h">今日部位建議（波動目標化）</h2>
    <div class="fcgrid" id="vtCells"></div>
    <p class="muted" id="vtNote"></p>
  </section>

  <section aria-labelledby="rec-h">
    <h2 id="rec-h">結論與建議</h2>
    <ul class="recs" id="recs"></ul>
  </section>

  <section aria-labelledby="set-h">
    <h2 id="set-h">方法</h2>
    <div class="panel"><dl id="setup"></dl></div>
  </section>

  <footer>本頁由 cloud/daily-report.mjs 依 daily.json 產生。預測與回測不代表未來績效，僅供研究參考，不構成投資建議。</footer>
</div>

<script type="application/json" id="data">${dataJson}</script>
<script src="${PLOTLY}" crossorigin="anonymous"></script>
<script>
(() => {
  const R = JSON.parse(document.getElementById('data').textContent);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fx = (v, d = 1) => Number.isFinite(v) ? v.toFixed(d) : '—';
  const pc = (v, d = 1) => Number.isFinite(v) ? (v * 100).toFixed(d) + '%' : '—';
  const sp = (v, d = 2) => Number.isFinite(v) ? (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%' : '—';
  const sg = (v, d = 2) => Number.isFinite(v) ? (v >= 0 ? '+' : '') + v.toFixed(d) : '—';
  const css = getComputedStyle(document.documentElement);
  const tok = (n) => css.getPropertyValue(n).trim();
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  const sd = (a) => { const m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); };
  const D = R.daily, N = D.dates.length, V = R.variants;
  const COLOR = { all: tok('--s1'), recent: tok('--s2') };
  const up = D.actRet.map(r => r > 0);
  const wilson = (p, n) => { const z = 1.96, d = 1 + z * z / n, c = (p + z * z / (2 * n)) / d, h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d; return [c - h, c + h]; };
  const da = (pred, idx) => { const ii = idx || D.dates.map((_, i) => i); const v = ii.filter(i => Number.isFinite(pred[i])); return v.filter(i => (pred[i] > 0) === up[i]).length / v.length; };

  // 基準
  const alwaysUp = up.filter(Boolean).length / N;
  const momPred = D.actRet.map((_, i) => i === 0 ? NaN : D.actRet[i - 1]);   // 猜和昨天同方向
  const momDA = da(momPred);
  const rwMAPE = mean(D.actual.map((a, i) => Math.abs(D.prev[i] / a - 1)));
  const S = V.map(v => {
    const p = R.pred[v.key], acc = da(p), [lo, hi] = wilson(acc, N);
    const mape = mean(D.actual.map((a, i) => Math.abs(D.prev[i] * Math.exp(p[i]) / a - 1)));
    return { ...v, p, acc, lo, hi, mape, upShare: p.filter(x => x > 0).length / N, runs: R.runsDA[v.key] };
  });
  const bestRule = Math.max(alwaysUp, momDA, 0.5);
  const bestRuleName = bestRule === alwaysUp ? '永遠猜漲' : bestRule === momDA ? '跟昨天同方向' : '擲硬幣';
  const best = [...S].sort((a, b) => b.acc - a.acc)[0];

  $('sub').textContent = '資料截至 ' + R.forecast.lastDate + '。2024 年起每季只用當時以前的資料重訓，預測該季每一個交易日的隔日漲跌（共 ' + N + ' 個每日樣本，' + R.folds.length + ' 季）；每個設定重訓 ' + R.config.repeats + ' 次取平均。';

  // ── 下一個交易日 ──
  const F = R.forecast;
  const nextDay = (() => { let t = Date.parse(F.lastDate + 'T00:00:00Z'); do { t += 864e5; } while ([0, 6].includes(new Date(t).getUTCDay())); return new Date(t).toISOString().slice(0, 10); })();
  const cell = (label, big, sub, cls = '') => '<div class="fc"><span class="label">' + label + '</span><span class="big ' + cls + '">' + big + '</span><span class="muted">' + sub + '</span></div>';
  $('fcCells').innerHTML = cell('最新收盤 · ' + esc(F.lastDate), fx(F.lastPrice, 3), '美元 / 盎司') +
    V.map(v => { const r = F.byVariant[v.key].mean, p = F.lastPrice * Math.exp(r), u = r >= 0; const runsUp = F.byVariant[v.key].runs.filter(x => x > 0).length;
      return cell(esc(v.name) + ' · ' + nextDay, fx(p, 3), '<span class="chg ' + (u ? 'up' : 'down') + '">' + (u ? '▲ 漲 ' : '▼ 跌 ') + sp(r) + '</span>　' + runsUp + '/' + F.byVariant[v.key].runs.length + ' 次重訓猜漲'); }).join('');
  const agree = V.every(v => (F.byVariant[v.key].mean > 0) === (F.byVariant[V[0].key].mean > 0));
  $('fcNote').textContent = (agree ? '兩種訓練視窗的方向一致。' : '兩種訓練視窗的方向不一致，訊號不明確。') + (F.byVariant[best.key].runs.every(x => x > 0) || F.byVariant[best.key].runs.every(x => x <= 0) ? '「' + best.name + '」' + F.byVariant[best.key].runs.length + ' 次重訓方向一致。' : '') + '2024 年以來，「' + best.name + '」的方向準確率較高（' + pc(best.acc) + '），但這是事後比較；預測幅度通常只有零點幾個百分點，遠小於白銀一天的正常波動（約 ' + pc(F.sigma / Math.sqrt(252)) + '）。';

  // ── 結論 ──
  const sig = best.lo > bestRule;
  $('verdict').innerHTML = '<p><strong>結論：' + (sig ? '「' + esc(best.name) + '」的隔日方向準確率顯著高於最強的簡單規則。' : '2024 年以來的隔日方向準確率沒有顯著勝過簡單規則。') + '</strong>' +
    S.map(s => esc(s.name) + ' <span class="num">' + pc(s.acc) + '</span>（95% 信賴區間 ' + pc(s.lo) + '–' + pc(s.hi) + '）').join('；') +
    '；最強的簡單規則「' + bestRuleName + '」是 <span class="num">' + pc(bestRule) + '</span>。</p>' +
    '<p>價格誤差：' + S.map(s => esc(s.name) + ' ' + pc(s.mape, 2)).join('、') + '，「明天 = 今天」' + pc(rwMAPE, 2) + '。' +
    (S.some(s => s.mape < rwMAPE) ? '' : '模型沒有比「明天不變」更準。') + '不過方向準確率不看漲跌幅度，照預測進出的回測結果見下方「照預測交易」與「優勢從哪裡來」。</p>';

  // ── 重點數字 ──
  $('stats').innerHTML = [
    [pc(best.acc), '最佳視窗的方向準確率（' + best.name + '）'],
    [pc(alwaysUp), '永遠猜漲的準確率（2024 年起白銀多頭）'],
    [N + ' 天', '每日樣本外預測數'],
    [pc(Math.min(...best.runs.filter(Number.isFinite)) / 100, 1) + '–' + pc(Math.max(...best.runs.filter(Number.isFinite)) / 100, 1), '單次重訓的準確率範圍'],
  ].map(([v, l]) => '<div class="stat"><div class="v" style="font-size:1.2rem">' + esc(v) + '</div><div class="l">' + esc(l) + '</div></div>').join('');

  // ── 總表 ──
  $('sumTable').innerHTML = '<thead><tr><th>做法</th><th class="n">方向準確率</th><th class="n">95% 信賴區間</th><th class="n">比永遠猜漲</th><th class="n">價格誤差 MAPE</th><th class="n">猜漲比例</th><th class="n">單次重訓範圍</th></tr></thead><tbody>' +
    S.map(s => '<tr><td><span class="sw" style="background:' + COLOR[s.key] + '"></span>' + esc(s.name) + '（' + R.config.repeats + ' 次集成）</td><td class="n"><strong>' + pc(s.acc) + '</strong></td><td class="n">' + pc(s.lo) + ' – ' + pc(s.hi) + '</td><td class="n ' + (s.acc > alwaysUp ? 'good' : 'bad') + '">' + sg((s.acc - alwaysUp) * 100, 1) + ' 點</td><td class="n">' + pc(s.mape, 2) + '</td><td class="n">' + pc(s.upShare, 0) + '</td><td class="n">' + fx(Math.min(...s.runs), 1) + '–' + fx(Math.max(...s.runs), 1) + '%</td></tr>').join('') +
    '<tr><td>永遠猜漲</td><td class="n">' + pc(alwaysUp) + '</td><td class="n">—</td><td class="n">—</td><td class="n">—</td><td class="n">100%</td><td class="n">—</td></tr>' +
    '<tr><td>跟昨天同方向</td><td class="n">' + pc(momDA) + '</td><td class="n">—</td><td class="n">' + sg((momDA - alwaysUp) * 100, 1) + ' 點</td><td class="n">—</td><td class="n">—</td><td class="n">—</td></tr>' +
    '<tr><td>明天 = 今天</td><td class="n">—</td><td class="n">—</td><td class="n">—</td><td class="n">' + pc(rwMAPE, 2) + '</td><td class="n">—</td><td class="n">—</td></tr></tbody>';
  $('sumNote').textContent = '每日樣本互不重疊，信賴區間直接用 ' + N + ' 個樣本計算。區間下緣高於最強簡單規則（' + pc(bestRule) + '）才算顯著。';

  // ── 圖表共用 ──
  const font = { family: tok('--font-body'), size: 12, color: tok('--ink-2') };
  const axis = (extra = {}) => ({ gridcolor: tok('--grid'), zeroline: false, linecolor: tok('--edge'), tickfont: { family: tok('--font-mono'), size: 11, color: tok('--ink-3') }, ...extra });
  const layout = (extra = {}) => ({ paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font, showlegend: false, hovermode: 'x unified',
    margin: { l: 56, r: 12, t: 8, b: 40 }, hoverlabel: { bgcolor: tok('--surface'), bordercolor: tok('--edge'), font: { family: tok('--font-mono'), color: tok('--ink') } }, xaxis: axis(), yaxis: axis(), ...extra });
  const cfg = { displayModeBar: false, responsive: true };
  const draw = (id, d, l) => window.Plotly ? Plotly.newPlot(id, d, l, cfg) : ($(id).innerHTML = '<p class="muted" style="padding:24px">圖表函式庫載入失敗（需連線到 cdn.jsdelivr.net）。</p>');
  const legend = (id, items) => { $(id).innerHTML = items.map(([n, c, dash]) => '<span class="key"><i ' + (dash ? 'class="line"' : 'style="background:' + c + '"') + '></i>' + esc(n) + '</span>').join(''); };

  // ── 各季 ──
  const qIdx = R.folds.map(f => D.dates.map((d, i) => i).filter(i => D.dates[i] >= f.from && D.dates[i] <= f.to));
  legend('qLegend', [...S.map(s => [s.name, COLOR[s.key]]), ['永遠猜漲', '', true]]);
  draw('qChart', [
    ...S.map((s, si) => ({ x: R.folds.map(f => f.q), y: qIdx.map(ii => da(s.p, ii) * 100), type: 'scatter', mode: 'markers', marker: { size: 11, color: COLOR[s.key], line: { color: tok('--surface'), width: 2 } }, hovertemplate: esc(s.name) + '：%{y:.1f}%<extra></extra>' })),
    { x: R.folds.map(f => f.q), y: qIdx.map(ii => ii.filter(i => up[i]).length / ii.length * 100), type: 'scatter', mode: 'lines', line: { color: tok('--base'), width: 2, dash: 'dash' }, hovertemplate: '永遠猜漲：%{y:.1f}%<extra></extra>' },
  ], layout({ yaxis: axis({ ticksuffix: '%' }), shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 50, y1: 50, line: { color: tok('--ink-3'), width: 1, dash: 'dot' } }] }));

  // ── 滾動 60 日 ──
  const roll = (pred) => D.dates.map((_, i) => { if (i < 59) return null; let h = 0; for (let k = i - 59; k <= i; k++) if ((pred[k] > 0) === up[k]) h++; return h / 60 * 100; });
  legend('rollLegend', S.map(s => [s.name, COLOR[s.key]]));
  draw('rollChart', S.map(s => ({ x: D.dates, y: roll(s.p), type: 'scatter', mode: 'lines', line: { color: COLOR[s.key], width: 1.75 }, hovertemplate: esc(s.name) + '：%{y:.0f}%<extra></extra>' })),
    layout({ xaxis: axis({ type: 'date' }), yaxis: axis({ ticksuffix: '%' }), shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 50, y1: 50, line: { color: tok('--ink-3'), width: 1, dash: 'dot' } }] }));

  // ── 信心分組、波動環境 ──
  const buckets = (vals, k) => { const ord = vals.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]).map(x => x[1]); return Array.from({ length: k }, (_, b) => ord.slice(Math.floor(b * N / k), Math.floor((b + 1) * N / k))); };
  $('confTable').innerHTML = '<thead><tr><th>組別</th>' + S.map(s => '<th class="n">' + esc(s.name) + '</th>').join('') + '</tr></thead><tbody>' +
    [0, 1, 2, 3, 4].map(b => '<tr><td>第 ' + (b + 1) + ' 組' + (b === 4 ? '（最有把握）' : b === 0 ? '（最沒把握）' : '') + '</td>' + S.map(s => { const g = buckets(s.p.map(Math.abs), 5)[b]; return '<td class="n">' + pc(da(s.p, g)) + '<span class="muted"> · ' + sp(mean(g.map(i => Math.abs(s.p[i])))) + '</span></td>'; }).join('') + '</tr>').join('') + '</tbody>';
  const vb = buckets(D.sigma, 3), vNames = ['低波動', '中波動', '高波動'];
  $('regTable').innerHTML = '<thead><tr><th>環境</th><th class="n">EWMA 波動</th>' + S.map(s => '<th class="n">' + esc(s.name) + '</th>').join('') + '<th class="n">永遠猜漲</th></tr></thead><tbody>' +
    vb.map((g, b) => '<tr><td>' + vNames[b] + '</td><td class="n">' + pc(Math.min(...g.map(i => D.sigma[i])), 0) + '–' + pc(Math.max(...g.map(i => D.sigma[i])), 0) + '</td>' + S.map(s => '<td class="n">' + pc(da(s.p, g)) + '</td>').join('') + '<td class="n">' + pc(g.filter(i => up[i]).length / g.length) + '</td></tr>').join('') + '</tbody>';

  // ── 交易回測 ──
  const ret = D.actual.map((a, i) => a / D.prev[i] - 1), COST = 3e-4;
  const vtW = (() => { let w = 0; return D.sigma.map(s => { const want = Math.min(0.2 / s, 1); if (Math.abs(want - w) > 0.1 || w === 0) w = want; return w; }); })();
  const sim = (wf) => { let w = 0; return ret.map((r, i) => { const nw = wf(i), c = Math.abs(nw - w) * COST; w = nw; return nw * r - c; }); };
  const strat = [
    { key: 'bh', name: '買進持有', d: sim(() => 1), color: tok('--base') },
    { key: 'vt', name: '波動目標化（只做多）', d: sim(i => vtW[i]), color: tok('--s3') },
    ...S.flatMap(s => [
      { key: s.key + '_lf', name: s.name + '：猜漲持有、猜跌空手', d: sim(i => s.p[i] > 0 ? 1 : 0), color: COLOR[s.key] },
      { key: s.key + '_ls', name: s.name + '：猜漲做多、猜跌放空', d: sim(i => s.p[i] > 0 ? 1 : -1), color: COLOR[s.key], dash: 'dot' },
      { key: s.key + '_vt', name: s.name + '：猜漲持有 × 波動縮放', d: sim(i => s.p[i] > 0 ? vtW[i] : 0), color: COLOR[s.key], dash: 'dash' },
    ]),
  ];
  const st = (d) => { let e = 1, pk = 1, mdd = 0; d.forEach(x => { e *= 1 + x; pk = Math.max(pk, e); mdd = Math.max(mdd, 1 - e / pk); }); const y = d.length / 252; return { cagr: Math.pow(e, 1 / y) - 1, vol: sd(d) * Math.sqrt(252), sharpe: mean(d) / sd(d) * Math.sqrt(252), mdd, total: e - 1 }; };
  let seed = 20261018; const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const boot = (a, b) => { const sh = (v) => mean(v) / sd(v) * Math.sqrt(252), out = []; for (let k = 0; k < 1500; k++) { const xa = [], ya = []; while (xa.length < N) { const s0 = Math.floor(rnd() * (N - 20)); for (let j = 0; j < 20 && xa.length < N; j++) { xa.push(a[s0 + j]); ya.push(b[s0 + j]); } } out.push(sh(xa) - sh(ya)); } out.sort((p, q) => p - q); return [out[37], out[1462]]; };
  strat.forEach(s => { s.st = st(s.d); if (s.key !== 'bh') s.ci = boot(s.d, strat[0].d); });
  const eq = (d) => { let e = 1; return d.map(x => (e *= 1 + x)); };
  const show = ['bh', 'vt', best.key + '_lf', best.key + '_vt'];
  legend('btLegend', strat.filter(s => show.includes(s.key)).map(s => [s.name, s.color]));
  draw('btChart', strat.filter(s => show.includes(s.key)).map(s => ({ x: D.dates, y: eq(s.d), type: 'scatter', mode: 'lines', line: { color: s.color, width: 2, dash: s.dash || 'solid' }, hovertemplate: esc(s.name) + '：%{y:.2f}<extra></extra>' })),
    layout({ xaxis: axis({ type: 'date' }), yaxis: axis({ type: 'log', title: { text: '淨值（起始 = 1）', font: { size: 11, color: tok('--ink-3') } } }) }));
  $('btTable').innerHTML = '<thead><tr><th>做法</th><th class="n">總報酬</th><th class="n">年化報酬</th><th class="n">年化波動</th><th class="n">夏普</th><th class="n">最大回撤</th><th class="n">夏普差 vs 買進持有</th></tr></thead><tbody>' +
    strat.map(s => '<tr><td>' + esc(s.name) + '</td><td class="n">' + pc(s.st.total) + '</td><td class="n">' + pc(s.st.cagr) + '</td><td class="n">' + pc(s.st.vol) + '</td><td class="n"><strong>' + fx(s.st.sharpe, 2) + '</strong></td><td class="n">' + pc(s.st.mdd) + '</td><td class="n">' +
      (s.ci ? '<span class="' + (s.ci[0] > 0 ? 'good' : s.ci[1] < 0 ? 'bad' : '') + '">' + sg(s.st.sharpe - strat[0].st.sharpe) + '</span><br><span class="muted">' + sg(s.ci[0]) + ' ~ ' + sg(s.ci[1]) + '</span>' : '—') + '</td></tr>').join('') + '</tbody>';

  // ── 今日部位 ──
  if (R.volTarget) {
    const C = R.volTarget.current, SS = R.volTarget.settings;
    $('vtSec').hidden = false;
    $('vtCells').innerHTML = cell('EWMA 預測年化波動 · ' + esc(C.date), pc(C.sigma), 'λ = ' + SS.lambda) +
      cell('建議白銀部位（下個交易日）', pc(C.weight, 0), '目標 ' + pc(SS.target, 0) + ' ÷ 預測波動，上限 ' + SS.maxLev + ' 倍') +
      cell('動作', C.adjust ? '調整到 ' + pc(C.target, 0) : '維持 ' + pc(C.held, 0), C.adjust ? '目前 ' + pc(C.held, 0) : (Math.abs(C.target - C.held) > 1e-6 ? '理論值 ' + pc(C.target, 0) + '，變化未超過 ' + pc(SS.band, 0) : '已在目標部位'));
    $('vtNote').textContent = '部位只依預測波動決定、和漲跌預測無關。用途是控制風險（10 年回測最大回撤由 ' + pc(R.volTarget.stats.bh.maxDD, 0) + ' 降到 ' + pc(R.volTarget.stats.vt.maxDD, 0) + '），不是多賺。';
  }

  // ── 優勢從哪裡來 ──
  const bh = strat[0];
  const lfOf = (sv) => strat.find(x => x.key === sv.key + '_lf');
  $('sepTable').innerHTML = '<thead><tr><th>隔日實際報酬（平均）</th>' + S.map(sv => '<th class="n">' + esc(sv.name) + '</th>').join('') + '</tr></thead><tbody>' +
    ['猜漲的日子', '猜跌的日子'].map((lab, k) => '<tr><td>' + lab + '</td>' + S.map(sv => { const ii = D.dates.map((_, i) => i).filter(i => k === 0 ? sv.p[i] > 0 : !(sv.p[i] > 0)); const m = mean(ii.map(i => ret[i])); return '<td class="n ' + (m >= 0 ? 'good' : 'bad') + '">' + sp(m) + '<span class="muted"> · ' + ii.length + ' 天</span></td>'; }).join('') + '</tr>').join('') + '</tbody>';
  // 拿掉「模型避開的最慘 k 天」後，猜漲持有和買進持有的夏普值
  const shp = (d) => mean(d) / sd(d) * Math.sqrt(252);
  const bestLf = lfOf(best);
  const avoided = D.dates.map((_, i) => i).filter(i => !(best.p[i] > 0)).sort((a, b) => ret[a] - ret[b]);
  const exRows = [0, 1, 3, 5, 10].map(k => { const ex = new Set(avoided.slice(0, k)); const ii = D.dates.map((_, i) => i).filter(i => !ex.has(i)); return [k, shp(ii.map(i => bestLf.d[i])), shp(ii.map(i => bh.d[i]))]; });
  $('exTable').innerHTML = '<thead><tr><th>拿掉模型避開的最慘 k 天</th><th class="n">猜漲持有</th><th class="n">買進持有</th></tr></thead><tbody>' +
    exRows.map(([k, a, c]) => '<tr><td>' + (k ? 'k = ' + k : '全部日子') + '</td><td class="n">' + fx(a, 2) + '</td><td class="n">' + fx(c, 2) + '</td></tr>').join('') + '</tbody>';
  const yrs = [...new Set(D.dates.map(d => d.slice(0, 4)))];
  $('yrTable').innerHTML = '<thead><tr><th>年（' + esc(best.name) + '）</th><th class="n">天數</th><th class="n">方向準確率</th><th class="n">上漲日比例</th><th class="n">猜漲持有 夏普</th><th class="n">買進持有 夏普</th><th class="n">猜漲持有 報酬</th><th class="n">買進持有 報酬</th></tr></thead><tbody>' +
    yrs.map(y => { const ii = D.dates.map((_, i) => i).filter(i => D.dates[i].startsWith(y)); const comp = (d) => ii.reduce((a, i) => a * (1 + d[i]), 1) - 1;
      return '<tr><td class="num">' + y + '</td><td class="n">' + ii.length + '</td><td class="n">' + pc(da(best.p, ii)) + '</td><td class="n">' + pc(ii.filter(i => up[i]).length / ii.length) + '</td><td class="n">' + fx(shp(ii.map(i => bestLf.d[i])), 2) + '</td><td class="n">' + fx(shp(ii.map(i => bh.d[i])), 2) + '</td><td class="n">' + pc(comp(bestLf.d)) + '</td><td class="n">' + pc(comp(bh.d)) + '</td></tr>'; }).join('') + '</tbody>';
  const ex10 = exRows[exRows.length - 1];
  $('srcNote').textContent = '方向準確率只計算「猜對幾次」，不看幅度。模型猜跌的日子平均真的下跌、猜漲的日子平均上漲，而且它避開了不少大跌日；拿掉它避開的最慘 10 天後，夏普值優勢從 ' + fx(exRows[0][1] - exRows[0][2], 2) + ' 縮小到 ' + fx(ex10[1] - ex10[2], 2) + '——優勢有相當部分集中在少數大跌日。';

  // ── 建議 ──
  const lf = bestLf, bh2 = bh, other = S.find(sv => sv.key !== best.key), otherLf = lfOf(other), lfvt = strat.find(x => x.key === best.key + '_vt');
  const recs = [
    '<strong>方向準確率沒有勝過「永遠猜漲」，但漲跌幅度有分辨力。</strong>' + esc(best.name) + ' 的準確率 ' + pc(best.acc) + '（信賴區間 ' + pc(best.lo) + '–' + pc(best.hi) + '），和永遠猜漲的 ' + pc(alwaysUp) + ' 一樣；但它猜跌的日子平均報酬為負、猜漲的日子為正，所以照它進出的回測夏普值 ' + fx(lf.st.sharpe, 2) + '，高於買進持有的 ' + fx(bh2.st.sharpe, 2) + '（差距信賴區間 ' + sg(lf.ci[0]) + ' ~ ' + sg(lf.ci[1]) + (lf.ci[0] > 0 ? '，顯著' : '') + '）。',
    '<strong>近 3 年訓練反而比較差。</strong>' + esc(other.name) + ' 準確率 ' + pc(other.acc) + '、猜漲持有夏普值 ' + fx(otherLf.st.sharpe, 2) + '。2024 年後行情雖然不同，丟掉 2023 年以前的資料讓模型少了經歷過的大跌與高波動樣本，沒有幫助。',
    '<strong>要保守看待。</strong>優勢有相當部分來自避開少數大跌日（拿掉最慘 10 天後夏普差剩 ' + fx(ex10[1] - ex10[2], 2) + '）；評估只有約 2.8 年；而且是在 2 種訓練視窗、多種交易方式中挑出最好的，有選擇偏誤。' + (() => { const y = yrs[yrs.length - 1], ii = D.dates.map((_, i) => i).filter(i => D.dates[i].startsWith(y)); return y + ' 年至今猜漲持有的夏普值只有 ' + fx(shp(ii.map(i => bestLf.d[i])), 2) + '（買進持有 ' + fx(shp(ii.map(i => bh.d[i])), 2) + '），表現比前兩年弱。'; })(),
    '<strong>建議用法：</strong>以「' + esc(lfvt.name) + '」作為候選規則（回測夏普 ' + fx(lfvt.st.sharpe, 2) + '、最大回撤 ' + pc(lfvt.st.mdd) + '），先模擬追蹤一段時間（例如 3 個月）確認樣本外仍有效，再考慮實際使用；部位大小仍以下方的波動目標化為上限。',
  ];
  $('recs').innerHTML = recs.map(r => '<li>' + r + '</li>').join('');

  $('setup').innerHTML = [
    ['預測目標', '隔日對數報酬（每個交易日一個樣本，不重疊）；方向取正負號，價格 = 今天收盤 × e^預測報酬'],
    ['評估期間', esc(R.evalFrom) + ' 起至 ' + esc(F.lastDate) + '，' + R.folds.length + ' 季、' + N + ' 個交易日'],
    ['滾動重訓', '每季第一天以前的資料訓練（最後 15% 當驗證、早停），預測整季；不會用到該季以後的任何資料'],
    ['訓練視窗', V.map(v => esc(v.name) + '：' + esc(v.desc)).join('；')],
    ['模型', esc(R.config.algorithm) + '，回看 ' + R.config.lookback + ' 天，' + R.config.layers + ' 個單元，Huber loss；每個設定重訓 ' + R.config.repeats + ' 次取平均'],
    ['特徵', R.config.features.length + ' 個：原有變數 + 不確定性（EPU、GPR、GPR Threats、信用利差、金融壓力指數）'],
    ['明日預測', '用到最新一天為止的全部資料（依各自的視窗）重訓 ' + R.config.repeats + ' 次的平均'],
    ['耗時', fx(R.durationSec / 60, 0) + ' 分鐘（4 核心平行）'],
  ].map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
})();
</script>`;

const full = `<!doctype html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)} body{margin:0} [hidden]{display:none!important}</style>
</head>
<body>
${body}
</body>
</html>
`;
writeFileSync(path.join(dir, 'daily.html'), full);
if (fragmentOut) writeFileSync(path.resolve(fragmentOut), body);
console.log('已產生 ' + path.join(path.relative(process.cwd(), dir) || '.', 'daily.html') + (fragmentOut ? '，以及 ' + fragmentOut : ''));

#!/usr/bin/env node
// 把 vol.mjs 產生的 vol-compare.json 做成網頁報告
//
//   node vol-report.mjs [輸出目錄=output]            → <目錄>/vol-compare.html
//   node vol-report.mjs [目錄] --fragment 檔案.html   → 不含 <html>/<head> 外殼的版本（發布成 Artifact 用）
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragIdx = args.indexOf('--fragment');
const fragmentOut = fragIdx >= 0 ? args[fragIdx + 1] : null;
const dir = path.resolve(HERE, args.find((a, i) => !a.startsWith('--') && !(fragIdx >= 0 && i === fragIdx + 1)) || 'output');
const R = JSON.parse(readFileSync(path.join(dir, 'vol-compare.json'), 'utf8'));
// 上一輪方向實驗的結果（對照用，可缺）
try {
  const c = JSON.parse(readFileSync(path.join(dir, 'feature-confirm.json'), 'utf8'));
  R.direction = c.summary.map(s => ({ name: s.name, DA: s.avgDA, edge: s.avgEdge }));
} catch (_) { /* 沒有方向實驗 */ }
const dataJson = JSON.stringify(R).replace(/<\//g, '<\\/');
const PLOTLY = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.24.1/plotly.min.js';

const body = `<title>白銀 波動率預測</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* 版面：單欄閱讀流。先回答問題（結論 + 三種目標的總表），再給證據（各期間散點、雜訊對比、單邊預測），最後是實驗設定。
   色彩沿用 Silver AI Trader 的冷調金屬語彙；模型系列用已驗證類別色的固定順序（藍、橘、綠、黃），實際波動用主要墨色，基準線用中性灰。 */
:root {
  --ground: #EEF2F4; --surface: #FFFFFF; --surface-2: #E4EAEE; --edge: #CDD6DC;
  --ink: #0F1A21; --ink-2: #3A4A55; --ink-3: #66767F;
  --up: #1F8A3A; --down: #C8461F;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --base: #8497A3; --grid: rgba(15,26,33,.08);
  --font-body: "IBM Plex Sans TC", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --down: #F0603A;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --base: #5B6C77; --grid: rgba(237,244,248,.08);
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --down: #F0603A;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --base: #5B6C77; --grid: rgba(237,244,248,.08);
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
@media (max-width: 480px) { .chart { height: 320px; } h1 { font-size: 1.45rem; } }
</style>
<div class="page">
  <header>
    <div class="eyebrow">Silver AI Trader · 波動率預測實驗</div>
    <h1>漲跌猜不準，那波動大小呢？</h1>
    <p class="muted" id="sub"></p>
  </header>

  <div class="verdict" id="verdict"></div>

  <div class="stats" id="stats"></div>

  <section aria-labelledby="sum-h">
    <h2 id="sum-h">各模型的總成績（五個期間平均）</h2>
    <div class="panel table-wrap"><table id="sumTable"></table></div>
    <p class="muted">QLIKE 越低越好，是評估波動率預測最常用的損失（Patton, 2011），對「低估波動」懲罰較重。RMSE 是年化波動率的誤差（百分點）。R² 以訓練期平均波動為基準，大於 0 代表比「一律猜平均」好。「升降」是預測未來 5 日波動比過去 5 日高或低的準確率。</p>
  </section>

  <section aria-labelledby="rel-h">
    <h2 id="rel-h">相對 HAR 的 QLIKE（各期間）</h2>
    <p class="muted">每格是該模型的 QLIKE 除以同期 HAR 的 QLIKE；小於 1（綠色）代表比 HAR 好。</p>
    <div class="panel table-wrap"><table id="relTable"></table></div>
  </section>

  <section aria-labelledby="path-h">
    <h2 id="path-h">最終測試段：實際波動與預測</h2>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="pathLegend"></div>
      <div id="pathChart" class="chart" role="img" aria-label="最終測試段的實際 5 日已實現波動率與各模型預測"></div>
      <p class="muted" id="pathNote"></p>
    </div>
  </section>

  <section aria-labelledby="dir-h" id="dirSec" hidden>
    <h2 id="dir-h">對照：先前的漲跌方向實驗</h2>
    <div class="panel table-wrap"><table id="dirTable"></table></div>
  </section>

  <section aria-labelledby="rec-h">
    <h2 id="rec-h">結論與建議</h2>
    <ul class="recs" id="recs"></ul>
  </section>

  <section aria-labelledby="set-h">
    <h2 id="set-h">實驗設定</h2>
    <div class="panel cols">
      <dl id="setup"></dl>
      <dl id="models"></dl>
    </div>
  </section>

  <section aria-labelledby="ref-h">
    <h2 id="ref-h">參考文獻</h2>
    <ul class="recs" style="font-size:.875rem">
      <li>Corsi, F. (2009). A simple approximate long-memory model of realized volatility. Journal of Financial Econometrics, 7(2), 174–196.（HAR 模型）</li>
      <li>Patton, A. J. (2011). Volatility forecast comparison using imperfect volatility proxies. Journal of Econometrics, 160(1), 246–256.（QLIKE）</li>
      <li>J.P. Morgan / Reuters (1996). RiskMetrics — Technical Document.（EWMA，λ = 0.94）</li>
      <li>Baker, Bloom & Davis (2016)；Caldara & Iacoviello (2022)；Baur & Smales (2020)。（HAR-X 中的不確定性指數）</li>
    </ul>
  </section>

  <footer>本頁由 cloud/vol-report.mjs 依 vol-compare.json 產生，僅供研究參考，不構成投資建議。</footer>
</div>

<script type="application/json" id="data">${dataJson}</script>
<script src="${PLOTLY}" crossorigin="anonymous"></script>
<script>
(() => {
  const R = JSON.parse(document.getElementById('data').textContent);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fx = (v, d = 1) => Number.isFinite(v) ? v.toFixed(d) : '—';
  const pct = (v) => Number.isFinite(v) ? (v >= 0 ? '+' : '') + (v * 100).toFixed(0) + '%' : '—';
  const css = getComputedStyle(document.documentElement);
  const tok = (n) => css.getPropertyValue(n).trim();
  const P = R.periods, S = R.summary;
  const by = Object.fromEntries(S.map(s => [s.key, s]));
  const row = (m, pi) => R.rows.find(r => r.model === m && r.period === pi);
  const har = by.har, harx = by.harx;
  // 主要比較：計量基準 + LSTM 集成（集成是重訓預測的平均，比單次穩定）
  const main = S.filter(s => s.kind !== 'lstm');
  const ranked = [...main].sort((a, b) => a.QLIKE - b.QLIKE);
  const best = ranked[0];
  const lstmBest = [...S.filter(s => s.kind === 'ensemble')].sort((a, b) => a.QLIKE - b.QLIKE)[0];

  $('sub').textContent = R.dataRange.from + ' ~ ' + R.dataRange.to + '，預測白銀未來 ' + R.horizon + ' 個交易日的已實現波動率（年化），' +
    P.length + ' 個評估期間；LSTM 每組每期重訓 ' + R.repeats + ' 次。';

  // ── 結論 ──
  const relHar = (s) => s.QLIKE / har.QLIKE - 1;
  const uncGainLin = relHar(harx);
  const lb = by.lstm_base_ens, lu = by.lstm_unc_ens;
  const uncGainLstm = lb && lu ? lu.QLIKE / lb.QLIKE - 1 : NaN;
  $('verdict').innerHTML =
    '<p><strong>結論：波動率可以預測，而且最好的是「' + esc(best.name) + '」。</strong>它的 QLIKE 比 HAR ' + pct(relHar(best)) +
    '，R² 為 <span class="num">' + fx(best.R2, 2) + '</span>，在 ' + best.beatsHar + '/' + P.length + ' 個期間勝過 HAR。和漲跌方向不同，波動率有明顯的持續性與均值回歸，簡單模型就能抓到大部分。</p>' +
    '<p>加入隱含波動率與不確定性指數：線性 HAR-X 比 HAR ' + pct(uncGainLin) + (Number.isFinite(uncGainLstm) ? '；LSTM 由原有變數加入不確定性後 ' + pct(uncGainLstm) : '') + '（QLIKE，負值代表變好）。' +
    '表現最好的 LSTM（' + esc(lstmBest.name) + '）QLIKE 比 HAR ' + pct(relHar(lstmBest)) + '、比 HAR-X ' + pct(lstmBest.QLIKE / harx.QLIKE - 1) + '。</p>';

  // ── 重點數字 ──
  const tiles = [
    [best.name, 'QLIKE 最低的模型'],
    [fx(best.R2, 2), best.name + ' 的 R²（> 0 = 比猜平均好）'],
    [pct(uncGainLin), 'HAR 加入不確定性後的 QLIKE 變化'],
    [fx(best.dirAcc, 0) + '%', best.name + ' 預測波動升降的準確率'],
  ];
  $('stats').innerHTML = tiles.map(([v, l]) => '<div class="stat"><div class="v" style="font-size:' + (String(v).length > 8 ? '1.05rem' : '1.5rem') + '">' + esc(v) + '</div><div class="l">' + esc(l) + '</div></div>').join('');

  // ── 總表 ──
  const kindName = { baseline: '計量基準', lstm: 'LSTM 單次平均', ensemble: 'LSTM 集成' };
  const order = [...S].sort((a, b) => a.QLIKE - b.QLIKE);
  $('sumTable').innerHTML = '<thead><tr><th>#</th><th>模型</th><th class="n">QLIKE</th><th class="n">比 HAR</th><th class="n">RMSE（百分點）</th><th class="n">R²</th><th class="n">升降準確率</th><th class="n">勝過 HAR</th></tr></thead><tbody>' +
    order.map((s, i) => '<tr class="' + (s.key === 'har' ? 'hl' : '') + '"><td class="num">' + (i + 1) + '</td><td>' + esc(s.name) + ' <span class="kind">' + kindName[s.kind] + '</span></td><td class="n">' + fx(s.QLIKE, 3) + '</td><td class="n ' + (s.key === 'har' ? '' : relHar(s) < 0 ? 'good' : 'bad') + '">' + (s.key === 'har' ? '基準' : pct(relHar(s))) +
      '</td><td class="n">' + fx(s.RMSE, 2) + '</td><td class="n ' + (s.R2 > 0 ? 'good' : 'bad') + '">' + fx(s.R2, 3) + '</td><td class="n">' + fx(s.dirAcc) + '%</td><td class="n">' + (s.key === 'har' ? '—' : s.beatsHar + ' / ' + P.length) + '</td></tr>').join('') + '</tbody>';

  // ── 相對 HAR（各期間）──
  const relModels = order.filter(s => s.kind !== 'lstm' && s.key !== 'har');
  $('relTable').innerHTML = '<thead><tr><th>模型</th>' + P.map(p => '<th class="n">' + esc(p.label) + '<br><span class="muted num">' + esc(p.from.slice(0, 7)) + '~' + esc(p.to.slice(0, 7)) + '</span></th>').join('') + '<th class="n">平均</th></tr></thead><tbody>' +
    relModels.map(s => '<tr><td>' + esc(s.name) + '</td>' + P.map((_, pi) => { const r = row(s.key, pi), h = row('har', pi); const v = r && h ? r.QLIKE / h.QLIKE : NaN; return '<td class="n ' + (v < 1 ? 'good' : 'bad') + '">' + fx(v, 2) + '</td>'; }).join('') +
      '<td class="n ' + (s.QLIKE < har.QLIKE ? 'good' : 'bad') + '"><strong>' + fx(s.QLIKE / har.QLIKE, 2) + '</strong></td></tr>').join('') + '</tbody>';

  // ── 最終測試段走勢 ──
  const F = R.finalPath;
  const lstmKey = lstmBest.key.replace(/_ens$/, '');
  const series = [
    { name: '實際', y: F.actual, color: tok('--ink'), width: 1.5, dash: 'solid' },
    { name: 'HAR', y: F.har, color: tok('--s1'), width: 2 },
    { name: 'HAR-X', y: F.harx, color: tok('--s2'), width: 2 },
    { name: lstmBest.name, y: F.lstm[lstmKey], color: tok('--s3'), width: 2 },
    { name: '隨機漫步', y: F.rw, color: tok('--s4'), width: 1.5 },
  ].filter(s => s.y);
  $('pathLegend').innerHTML = series.map(s => '<span class="key"><i style="background:' + s.color + '"></i>' + esc(s.name) + '</span>').join('');
  const font = { family: tok('--font-body'), size: 12, color: tok('--ink-2') };
  const axis = (extra = {}) => ({ gridcolor: tok('--grid'), zeroline: false, linecolor: tok('--edge'), tickfont: { family: tok('--font-mono'), size: 11, color: tok('--ink-3') }, ...extra });
  if (window.Plotly) {
    Plotly.newPlot('pathChart', series.map(s => ({ x: F.dates, y: s.y, type: 'scatter', mode: 'lines', name: s.name, line: { color: s.color, width: s.width },
      opacity: s.name === '隨機漫步' ? 0.55 : 1, hovertemplate: esc(s.name) + '：%{y:.1f}%<extra></extra>' })), {
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font, showlegend: false, hovermode: 'x unified',
      margin: { l: 52, r: 12, t: 8, b: 40 },
      hoverlabel: { bgcolor: tok('--surface'), bordercolor: tok('--edge'), font: { family: tok('--font-mono'), color: tok('--ink') } },
      xaxis: axis({ type: 'date' }), yaxis: axis({ ticksuffix: '%', title: { text: '年化波動率', font: { size: 11, color: tok('--ink-3') } } }),
    }, { displayModeBar: false, responsive: true });
  } else $('pathChart').innerHTML = '<p class="muted" style="padding:24px">圖表函式庫載入失敗（需連線到 cdn.jsdelivr.net）。</p>';
  $('pathNote').textContent = '日期是預測當天，實際值是之後 ' + R.horizon + ' 個交易日的已實現波動率。單日報酬算出的已實現波動雜訊很大，預測線較平滑是正常的。';

  // ── 對照方向實驗 ──
  if (R.direction) {
    $('dirSec').hidden = false;
    $('dirTable').innerHTML = '<thead><tr><th>預測目標</th><th>模型</th><th>成績</th><th>和簡單基準比</th></tr></thead><tbody>' +
      R.direction.map(d => '<tr><td>5 日漲跌方向</td><td>' + esc(d.name) + '</td><td class="num">準確率 ' + fx(d.DA) + '%</td><td class="num bad">比「一直猜多數方向」' + (d.edge >= 0 ? '+' : '') + fx(d.edge) + ' 點</td></tr>').join('') +
      '<tr><td>5 日波動率</td><td>' + esc(best.name) + '</td><td class="num">R² ' + fx(best.R2, 2) + '</td><td class="num ' + (best.R2 > 0 ? 'good' : 'bad') + '">比「一律猜平均」好 ' + fx(best.R2 * 100, 0) + '% 的誤差</td></tr>' +
      '<tr><td>5 日波動率</td><td>' + esc(lstmBest.name) + '</td><td class="num">R² ' + fx(lstmBest.R2, 2) + '</td><td class="num ' + (lstmBest.R2 > 0 ? 'good' : 'bad') + '">QLIKE 比 HAR ' + pct(relHar(lstmBest)) + '</td></tr></tbody>';
  }

  // ── 建議 ──
  const recs = [];
  recs.push('<strong>改用波動率當預測目標是對的方向。</strong>' + esc(best.name) + ' 的 R² 為 ' + fx(best.R2, 2) + '，代表未來 5 日波動約有 ' + fx(best.R2 * 100, 0) + '% 的變化可以事先預測（只用日報酬算的已實現波動雜訊很大，這已是不錯的水準）；漲跌方向的實驗則一直贏不了最簡單的猜法。');
  if (lstmBest.QLIKE < Math.min(har.QLIKE, harx.QLIKE)) recs.push('<strong>LSTM 有加分。</strong>' + esc(lstmBest.name) + ' 勝過 HAR 與 HAR-X，可以作為主模型；建議再以更多次重訓確認。');
  else recs.push('<strong>LSTM 沒有贏過線性模型。</strong>' + esc(lstmBest.name) + ' 的 QLIKE 比 ' + (harx.QLIKE < har.QLIKE ? 'HAR-X' : 'HAR') + ' 差，這和文獻一致：波動率的結構很簡單，線性 HAR 類模型很難被打敗。實務上用 ' + esc(best.name) + ' 即可，運算快、可解釋。');
  const xWins = P.map((p, pi) => ({ p, w: row('harx', pi).QLIKE < row('har', pi).QLIKE }));
  if (uncGainLin < 0) recs.push('<strong>隱含波動率與不確定性指數有幫助，但集中在危機期間。</strong>HAR-X 的平均 QLIKE 比 HAR ' + pct(uncGainLin) + '，但只在 ' +
    xWins.filter(x => x.w).map(x => esc(x.p.label) + '（' + esc(x.p.from.slice(0, 7)) + '~' + esc(x.p.to.slice(0, 7)) + '）').join('、') + ' 較好，其餘 ' + xWins.filter(x => !x.w).length + ' 期反而稍差。它的價值在於波動突然暴增時（例如 2020 年疫情）較早拉高預測，平時則和 HAR 差不多。');
  else recs.push('<strong>不確定性指數對波動的幫助不明顯。</strong>HAR-X 沒有比 HAR 好（' + pct(uncGainLin) + '）。');
  recs.push('<strong>可以怎麼用：</strong>依預測波動調整部位大小（波動目標化：預測波動高時減碼），或作為選擇權、停損距離的參考。這些用途不需要猜對方向。');
  $('recs').innerHTML = recs.map(r => '<li>' + r + '</li>').join('');

  // ── 設定 ──
  const dl = (pairs) => pairs.map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
  $('setup').innerHTML = dl([
    ['資料', esc(R.dataRange.from) + ' ~ ' + esc(R.dataRange.to) + '（' + R.dataRange.days + ' 個交易日）'],
    ['目標', '未來 ' + R.horizon + ' 日日報酬平方的平均，開根號後年化（對數）'],
    ['評估', (P.length - 1) + ' 段 walk-forward + 最終測試段；每段只用之前的資料估計，訓練段尾端去掉 ' + (R.horizon - 1) + ' 筆重疊樣本'],
    ['LSTM', esc(R.config.algorithm) + '，回看 ' + R.config.lookback + ' 天，' + R.config.layers + ' 個單元，Huber loss，驗證段早停；特徵只用訓練段做正規化'],
    ['偏誤修正', '對數預測轉回波動率時，以驗證段（HAR 為訓練段）殘差變異數做對數常態修正'],
    ['HAR-X 變數', esc(R.harxKeys.join('、'))],
    ['耗時', fx(R.durationSec / 60, 0) + ' 分鐘（4 核心平行）'],
  ]);
  $('models').innerHTML = dl(R.models.filter(m => m.kind !== 'ensemble').map(m => [esc(m.name), esc(m.desc || (m.kind === 'baseline' ? '計量基準，每期用訓練段估計' : '')) + (m.features ? '（' + m.features.length + ' 個特徵）' : '')]));
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
writeFileSync(path.join(dir, 'vol-compare.html'), full);
if (fragmentOut) writeFileSync(path.resolve(fragmentOut), body);
console.log('已產生 ' + path.join(path.relative(process.cwd(), dir) || '.', 'vol-compare.html') + (fragmentOut ? '，以及 ' + fragmentOut : ''));

#!/usr/bin/env node
// 把 voltarget.mjs 產生的 voltarget.json 做成網頁報告
//
//   node voltarget-report.mjs [輸出目錄=output]            → <目錄>/voltarget.html
//   node voltarget-report.mjs [目錄] --fragment 檔案.html   → 不含 <html>/<head> 外殼的版本（發布成 Artifact 用）
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragIdx = args.indexOf('--fragment');
const fragmentOut = fragIdx >= 0 ? args[fragIdx + 1] : null;
const dir = path.resolve(HERE, args.find((a, i) => !a.startsWith('--') && !(fragIdx >= 0 && i === fragIdx + 1)) || 'output');
const R = JSON.parse(readFileSync(path.join(dir, 'voltarget.json'), 'utf8'));
const dataJson = JSON.stringify(R).replace(/<\//g, '<\\/');
const PLOTLY = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.24.1/plotly.min.js';

const body = `<title>白銀 波動目標化回測</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* 版面：單欄閱讀流。先回答問題（結論 + 三種目標的總表），再給證據（各期間散點、雜訊對比、單邊預測），最後是實驗設定。
   色彩沿用 Silver AI Trader 的冷調金屬語彙；策略／買進持有／同風險買進持有依序用已驗證類別色（藍、橘、綠），基準線用中性灰。 */
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
.seg { display:inline-flex; border:1px solid var(--edge); border-radius:8px; overflow:hidden; }
.seg button { font: inherit; font-size:.85rem; padding:6px 12px; border:0; background:var(--surface); color:var(--ink-2); cursor:pointer; }
.seg button[aria-pressed="true"] { background:var(--surface-2); color:var(--ink); font-weight:600; }
.chart.mini { height: 220px; }
@media (max-width: 480px) { .chart { height: 320px; } h1 { font-size: 1.45rem; } }
</style>
<div class="page">
  <header>
    <div class="eyebrow">Silver AI Trader · 波動目標化回測</div>
    <h1>依預測波動調整白銀部位，風險和報酬會怎麼變？</h1>
    <p class="muted" id="sub"></p>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="muted">標的</span><div class="seg" id="symSeg" role="group" aria-label="選擇標的"></div></div>
  </header>

  <div class="verdict" id="verdict"></div>
  <div class="stats" id="stats"></div>

  <section aria-labelledby="eq-h">
    <h2 id="eq-h">淨值（對數刻度）</h2>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="eqLegend"></div>
      <div id="eqChart" class="chart" role="img" aria-label="三種做法的淨值走勢"></div>
    </div>
  </section>

  <div class="cols">
    <section aria-labelledby="dd-h">
      <h2 id="dd-h">回撤</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div class="legend" id="ddLegend"></div>
        <div id="ddChart" class="chart short" role="img" aria-label="三種做法的回撤"></div>
      </div>
    </section>
    <section aria-labelledby="rv-h">
      <h2 id="rv-h">滾動 63 日實現波動</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div class="legend" id="rvLegend"></div>
        <div id="rvChart" class="chart short" role="img" aria-label="策略與買進持有的滾動波動"></div>
        <p class="muted" id="rvNote"></p>
      </div>
    </section>
  </div>

  <section aria-labelledby="w-h">
    <h2 id="w-h">部位怎麼調整：EWMA 預測波動與持有比例</h2>
    <div class="panel" style="display:grid;gap:6px">
      <div id="sigChart" class="chart mini" role="img" aria-label="EWMA 預測的年化波動"></div>
      <div id="wChart" class="chart mini" role="img" aria-label="策略的白銀持有比例"></div>
      <p class="muted" id="wNote"></p>
    </div>
  </section>

  <section aria-labelledby="var-h">
    <h2 id="var-h">各種做法比較</h2>
    <div class="panel table-wrap"><table id="varTable"></table></div>
    <p class="muted">「同風險買進持有」是把買進持有固定縮小到和策略相同的實現波動，用來檢驗：策略的好處是來自「擇時」，還是只是「持有比較少」。注意這個比例是事後才知道的。</p>
  </section>

  <div class="cols">
    <section aria-labelledby="tg-h">
      <h2 id="tg-h">目標波動的敏感度</h2>
      <div class="panel table-wrap"><table id="tgTable"></table></div>
    </section>
    <section aria-labelledby="yr-h">
      <h2 id="yr-h">逐年報酬</h2>
      <div class="panel table-wrap"><table id="yrTable"></table></div>
    </section>
  </div>

  <section aria-labelledby="rec-h">
    <h2 id="rec-h">結論與建議</h2>
    <ul class="recs" id="recs"></ul>
  </section>

  <section aria-labelledby="set-h">
    <h2 id="set-h">回測設定</h2>
    <div class="panel"><dl id="setup"></dl></div>
  </section>

  <section aria-labelledby="ref-h">
    <h2 id="ref-h">參考文獻</h2>
    <ul class="recs" style="font-size:.875rem">
      <li>Moreira, A., & Muir, T. (2017). Volatility-managed portfolios. Journal of Finance, 72(4), 1611–1644.</li>
      <li>Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M., & Van Hemert, O. (2018). The impact of volatility targeting. Journal of Portfolio Management, 45(1), 14–33.（波動目標化能穩定風險、降低尾部損失；夏普值改善主要出現在股票與信用資產，商品與匯率不明顯）</li>
      <li>J.P. Morgan / Reuters (1996). RiskMetrics — Technical Document.（EWMA，λ = 0.94）</li>
    </ul>
  </section>

  <footer>本頁由 cloud/voltarget-report.mjs 依 voltarget.json 產生。回測不代表未來績效，僅供研究參考，不構成投資建議。</footer>
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
  const sp = (v, d = 1) => Number.isFinite(v) ? (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%' : '—';
  const css = getComputedStyle(document.documentElement);
  const tok = (n) => css.getPropertyValue(n).trim();
  const ST = R.settings;
  let sym = 'SI=F';
  try { sym = localStorage.getItem('vt-sym') || sym; } catch (_) {}
  if (!R.underlyings[sym]) sym = 'SI=F';

  $('symSeg').innerHTML = Object.entries(R.underlyings).map(([k, u]) => '<button type="button" data-k="' + esc(k) + '">' + esc(u.name) + '</button>').join('');
  $('symSeg').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; sym = b.dataset.k; try { localStorage.setItem('vt-sym', sym); } catch (_) {} render(); });

  const font = { family: tok('--font-body'), size: 12, color: tok('--ink-2') };
  const axis = (extra = {}) => ({ gridcolor: tok('--grid'), zeroline: false, linecolor: tok('--edge'), tickfont: { family: tok('--font-mono'), size: 11, color: tok('--ink-3') }, ...extra });
  const layout = (extra = {}) => ({ paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font, showlegend: false, hovermode: 'x unified',
    margin: { l: 56, r: 12, t: 8, b: 36 }, hoverlabel: { bgcolor: tok('--surface'), bordercolor: tok('--edge'), font: { family: tok('--font-mono'), color: tok('--ink') } },
    xaxis: axis({ type: 'date' }), yaxis: axis(), ...extra });
  const cfg = { displayModeBar: false, responsive: true };
  const draw = (id, d, l) => window.Plotly ? Plotly.react(id, d, l, cfg) : ($(id).innerHTML = '<p class="muted" style="padding:24px">圖表函式庫載入失敗（需連線到 cdn.jsdelivr.net）。</p>');
  const legend = (id, items) => { $(id).innerHTML = items.map(([n, c, dash]) => '<span class="key"><i style="background:' + (dash ? 'transparent;border-top:2px dashed ' + c + ';border-radius:0;height:0;width:16px' : c) + '"></i>' + esc(n) + '</span>').join(''); };

  const render = () => {
    [...$('symSeg').children].forEach(b => b.setAttribute('aria-pressed', String(b.dataset.k === sym)));
    const U = R.underlyings[sym], V = Object.fromEntries(U.variants.map(v => [v.key, v])), S = U.series;
    const vt = V.vt, bh = V.bh, bhm = V.bh_m, T = U.sharpeTest.vsBh;
    const C = { vt: tok('--s1'), bh: tok('--s2'), bhm: tok('--s3') };
    const nameVt = '波動目標 ' + (ST.target * 100) + '%', nameBhm = '同風險買進持有（' + fx(U.volMatchK * 100, 0) + '%）';

    $('sub').textContent = R.dataRange.from + ' ~ ' + R.dataRange.to + '，每天收盤後依 EWMA 預測波動決定隔天的白銀部位：部位 = min(目標波動 ' + (ST.target * 100) + '% ÷ 預測波動, ' + ST.maxLev + ' 倍)，換倉成本 ' + ST.costBps + ' bps。';

    const sharpeSame = T.lo < 0 && T.hi > 0;
    $('verdict').innerHTML =
      '<p><strong>結論：波動目標化成功控制了風險，但沒有提高報酬風險比。</strong>年化波動從 <span class="num">' + pc(bh.vol) + '</span> 降到 <span class="num">' + pc(vt.vol) + '</span>，最大回撤從 <span class="num">' + pc(bh.maxDD) + '</span> 降到 <span class="num">' + pc(vt.maxDD) + '</span>，最慘單日從 <span class="num">' + pc(bh.worstDay) + '</span> 縮小到 <span class="num">' + pc(vt.worstDay) + '</span>；代價是年化報酬從 ' + pc(bh.cagr) + ' 降到 ' + pc(vt.cagr) + '。</p>' +
      '<p>夏普值 ' + fx(vt.sharpe, 2) + ' 對 ' + fx(bh.sharpe, 2) + '，差距 ' + (T.obs >= 0 ? '+' : '') + fx(T.obs, 2) + '（95% 信賴區間 ' + fx(T.lo, 2) + ' ~ ' + fx(T.hi, 2) + (sharpeSame ? '，和 0 沒有差別' : '') + '）。和「固定持有 ' + fx(U.volMatchK * 100, 0) + '%」相比，最大回撤 ' + pc(bhm.maxDD) + '、夏普值 ' + fx(bhm.sharpe, 2) + ' 都差不多——<strong>它的好處是讓風險穩定、避開極端單日，而不是擇時賺更多</strong>。</p>';

    const tiles = [
      [pc(vt.maxDD, 0) + ' vs ' + pc(bh.maxDD, 0), '最大回撤（策略 vs 買進持有）'],
      [pc(vt.worstDay, 1) + ' vs ' + pc(bh.worstDay, 1), '最慘單日'],
      [fx(vt.rollVolMin * 100, 0) + '–' + fx(vt.rollVolMax * 100, 0) + '%', '策略的滾動波動範圍（買進持有 ' + fx(bh.rollVolMin * 100, 0) + '–' + fx(bh.rollVolMax * 100, 0) + '%）'],
      [fx(vt.sharpe, 2) + ' vs ' + fx(bh.sharpe, 2), '夏普值（策略 vs 買進持有）'],
    ];
    $('stats').innerHTML = tiles.map(([v, l]) => '<div class="stat"><div class="v" style="font-size:1.2rem">' + esc(v) + '</div><div class="l">' + esc(l) + '</div></div>').join('');

    const L3 = [[nameVt, C.vt], ['買進持有', C.bh], [nameBhm, C.bhm]];
    legend('eqLegend', L3); legend('ddLegend', L3);
    const line = (y, name, c, extra = {}) => ({ x: S.dates, y, type: 'scatter', mode: 'lines', name, line: { color: c, width: 2 }, ...extra });
    draw('eqChart', [line(S.vt, nameVt, C.vt, { hovertemplate: esc(nameVt) + '：%{y:.2f}<extra></extra>' }), line(S.bh, '買進持有', C.bh, { hovertemplate: '買進持有：%{y:.2f}<extra></extra>' }), line(S.bhm, nameBhm, C.bhm, { hovertemplate: '同風險：%{y:.2f}<extra></extra>' })],
      layout({ yaxis: axis({ type: 'log', title: { text: '淨值（起始 = 1）', font: { size: 11, color: tok('--ink-3') } } }) }));
    draw('ddChart', [line(S.ddVt, nameVt, C.vt, { hovertemplate: esc(nameVt) + '：%{y:.1%}<extra></extra>' }), line(S.ddBh, '買進持有', C.bh, { hovertemplate: '買進持有：%{y:.1%}<extra></extra>' }), line(S.ddBhm, nameBhm, C.bhm, { hovertemplate: '同風險：%{y:.1%}<extra></extra>' })],
      layout({ yaxis: axis({ tickformat: '.0%' }) }));
    legend('rvLegend', [[nameVt, C.vt], ['買進持有', C.bh], ['目標 ' + (ST.target * 100) + '%', tok('--ink-3'), true]]);
    draw('rvChart', [line(S.rollVt, nameVt, C.vt, { hovertemplate: esc(nameVt) + '：%{y:.0f}%<extra></extra>' }), line(S.rollBh, '買進持有', C.bh, { hovertemplate: '買進持有：%{y:.0f}%<extra></extra>' })],
      layout({ yaxis: axis({ ticksuffix: '%' }), shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: ST.target * 100, y1: ST.target * 100, line: { color: tok('--ink-3'), width: 1.5, dash: 'dash' } }] }));
    $('rvNote').textContent = '策略的滾動波動標準差 ' + fx(vt.rollVolSd * 100, 1) + ' 個百分點，買進持有 ' + fx(bh.rollVolSd * 100, 1) + ' 個百分點。槓桿上限 ' + ST.maxLev + ' 倍時，平靜期無法加碼到目標，波動會低於 ' + (ST.target * 100) + '%。';
    draw('sigChart', [{ x: S.dates, y: S.sigma, type: 'scatter', mode: 'lines', line: { color: tok('--s2'), width: 1.5 }, hovertemplate: 'EWMA 預測波動：%{y:.0f}%<extra></extra>' }],
      layout({ margin: { l: 56, r: 12, t: 4, b: 24 }, yaxis: axis({ ticksuffix: '%', title: { text: '預測波動', font: { size: 11, color: tok('--ink-3') } } }) }));
    draw('wChart', [{ x: S.dates, y: S.weight, type: 'scatter', mode: 'lines', fill: 'tozeroy', line: { color: tok('--s1'), width: 1.5 }, fillcolor: tok('--s1') + '33', hovertemplate: '持有比例：%{y:.0%}<extra></extra>' }],
      layout({ margin: { l: 56, r: 12, t: 4, b: 36 }, yaxis: axis({ tickformat: '.0%', range: [0, Math.max(1.05, ...S.weight) ], title: { text: '持有比例', font: { size: 11, color: tok('--ink-3') } } }) }));
    $('wNote').textContent = '上圖是 EWMA 預測的年化波動，下圖是策略持有的白銀比例（平均 ' + pc(vt.avgWeight, 0) + '，最低 ' + pc(vt.minWeight, 0) + '）。預測波動越高，持有越少；兩張圖共用時間軸、各用自己的刻度。';

    $('varTable').innerHTML = '<thead><tr><th>做法</th><th class="n">年化報酬</th><th class="n">年化波動</th><th class="n">夏普</th><th class="n">Sortino</th><th class="n">最大回撤</th><th class="n">最慘月</th><th class="n">最慘日</th><th class="n">平均部位</th><th class="n">年換手</th></tr></thead><tbody>' +
      U.variants.map(v => '<tr class="' + (v.key === 'vt' ? 'hl' : '') + '"><td>' + esc(v.name) + '</td><td class="n">' + pc(v.cagr) + '</td><td class="n">' + pc(v.vol) + '</td><td class="n">' + fx(v.sharpe, 2) + '</td><td class="n">' + fx(v.sortino, 2) + '</td><td class="n">' + pc(v.maxDD) + '</td><td class="n">' + pc(v.worstMonth) + '</td><td class="n">' + pc(v.worstDay) + '</td><td class="n">' + pc(v.avgWeight, 0) + '</td><td class="n">' + fx(v.turnover / (R.dataRange.days / 252), 1) + '×</td></tr>').join('') + '</tbody>';
    $('tgTable').innerHTML = '<thead><tr><th class="n">目標波動</th><th class="n">實現波動</th><th class="n">年化報酬</th><th class="n">夏普</th><th class="n">最大回撤</th><th class="n">平均部位</th></tr></thead><tbody>' +
      U.targets.map(t => '<tr class="' + (t.target === ST.target * 100 ? 'hl' : '') + '"><td class="n">' + t.target + '%</td><td class="n">' + pc(t.vol) + '</td><td class="n">' + pc(t.cagr) + '</td><td class="n">' + fx(t.sharpe, 2) + '</td><td class="n">' + pc(t.maxDD) + '</td><td class="n">' + pc(t.avgWeight, 0) + '</td></tr>').join('') + '</tbody>';
    $('yrTable').innerHTML = '<thead><tr><th>年</th><th class="n">策略</th><th class="n">買進持有</th><th class="n">同風險</th><th class="n">策略波動</th><th class="n">買進持有波動</th><th class="n">平均部位</th></tr></thead><tbody>' +
      U.years.map(y => '<tr><td class="num">' + esc(y.year) + (y.days < 200 ? '<span class="muted">*</span>' : '') + '</td><td class="n ' + (y.vt >= 0 ? 'good' : 'bad') + '">' + sp(y.vt) + '</td><td class="n ' + (y.bh >= 0 ? 'good' : 'bad') + '">' + sp(y.bh) + '</td><td class="n ' + (y.bhm >= 0 ? 'good' : 'bad') + '">' + sp(y.bhm) + '</td><td class="n">' + pc(y.vtVol, 0) + '</td><td class="n">' + pc(y.bhVol, 0) + '</td><td class="n">' + pc(y.avgW, 0) + '</td></tr>').join('') + '</tbody>';

    const c25 = U.costs.find(c => c.costBps === 25);
    const recs = [
      '<strong>適合用來控制風險，不適合期待多賺。</strong>這和文獻一致：Harvey 等人（2018）發現波動目標化對商品的夏普值幫助不大，但能穩定風險、減少極端損失。白銀單日最大跌幅 ' + pc(bh.worstDay) + '，策略只有 ' + pc(vt.worstDay) + '，因為暴跌前波動通常已先升高：' + esc(U.crash.date) + ' 白銀單日 ' + pc(U.crash.ret) + ' 之前，預測波動已經拉高，部位只剩 ' + pc(U.crash.weight, 0) + '，策略當天損失 ' + pc(U.crash.vtRet) + '。',
      '<strong>想要較高報酬就提高目標波動。</strong>目標 ' + U.targets.at(-2).target + '% 時年化報酬 ' + pc(U.targets.at(-2).cagr) + '、最大回撤 ' + pc(U.targets.at(-2).maxDD) + '；目標越高越接近買進持有。夏普值在各目標之間差異不大（' + fx(Math.min(...U.targets.map(t => t.sharpe)), 2) + '–' + fx(Math.max(...U.targets.map(t => t.sharpe)), 2) + '）。',
      '<strong>用「變化超過 10% 才調整」降低交易成本。</strong>換手次數大幅減少，績效反而略好（夏普 ' + fx(V.vt_band.sharpe, 2) + '）。成本拉高到 25 bps 時，每日調整版的夏普值降到 ' + fx(c25.sharpe, 2) + '。',
      '<strong>EWMA 和 22 日歷史波動的結果幾乎一樣</strong>（夏普 ' + fx(vt.sharpe, 2) + ' vs ' + fx(V.vt_22.sharpe, 2) + '）：部位調整對波動預測的精準度不太敏感，簡單的估計就夠用。',
      '<strong>注意：</strong>SI=F 是 Yahoo 的連續近月合約，換月時的價差會混入報酬；SLV 結果（可切換）和期貨一致，表示結論不受影響。回測期間白銀大漲（2025 年 +142%），買進持有的絕對報酬因此偏高。',
    ];
    $('recs').innerHTML = recs.map(r => '<li>' + r + '</li>').join('');
    $('setup').innerHTML = [
      ['期間', esc(R.dataRange.from) + ' ~ ' + esc(R.dataRange.to) + '（' + R.dataRange.days + ' 個交易日，前 ' + ST.warmup + ' 天為 EWMA 暖機）'],
      ['波動預測', 'EWMA，λ = ' + ST.lambda + '（RiskMetrics 標準值，未用資料估計，整段都是樣本外）'],
      ['部位', 'min(目標波動 ÷ 預測波動, ' + ST.maxLev + ' 倍)；t 日收盤決定、t+1 日生效'],
      ['成本', '每次換倉 |Δ部位| × ' + ST.costBps + ' bps（手續費 + 滑價）'],
      ['現金', '未投入部分報酬為 0；期貨報酬本身已是超額報酬，夏普值不扣無風險利率'],
      ['夏普差檢定', '20 日區塊拔靴法，2000 次'],
    ].map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
  };
  render();
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
writeFileSync(path.join(dir, 'voltarget.html'), full);
if (fragmentOut) writeFileSync(path.resolve(fragmentOut), body);
console.log('已產生 ' + path.join(path.relative(process.cwd(), dir) || '.', 'voltarget.html') + (fragmentOut ? '，以及 ' + fragmentOut : ''));

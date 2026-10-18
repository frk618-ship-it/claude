#!/usr/bin/env node
// 把 combo.mjs 產生的 combo.json 做成網頁報告
//
//   node combo-report.mjs [輸出目錄=output]            → <目錄>/combo.html
//   node combo-report.mjs [目錄] --fragment 檔案.html   → 不含 <html>/<head> 外殼的版本（發布成 Artifact 用）
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragIdx = args.indexOf('--fragment');
const fragmentOut = fragIdx >= 0 ? args[fragIdx + 1] : null;
const dir = path.resolve(HERE, args.find((a, i) => !a.startsWith('--') && !(fragIdx >= 0 && i === fragIdx + 1)) || 'output');
const R = JSON.parse(readFileSync(path.join(dir, 'combo.json'), 'utf8'));
const dataJson = JSON.stringify(R).replace(/<\//g, '<\\/');
const PLOTLY = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.24.1/plotly.min.js';

const body = `<title>白銀 部位規則比較</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* 版面：單欄閱讀流。先回答問題（結論 + 三種目標的總表），再給證據（各期間散點、雜訊對比、單邊預測），最後是實驗設定。
   色彩沿用 Silver AI Trader 的冷調金屬語彙；策略依固定順序使用已驗證類別色（藍、橘、綠、黃、洋紅），不隨篩選重新上色，基準線用中性灰。 */
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
@media (max-width: 480px) { .chart { height: 320px; } h1 { font-size: 1.45rem; } }
</style>
<div class="page">
  <header>
    <div class="eyebrow">Silver AI Trader · 部位規則比較</div>
    <h1>把趨勢、下跌風險加進波動目標化，報酬會更好嗎？</h1>
    <p class="muted" id="sub"></p>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="muted">標的</span><div class="seg" id="symSeg" role="group" aria-label="選擇標的"></div></div>
  </header>

  <div class="verdict" id="verdict"></div>
  <div class="stats" id="stats"></div>

  <section aria-labelledby="rr-h">
    <h2 id="rr-h">報酬與風險</h2>
    <div class="panel" style="display:grid;gap:10px">
      <div id="rrChart" class="chart" role="img" aria-label="各策略的年化波動與年化報酬"></div>
      <p class="muted">虛線是和買進持有相同夏普值的連線：點在線上方代表單位風險的報酬比買進持有好，下方代表較差。</p>
    </div>
  </section>

  <section aria-labelledby="tb-h">
    <h2 id="tb-h">各策略績效</h2>
    <div class="panel table-wrap"><table id="mainTable"></table></div>
    <p class="muted">「上漲／下跌捕捉」是買進持有上漲（下跌）的月份裡，策略拿到幾成的漲幅（跌幅）；上漲捕捉高於下跌捕捉才代表「漲時多拿、跌時少賠」。夏普差的區間是 20 日區塊拔靴法的 95% 信賴區間。</p>
  </section>

  <section aria-labelledby="eq-h">
    <h2 id="eq-h">淨值（對數刻度）</h2>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="eqLegend"></div>
      <div id="eqChart" class="chart" role="img" aria-label="主要策略的淨值走勢"></div>
    </div>
  </section>

  <div class="cols">
    <section aria-labelledby="dd-h">
      <h2 id="dd-h">回撤</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div class="legend" id="ddLegend"></div>
        <div id="ddChart" class="chart short" role="img" aria-label="主要策略的回撤"></div>
      </div>
    </section>
    <section aria-labelledby="w-h">
      <h2 id="w-h">白銀價格與持有比例</h2>
      <div class="panel" style="display:grid;gap:6px">
        <div id="pxChart" class="chart mini" role="img" aria-label="白銀價格"></div>
        <div id="wChart" class="chart mini" role="img" aria-label="兩種策略的持有比例"></div>
        <div class="legend" id="wLegend"></div>
      </div>
    </section>
  </div>

  <section aria-labelledby="yr-h">
    <h2 id="yr-h">逐年報酬</h2>
    <div class="panel table-wrap"><table id="yrTable"></table></div>
    <p class="muted" id="yrNote"></p>
  </section>

  <section aria-labelledby="rb-h">
    <h2 id="rb-h">穩健性：「趨勢 × 下跌波動目標」換參數</h2>
    <div class="cols">
      <div class="panel table-wrap"><table id="rbLb"></table></div>
      <div class="panel table-wrap"><table id="rbTg"></table></div>
      <div class="panel table-wrap"><table id="rbCost"></table></div>
    </div>
  </section>

  <section aria-labelledby="rec-h">
    <h2 id="rec-h">結論與建議</h2>
    <ul class="recs" id="recs"></ul>
  </section>

  <section aria-labelledby="set-h">
    <h2 id="set-h">回測設定</h2>
    <div class="panel cols"><dl id="setup"></dl><dl id="defs"></dl></div>
  </section>

  <section aria-labelledby="ref-h">
    <h2 id="ref-h">參考文獻</h2>
    <ul class="recs" style="font-size:.875rem">
      <li>Moskowitz, T. J., Ooi, Y. H., & Pedersen, L. H. (2012). Time series momentum. Journal of Financial Economics, 104(2), 228–250.</li>
      <li>Wang, F., & Yan, X. S. (2021). Downside risk and the performance of volatility-managed portfolios. Journal of Banking & Finance, 131, 106198.（樣本是股票因子，不是商品）</li>
      <li>Cederburg, S., O'Doherty, M. S., Wang, F., & Yan, X. S. (2020). On the performance of volatility-managed portfolios. Journal of Financial Economics, 138(1), 95–117.（波動管理在直接比較中並未系統性勝過不管理的組合）</li>
      <li>Moreira, A., & Muir, T. (2017). Volatility-managed portfolios. Journal of Finance, 72(4), 1611–1644.</li>
      <li>Harvey, C. R. 等 (2018). The impact of volatility targeting. Journal of Portfolio Management, 45(1), 14–33.</li>
      <li>Kelly, J. L. (1956). A new interpretation of information rate. Bell System Technical Journal, 35(4), 917–926.</li>
    </ul>
  </section>

  <footer>本頁由 cloud/combo-report.mjs 依 combo.json 產生。回測不代表未來績效，僅供研究參考，不構成投資建議。</footer>
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
  const sg = (v, d = 2) => Number.isFinite(v) ? (v >= 0 ? '+' : '') + v.toFixed(d) : '—';
  const css = getComputedStyle(document.documentElement);
  const tok = (n) => css.getPropertyValue(n).trim();
  const ST = R.settings;
  // 顏色跟著策略走（固定順序），不跟排名走
  const COLOR = { bh: '--s2', vt: '--s1', trend: '--s4', trend_dvt: '--s3', trend_dvt_lev: '--s5', bh_m: '--base' };
  let sym = 'SI=F';
  try { sym = localStorage.getItem('combo-sym') || sym; } catch (_) {}
  if (!R.underlyings[sym]) sym = 'SI=F';
  $('symSeg').innerHTML = Object.entries(R.underlyings).map(([k, u]) => '<button type="button" data-k="' + esc(k) + '">' + esc(u.name) + '</button>').join('');
  $('symSeg').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; sym = b.dataset.k; try { localStorage.setItem('combo-sym', sym); } catch (_) {} render(); });

  const font = { family: tok('--font-body'), size: 12, color: tok('--ink-2') };
  const axis = (extra = {}) => ({ gridcolor: tok('--grid'), zeroline: false, linecolor: tok('--edge'), tickfont: { family: tok('--font-mono'), size: 11, color: tok('--ink-3') }, ...extra });
  const layout = (extra = {}) => ({ paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font, showlegend: false, hovermode: 'x unified',
    margin: { l: 56, r: 12, t: 8, b: 36 }, hoverlabel: { bgcolor: tok('--surface'), bordercolor: tok('--edge'), font: { family: tok('--font-mono'), color: tok('--ink') } },
    xaxis: axis({ type: 'date' }), yaxis: axis(), ...extra });
  const cfg = { displayModeBar: false, responsive: true };
  const draw = (id, d, l) => window.Plotly ? Plotly.react(id, d, l, cfg) : ($(id).innerHTML = '<p class="muted" style="padding:24px">圖表函式庫載入失敗（需連線到 cdn.jsdelivr.net）。</p>');
  const legend = (id, items) => { $(id).innerHTML = items.map(([n, c]) => '<span class="key"><i style="background:' + c + '"></i>' + esc(n) + '</span>').join(''); };

  const render = () => {
    [...$('symSeg').children].forEach(b => b.setAttribute('aria-pressed', String(b.dataset.k === sym)));
    const U = R.underlyings[sym], rows = U.rows, by = Object.fromEntries(rows.map(r => [r.key, r])), S = U.series;
    const bh = by.bh, vt = by.vt, main = by.trend_dvt;
    const nm = (k) => by[k].name;
    const c = (k) => tok(COLOR[k] || '--base');

    $('sub').textContent = R.dataRange.from + ' ~ ' + R.dataRange.to + '，都只做多、未投入部分為現金；每天收盤決定隔天部位，換倉成本 ' + ST.costBps + ' bps，目標波動 ' + (ST.target * 100) + '%，權重變化小於 ' + (ST.band * 100) + '% 不調整。';

    // ── 結論（由數據計算）──
    const cands = rows.filter(r => !['bh', 'vt', 'bh_m'].includes(r.key));
    const sigBetter = cands.filter(r => r.vsVt && r.vsVt.lo > 0);
    const bestSharpe = [...rows.filter(r => r.key !== 'bh_m')].sort((a, b) => b.sharpe - a.sharpe)[0];
    $('verdict').innerHTML =
      '<p><strong>結論：' + (sigBetter.length ? '「' + sigBetter.map(r => esc(r.name)).join('、') + '」顯著優於單純波動目標化。' : '沒有一種結合方式顯著勝過單純的波動目標化。') + '</strong>' +
      '「' + esc(main.name) + '」夏普值 ' + fx(main.sharpe, 2) + '，波動目標化 ' + fx(vt.sharpe, 2) + '，買進持有 ' + fx(bh.sharpe, 2) + '；和波動目標化的差距 ' + sg(main.vsVt.obs) + '（95% 信賴區間 ' + sg(main.vsVt.lo) + ' ~ ' + sg(main.vsVt.hi) + '）。這段期間夏普值最高的是「' + esc(bestSharpe.name) + '」（' + fx(bestSharpe.sharpe, 2) + '）。</p>' +
      '<p>原因有兩個：<strong>12 個月趨勢在白銀上表現不穩</strong>（單獨使用夏普值 ' + fx(by.trend.sharpe, 2) + '，2022–2023 年來回被洗），<strong>下跌波動和總波動幾乎同步</strong>（白銀漲時也常有大跌日），所以沒能在漲勢中多留部位。半 Kelly 因為早期的歷史平均報酬偏低，大部分時間部位過小（平均 ' + pc(by.kelly.avgWeight, 0) + '）。</p>';

    const tiles = [
      [fx(main.sharpe, 2) + ' / ' + fx(vt.sharpe, 2) + ' / ' + fx(bh.sharpe, 2), '夏普值：結合版 / 波動目標 / 買進持有'],
      [pc(main.upCapture, 0) + ' / ' + pc(main.downCapture, 0), '結合版的上漲 / 下跌捕捉'],
      [pc(main.inMarket, 0), '結合版的在場時間'],
      [pc(main.maxDD, 0) + ' vs ' + pc(bh.maxDD, 0), '最大回撤（結合版 vs 買進持有）'],
    ];
    $('stats').innerHTML = tiles.map(([v, l]) => '<div class="stat"><div class="v" style="font-size:1.15rem">' + esc(v) + '</div><div class="l">' + esc(l) + '</div></div>').join('');

    // ── 報酬風險散點 ──
    const pts = rows;
    const maxVol = Math.max(...pts.map(r => r.vol)) * 1.12;
    const slope = bh.cagr / bh.vol;
    const short = (r) => r.name.replace('，最多 2 倍', ' 2x').replace('同風險買進持有', '同風險').replace(/（.*?）/, '');
    draw('rrChart', [
      { x: [0, maxVol * 100], y: [0, slope * maxVol * 100], type: 'scatter', mode: 'lines', line: { color: tok('--ink-3'), width: 1.5, dash: 'dash' }, hoverinfo: 'skip' },
      { x: pts.map(r => r.vol * 100), y: pts.map(r => r.cagr * 100), type: 'scatter', mode: 'markers+text', text: pts.map(short), textposition: pts.map(r => ({ bh: 'top left', vt: 'top left', trend: 'top center', kelly: 'bottom center', dvt: 'bottom right', trend_vt: 'bottom left', trend_dvt: 'bottom right', trend_dvt_lev: 'bottom center', bh_m: 'top left' }[r.key] || 'top center')),
        textfont: { size: 11, color: tok('--ink-2') }, marker: { size: 12, color: pts.map(r => c(r.key) || tok('--base')), line: { color: tok('--surface'), width: 2 } },
        customdata: pts.map(r => [r.name, r.sharpe, r.maxDD * 100]), hovertemplate: '%{customdata[0]}<br>波動 %{x:.1f}%　報酬 %{y:.1f}%<br>夏普 %{customdata[1]:.2f}　最大回撤 %{customdata[2]:.0f}%<extra></extra>' },
    ], layout({ hovermode: 'closest', margin: { l: 56, r: 16, t: 16, b: 48 },
      xaxis: axis({ range: [0, maxVol * 100], ticksuffix: '%', title: { text: '年化波動', font: { size: 11, color: tok('--ink-3') } } }),
      yaxis: axis({ ticksuffix: '%', rangemode: 'tozero', title: { text: '年化報酬', font: { size: 11, color: tok('--ink-3') } } }) }));

    // ── 主表 ──
    const ci = (t) => t ? '<span class="' + (t.lo > 0 ? 'good' : t.hi < 0 ? 'bad' : '') + '">' + sg(t.obs) + '</span><br><span class="muted">' + sg(t.lo) + ' ~ ' + sg(t.hi) + '</span>' : '—';
    $('mainTable').innerHTML = '<thead><tr><th>策略</th><th class="n">年化報酬</th><th class="n">年化波動</th><th class="n">夏普</th><th class="n">最大回撤</th><th class="n">最慘日</th><th class="n">上漲捕捉</th><th class="n">下跌捕捉</th><th class="n">在場</th><th class="n">年換手</th><th class="n">夏普差 vs 買進持有</th><th class="n">夏普差 vs 波動目標</th></tr></thead><tbody>' +
      rows.map(r => '<tr class="' + (r.key === 'trend_dvt' ? 'hl' : '') + '"><td><span class="grp">' + esc(r.group) + '</span>' + esc(r.name) + '</td><td class="n">' + pc(r.cagr) + '</td><td class="n">' + pc(r.vol) + '</td><td class="n"><strong>' + fx(r.sharpe, 2) + '</strong></td><td class="n">' + pc(r.maxDD) + '</td><td class="n">' + pc(r.worstDay) + '</td><td class="n">' + pc(r.upCapture, 0) + '</td><td class="n ' + (r.downCapture < r.upCapture - 0.02 ? 'good' : '') + '">' + pc(r.downCapture, 0) + '</td><td class="n">' + pc(r.inMarket, 0) + '</td><td class="n">' + fx(r.turnoverPerYear, 1) + '×</td><td class="n">' + ci(r.vsBh) + '</td><td class="n">' + ci(r.vsVt) + '</td></tr>').join('') + '</tbody>';

    // ── 淨值與回撤 ──
    const EQ = ['bh', 'vt', 'trend', 'trend_dvt', 'trend_dvt_lev'];
    legend('eqLegend', EQ.map(k => [nm(k), c(k)]));
    draw('eqChart', EQ.map(k => ({ x: S.dates, y: S.equity[k], type: 'scatter', mode: 'lines', line: { color: c(k), width: k === 'trend_dvt' ? 2.5 : 1.75 }, hovertemplate: esc(nm(k)) + '：%{y:.2f}<extra></extra>' })),
      layout({ yaxis: axis({ type: 'log', title: { text: '淨值（起始 = 1）', font: { size: 11, color: tok('--ink-3') } } }) }));
    const DD = ['bh', 'vt', 'trend_dvt'];
    legend('ddLegend', DD.map(k => [nm(k), c(k)]));
    draw('ddChart', DD.map(k => ({ x: S.dates, y: S.dd[k], type: 'scatter', mode: 'lines', line: { color: c(k), width: 1.75 }, hovertemplate: esc(nm(k)) + '：%{y:.1%}<extra></extra>' })), layout({ yaxis: axis({ tickformat: '.0%' }) }));
    draw('pxChart', [{ x: S.dates, y: S.price, type: 'scatter', mode: 'lines', line: { color: tok('--ink-2'), width: 1.5 }, hovertemplate: '價格：%{y:.2f}<extra></extra>' }],
      layout({ margin: { l: 56, r: 12, t: 4, b: 24 }, yaxis: axis({ type: 'log', title: { text: '白銀價格', font: { size: 11, color: tok('--ink-3') } } }) }));
    legend('wLegend', [[nm('trend_dvt'), c('trend_dvt')], [nm('vt'), c('vt')]]);
    draw('wChart', [
      { x: S.dates, y: S.weight.vt, type: 'scatter', mode: 'lines', line: { color: c('vt'), width: 1.5 }, hovertemplate: esc(nm('vt')) + '：%{y:.0%}<extra></extra>' },
      { x: S.dates, y: S.weight.trend_dvt, type: 'scatter', mode: 'lines', line: { color: c('trend_dvt'), width: 1.75 }, hovertemplate: esc(nm('trend_dvt')) + '：%{y:.0%}<extra></extra>' },
    ], layout({ margin: { l: 56, r: 12, t: 4, b: 36 }, yaxis: axis({ tickformat: '.0%', range: [0, 1.05], title: { text: '持有比例', font: { size: 11, color: tok('--ink-3') } } }) }));

    // ── 逐年 ──
    const YK = [['bh', '買進持有'], ['vt', '波動目標'], ['trend', '12 個月趨勢'], ['dvt', '下跌波動目標'], ['trend_dvt', '趨勢 × 下跌波動']];
    $('yrTable').innerHTML = '<thead><tr><th>年</th>' + YK.map(([, n]) => '<th class="n">' + n + '</th>').join('') + '</tr></thead><tbody>' +
      U.years.map(y => '<tr><td class="num">' + esc(y.year) + (y.days < 200 ? '<span class="muted">*</span>' : '') + '</td>' + YK.map(([k]) => '<td class="n ' + (y[k] >= 0 ? 'good' : 'bad') + '">' + sp(y[k]) + '</td>').join('') + '</tr>').join('') + '</tbody>';
    const worstTrend = [...U.years].sort((a, b) => a.trend - b.trend)[0];
    $('yrNote').textContent = '* 不滿一年。趨勢策略最差的一年是 ' + worstTrend.year + '（' + sp(worstTrend.trend) + '，買進持有 ' + sp(worstTrend.bh) + '）：白銀在區間內來回，趨勢訊號反覆進出、兩邊挨打。';

    // ── 穩健性 ──
    const rt = (head, list, lab) => '<thead><tr><th class="n">' + head + '</th><th class="n">夏普</th><th class="n">年化報酬</th><th class="n">最大回撤</th></tr></thead><tbody>' +
      list.map(x => '<tr class="' + (lab(x).hl ? 'hl' : '') + '"><td class="n">' + lab(x).t + '</td><td class="n">' + fx(x.sharpe, 2) + '</td><td class="n">' + pc(x.cagr) + '</td><td class="n">' + pc(x.maxDD) + '</td></tr>').join('') + '</tbody>';
    $('rbLb').innerHTML = rt('趨勢回看', U.robust.trendLb, x => ({ t: x.lb + ' 日', hl: x.lb === ST.trendLb }));
    $('rbTg').innerHTML = rt('目標波動', U.robust.target, x => ({ t: x.target + '%', hl: x.target === ST.target * 100 }));
    $('rbCost').innerHTML = rt('成本', U.robust.cost, x => ({ t: x.costBps + ' bps', hl: x.costBps === ST.costBps }));

    // ── 建議 ──
    const lb63 = U.robust.trendLb.find(x => x.lb === 63);
    const other = sym === 'SI=F' ? R.underlyings.SLV : R.underlyings['SI=F'];
    const oMain = other.rows.find(r => r.key === 'trend_dvt'), oVt = other.rows.find(r => r.key === 'vt');
    const recs = [
      '<strong>繼續用單純的波動目標化（' + (ST.target * 100) + '%，權重變化 > 10% 才調整）。</strong>加入趨勢、下跌波動或 Kelly 都沒有穩定的改善，反而增加規則與參數；兩個標的上，結合版和波動目標化的夏普差都不顯著（' + esc(U.name) + ' ' + sg(main.vsVt.obs) + '，' + esc(other.name) + ' ' + sg(oMain.sharpe - oVt.sharpe) + '）。',
      '<strong>趨勢參數非常敏感，是過度擬合的警訊。</strong>回看 63 日時結合版年化報酬 ' + pc(lb63.cagr) + '、夏普 ' + fx(lb63.sharpe, 2) + '，126 與 252 日則好得多。只有在換參數也都穩定時，才值得相信一個規則。',
      '<strong>想要更高報酬，最直接的是提高目標波動。</strong>波動目標化在不同目標下的夏普值差異不大，報酬大致隨目標等比例增加；加入槓桿的結合版（' + esc(nm('trend_dvt_lev')) + '）年化 ' + pc(by.trend_dvt_lev.cagr) + '，但最大回撤 ' + pc(by.trend_dvt_lev.maxDD) + '，接近買進持有。',
      '<strong>報酬最佳化的上限取決於能不能預測報酬。</strong>先前的實驗顯示白銀 5 日方向無法預測；這次的 12 個月趨勢單獨使用時夏普值 ' + fx(by.trend.sharpe, 2) + '，也沒有勝過買進持有（' + fx(bh.sharpe, 2) + '）。沒有可靠的報酬訊號時，「控制風險」就是部位規則能做到的全部。',
      '<strong>注意：</strong>同時比較 ' + (rows.length - 1) + ' 種策略，最好的那個本身就可能是運氣；回測期間只有約 9 年、且包含 2025 年的極端行情。SI=F 連續合約的換月價差會混入報酬，SLV 的結果可切換對照。',
    ];
    $('recs').innerHTML = recs.map(r => '<li>' + r + '</li>').join('');
    $('setup').innerHTML = [
      ['期間', esc(R.dataRange.from) + ' ~ ' + esc(R.dataRange.to) + '（前 ' + ST.warmup + ' 天為暖機，12 個月趨勢需要一年資料；起始日比上一份波動目標化報告晚，所以同一策略的數字略有不同）'],
      ['波動預測', 'EWMA，λ = ' + ST.lambda + '；下跌波動只累積下跌日的平方報酬 × 2'],
      ['趨勢', '收盤價高於 ' + ST.trendLb + ' 個交易日前 → 持有，否則空手（不放空）'],
      ['半 Kelly', '½ × 擴張視窗平均報酬 ÷ EWMA 變異數，上限 1 倍'],
      ['成本', '|Δ部位| × ' + ST.costBps + ' bps'],
      ['同風險買進持有', '事後把買進持有縮小到和「趨勢 × 下跌波動目標」相同的波動（固定 ' + fx(U.volMatchK * 100, 0) + '%）'],
    ].map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
    $('defs').innerHTML = rows.filter(r => r.desc).map(r => '<dt>' + esc(r.name) + '</dt><dd>' + esc(r.desc) + '</dd>').join('');
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
writeFileSync(path.join(dir, 'combo.html'), full);
if (fragmentOut) writeFileSync(path.resolve(fragmentOut), body);
console.log('已產生 ' + path.join(path.relative(process.cwd(), dir) || '.', 'combo.html') + (fragmentOut ? '，以及 ' + fragmentOut : ''));

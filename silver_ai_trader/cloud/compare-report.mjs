#!/usr/bin/env node
// 把 train.mjs --compare-loss 產生的 loss-compare.json 做成網頁報告
//
//   node compare-report.mjs [輸出目錄=output]            → <目錄>/loss-compare.html
//   node compare-report.mjs [目錄] --fragment 檔案.html   → 不含 <html>/<head> 外殼的版本（發布成 Artifact 用）
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragIdx = args.indexOf('--fragment');
const fragmentOut = fragIdx >= 0 ? args[fragIdx + 1] : null;
const dir = path.resolve(HERE, args.find((a, i) => !a.startsWith('--') && !(fragIdx >= 0 && i === fragIdx + 1)) || 'output');
const R = JSON.parse(readFileSync(path.join(dir, 'loss-compare.json'), 'utf8'));
const dataJson = JSON.stringify(R).replace(/<\//g, '<\\/');
const PLOTLY = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.24.1/plotly.min.js';

const body = `<title>白銀 Loss 函數比較</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* 版面：單欄閱讀流。先回答問題（結論 + 三種目標的總表），再給證據（各期間散點、雜訊對比、單邊預測），最後是實驗設定。
   色彩沿用 Silver AI Trader 的冷調金屬語彙；三種訓練目標用已驗證的類別色（藍、橘、綠），基準線用中性灰。 */
:root {
  --ground: #EEF2F4; --surface: #FFFFFF; --surface-2: #E4EAEE; --edge: #CDD6DC;
  --ink: #0F1A21; --ink-2: #3A4A55; --ink-3: #66767F;
  --up: #1F8A3A; --down: #C8461F;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --base: #8497A3; --grid: rgba(15,26,33,.08);
  --font-body: "IBM Plex Sans TC", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --down: #F0603A;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --base: #5B6C77; --grid: rgba(237,244,248,.08);
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --down: #F0603A;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --base: #5B6C77; --grid: rgba(237,244,248,.08);
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
@media (max-width: 480px) { .chart { height: 320px; } h1 { font-size: 1.45rem; } }
</style>

<div class="page">
  <header>
    <div class="eyebrow">Silver AI Trader · 訓練目標比較實驗</div>
    <h1>該讓模型猜對方向，還是讓價格誤差最小？</h1>
    <p class="muted" id="sub"></p>
  </header>

  <div class="verdict" id="verdict"></div>

  <section aria-labelledby="sum-h">
    <h2 id="sum-h">三種訓練目標的總成績</h2>
    <div class="panel table-wrap"><table id="sumTable"></table></div>
    <p class="muted">「比多數方向」是方向準確率減去該期間「一直猜比較常出現的方向」的準確率，正值才代表模型有用。價格誤差只有能輸出漲跌幅度的模型才有。</p>
  </section>

  <section aria-labelledby="dot-h">
    <h2 id="dot-h">各期間的方向準確率</h2>
    <p class="muted">小點是每一次訓練，大點是同一目標三次的平均，虛線是「一直猜多數方向」的基準。</p>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="dotLegend"></div>
      <div id="dotChart" class="chart" role="img" aria-label="各期間三種訓練目標的方向準確率"></div>
    </div>
  </section>

  <div class="cols">
    <section aria-labelledby="noise-h">
      <h2 id="noise-h">換 loss 的差距 vs. 重訓的差距</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div class="legend">
          <span class="key"><i style="background:var(--s1)"></i>三種目標平均值的最大差距</span>
          <span class="key"><i style="background:var(--s2)"></i>同一目標重訓三次的最大差距</span>
        </div>
        <div id="noiseChart" class="chart short" role="img" aria-label="loss 之間的差距與重訓差距的比較"></div>
        <p class="muted" id="noiseNote"></p>
      </div>
    </section>
    <section aria-labelledby="one-h">
      <h2 id="one-h">模型是否只猜同一個方向</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div id="oneChart" class="chart short" role="img" aria-label="每次訓練預測上漲的比例"></div>
        <p class="muted" id="oneNote"></p>
      </div>
    </section>
  </div>

  <section aria-labelledby="per-h">
    <h2 id="per-h">各期間明細</h2>
    <div class="panel table-wrap"><table id="perTable"></table></div>
  </section>

  <section aria-labelledby="rec-h">
    <h2 id="rec-h">建議</h2>
    <ul class="recs">
      <li><strong>兩者沒有優劣時，選誤差導向（對數報酬率 + Huber）。</strong>它除了方向還會給出預估的漲跌幅度，可以設門檻，例如只在預測漲跌超過 1% 時交易；純方向模型只給機率，比較容易退化成永遠猜同一邊。</li>
      <li><strong>不建議瀏覽器版預設的「價格 + 方向懲罰」。</strong>它預測的是價格水準，價格創新高時必須外插，先前的實驗中測試期誤差曾暴增到 46%。</li>
      <li><strong>真正該改善的是訊號，而不是 loss。</strong>目前的價格、技術指標、COT、TIPS 對白銀 5 日走勢的解釋力接近零。可以嘗試更有資訊量的資料（期貨期限結構、ETF 資金流、實質利率的變化率），或改為預測波動率這類比方向更容易預測的目標。</li>
      <li><strong>評估一律看多個期間、多次重訓。</strong>單一期間、單次訓練的成績可以相差十幾個百分點，很容易把運氣當成能力。</li>
    </ul>
  </section>

  <section aria-labelledby="set-h">
    <h2 id="set-h">實驗設定</h2>
    <div class="panel cols">
      <dl id="setup"></dl>
      <dl id="variants"></dl>
    </div>
  </section>

  <footer>本頁由 cloud/compare-report.mjs 依 loss-compare.json 產生，僅供研究參考，不構成投資建議。</footer>
</div>

<script type="application/json" id="data">${dataJson}</script>
<script src="${PLOTLY}" crossorigin="anonymous"></script>
<script>
(() => {
  const R = JSON.parse(document.getElementById('data').textContent);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fx = (v, d = 1) => Number.isFinite(v) ? v.toFixed(d) : '—';
  const sgn = (v, d = 1) => Number.isFinite(v) ? (v >= 0 ? '+' : '') + v.toFixed(d) : '—';
  const css = getComputedStyle(document.documentElement);
  const tok = (n) => css.getPropertyValue(n).trim();
  const COLORS = { error: '--s1', direction: '--s2', hybrid: '--s3' };
  const S = R.summary, P = R.periods, U = R.setup;
  const majority = (pi) => { const up = S[0].perPeriod[pi].alwaysUpDA; return Math.max(up, 100 - up); };

  $('sub').innerHTML = '同樣的資料、模型與特徵，只換訓練目標。' + esc(R.dataRange.from) + ' ~ ' + esc(R.dataRange.to) +
    '，預測 ' + U.horizon + ' 個交易日後的漲跌，' + P.length + ' 個評估期間 × 每種目標重訓 ' + U.repeats + ' 次，共 ' + R.runs.length + ' 次訓練。';

  // ── 結論（由數據計算）──
  const avgs = S.map(s => s.avgDA);
  const lossGap = Math.max(...avgs) - Math.min(...avgs);
  const runs = R.runs.filter(r => !r.error);
  const seedRanges = [];
  S.forEach(s => P.forEach((_, pi) => {
    const d = runs.filter(r => r.variant === s.key && r.period === pi).map(r => r.DA);
    if (d.length > 1) seedRanges.push(Math.max(...d) - Math.min(...d));
  }));
  const maxSeed = Math.max(...seedRanges);
  const best = [...S].sort((a, b) => b.avgEdge - a.avgEdge)[0];
  const anyEdge = S.some(s => s.avgEdge > 0);
  const oneSided = runs.filter(r => r.upShare >= 0.9 || r.upShare <= 0.1).length;
  $('verdict').innerHTML =
    '<p><strong>結論：沒有一種比較好。</strong>三種訓練目標的平均方向準確率是 ' + S.map(s => esc(s.name) + ' <span class="num">' + fx(s.avgDA) + '%</span>').join('、') +
    '，彼此只差 <span class="num">' + fx(lossGap) + '</span> 個百分點；同一種目標只是重訓，結果就可以相差 <span class="num">' + fx(maxSeed) + '</span> 個百分點。換 loss 的影響淹沒在隨機起始值的雜訊裡。</p>' +
    '<p>' + (anyEdge ? '只有' + esc(best.name) + '平均略高於多數方向基準。' : '三種目標平均都<strong>沒有</strong>贏過「一直猜多數方向」的基準（' + S.map(s => esc(s.name) + ' ' + sgn(s.avgEdge) + ' 點').join('、') + '）。') +
    '價格誤差也都比「' + U.horizon + ' 天後 = 今天」差。' + runs.length + ' 次訓練中有 ' + oneSided + ' 次幾乎只猜同一個方向，表示模型常常沒有學到可用的訊號。</p>';

  // ── 總表 ──
  const sw = (k) => '<span class="sw" style="background:var(' + COLORS[k] + ')"></span>';
  $('sumTable').innerHTML = '<thead><tr><th>訓練目標</th><th class="n">平均方向準確率</th><th class="n">比多數方向</th><th class="n">勝過多數方向</th><th class="n">價格誤差</th><th class="n">不變基準</th><th class="n">平均猜漲比例</th><th class="n">策略報酬（平均）</th></tr></thead><tbody>' +
    S.map(s => '<tr><td>' + sw(s.key) + esc(s.name) + '</td><td class="n">' + fx(s.avgDA) + '%</td><td class="n ' + (s.avgEdge > 0 ? 'good' : 'bad') + '">' + sgn(s.avgEdge) + '</td><td class="n">' + s.periodsBeatingMajority + ' / ' + P.length + ' 期</td><td class="n">' + (s.avgMAPE != null ? fx(s.avgMAPE, 2) + '%' : '—') + '</td><td class="n">' + fx(s.avgNaiveMAPE, 2) + '%</td><td class="n">' + fx(s.avgUpShare * 100, 0) + '%</td><td class="n">' + sgn(s.avgStrategy * 100) + '%</td></tr>').join('') + '</tbody>';

  // ── 圖表共用 ──
  const font = { family: tok('--font-body'), size: 12, color: tok('--ink-2') };
  const axis = (extra = {}) => ({ gridcolor: tok('--grid'), zeroline: false, linecolor: tok('--edge'), tickfont: { family: tok('--font-mono'), size: 11, color: tok('--ink-3') }, ...extra });
  const layout = (extra = {}) => ({ paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font, showlegend: false,
    margin: { l: 52, r: 12, t: 8, b: 56 }, hoverlabel: { bgcolor: tok('--surface'), bordercolor: tok('--edge'), font: { family: tok('--font-mono'), color: tok('--ink') } },
    xaxis: axis(), yaxis: axis(), ...extra });
  const cfg = { displayModeBar: false, responsive: true };
  const draw = (id, d, l) => window.Plotly ? Plotly.newPlot(id, d, l, cfg) : ($(id).innerHTML = '<p class="muted" style="padding:24px">圖表函式庫載入失敗（需連線到 cdn.jsdelivr.net）。</p>');
  const pLabel = (p) => p.label + '<br>' + p.from.slice(0, 7) + '~' + p.to.slice(0, 7);

  // ── 各期間散點 ──
  $('dotLegend').innerHTML = S.map(s => '<span class="key"><i style="background:var(' + COLORS[s.key] + ')"></i>' + esc(s.name) + '</span>').join('') +
    '<span class="key"><i class="line"></i>多數方向基準</span>';
  const off = { error: -0.2, direction: 0, hybrid: 0.2 };
  const traces = [];
  S.forEach(s => {
    const c = tok(COLORS[s.key]);
    const rs = runs.filter(r => r.variant === s.key);
    traces.push({ x: rs.map(r => r.period + off[s.key] + (r.rep - 1) * 0.05), y: rs.map(r => r.DA), type: 'scatter', mode: 'markers',
      marker: { size: 7, color: c, opacity: 0.45 }, name: s.name, hovertemplate: esc(s.name) + ' 單次：%{y:.1f}%<extra></extra>' });
    traces.push({ x: P.map((_, i) => i + off[s.key]), y: s.perPeriod.map(p => p.DA), type: 'scatter', mode: 'markers',
      marker: { size: 13, color: c, line: { color: tok('--surface'), width: 2 } }, name: s.name + ' 平均', hovertemplate: esc(s.name) + ' 平均：%{y:.1f}%<extra></extra>' });
  });
  P.forEach((_, i) => traces.push({ x: [i - 0.38, i + 0.38], y: [majority(i), majority(i)], type: 'scatter', mode: 'lines',
    line: { color: tok('--base'), width: 2, dash: 'dash' }, hovertemplate: '多數方向基準：%{y:.1f}%<extra></extra>' }));
  draw('dotChart', traces, layout({
    hovermode: 'closest',
    xaxis: axis({ tickvals: P.map((_, i) => i), ticktext: P.map(pLabel), range: [-0.6, P.length - 0.4] }),
    yaxis: axis({ title: { text: '方向準確率 (%)', font: { size: 11, color: tok('--ink-3') } }, ticksuffix: '%' }),
    shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 50, y1: 50, line: { color: tok('--ink-3'), width: 1, dash: 'dot' } }],
  }));

  // ── 雜訊對比 ──
  const lossSpread = P.map((_, i) => { const d = S.map(s => s.perPeriod[i].DA); return Math.max(...d) - Math.min(...d); });
  const seedSpread = P.map((_, i) => Math.max(...S.map(s => { const d = runs.filter(r => r.variant === s.key && r.period === i).map(r => r.DA); return d.length > 1 ? Math.max(...d) - Math.min(...d) : 0; })));
  draw('noiseChart', [
    { x: P.map(pLabel), y: lossSpread, type: 'bar', marker: { color: tok('--s1') }, name: '換 loss', hovertemplate: '%{y:.1f} 點<extra></extra>' },
    { x: P.map(pLabel), y: seedSpread, type: 'bar', marker: { color: tok('--s2') }, name: '重訓', hovertemplate: '%{y:.1f} 點<extra></extra>' },
  ], layout({ barmode: 'group', bargap: 0.3, bargroupgap: 0.08, hovermode: 'x unified', yaxis: axis({ title: { text: '百分點', font: { size: 11, color: tok('--ink-3') } } }) }));
  const bigger = lossSpread.filter((v, i) => seedSpread[i] > v).length;
  $('noiseNote').textContent = P.length + ' 個期間中有 ' + bigger + ' 個，重訓的差距大於換 loss 的差距。';

  // ── 單邊預測 ──
  const oneTraces = S.map((s, si) => {
    const rs = runs.filter(r => r.variant === s.key);
    return { x: rs.map(r => r.upShare * 100), y: rs.map((r, k) => si + ((k % 5) - 2) * 0.07), type: 'scatter', mode: 'markers',
      marker: { size: 9, color: tok(COLORS[s.key]), opacity: 0.75 }, name: s.name, hovertemplate: esc(s.name) + '：%{x:.0f}% 的時間猜漲<extra></extra>' };
  });
  draw('oneChart', oneTraces, layout({
    hovermode: 'closest', margin: { l: 72, r: 12, t: 8, b: 44 },
    xaxis: axis({ range: [-4, 104], ticksuffix: '%', title: { text: '預測上漲的比例', font: { size: 11, color: tok('--ink-3') } } }),
    yaxis: axis({ tickvals: S.map((_, i) => i), ticktext: S.map(s => s.name), range: [-0.6, S.length - 0.4], showgrid: false }),
    shapes: [0, 100].map(x0 => ({ type: 'rect', x0: x0 === 0 ? -4 : 90, x1: x0 === 0 ? 10 : 104, yref: 'paper', y0: 0, y1: 1, fillcolor: tok('--grid'), line: { width: 0 }, layer: 'below' })),
  }));
  $('oneNote').textContent = runs.length + ' 次訓練中有 ' + oneSided + ' 次（' + fx(oneSided / runs.length * 100, 0) + '%）落在灰色區域：90% 以上的時間都猜同一個方向。';

  // ── 各期間明細 ──
  $('perTable').innerHTML = '<thead><tr><th>期間</th><th class="n">多數方向基準</th>' + S.map(s => '<th class="n">' + esc(s.name) + '（平均 ± 標準差）</th>').join('') + '<th class="n">不變基準 MAPE</th></tr></thead><tbody>' +
    P.map((p, i) => '<tr><td>' + esc(p.label) + ' <span class="muted num">' + esc(p.from) + ' ~ ' + esc(p.to) + '</span></td><td class="n">' + fx(majority(i)) + '%</td>' +
      S.map(s => { const pp = s.perPeriod[i]; return '<td class="n ' + (pp.DA > majority(i) ? 'good' : '') + '">' + fx(pp.DA) + '% ± ' + fx(pp.DAsd) + (pp.MAPE != null ? '<br><span class="muted">MAPE ' + fx(pp.MAPE, 2) + '%</span>' : '') + '</td>'; }).join('') +
      '<td class="n">' + fx(S[0].perPeriod[i].naiveMAPE, 2) + '%</td></tr>').join('') + '</tbody>';

  // ── 設定 ──
  const dl = (pairs) => pairs.map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
  $('setup').innerHTML = dl([
    ['資料', esc(R.dataRange.from) + ' ~ ' + esc(R.dataRange.to) + '（' + R.dataRange.days + ' 個交易日）'],
    ['模型', esc(U.algorithm) + '，回看 ' + U.lookback + ' 天，' + U.layers + ' 個單元'],
    ['預測目標', U.horizon + ' 個交易日後的漲跌'],
    ['評估', P.length - 1 + ' 段 walk-forward + 最終測試段，每段之前的資料訓練、驗證段早停'],
    ['重複', '每種目標每個期間重訓 ' + U.repeats + ' 次'],
    ['特徵', U.features.length + ' 個（' + U.features.slice(0, 6).map(esc).join('、') + ' 等）'],
    ['耗時', fx(R.durationSec / 60, 0) + ' 分鐘（4 核心平行）'],
  ]);
  $('variants').innerHTML = dl(S.map(s => [sw(s.key) + esc(s.name), esc(s.desc)]));
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
writeFileSync(path.join(dir, 'loss-compare.html'), full);
if (fragmentOut) writeFileSync(path.resolve(fragmentOut), body);
console.log('已產生 ' + path.join(path.relative(process.cwd(), dir) || '.', 'loss-compare.html') + (fragmentOut ? '，以及 ' + fragmentOut : ''));

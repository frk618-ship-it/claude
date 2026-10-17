#!/usr/bin/env node
// 把 train.mjs --compare-features 產生的 feature-compare.json 做成網頁報告
//
//   node feature-report.mjs [輸出目錄=output]            → <目錄>/feature-compare.html
//   node feature-report.mjs [目錄] --fragment 檔案.html   → 不含 <html>/<head> 外殼的版本（發布成 Artifact 用）
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragIdx = args.indexOf('--fragment');
const fragmentOut = fragIdx >= 0 ? args[fragIdx + 1] : null;
const dir = path.resolve(HERE, args.find((a, i) => !a.startsWith('--') && !(fragIdx >= 0 && i === fragIdx + 1)) || 'output');
const R = JSON.parse(readFileSync(path.join(dir, 'feature-compare.json'), 'utf8'));

// 文獻出處與每個變數代表的經濟意義（報告用）
R.refs = {
  'Baur & Lucey (2010)': { cite: 'Baur, D. G., & Lucey, B. M. (2010). Is gold a hedge or a safe haven? An analysis of stocks, bonds and gold. Financial Review, 45(2), 217–229.', url: 'https://www.tcd.ie/triss/assets/PDFs/iiis/iiisdp198.pdf' },
  'Sari, Hammoudeh & Soytas (2010)': { cite: 'Sari, R., Hammoudeh, S., & Soytas, U. (2010). Dynamics of oil price, precious metal prices, and exchange rate. Energy Economics, 32(2), 351–362.', url: 'https://open.metu.edu.tr/handle/11511/36543' },
  'Batten, Ciner & Lucey (2010)': { cite: 'Batten, J. A., Ciner, C., & Lucey, B. M. (2010). The macroeconomic determinants of volatility in precious metals markets. Resources Policy, 35(2), 65–71.', url: 'https://researchers.mq.edu.au/en/publications/the-macroeconomic-determinants-of-volatility-in-precious-metals-m/' },
  'Pierdzioch, Risse & Rohloff (2016)': { cite: 'Pierdzioch, C., Risse, M., & Rohloff, S. (2016). A boosting approach to forecasting gold and silver returns: economic and statistical forecast evaluation. Applied Economics Letters, 23(5), 347–352.', url: 'https://ideas.repec.org/a/taf/apeclt/v23y2016i5p347-352.html' },
  'Pierdzioch & Risse (2020)': { cite: 'Pierdzioch, C., & Risse, M. (2020). Forecasting precious metal returns with multivariate random forests. Empirical Economics, 58(3), 1167–1184.', url: 'https://authors.repec.org/pro/pri316' },
  'CBOE 貴金屬波動率指數文獻': { cite: '以 GVZ 與 VXSLV 解釋中國白銀期貨波動率的研究（E3S Web of Conferences, 253, 02023, 2021）。白銀波動率指數 VXSLV 已停止發布，改用黃金的 GVZ。', url: 'https://www.e3s-conferences.org/10.1051/e3sconf/202125302023' },
  'Corsi (2009) HAR': { cite: 'Corsi, F. (2009). A simple approximate long-memory model of realized volatility. Journal of Financial Econometrics, 7(2), 174–196.', url: null },
  'Moskowitz, Ooi & Pedersen (2012)': { cite: 'Moskowitz, T. J., Ooi, Y. H., & Pedersen, L. H. (2012). Time series momentum. Journal of Financial Economics, 104(2), 228–250.', url: null },
};
R.why = {
  spxRet: '股市的風險偏好。黃金在股災時是避險資產，白銀同時有工業金屬的一面，和股市的關係可能隨景氣改變。',
  platRet: '貴金屬之間有強烈的短期回饋：一個金屬的衝擊會暫時傳到其他金屬。',
  pallRet: '同上；鈀金和白銀一樣有大量工業（汽車觸媒）需求。',
  termSpread: '長短期利差反映景氣循環預期，是預測資產報酬的經典總體變數之一。',
  breakeven5y: '名目殖利率減實質殖利率＝市場的通膨預期。該文發現貨幣變數對黃金有解釋力、對白銀較弱，正好可以檢驗。',
  gvz: '選擇權隱含的貴金屬波動預期，反映市場的避險需求與不確定性。',
  eurusdRet: '美元以外的主要匯率；文獻發現匯率衝擊會短暫傳到貴金屬。',
  usdcnyRet: '中國是白銀最大的工業需求國，人民幣走勢反映當地購買力。',
  usdinrRet: '印度是白銀最大的實物（首飾、投資）需求國。',
  rv5: '白銀近一週的實際波動。HAR 模型用日、週、月三種尺度的過去波動預測未來波動。',
  rv22: '白銀近一個月的實際波動（HAR 的月尺度）。',
  tsMom63: '時間序列動能：過去 3 個月漲的商品期貨，接下來傾向繼續漲；文獻樣本包含白銀。',
  tsMom252: '時間序列動能的 12 個月版本，是該文主要的訊號。',
};
R.notAdded = [
  { name: '經濟政策不確定性指數 EPU', ref: 'Baker, Bloom & Davis (2016), Quarterly Journal of Economics', why: '雲端網路無法連到 policyuncertainty.com' },
  { name: '地緣政治風險指數 GPR', ref: 'Caldara & Iacoviello (2022), American Economic Review；Baur & Smales (2020), Journal of Banking & Finance', why: '雲端網路無法連到 matteoiacoviello.com' },
  { name: '期貨期限結構（近遠月價差、展期報酬）', ref: 'Gorton & Rouwenhorst (2006)；Szymanowska et al. (2014)', why: 'Yahoo 只有連續近月合約，沒有歷史的次月合約' },
  { name: '信用利差（高收益債利差）', ref: 'Pierdzioch 等人的金銀報酬預測研究', why: '雲端網路無法連到 FRED' },
  { name: 'ETF 持有量 / 資金流', ref: '貴金屬 ETF 文獻', why: '沒有免費的每日歷史資料（目前只用 SLV 每股含銀量當代理）' },
];
const dataJson = JSON.stringify(R).replace(/<\//g, '<\\/');
const PLOTLY = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.24.1/plotly.min.js';

const body = `<title>白銀 輸入變數比較</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* 版面：單欄閱讀流。先回答問題（結論 + 三種目標的總表），再給證據（各期間散點、雜訊對比、單邊預測），最後是實驗設定。
   色彩沿用 Silver AI Trader 的冷調金屬語彙；三組輸入變數用已驗證的類別色（藍、橘、綠），基準線用中性灰。 */
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
.pill { display:inline-block; font-size:.75rem; padding:1px 8px; border-radius:999px; background: var(--surface-2); color: var(--ink-2); }
.pill.off { color: var(--down); }
td.wrap { white-space: normal; min-width: 14em; }
a { color: var(--s1); }
@media (max-width: 480px) { .chart { height: 320px; } h1 { font-size: 1.45rem; } }
</style>
<div class="page">
  <header>
    <div class="eyebrow">Silver AI Trader · 輸入變數比較實驗</div>
    <h1>加入文獻建議的變數，模型會變好嗎？</h1>
    <p class="muted" id="sub"></p>
  </header>

  <div class="verdict" id="verdict"></div>

  <section aria-labelledby="lit-h">
    <h2 id="lit-h">新增的 13 個變數與文獻依據</h2>
    <p class="muted">都是目前模型沒有的變數。價格類一律轉成日對數報酬率，比價格水準平穩。</p>
    <div class="panel table-wrap"><table id="litTable"></table></div>
  </section>

  <section aria-labelledby="sum-h">
    <h2 id="sum-h">三組輸入變數的總成績</h2>
    <div class="panel table-wrap"><table id="sumTable"></table></div>
    <p class="muted">「比多數方向」是方向準確率減去該期間「一直猜比較常出現的方向」的準確率，正值才代表模型有用。「不變基準」是預測 5 天後價格等於今天。</p>
  </section>

  <section aria-labelledby="dot-h">
    <h2 id="dot-h">各期間的方向準確率</h2>
    <p class="muted">小點是每一次訓練，大點是同一組變數三次的平均，虛線是「一直猜多數方向」的基準。</p>
    <div class="panel" style="display:grid;gap:10px">
      <div class="legend" id="dotLegend"></div>
      <div id="dotChart" class="chart" role="img" aria-label="各期間三組輸入變數的方向準確率"></div>
    </div>
  </section>

  <div class="cols">
    <section aria-labelledby="noise-h">
      <h2 id="noise-h">換變數的差距 vs. 重訓的差距</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div class="legend">
          <span class="key"><i style="background:var(--s1)"></i>三組變數平均值的最大差距</span>
          <span class="key"><i style="background:var(--s2)"></i>同一組變數重訓三次的最大差距</span>
        </div>
        <div id="noiseChart" class="chart short" role="img" aria-label="換變數的差距與重訓差距的比較"></div>
        <p class="muted" id="noiseNote"></p>
      </div>
    </section>
    <section aria-labelledby="mape-h">
      <h2 id="mape-h">價格誤差（5 日後）</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div id="mapeChart" class="chart short" role="img" aria-label="各期間的價格誤差與不變基準"></div>
        <p class="muted" id="mapeNote"></p>
      </div>
    </section>
  </div>

  <section aria-labelledby="per-h">
    <h2 id="per-h">各期間明細</h2>
    <div class="panel table-wrap"><table id="perTable"></table></div>
  </section>

  <section aria-labelledby="na-h">
    <h2 id="na-h">文獻有建議、但這次抓不到的變數</h2>
    <div class="panel table-wrap"><table id="naTable"></table></div>
  </section>

  <section aria-labelledby="rec-h">
    <h2 id="rec-h">建議</h2>
    <ul class="recs" id="recs"></ul>
  </section>

  <section aria-labelledby="set-h">
    <h2 id="set-h">實驗設定</h2>
    <div class="panel cols">
      <dl id="setup"></dl>
      <dl id="variants"></dl>
    </div>
  </section>

  <section aria-labelledby="ref-h">
    <h2 id="ref-h">參考文獻</h2>
    <ul class="recs" id="refs" style="font-size:.875rem"></ul>
  </section>

  <footer>本頁由 cloud/feature-report.mjs 依 feature-compare.json 產生，僅供研究參考，不構成投資建議。</footer>
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
  const COLORS = { base: '--s1', plus: '--s2', lit: '--s3' };
  const S = R.summary, P = R.periods, U = R.setup;
  const byKey = Object.fromEntries(S.map(s => [s.key, s]));
  const majority = (pi) => { const up = S[0].perPeriod[pi].alwaysUpDA; return Math.max(up, 100 - up); };
  const runs = R.runs.filter(r => !r.error);
  const nLit = R.literature.filter(l => l.used).length;

  $('sub').innerHTML = '同樣的資料、模型（' + esc(U.algorithm) + '）與訓練目標（對數報酬率 + Huber），只換輸入變數。' + esc(R.dataRange.from) + ' ~ ' + esc(R.dataRange.to) +
    '，預測 ' + U.horizon + ' 個交易日後的漲跌，' + P.length + ' 個評估期間 × 每組重訓 ' + U.repeats + ' 次，共 ' + R.runs.length + ' 次訓練。';

  // ── 結論（由數據計算）──
  const seedRange = (k, pi) => { const d = runs.filter(r => r.variant === k && r.period === pi).map(r => r.DA); return d.length > 1 ? Math.max(...d) - Math.min(...d) : 0; };
  const seedRanges = S.flatMap(s => P.map((_, pi) => seedRange(s.key, pi)));
  const avgSeed = seedRanges.reduce((a, b) => a + b, 0) / seedRanges.length;
  const b = byKey.base, pl = byKey.plus, li = byKey.lit;
  const gain = pl.avgDA - b.avgDA;
  const wins = P.filter((_, i) => pl.perPeriod[i].DA > b.perPeriod[i].DA).length;
  const best = [...S].sort((x, y) => y.avgEdge - x.avgEdge)[0];
  const mapeGain = (pl.avgMAPE ?? NaN) - (b.avgMAPE ?? NaN);
  const meaningful = Math.abs(gain) > avgSeed / 2 && wins >= Math.ceil(P.length * 0.8);
  const head = meaningful
    ? (gain > 0 ? '加入新變數有幫助' : '加入新變數反而變差')
    : '加入新變數沒有帶來可靠的改善';
  $('verdict').innerHTML =
    '<p><strong>結論：' + head + '。</strong>原有變數平均方向準確率 <span class="num">' + fx(b.avgDA) + '%</span>，加入 ' + nLit + ' 個文獻變數後 <span class="num">' + fx(pl.avgDA) + '%</span>（' + sgn(gain) + ' 點），' +
    P.length + ' 個期間中有 ' + wins + ' 個變好；只用文獻變數 <span class="num">' + fx(li.avgDA) + '%</span>。同一組變數只是重訓，平均就會相差 <span class="num">' + fx(avgSeed) + '</span> 點。</p>' +
    '<p>' + (S.some(s => s.avgEdge > 0) ? '只有「' + esc(best.name) + '」平均高於多數方向基準（' + sgn(best.avgEdge) + ' 點）。' : '三組變數平均都<strong>沒有</strong>贏過「一直猜多數方向」的基準（' + S.map(s => esc(s.name) + ' ' + sgn(s.avgEdge) + ' 點').join('、') + '）。') +
    '價格誤差：原有 ' + fx(b.avgMAPE, 2) + '%、加入後 ' + fx(pl.avgMAPE, 2) + '%，「' + U.horizon + ' 天後 = 今天」是 ' + fx(b.avgNaiveMAPE, 2) + '%。</p>';

  // ── 文獻變數表 ──
  $('litTable').innerHTML = '<thead><tr><th>變數</th><th>類別</th><th>代表什麼</th><th>文獻依據</th><th>本次</th></tr></thead><tbody>' +
    R.literature.map(l => '<tr><td>' + esc(l.name) + '</td><td>' + esc(l.group) + '</td><td class="wrap">' + esc(R.why[l.key] || '') + '</td><td class="wrap">' + esc(l.ref) + '</td><td>' +
      (l.used ? '<span class="pill">使用</span>' : '<span class="pill off" title="' + esc(l.excluded || '') + '">未使用</span>') + '</td></tr>').join('') + '</tbody>';

  // ── 總表 ──
  const sw = (k) => '<span class="sw" style="background:var(' + COLORS[k] + ')"></span>';
  $('sumTable').innerHTML = '<thead><tr><th>輸入變數</th><th class="n">變數數</th><th class="n">平均方向準確率</th><th class="n">比多數方向</th><th class="n">勝過多數方向</th><th class="n">價格誤差</th><th class="n">不變基準</th><th class="n">平均猜漲比例</th><th class="n">策略報酬（平均）</th></tr></thead><tbody>' +
    S.map(s => '<tr><td>' + sw(s.key) + esc(s.name) + '</td><td class="n">' + s.features.length + '</td><td class="n">' + fx(s.avgDA) + '%</td><td class="n ' + (s.avgEdge > 0 ? 'good' : 'bad') + '">' + sgn(s.avgEdge) + '</td><td class="n">' + s.periodsBeatingMajority + ' / ' + P.length + ' 期</td><td class="n ' + (s.avgMAPE < s.avgNaiveMAPE ? 'good' : 'bad') + '">' + fx(s.avgMAPE, 2) + '%</td><td class="n">' + fx(s.avgNaiveMAPE, 2) + '%</td><td class="n">' + fx(s.avgUpShare * 100, 0) + '%</td><td class="n">' + sgn(s.avgStrategy * 100) + '%</td></tr>').join('') + '</tbody>';

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
  const off = { base: -0.2, plus: 0, lit: 0.2 };
  const traces = [];
  S.forEach(s => {
    const c = tok(COLORS[s.key]);
    const rs = runs.filter(r => r.variant === s.key);
    traces.push({ x: rs.map(r => r.period + off[s.key] + (r.rep - 1) * 0.05), y: rs.map(r => r.DA), type: 'scatter', mode: 'markers',
      marker: { size: 7, color: c, opacity: 0.45 }, hovertemplate: esc(s.name) + ' 單次：%{y:.1f}%<extra></extra>' });
    traces.push({ x: P.map((_, i) => i + off[s.key]), y: s.perPeriod.map(p => p.DA), type: 'scatter', mode: 'markers',
      marker: { size: 13, color: c, line: { color: tok('--surface'), width: 2 } }, hovertemplate: esc(s.name) + ' 平均：%{y:.1f}%<extra></extra>' });
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
  const featSpread = P.map((_, i) => { const d = S.map(s => s.perPeriod[i].DA); return Math.max(...d) - Math.min(...d); });
  const seedSpread = P.map((_, i) => Math.max(...S.map(s => seedRange(s.key, i))));
  draw('noiseChart', [
    { x: P.map(pLabel), y: featSpread, type: 'bar', marker: { color: tok('--s1') }, hovertemplate: '換變數：%{y:.1f} 點<extra></extra>' },
    { x: P.map(pLabel), y: seedSpread, type: 'bar', marker: { color: tok('--s2') }, hovertemplate: '重訓：%{y:.1f} 點<extra></extra>' },
  ], layout({ barmode: 'group', bargap: 0.3, bargroupgap: 0.08, hovermode: 'x unified', yaxis: axis({ title: { text: '百分點', font: { size: 11, color: tok('--ink-3') } } }) }));
  const bigger = featSpread.filter((v, i) => seedSpread[i] > v).length;
  $('noiseNote').textContent = P.length + ' 個期間中有 ' + bigger + ' 個，重訓的差距大於換變數的差距。';

  // ── 價格誤差 ──
  const mt = S.map(s => ({ x: P.map(pLabel), y: s.perPeriod.map(p => p.MAPE), type: 'scatter', mode: 'lines+markers',
    line: { color: tok(COLORS[s.key]), width: 2 }, marker: { size: 7, color: tok(COLORS[s.key]) }, hovertemplate: esc(s.name) + '：%{y:.2f}%<extra></extra>' }));
  mt.push({ x: P.map(pLabel), y: S[0].perPeriod.map(p => p.naiveMAPE), type: 'scatter', mode: 'lines', line: { color: tok('--base'), width: 2, dash: 'dash' }, hovertemplate: '不變基準：%{y:.2f}%<extra></extra>' });
  draw('mapeChart', mt, layout({ hovermode: 'x unified', yaxis: axis({ ticksuffix: '%', title: { text: 'MAPE', font: { size: 11, color: tok('--ink-3') } } }) }));
  const beatNaive = S.map(s => s.perPeriod.filter(p => p.MAPE < p.naiveMAPE).length);
  $('mapeNote').textContent = '虛線是「' + U.horizon + ' 天後 = 今天」。各組低於虛線的期間數：' + S.map((s, i) => s.name + ' ' + beatNaive[i] + '/' + P.length).join('、') + '。';

  // ── 各期間明細 ──
  $('perTable').innerHTML = '<thead><tr><th>期間</th><th class="n">多數方向基準</th>' + S.map(s => '<th class="n">' + esc(s.name) + '（平均 ± 標準差）</th>').join('') + '<th class="n">不變基準 MAPE</th></tr></thead><tbody>' +
    P.map((p, i) => '<tr><td>' + esc(p.label) + ' <span class="muted num">' + esc(p.from) + ' ~ ' + esc(p.to) + '</span></td><td class="n">' + fx(majority(i)) + '%</td>' +
      S.map(s => { const pp = s.perPeriod[i]; return '<td class="n ' + (pp.DA > majority(i) ? 'good' : '') + '">' + fx(pp.DA) + '% ± ' + fx(pp.DAsd) + '<br><span class="muted">MAPE ' + fx(pp.MAPE, 2) + '%　猜漲 ' + fx(pp.upShare * 100, 0) + '%</span></td>'; }).join('') +
      '<td class="n">' + fx(S[0].perPeriod[i].naiveMAPE, 2) + '%</td></tr>').join('') + '</tbody>';

  // ── 抓不到的變數 ──
  $('naTable').innerHTML = '<thead><tr><th>變數</th><th>文獻</th><th>原因</th></tr></thead><tbody>' +
    R.notAdded.map(n => '<tr><td>' + esc(n.name) + '</td><td class="wrap">' + esc(n.ref) + '</td><td class="wrap">' + esc(n.why) + '</td></tr>').join('') + '</tbody>';

  // ── 建議（依結果）──
  const recs = [];
  if (meaningful && gain > 0) recs.push('<strong>採用新變數。</strong>加入文獻變數在多數期間都變好，幅度也超過重訓雜訊。');
  else recs.push('<strong>新變數不足以讓模型可用。</strong>差距在重訓雜訊範圍內；這些變數在文獻中多半解釋的是「同期」或「月度」的關係，對白銀未來 5 日的方向幾乎沒有預測力，這和效率市場的預期一致。');
  if (li.avgDA >= b.avgDA) recs.push('<strong>可以考慮精簡變數。</strong>只用白銀價格加文獻變數（' + li.features.length + ' 個）表現不比原有的 ' + b.features.length + ' 個差，變數少、過擬合風險也低。');
  recs.push('<strong>剩下值得試的方向：</strong>文獻中預測力較強的是期貨期限結構（展期報酬）與地緣政治/政策不確定性指數，需要付費資料或開放網路；另一條路是改預測波動率（HAR 類模型在文獻中有穩定的預測力），而不是方向。');
  recs.push('<strong>評估一律看多個期間、多次重訓。</strong>單一期間、單次訓練的成績差距可以超過 10 個百分點。');
  $('recs').innerHTML = recs.map(r => '<li>' + r + '</li>').join('');

  // ── 設定 ──
  const dl = (pairs) => pairs.map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
  $('setup').innerHTML = dl([
    ['資料', esc(R.dataRange.from) + ' ~ ' + esc(R.dataRange.to) + '（' + R.dataRange.days + ' 個交易日）'],
    ['模型', esc(U.algorithm) + '，回看 ' + U.lookback + ' 天，' + U.layers + ' 個單元'],
    ['訓練目標', U.horizon + ' 日對數報酬率 + Huber loss'],
    ['評估', P.length - 1 + ' 段 walk-forward + 最終測試段，每段之前的資料訓練、驗證段早停'],
    ['重複', '每組變數每個期間重訓 ' + U.repeats + ' 次'],
    ['排除', R.excludedFactors.length ? R.excludedFactors.map(e => esc(e.key) + '（' + esc(e.reason) + '）').join('；') : '無'],
    ['耗時', fx(R.durationSec / 60, 0) + ' 分鐘（4 核心平行）'],
  ]);
  $('variants').innerHTML = dl(S.map(s => [sw(s.key) + esc(s.name), esc(s.desc) + '（' + s.features.length + ' 個）']));

  // ── 參考文獻 ──
  $('refs').innerHTML = Object.values(R.refs).map(r => '<li>' + esc(r.cite) + (r.url ? ' <a href="' + esc(r.url) + '" target="_blank" rel="noopener">連結</a>' : '') + '</li>').join('') +
    '<li>Sadorsky, P. (2021). Predicting gold and silver price direction using tree-based classifiers. Journal of Risk and Financial Management, 14(5), 198.（技術指標是最重要的預測變數，目前模型已包含 RSI、均線、MACD）</li>';
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
writeFileSync(path.join(dir, 'feature-compare.html'), full);
if (fragmentOut) writeFileSync(path.resolve(fragmentOut), body);
console.log('已產生 ' + path.join(path.relative(process.cwd(), dir) || '.', 'feature-compare.html') + (fragmentOut ? '，以及 ' + fragmentOut : ''));

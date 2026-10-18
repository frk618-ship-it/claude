#!/usr/bin/env node
// 把 train.mjs 產生的 results.json 做成網頁報告
//
//   node report.mjs [輸出目錄=output]            → <目錄>/report.html（可直接用瀏覽器開啟）
//   node report.mjs [目錄] --fragment 檔案.html   → 不含 <html>/<head> 外殼的版本（發布成 claude.ai Artifact 用）
//
// 圖表用 Plotly（jsDelivr，版本鎖定）；資料以 JSON 內嵌在頁面裡，不需要伺服器。
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const fragIdx = args.indexOf('--fragment');
const fragmentOut = fragIdx >= 0 ? args[fragIdx + 1] : null;
const dir = path.resolve(HERE, args.find((a, i) => !a.startsWith('--') && !(fragIdx >= 0 && i === fragIdx + 1)) || 'output');
const r = JSON.parse(readFileSync(path.join(dir, 'results.json'), 'utf8'));

const PLOTLY = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.24.1/plotly.min.js';
// JSON 內嵌進 <script>：跳脫 "</" 以免提早結束標籤
const dataJson = JSON.stringify(r).replace(/<\//g, '<\\/');

const body = `<title>白銀雲端訓練報告</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* 版面：單欄閱讀流，上方是「結論 + 預測」，往下依序是證據（價格、測試、回測、訓練、因子）與執行細節。
   色彩沿用瀏覽器版的冷調金屬語彙；唯一的飽和色是漲跌方向，圖表系列色另取自已驗證的類別色。 */
:root {
  --ground: #EEF2F4; --surface: #FFFFFF; --surface-2: #E4EAEE; --edge: #CDD6DC;
  --ink: #0F1A21; --ink-2: #3A4A55; --ink-3: #66767F;
  --up: #1F8A3A; --up-soft: rgba(31,138,58,.10); --down: #C8461F; --down-soft: rgba(200,70,31,.10); --warn: #A26F0A;
  --s1: #2a78d6; --s2: #eb6834; --band: rgba(42,120,214,.07); --grid: rgba(15,26,33,.08);
  --font-body: "IBM Plex Sans TC", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --up-soft: rgba(63,185,80,.12); --down: #F0603A; --down-soft: rgba(240,96,58,.12); --warn: #D9A441;
  --s1: #3987e5; --s2: #d95926; --band: rgba(57,135,229,.10); --grid: rgba(237,244,248,.08);
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --ground: #0B1014; --surface: #131C22; --surface-2: #1A252C; --edge: #26343D;
  --ink: #EDF4F8; --ink-2: #C4D2DA; --ink-3: #8497A3;
  --up: #3FB950; --up-soft: rgba(63,185,80,.12); --down: #F0603A; --down-soft: rgba(240,96,58,.12); --warn: #D9A441;
  --s1: #3987e5; --s2: #d95926; --band: rgba(57,135,229,.10); --grid: rgba(237,244,248,.08);
  color-scheme: dark; }

body { background: var(--ground); color: var(--ink); font-family: var(--font-body); font-size: 15px; line-height: 1.6; }
.page { max-width: 1040px; margin: 0 auto; padding-block: 28px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }
h1, h2 { text-wrap: balance; margin: 0; }
h1 { font-size: 1.75rem; font-weight: 700; letter-spacing: .01em; }
h2 { font-size: 1.1rem; font-weight: 600; }
p { margin: 0; max-width: 68ch; }
.eyebrow { font-family: var(--font-mono); font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
.muted { color: var(--ink-3); font-size: .875rem; }
.num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
header { display: grid; gap: 8px; }
.meta-line { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--ink-2); font-size: .875rem; }

/* 預測：最重要的數字 */
.forecast { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px; background: var(--edge); border: 1px solid var(--edge); border-radius: 10px; overflow: hidden; }
.fc { background: var(--surface); padding: 16px 18px; display: grid; gap: 4px; min-width: 0; }
.fc .label { font-size: .8rem; color: var(--ink-3); }
.fc .price { font-family: var(--font-mono); font-size: 1.6rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.chg { display: inline-flex; align-items: center; gap: 6px; font-family: var(--font-mono); font-size: .85rem; font-variant-numeric: tabular-nums; width: fit-content; padding: 1px 8px; border-radius: 999px; }
.chg.up { color: var(--up); background: var(--up-soft); }
.chg.down { color: var(--down); background: var(--down-soft); }

.verdict { border-left: 3px solid var(--ink-3); padding: 4px 0 4px 16px; display: grid; gap: 6px; }
.verdict.good { border-color: var(--up); } .verdict.bad { border-color: var(--down); }

section { display: grid; gap: 12px; }
.panel { background: var(--surface); border: 1px solid var(--edge); border-radius: 10px; padding: 16px; min-width: 0; }
.chart { width: 100%; height: 360px; }
.chart.short { height: 300px; }
.chart.mini { height: 200px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: .85rem; color: var(--ink-2); }
.key { display: inline-flex; align-items: center; gap: 6px; }
.key i { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
.key i.dash { background: repeating-linear-gradient(90deg, var(--s2) 0 4px, transparent 4px 7px) !important; }
.key i.zone { height: 10px; background: var(--band); border: 1px solid var(--edge); }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; }
.stat { display: grid; gap: 2px; min-width: 0; }
.stat .v { font-family: var(--font-mono); font-size: 1.15rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.stat .k { font-size: .8rem; color: var(--ink-3); }

.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--edge); white-space: nowrap; }
th { font-weight: 500; color: var(--ink-3); font-size: .8rem; }
td.n, th.n { text-align: right; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
tr.model td { font-weight: 600; }
.pill { display: inline-block; font-size: .75rem; padding: 0 8px; border-radius: 999px; border: 1px solid var(--edge); color: var(--ink-2); }
.pill.good { color: var(--up); border-color: currentColor; } .pill.bad { color: var(--down); border-color: currentColor; }

.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; align-items: start; }
.cols > * { min-width: 0; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0; font-size: .9rem; }
dt { color: var(--ink-3); } dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.sources { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; font-size: .9rem; }
.sources li { display: flex; gap: 8px; align-items: baseline; min-width: 0; }
.sources .st { font-family: var(--font-mono); font-size: .8rem; width: 1.2em; flex: none; }
.sources .st.ok { color: var(--up); } .sources .st.fail { color: var(--down); }
.sources .nm { flex: none; min-width: 9em; } .sources .dt { color: var(--ink-3); min-width: 0; overflow-wrap: anywhere; }
.chip { font-size: .8rem; padding: 2px 8px; border-radius: 6px; background: var(--surface-2); color: var(--ink-2); }
footer { color: var(--ink-3); font-size: .8rem; border-top: 1px solid var(--edge); padding-top: 16px; }
@media (max-width: 480px) { .chart { height: 300px; } .chart.short { height: 260px; } h1 { font-size: 1.45rem; } }
</style>

<div class="page">
  <header>
    <div class="eyebrow">Silver AI Trader · 雲端訓練報告</div>
    <h1>白銀期貨 (SI=F) 價格預測</h1>
    <div class="meta-line" id="metaLine"></div>
  </header>

  <section aria-labelledby="fc-h">
    <h2 id="fc-h">未來走勢預測</h2>
    <div class="forecast" id="forecast"></div>
    <p class="muted" id="fcNote"></p>
  </section>

  <section aria-labelledby="vt-h" id="vtSec" hidden>
    <h2 id="vt-h">今日部位建議（波動目標化）</h2>
    <div class="forecast" id="vtCells" style="grid-template-columns: repeat(auto-fit, minmax(210px, 1fr))"></div>
    <div class="cols">
      <div class="panel" style="display:grid;gap:8px">
        <div class="legend"><span class="key"><i style="background:var(--s1)"></i>建議持有比例（最近兩年）</span></div>
        <div id="vtChart" class="chart mini" role="img" aria-label="最近兩年的建議持有比例"></div>
      </div>
      <div class="panel table-wrap"><table id="vtTable"></table></div>
    </div>
    <p class="muted" id="vtNote"></p>
  </section>

  <div class="verdict" id="verdict"></div>

  <section aria-labelledby="price-h">
    <h2 id="price-h">價格與模型預測</h2>
    <div class="panel">
      <div class="legend">
        <span class="key"><i style="background:var(--s1)"></i>實際收盤</span>
        <span class="key"><i style="background:var(--s2)"></i><span id="predLegend">模型逐日預測（前一天資料 → 下一天）</span></span>
        <span class="key"><i class="dash"></i>未來預測</span>
        <span class="key"><i class="zone"></i>測試期間（模型訓練時沒看過）</span>
      </div>
      <div id="priceChart" class="chart" role="img" aria-label="白銀期貨實際收盤價與模型預測"></div>
    </div>
  </section>

  <section aria-labelledby="test-h">
    <h2 id="test-h">測試期間表現</h2>
    <p class="muted" id="testNote"></p>
    <div class="panel table-wrap"><table id="testTable"></table></div>
  </section>

  <section aria-labelledby="bt-h" id="btSection">
    <h2 id="bt-h">回測（測試期間）</h2>
    <p class="muted" id="btNote"></p>
    <div class="panel" style="display:grid;gap:14px">
      <div class="stats" id="btStats"></div>
      <div class="legend">
        <span class="key"><i style="background:var(--s1)"></i>依模型訊號交易</span>
        <span class="key"><i style="background:var(--s2)"></i>買進持有</span>
      </div>
      <div id="btChart" class="chart short" role="img" aria-label="回測資產曲線"></div>
    </div>
  </section>

  <div class="cols">
    <section aria-labelledby="train-h">
      <h2 id="train-h">訓練過程</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div class="legend">
          <span class="key"><i style="background:var(--s1)"></i>訓練 loss</span>
          <span class="key"><i style="background:var(--s2)"></i>驗證 loss</span>
        </div>
        <div id="lossChart" class="chart short" role="img" aria-label="訓練與驗證 loss"></div>
        <p class="muted" id="trainNote"></p>
      </div>
    </section>
    <section aria-labelledby="inf-h">
      <h2 id="inf-h">因子影響（敏感度）</h2>
      <div class="panel" style="display:grid;gap:10px">
        <div id="infChart" class="chart short" role="img" aria-label="各因子對下一日預測的影響"></div>
        <p class="muted">把最後一段輸入中的單一因子調高 / 調低後，下一日預測價格的變化方向與幅度（相對值）。</p>
      </div>
    </section>
  </div>

  <section aria-labelledby="cv-h" id="cvSection" hidden>
    <h2 id="cv-h">Walk-forward 交叉驗證</h2>
    <div class="panel table-wrap"><table id="cvTable"></table></div>
  </section>

  <section aria-labelledby="search-h" id="searchSection" hidden>
    <h2 id="search-h">超參數搜尋</h2>
    <p class="muted" id="searchNote">依驗證期間的方向準確率排序；測試期間不參與挑選。</p>
    <div class="panel table-wrap"><table id="searchTable"></table></div>
  </section>

  <section aria-labelledby="run-h">
    <h2 id="run-h">執行細節</h2>
    <div class="panel cols">
      <dl id="runInfo"></dl>
      <dl id="paramInfo"></dl>
    </div>
    <div class="panel" style="display:grid;gap:10px">
      <div class="muted" id="srcLabel" hidden>輔助資料來源</div>
      <ul class="sources" id="sources" hidden></ul>
      <div class="muted">使用的特徵</div>
      <div class="chips" id="features"></div>
      <div id="excluded"></div>
    </div>
  </section>

  <footer>本報告由模型自動產生，僅供研究參考，不構成投資建議。過去的測試結果不保證未來表現。</footer>
</div>

<script type="application/json" id="results">${dataJson}</script>
<script src="${PLOTLY}" crossorigin="anonymous"></script>
<script>
(() => {
  const R = JSON.parse(document.getElementById('results').textContent);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fx = (v, d = 2) => Number.isFinite(v) ? v.toFixed(d) : '—';
  const pct = (v, d = 1) => Number.isFinite(v) ? (v * 100).toFixed(d) + '%' : '—';
  const signed = (v, d = 2) => Number.isFinite(v) ? (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%' : '—';
  const ALG = { BiGRU: '雙向 GRU', LSTM: 'LSTM', Transformer: 'Transformer', TimesFM: 'TimesFM 架構' };
  const TARGET = { price: '價格迴歸', logreturn: '對數報酬率', direction: '漲跌方向', dual: '價格 + 方向' };

  // 方向準確率的 95% 信賴區間（Wilson）：樣本只有一百多天時，幾個百分點的差距可能只是運氣
  const wilson = (pctv, n) => {
    if (!n) return [NaN, NaN];
    const p = pctv / 100, z = 1.96, d = 1 + z * z / n;
    const c = (p + z * z / (2 * n)) / d, h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
    return [(c - h) * 100, (c + h) * 100];
  };

  // ── 頁首 ──
  const m = R.meta, P = R.params;
  const H = (R.test && R.test.horizon) || 1;          // 預測幾個交易日後
  const hTxt = H > 1 ? H + ' 個交易日後' : '隔日';
  $('metaLine').innerHTML = [
    '資料截至 <span class="num">' + esc(R.forecast.lastDate) + '</span>',
    esc(ALG[P.algorithm] || P.algorithm) + ' · ' + (H > 1 ? '預測 ' + H + ' 日報酬' : esc(TARGET[P.predTarget] || P.predTarget)),
    '訓練於 Claude 雲端' + (m.dataSource === 'yahoo' ? '' : '（瀏覽器匯出資料）'),
    '產生於 <span class="num">' + esc(m.createdAt.slice(0, 16).replace('T', ' ')) + ' UTC</span>',
  ].map(s => '<span>' + s + '</span>').join('');

  // ── 預測 ──
  const F = R.forecast;
  const cells = ['<div class="fc"><span class="label">最新收盤 · ' + esc(F.lastDate) + '</span><span class="price">' + fx(F.lastPrice, 3) + '</span><span class="muted">美元 / 盎司</span></div>'];
  F.dates.forEach((d, i) => {
    const p = F.prices[i], chg = p / F.lastPrice - 1, up = chg >= 0;
    cells.push('<div class="fc"><span class="label">T+' + (H > 1 ? H : i + 1) + ' · ' + esc(d) + '</span><span class="price">' + fx(p, 3) +
      '</span><span class="chg ' + (up ? 'up' : 'down') + '">' + (up ? '▲ 漲 ' : '▼ 跌 ') + signed(chg) + '</span></div>');
  });
  $('forecast').innerHTML = cells.join('');
  $('fcNote').textContent = (R.training.refit ? '預測使用以全部資料重新訓練的模型。' : '') +
    (H > 1 ? '模型直接預測 ' + H + ' 個交易日後的收盤價（以今天收盤為起點的 ' + H + ' 日報酬），不是逐日推算。'
           : 'T+2 之後是把前一步的預測再餵回模型推算，誤差會累積，參考價值隨天數遞減。');

  // ── 結論 ──
  const T = R.test, B = T.baselines;
  // H 日目標互相重疊、彼此不獨立，信賴區間用有效樣本數（約 n / H）
  const effN = T.effectiveN || T.n;
  const [lo, hi] = wilson(T.DA, effN);
  // 顯著性要和「最強的簡單規則」比，不是和 50% 比：行情單邊時，永遠猜漲就能遠高於 50%
  const bestRule = Math.max(50, B.alwaysUpDA || 0, B.momentumDA || 0);
  const bestRuleName = bestRule === B.alwaysUpDA ? '永遠猜漲' : bestRule === B.momentumDA ? '跟前一段同方向' : '擲硬幣';
  const beatsUp = T.DA > B.alwaysUpDA, sig = lo > bestRule;
  // 交叉驗證：各折平均和測試期間差太多，代表表現隨時期大幅變化
  const cvDA = (R.cv || []).filter(c => Number.isFinite(c.DA));
  const cvMean = cvDA.length ? cvDA.reduce((a, c) => a + c.DA, 0) / cvDA.length : NaN;
  const cvGap = Number.isFinite(cvMean) && Math.abs(cvMean - T.DA) > 8;
  // 價格誤差差距在 2% 以內視為相當，不宣稱勝負
  const rwRel = (T.MAPE - B.randomWalkMAPE) / B.randomWalkMAPE;
  const rwVerdict = Math.abs(rwRel) <= 0.02 ? 'tie' : (rwRel < 0 ? 'better' : 'worse');
  // 模型是否幾乎只猜同一個方向：以每筆樣本的基準價（目標日往前 H 筆的實際價）判定
  const Fx = R.fitted, base0 = Fx.dates.indexOf(T.dates[0]);
  let upPred = 0, nPred = 0;
  T.pred.forEach((p, k) => { const b = Fx.actual[base0 + k - H]; if (Number.isFinite(b) && Number.isFinite(p)) { nPred++; if (p >= b) upPred++; } });
  const upShare = nPred ? upPred / nPred : NaN;
  const oneSided = Number.isFinite(upShare) && (upShare >= 0.9 || upShare <= 0.1);
  const v = $('verdict');
  v.className = 'verdict ' + (beatsUp && sig && !cvGap ? 'good' : (!beatsUp ? 'bad' : ''));
  v.innerHTML =
    '<p><strong>方向：</strong>測試期間 ' + T.n + ' 個交易日' + (H > 1 ? '（' + H + ' 日報酬互相重疊，約等於 ' + effN + ' 個獨立樣本）' : '') + '，猜對' + (H > 1 ? ' ' + H + ' 日' : '') + '漲跌 <span class="num">' + fx(T.DA, 1) + '%</span>' +
    '（95% 信賴區間 <span class="num">' + fx(lo, 0) + '–' + fx(hi, 0) + '%</span>），' +
    (beatsUp ? '高於' : '沒有高於') + '「永遠猜漲」的 <span class="num">' + fx(B.alwaysUpDA, 1) + '%</span>。' +
    (sig ? '區間下緣也高於最強的簡單規則（' + bestRuleName + ' ' + fx(bestRule, 1) + '%），不太可能只是運氣。'
         : '區間下緣 ' + fx(lo, 0) + '% 沒有高於最強的簡單規則（' + bestRuleName + ' ' + fx(bestRule, 1) + '%），還不能確定模型比簡單規則好。') + '</p>' +
    (cvGap ? '<p><strong>穩定性：</strong>交叉驗證 ' + cvDA.length + ' 折的方向準確率平均只有 <span class="num">' + fx(cvMean, 1) + '%</span>，和測試期間的 ' + fx(T.DA, 1) +
      '% 差距很大。表現隨時期變化劇烈，測試期間的成績可能只是剛好適合那段行情。</p>' : '') +
    (oneSided ? '<p><strong>注意：</strong>模型在測試期間有 <span class="num">' + fx(upShare * 100, 0) + '%</span> 的時間都預測' + (upShare >= 0.5 ? '上漲' : '下跌') +
      '，等於沒有學到漲跌訊號，只是學到訓練期間的平均方向。</p>' : '') +
    '<p><strong>價格：</strong>平均誤差 <span class="num">' + fx(T.MAPE, 2) + '%</span>，' +
    ({ better: '優於', tie: '與', worse: '不如' })[rwVerdict] + '「' + (H > 1 ? H + ' 天後 = 今天' : '明天 = 今天') + '」的 <span class="num">' + fx(B.randomWalkMAPE, 2) + '%</span>' +
    ({ better: '。', tie: '相當，模型對價格水準沒有額外資訊。', worse: '，價格水準請以方向判讀為主。' })[rwVerdict] + '</p>';

  // ── 圖表共用 ──
  const css = getComputedStyle(document.documentElement);
  const tok = (n) => css.getPropertyValue(n).trim();
  const font = { family: tok('--font-body'), size: 12, color: tok('--ink-2') };
  const axis = (extra = {}) => ({ gridcolor: tok('--grid'), zeroline: false, linecolor: tok('--edge'), tickfont: { family: tok('--font-mono'), size: 11, color: tok('--ink-3') }, ...extra });
  const layout = (extra = {}) => ({
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font, showlegend: false,
    margin: { l: 56, r: 16, t: 8, b: 36 }, hovermode: 'x unified',
    hoverlabel: { bgcolor: tok('--surface'), bordercolor: tok('--edge'), font: { family: tok('--font-mono'), color: tok('--ink') } },
    xaxis: axis(), yaxis: axis(), ...extra,
  });
  const cfg = { displayModeBar: false, responsive: true };
  const draw = (id, data, lay) => { if (window.Plotly) Plotly.newPlot(id, data, lay, cfg); else $(id).innerHTML = '<p class="muted" style="padding:24px">圖表函式庫載入失敗（需連線到 cdn.jsdelivr.net）。</p>'; };

  // ── 今日部位建議（波動目標化）──
  const V = R.volTarget;
  if (V) {
    $('vtSec').hidden = false;
    const C = V.current, S = V.settings, vs = V.stats.vt, bs = V.stats.bh;
    const pc = (v, d = 1) => Number.isFinite(v) ? (v * 100).toFixed(d) + '%' : '—';
    const moved = Math.abs(C.target - C.held) > 1e-6;
    $('vtCells').innerHTML = [
      '<div class="fc"><span class="label">EWMA 預測年化波動 · ' + esc(C.date) + '</span><span class="price">' + pc(C.sigma) + '</span><span class="muted">λ = ' + S.lambda + '</span></div>',
      '<div class="fc"><span class="label">建議白銀部位（下個交易日）</span><span class="price">' + pc(C.weight, 0) + '</span><span class="muted">目標波動 ' + pc(S.target, 0) + ' ÷ 預測波動，上限 ' + S.maxLev + ' 倍</span></div>',
      '<div class="fc"><span class="label">動作</span><span class="price" style="font-size:1.15rem">' + (C.adjust ? '調整到 ' + pc(C.target, 0) : '維持 ' + pc(C.held, 0)) + '</span><span class="muted">' +
        (C.adjust ? '目前 ' + pc(C.held, 0) + '，變化超過 ' + pc(S.band, 0) : moved ? '理論值 ' + pc(C.target, 0) + '，變化未超過 ' + pc(S.band, 0) + ' 不調整' : '已在目標部位') + '</span></div>',
    ].join('');
    draw('vtChart', [{ x: V.recent.dates, y: V.recent.weight, type: 'scatter', mode: 'lines', fill: 'tozeroy', line: { color: tok('--s1'), width: 1.5 }, fillcolor: tok('--band'), hovertemplate: '持有比例：%{y:.0%}<extra></extra>' }],
      layout({ margin: { l: 48, r: 12, t: 6, b: 30 }, yaxis: axis({ tickformat: '.0%', rangemode: 'tozero' }) }));
    const rows = [['年化報酬', pc(vs.cagr), pc(bs.cagr)], ['年化波動', pc(vs.vol), pc(bs.vol)], ['夏普值', fx(vs.sharpe, 2), fx(bs.sharpe, 2)], ['最大回撤', pc(vs.maxDD), pc(bs.maxDD)], ['最慘單日', pc(vs.worstDay), pc(bs.worstDay)], ['平均部位', pc(vs.avgWeight, 0), '100%']];
    $('vtTable').innerHTML = '<thead><tr><th>回測 ' + esc(V.range.from) + ' ~ ' + esc(V.range.to) + '</th><th class="n">波動目標</th><th class="n">買進持有</th></tr></thead><tbody>' +
      rows.map(([k, a, b]) => '<tr><td>' + k + '</td><td class="n">' + a + '</td><td class="n">' + b + '</td></tr>').join('') + '</tbody>';
    $('vtNote').textContent = '部位只依預測波動決定、只做多，和上面的漲跌預測無關：預測波動越高，持有越少（未投入部分為現金）。權重變化小於 ' + pc(S.band, 0) + ' 不調整，換倉成本 ' + S.costBps + ' bps。' +
      '回測中它把最大回撤從 ' + pc(bs.maxDD, 0) + ' 降到 ' + pc(vs.maxDD, 0) + '，但夏普值和買進持有差不多——用途是控制風險，不是多賺。';
  }

  // ── 價格圖 ──
  if (H > 1) $('predLegend').textContent = '模型預測（' + H + ' 天前的資料 → 預測當天）';
  const Fi = R.fitted;
  const priceData = [
    { x: Fi.dates, y: Fi.actual, name: '實際收盤', type: 'scatter', mode: 'lines', line: { color: tok('--s1'), width: 2 }, hovertemplate: '%{y:.3f}' },
  ];
  if (Fi.pred) priceData.push({ x: Fi.dates, y: Fi.pred, name: '模型預測', type: 'scatter', mode: 'lines', line: { color: tok('--s2'), width: 1.5 }, hovertemplate: '%{y:.3f}' });
  priceData.push({ x: [F.lastDate, ...F.dates], y: [F.lastPrice, ...F.prices], name: '未來預測', type: 'scatter', mode: 'lines+markers',
    line: { color: tok('--s2'), width: 2, dash: 'dot' }, marker: { size: 8, color: tok('--s2'), line: { color: tok('--surface'), width: 2 } }, hovertemplate: '%{y:.3f}' });
  draw('priceChart', priceData, layout({
    yaxis: axis({ title: { text: '美元 / 盎司', font: { size: 11, color: tok('--ink-3') } } }),
    shapes: [{ type: 'rect', xref: 'x', yref: 'paper', x0: Fi.testFrom, x1: F.lastDate, y0: 0, y1: 1, fillcolor: tok('--band'), line: { width: 0 }, layer: 'below' }],
    annotations: [{ x: Fi.testFrom, y: 1, xref: 'x', yref: 'paper', text: '測試期間', showarrow: false, xanchor: 'left', yanchor: 'top', font: { size: 11, color: tok('--ink-3') } }],
  }));

  // ── 測試表 ──
  $('testNote').textContent = R.split.test.from + ' ~ ' + R.split.test.to + '。這段資料在訓練、早停與參數挑選時都沒有用到' +
    (H > 1 ? '；各期間之間留了 ' + (H - 1) + ' 天間隔，訓練標籤不會跨進測試期間。' : '。');
  const row = (name, da, mape, note, cls = '') => '<tr class="' + cls + '"><td>' + name + '</td><td class="n">' + da + '</td><td class="n">' + mape + '</td><td>' + note + '</td></tr>';
  $('testTable').innerHTML = '<thead><tr><th>方法</th><th class="n">方向準確率</th><th class="n">價格誤差 (MAPE)</th><th>說明</th></tr></thead><tbody>' +
    row('本模型', fx(T.DA, 1) + '%', fx(T.MAPE, 2) + '%', '<span class="pill ' + (sig ? 'good' : '') + '">95% 區間 ' + fx(lo, 0) + '–' + fx(hi, 0) + '%' + (H > 1 ? '（有效樣本 ' + effN + '）' : '') + '</span>', 'model') +
    row('永遠猜漲', fx(B.alwaysUpDA, 1) + '%', '—', '測試期間上漲天數的比例') +
    row(H > 1 ? '跟前 ' + H + ' 天同方向' : '跟昨天同方向', fx(B.momentumDA, 1) + '%', '—', H > 1 ? '過去 ' + H + ' 天漲就猜接下來也漲' : '昨天漲就猜漲、跌就猜跌') +
    row(H > 1 ? H + ' 天後 = 今天' : '明天 = 今天', '—', fx(B.randomWalkMAPE, 2) + '%', '隨機漫步：直接拿今天收盤當預測') + '</tbody>';

  // ── 回測 ──
  const BT = R.backtest;
  if (!BT) { $('btSection').hidden = true; } else {
    const c = BT.config;
    const hd = BT.holdDays || 1;
    $('btNote').textContent = (hd > 1 ? '每 ' + hd + ' 個交易日依模型預測的漲跌做多' + (c.allowShort ? '或放空' : '') + '，持有 ' + hd + ' 天後收盤平倉（交易不重疊）；'
                                      : '每天依模型預測的漲跌做多' + (c.allowShort ? '或放空' : '') + '，隔天收盤平倉；') + '單邊手續費 ' + c.feeBps + ' bps、滑價 ' + c.slippageBps + ' bps，槓桿 ' + c.leverage + ' 倍。';
    const stat = (k, v, cls = '') => '<div class="stat"><span class="v ' + cls + '">' + v + '</span><span class="k">' + k + '</span></div>';
    $('btStats').innerHTML = stat('策略報酬', signed(BT.totalReturn, 1)) + stat('買進持有', signed(BT.buyHoldReturn, 1)) +
      stat('最大回撤', pct(BT.maxDD, 1)) + stat('Sharpe（年化）', fx(BT.sharpe, 2)) + stat('勝率', pct(BT.winRate, 1)) + stat('交易次數', BT.tradeCount);
    // 買進持有：測試期間第一天的前一日收盤買進，之後每天以收盤價計值
    const ec = BT.equityCurve;
    const prev0 = Number.isFinite(BT.buyHoldEntry) ? BT.buyHoldEntry : (() => { const i = R.fitted.dates.indexOf(R.test.dates[0]); return i > 0 ? R.fitted.actual[i - 1] : R.test.actual[0]; })();
    const bh = ec.map(d => { const i = R.fitted.dates.indexOf(d.date); return BT.initialCapital * (i >= 0 ? R.fitted.actual[i] : NaN) / prev0; });
    draw('btChart', [
      { x: ec.map(d => d.date), y: ec.map(d => d.equity), type: 'scatter', mode: 'lines', line: { color: tok('--s1'), width: 2 }, name: '模型訊號', hovertemplate: '%{y:,.0f}' },
      { x: ec.map(d => d.date), y: bh, type: 'scatter', mode: 'lines', line: { color: tok('--s2'), width: 2 }, name: '買進持有', hovertemplate: '%{y:,.0f}' },
    ], layout({ yaxis: axis({ tickformat: ',.0f', title: { text: '資產（美元）', font: { size: 11, color: tok('--ink-3') } } }) }));
  }

  // ── 訓練過程 ──
  const HIST = R.training.history;
  // loss 跨越一個數量級以上才用對數刻度，否則線性刻度比較好讀
  const lossVals = HIST.flatMap(h => [h.loss, h.val_loss]).filter(v => Number.isFinite(v) && v > 0);
  const logScale = lossVals.length && Math.max(...lossVals) / Math.min(...lossVals) > 30;
  draw('lossChart', [
    { x: HIST.map(h => h.epoch), y: HIST.map(h => h.loss), type: 'scatter', mode: 'lines', line: { color: tok('--s1'), width: 2 }, name: '訓練', hovertemplate: '%{y:.5f}' },
    { x: HIST.map(h => h.epoch), y: HIST.map(h => h.val_loss), type: 'scatter', mode: 'lines', line: { color: tok('--s2'), width: 2 }, name: '驗證', hovertemplate: '%{y:.5f}' },
  ], layout({
    xaxis: axis({ title: { text: 'epoch', font: { size: 11, color: tok('--ink-3') } } }),
    yaxis: axis(logScale ? { type: 'log', dtick: 1, exponentformat: 'power', title: { text: 'loss（對數刻度）', font: { size: 11, color: tok('--ink-3') } } }
                         : { title: { text: 'loss', font: { size: 11, color: tok('--ink-3') } } }),
    shapes: [{ type: 'line', x0: R.training.bestEpoch, x1: R.training.bestEpoch, yref: 'paper', y0: 0, y1: 1, line: { color: tok('--ink-3'), width: 1, dash: 'dot' } }],
    annotations: [{ x: R.training.bestEpoch, y: 1, yref: 'paper', text: '採用的 epoch', showarrow: false, xanchor: 'left', yanchor: 'top', font: { size: 11, color: tok('--ink-3') } }],
  }));
  const tr = R.training;
  $('trainNote').textContent = '共 ' + HIST.length + ' 個 epoch，驗證 loss 最低在第 ' + tr.bestEpoch + ' 個（之後連續 ' + R.patience + ' 個沒進步即停止）。' +
    '訓練耗時 ' + fx(tr.seconds, 1) + ' 秒，平均每個 epoch ' + fx(tr.msPerEpoch, 0) + ' 毫秒。' +
    (tr.refit ? '評估後再用全部 ' + tr.refit.samples + ' 筆資料重訓 ' + tr.refit.epochs + ' 個 epoch 作為輸出模型。' : '');

  // ── 因子影響 ──
  const inf = Object.entries(R.influence || {}).filter(([, v]) => Number.isFinite(v)).sort((a, b) => Math.abs(a[1]) - Math.abs(b[1]));
  if (inf.length) {
    $('infChart').style.height = Math.max(260, inf.length * 22 + 40) + 'px';
    draw('infChart', [{ type: 'bar', orientation: 'h', x: inf.map(e => e[1]), y: inf.map(e => e[0]),
      marker: { color: inf.map(e => e[1] >= 0 ? tok('--up') : tok('--down')) }, hovertemplate: '%{y}: %{x:.3f}<extra></extra>' }],
      layout({ margin: { l: 170, r: 16, t: 8, b: 30 }, hovermode: 'closest', bargap: 0.35, xaxis: axis({ zeroline: true, zerolinecolor: tok('--ink-3') }), yaxis: axis({ automargin: true, tickfont: { size: 11, color: tok('--ink-2') } }) }));
  } else { $('infChart').outerHTML = '<p class="muted">沒有因子影響資料。</p>'; }

  // ── 交叉驗證 / 搜尋 ──
  if (R.cv && R.cv.length) {
    $('cvSection').hidden = false;
    $('cvTable').innerHTML = '<thead><tr><th>折</th><th>測試期間</th><th class="n">訓練筆數</th><th class="n">方向準確率</th><th class="n">MAPE</th></tr></thead><tbody>' +
      R.cv.map(c => '<tr><td>' + c.fold + '</td><td>' + esc(c.testFrom) + ' ~ ' + esc(c.testTo) + '</td><td class="n">' + c.trainN + '</td><td class="n">' + fx(c.DA, 1) + '%</td><td class="n">' + fx(c.MAPE, 2) + '%</td></tr>').join('') + '</tbody>';
  }
  if (R.search && R.search.trials && R.search.trials.length) {
    $('searchSection').hidden = false;
    const valEff = Math.max(1, Math.floor((R.split.val.n || 0) / H));
    if (R.search.wallSec) $('searchNote').textContent = R.search.trials.length + ' 組試驗以 ' + R.search.workers + ' 個行程平行執行，耗時 ' + Math.round(R.search.wallSec) + ' 秒（逐一執行約需 ' + Math.round(R.search.cpuSec) + ' 秒）。' +
      (R.search.gate ? '驗證期間價格誤差超過「不變」基準 ' + R.search.gate + ' 倍的組合不列入挑選（標示「價格失準」），其餘依驗證期間方向準確率排序。' : '依驗證期間的方向準確率排序。') + '測試期間不參與挑選。' +
      '驗證期間約只有 ' + valEff + ' 個獨立樣本，每多猜對一個就差 ' + fx(100 / valEff, 0) + ' 個百分點，名次之間的差距大多是雜訊。';
    // 先列通過價格合理性檢查的，再依驗證 DA 排序（與實際挑選方式一致）
    const rows = R.search.trials.filter(t => Number.isFinite(t.valDA)).sort((a, b) => ((b.passed !== false) - (a.passed !== false)) || (b.valDA - a.valDA));
    $('searchTable').innerHTML = '<thead><tr><th>排名</th><th>演算法</th><th>預測目標</th><th class="n">lookback</th><th class="n">單元</th><th class="n">學習率</th><th class="n">λ</th><th class="n">因子數</th><th class="n">驗證 DA</th><th class="n">驗證 MAPE</th><th class="n">秒</th></tr></thead><tbody>' +
      rows.map((t, i) => '<tr' + (i === 0 ? ' class="model"' : '') + '><td>' + (i + 1) + (i === 0 ? ' <span class="pill good">採用</span>' : '') + '</td><td>' + esc(ALG[t.params.algorithm] || t.params.algorithm) + '</td><td>' + esc(TARGET[t.params.predTarget] || t.params.predTarget || '') + '</td><td class="n">' + t.params.lookback + '</td><td class="n">' + t.params.layers + '</td><td class="n">' + t.params.lr + '</td><td class="n">' + t.params.directionalLambda + '</td><td class="n">' + (Object.values(t.factors).filter(Boolean).length + 1) + '</td><td class="n">' + fx(t.valDA, 1) + '%</td><td class="n">' + fx(t.valMAPE, 2) + '%' + (t.passed === false ? ' <span class="pill bad">價格失準</span>' : '') + '</td><td class="n">' + fx(t.sec, 0) + '</td></tr>').join('') + '</tbody>';
  }

  // ── 執行細節 ──
  const S = R.split, rt = m.runtime;
  const dl = (pairs) => pairs.map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
  $('runInfo').innerHTML = dl([
    ['資料來源', m.dataSource === 'yahoo' ? 'Yahoo Finance（8 個市場品種）＋ 輔助資料（見下方）' : '瀏覽器匯出資料'],
    ['資料期間', '<span class="num">' + m.dataRange.from + ' ~ ' + m.dataRange.to + '</span>（' + m.dataRange.days + ' 個交易日）'],
    ['訓練期間', '<span class="num">' + S.train.from + ' ~ ' + S.train.to + '</span>（' + S.train.n + '）'],
    ['驗證期間', '<span class="num">' + S.val.from + ' ~ ' + S.val.to + '</span>（' + S.val.n + '）'],
    ['測試期間', '<span class="num">' + S.test.from + ' ~ ' + S.test.to + '</span>（' + S.test.n + '）'],
    ['運算環境', 'TensorFlow ' + esc(rt.tfjs) + '（原生 ' + esc(rt.backend) + '）· ' + rt.cpus + ' 核 CPU'],
    ['總耗時', fx(m.durationSec, 0) + ' 秒（含資料抓取' + (R.search ? '、參數搜尋' : '') + '）'],
  ]);
  $('paramInfo').innerHTML = dl([
    ['演算法', esc(ALG[P.algorithm] || P.algorithm)],
    ['預測目標', esc(TARGET[P.predTarget] || P.predTarget)],
    ['lookback', P.lookback + ' 天'], ['隱藏單元', P.layers], ['學習率', P.lr], ['batch', P.batchSize],
    ['方向懲罰 λ', P.directionalLambda], ['預測天數', P.futureDays],
  ]);
  if (m.sources && m.sources.length) {
    $('srcLabel').hidden = false; $('sources').hidden = false;
    $('sources').innerHTML = m.sources.map(x => '<li><span class="st ' + (x.ok ? 'ok' : 'fail') + '">' + (x.ok ? '✓' : '✗') + '</span><span class="nm">' + esc(x.name) + '</span><span class="dt">' + esc(x.detail) + '</span></li>').join('');
  }
  $('features').innerHTML = R.features.map(f => '<span class="chip">' + esc(f.name) + '</span>').join('');
  if (m.excludedFactors && m.excludedFactors.length) {
    $('excluded').innerHTML = '<p class="muted">未使用：' + m.excludedFactors.map(e => esc(e.reason)).join('；') + '。</p>';
  }
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
writeFileSync(path.join(dir, 'report.html'), full);
if (fragmentOut) writeFileSync(path.resolve(fragmentOut), body);
console.log('已產生 ' + path.join(path.relative(process.cwd(), dir) || '.', 'report.html') + (fragmentOut ? '，以及 ' + fragmentOut : ''));

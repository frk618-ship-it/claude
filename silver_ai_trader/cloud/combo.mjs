#!/usr/bin/env node
// 波動目標化 × 報酬訊號：比較幾種把「部位大小」和「預期報酬」結合的規則
//
//   node combo.mjs [--data runs/vol-data.json] [--target 20] [--fee-bps 2] [--slip-bps 1]
//
// 策略（都只做多，沒投入的部分是現金、報酬 0；t 日收盤決定、t+1 日生效；扣換倉成本）
//   買進持有、波動目標（EWMA）、12 個月趨勢、趨勢 × 波動目標、
//   下跌波動目標、趨勢 × 下跌波動目標、半 Kelly（預期報酬 ÷ 變異數）、趨勢 × 下跌波動目標加槓桿
// 參數都用文獻的標準值（λ = 0.94、12 個月趨勢、半 Kelly），沒有用這段資料調整，
// 但同時比較多種策略本身就有「挑到運氣好的那個」的風險，報告會提醒。
// 輸出：output/combo.json
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const arg = (name, def = null) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const OUT = path.resolve(HERE, arg('--out', 'output'));
const raw = JSON.parse(readFileSync(path.resolve(HERE, arg('--data', 'runs/vol-data.json')), 'utf8')).raw;
const COST = (parseFloat(arg('--fee-bps', '2')) + parseFloat(arg('--slip-bps', '1'))) / 1e4;
const TARGET = parseFloat(arg('--target', '20')) / 100;
const LAMBDA = 0.94;
const TREND_LB = 252;
const BAND = 0.10;
const WARMUP = 252;   // 12 個月趨勢需要一年的資料；所有策略從同一天開始，比較才公平

const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
const stdev = (a) => { const m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); };
const dates = raw.dates;

// ── 訊號 ──────────────────────────────────────────────────────
const simpleRet = (p) => p.map((v, i) => i === 0 ? 0 : v / p[i - 1] - 1);
const logRet = (p) => p.map((v, i) => i === 0 ? 0 : Math.log(v / p[i - 1]));
// EWMA 年化波動。down = true 時只累積下跌日（半變異數 × 2，在對稱分布下和總波動同尺度）
const ewmaVol = (r, down = false) => {
    const sq = r.map(x => down ? 2 * Math.min(x, 0) ** 2 : x * x);
    const out = new Array(r.length);
    let h = mean(sq.slice(1, 21));
    for (let i = 0; i < r.length; i++) { if (i > 0) h = LAMBDA * h + (1 - LAMBDA) * sq[i]; out[i] = Math.sqrt(h * 252); }
    return out;
};
const trendOf = (p, lb) => p.map((v, i) => i < lb ? 0 : (v > p[i - lb] ? 1 : 0));
// 擴張視窗的平均報酬（年化）：只用到 t 為止的資料
const expandingMu = (r) => { let s = 0; return r.map((x, i) => { s += x; return i === 0 ? 0 : s / i * 252; }); };

// ── 回測引擎 ──────────────────────────────────────────────────
// want(t)：t 日收盤時想要的部位；band：變化小於此值不調整（從空手進場或出場一律執行）
const run = (ret, want, { band = 0, cost = COST } = {}) => {
    const n = ret.length;
    const daily = new Array(n).fill(0), weight = new Array(n).fill(0);
    let w = 0, turnover = 0, trades = 0;
    for (let t = WARMUP; t < n - 1; t++) {
        const target = Math.max(0, want(t));
        const next = (Math.abs(target - w) > band || target === 0 || w === 0) ? target : w;
        const dw = Math.abs(next - w);
        if (dw > 1e-12) { turnover += dw; trades++; }
        w = next; weight[t] = w;
        daily[t + 1] = w * ret[t + 1] - dw * cost;
    }
    return { daily, weight, turnover, trades };
};

const monthly = (daily) => {
    const m = new Map();
    for (let i = WARMUP + 1; i < daily.length; i++) { const k = dates[i].slice(0, 7); m.set(k, (m.get(k) ?? 1) * (1 + daily[i])); }
    return [...m.entries()].map(([k, v]) => [k, v - 1]);
};
const stats = (res, bhDaily) => {
    const r = res.daily.slice(WARMUP + 1);
    const eq = [1]; r.forEach(x => eq.push(eq[eq.length - 1] * (1 + x)));
    let peak = 1, mdd = 0;
    eq.forEach(v => { peak = Math.max(peak, v); mdd = Math.max(mdd, 1 - v / peak); });
    const years = r.length / 252;
    const cagr = Math.pow(eq[eq.length - 1], 1 / years) - 1;
    const down = r.filter(x => x < 0);
    const mr = monthly(res.daily).map(x => x[1]), mb = monthly(bhDaily).map(x => x[1]);
    const upIdx = mb.map((v, i) => v > 0 ? i : -1).filter(i => i >= 0), dnIdx = mb.map((v, i) => v < 0 ? i : -1).filter(i => i >= 0);
    const w = res.weight.slice(WARMUP, -1);
    return {
        cagr, vol: stdev(r) * Math.sqrt(252), sharpe: mean(r) / stdev(r) * Math.sqrt(252),
        sortino: mean(r) / Math.sqrt(mean(down.map(x => x * x))) * Math.sqrt(252),
        maxDD: mdd, calmar: cagr / mdd, worstDay: Math.min(...r), worstMonth: Math.min(...mr),
        // 上漲／下跌捕捉率：買進持有上漲（下跌）的月份裡，策略拿到幾成
        upCapture: mean(upIdx.map(i => mr[i])) / mean(upIdx.map(i => mb[i])),
        downCapture: mean(dnIdx.map(i => mr[i])) / mean(dnIdx.map(i => mb[i])),
        avgWeight: mean(w), inMarket: w.filter(x => x > 0).length / w.length,
        turnoverPerYear: res.turnover / years, tradesPerYear: res.trades / years,
    };
};
const bootSharpeDiff = (a, b, block = 20, B = 2000, seed = 20261018) => {
    let s = seed >>> 0; const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    const x = a.slice(WARMUP + 1), y = b.slice(WARMUP + 1), n = x.length;
    const sh = (v) => mean(v) / stdev(v) * Math.sqrt(252);
    const diffs = [];
    for (let k = 0; k < B; k++) {
        const xa = [], ya = [];
        while (xa.length < n) { const st = Math.floor(rnd() * (n - block)); for (let j = 0; j < block && xa.length < n; j++) { xa.push(x[st + j]); ya.push(y[st + j]); } }
        diffs.push(sh(xa) - sh(ya));
    }
    diffs.sort((p, q) => p - q);
    return { obs: sh(x) - sh(y), lo: diffs[Math.floor(B * 0.025)], hi: diffs[Math.floor(B * 0.975)], pPos: diffs.filter(d => d > 0).length / B };
};

// ── 策略定義 ──────────────────────────────────────────────────
const strategies = (p, opts = {}) => {
    const r = simpleRet(p), lr = logRet(p);
    const sig = ewmaVol(lr), sigD = ewmaVol(lr, true);
    const tr = trendOf(p, opts.trendLb ?? TREND_LB), mu = expandingMu(r);
    const T = opts.target ?? TARGET;
    const vt = (s, cap = 1, tg = T) => (t) => Math.min(tg / s[t], cap);
    return {
        r, sig, sigD, tr,
        list: [
            { key: 'bh',        name: '買進持有',                         group: '基準',   want: () => 1 },
            { key: 'vt',        name: `波動目標 ${T * 100}%`,              group: '基準',   want: vt(sig), band: BAND, desc: '上一份報告的建議版本（EWMA，權重變化 > 10% 才調整）' },
            { key: 'trend',     name: '12 個月趨勢',                       group: '只看報酬', want: (t) => tr[t], desc: '過去 12 個月上漲就全額持有，否則空手' },
            { key: 'kelly',     name: '半 Kelly',                          group: '報酬 ÷ 風險', want: (t) => Math.min(0.5 * mu[t] / (sig[t] ** 2), 1), band: BAND, desc: '部位 = ½ × 預期報酬 ÷ 變異數；預期報酬用到當天為止的歷史平均' },
            { key: 'dvt',       name: `下跌波動目標 ${T * 100}%`,          group: '只看風險', want: vt(sigD), band: BAND, desc: '只用下跌日的波動縮放部位' },
            { key: 'trend_vt',  name: '趨勢 × 波動目標',                   group: '結合',   want: (t) => tr[t] * vt(sig)(t), band: BAND, desc: '趨勢向上時才持有，部位依總波動縮放' },
            { key: 'trend_dvt', name: '趨勢 × 下跌波動目標',               group: '結合',   want: (t) => tr[t] * vt(sigD)(t), band: BAND, desc: '趨勢向上時才持有，部位依下跌波動縮放（主要候選）' },
            { key: 'trend_dvt_lev', name: '趨勢 × 下跌波動目標 30%，最多 2 倍', group: '結合 + 槓桿', want: (t) => tr[t] * vt(sigD, 2, 0.30)(t), band: BAND, desc: '同上，目標波動提高到 30%、允許最多 2 倍槓桿' },
        ],
    };
};

const result = { createdAt: new Date().toISOString(), dataRange: { from: dates[WARMUP + 1], to: dates[dates.length - 1], days: dates.length - WARMUP - 1 },
    settings: { target: TARGET, costBps: Math.round(COST * 1e6) / 100, lambda: LAMBDA, trendLb: TREND_LB, band: BAND, warmup: WARMUP }, underlyings: {} };

for (const [sym, u] of Object.entries({ 'SI=F': { name: '白銀期貨（SI=F 連續近月）', prices: raw.prices }, 'SLV': { name: 'SLV 白銀 ETF', prices: raw.slvPrices } })) {
    const S = strategies(u.prices);
    const runs = Object.fromEntries(S.list.map(s => [s.key, run(S.r, s.want, { band: s.band ?? 0 })]));
    const bh = runs.bh;
    const rows = S.list.map(s => ({ key: s.key, name: s.name, group: s.group, desc: s.desc || '', ...stats(runs[s.key], bh.daily),
        vsBh: s.key === 'bh' ? null : bootSharpeDiff(runs[s.key].daily, bh.daily),
        vsVt: ['bh', 'vt'].includes(s.key) ? null : bootSharpeDiff(runs[s.key].daily, runs.vt.daily) }));
    // 同風險買進持有：固定部位，實現波動和主要候選相同（事後縮放，只用來比較）
    const main = rows.find(x => x.key === 'trend_dvt'), bhRow = rows.find(x => x.key === 'bh');
    const k = main.vol / bhRow.vol;
    const bhm = run(S.r, () => k);
    rows.push({ key: 'bh_m', name: `同風險買進持有（固定 ${(k * 100).toFixed(0)}%）`, group: '基準', desc: '事後把買進持有縮小到和主要候選相同的波動', ...stats(bhm, bh.daily), vsBh: null, vsVt: null });

    // 穩健性：趨勢回看期間、目標波動、成本
    const robust = {
        trendLb: [63, 126, 252].map(lb => { const s = strategies(u.prices, { trendLb: lb }).list.find(x => x.key === 'trend_dvt'); return { lb, ...stats(run(S.r, s.want, { band: BAND }), bh.daily) }; }),
        target: [10, 15, 20, 25, 30, 40].map(tv => { const s = strategies(u.prices, { target: tv / 100 }).list.find(x => x.key === 'trend_dvt'); return { target: tv, ...stats(run(S.r, s.want, { band: BAND }), bh.daily) }; }),
        cost: [0, 3, 10, 25].map(c => { const s = S.list.find(x => x.key === 'trend_dvt'); return { costBps: c, ...stats(run(S.r, s.want, { band: BAND, cost: c / 1e4 }), bh.daily) }; }),
    };
    // 逐年
    const yrs = [...new Set(dates.slice(WARMUP + 1).map(d => d.slice(0, 4)))];
    const years = yrs.map(y => {
        const idx = dates.map((d, i) => i).filter(i => i > WARMUP && dates[i].startsWith(y));
        const comp = (dl) => idx.reduce((a, i) => a * (1 + dl[i]), 1) - 1;
        return { year: y, days: idx.length, bh: comp(bh.daily), vt: comp(runs.vt.daily), trend: comp(runs.trend.daily), trend_dvt: comp(runs.trend_dvt.daily), dvt: comp(runs.dvt.daily) };
    });
    // 圖表序列（每 2 天取樣）
    const step = 2, keep = (a) => a.filter((_, i) => i % step === 0 || i === a.length - 1);
    const eqOf = (dl) => { const e = []; let v = 1; for (let i = WARMUP + 1; i < dl.length; i++) { v *= 1 + dl[i]; e.push(+v.toFixed(4)); } return keep(e); };
    const ddOf = (dl) => { const e = []; let v = 1, pk = 1; for (let i = WARMUP + 1; i < dl.length; i++) { v *= 1 + dl[i]; pk = Math.max(pk, v); e.push(+(v / pk - 1).toFixed(4)); } return keep(e); };
    const CH = ['bh', 'vt', 'trend', 'trend_dvt', 'trend_dvt_lev'];
    result.underlyings[sym] = {
        name: u.name, volMatchK: k, rows, robust, years,
        series: {
            dates: keep(dates.slice(WARMUP + 1)),
            equity: Object.fromEntries([...CH, 'bh_m'].map(c => [c, eqOf(c === 'bh_m' ? bhm.daily : runs[c].daily)])),
            dd: Object.fromEntries(['bh', 'vt', 'trend_dvt', 'bh_m'].map(c => [c, ddOf(c === 'bh_m' ? bhm.daily : runs[c].daily)])),
            weight: { trend_dvt: keep(runs.trend_dvt.weight.slice(WARMUP, -1)).map(v => +v.toFixed(3)), vt: keep(runs.vt.weight.slice(WARMUP, -1)).map(v => +v.toFixed(3)) },
            sig: keep(S.sig.slice(WARMUP, -1)).map(v => +(v * 100).toFixed(1)), sigD: keep(S.sigD.slice(WARMUP, -1)).map(v => +(v * 100).toFixed(1)),
            price: keep(u.prices.slice(WARMUP, -1)).map(v => +v.toFixed(3)),
        },
    };
    console.log(`── ${u.name} ──`);
    for (const x of rows) console.log(`  ${x.name.padEnd(26)} 年化 ${(x.cagr * 100).toFixed(1).padStart(5)}%  波動 ${(x.vol * 100).toFixed(1).padStart(5)}%  夏普 ${x.sharpe.toFixed(2)}  回撤 ${(x.maxDD * 100).toFixed(1).padStart(5)}%  上漲捕捉 ${(x.upCapture * 100).toFixed(0)}%  下跌捕捉 ${(x.downCapture * 100).toFixed(0)}%  在場 ${(x.inMarket * 100).toFixed(0)}%${x.vsBh ? `  夏普差 ${x.vsBh.obs.toFixed(2)} [${x.vsBh.lo.toFixed(2)}, ${x.vsBh.hi.toFixed(2)}]` : ''}`);
}
mkdirSync(OUT, { recursive: true });
writeFileSync(path.join(OUT, 'combo.json'), JSON.stringify(result));
console.log('輸出到', path.relative(process.cwd(), path.join(OUT, 'combo.json')));

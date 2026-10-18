#!/usr/bin/env node
// 波動目標化（volatility targeting）回測：依 EWMA 預測波動調整白銀部位大小
//
//   node voltarget.mjs [--data runs/vol-data.json] [--target 20] [--max-lev 1] [--fee-bps 2] [--slip-bps 1]
//
// 規則：每天收盤後用 EWMA（λ = 0.94）估計年化波動 σ̂，部位 w = min(目標波動 / σ̂, 槓桿上限)，
//       隔天的報酬按 w 計算；換倉成本 = |Δw| ×（手續費 + 滑價）。
// 白銀期貨的報酬本身就是超額報酬（不需要投入現金），所以夏普值不另外扣無風險利率；
// 未投入的部分視為現金、報酬 0（保守）。
// λ 用 RiskMetrics 的標準值、沒有用資料估計，整段期間都是樣本外。
// 輸出：output/voltarget.json
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const arg = (name, def = null) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const OUT = path.resolve(HERE, arg('--out', 'output'));
const dump = JSON.parse(readFileSync(path.resolve(HERE, arg('--data', 'runs/vol-data.json')), 'utf8'));
const raw = dump.raw;
const COST = (parseFloat(arg('--fee-bps', '2')) + parseFloat(arg('--slip-bps', '1'))) / 1e4;
const TARGET = parseFloat(arg('--target', '20')) / 100;
const MAX_LEV = parseFloat(arg('--max-lev', '1'));
const LAMBDA = 0.94;
const WARMUP = 60;   // EWMA 暖機天數，之前不交易

const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
const stdev = (a) => { const m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); };

// ── 波動估計 ──────────────────────────────────────────────────
const logRet = (p) => p.map((v, i) => i === 0 ? 0 : Math.log(v / p[i - 1]));
const simpleRet = (p) => p.map((v, i) => i === 0 ? 0 : v / p[i - 1] - 1);
const ewmaVol = (r) => {
    const out = new Array(r.length);
    let h = mean(r.slice(1, 21).map(x => x * x));   // 用前 20 天初始化
    for (let i = 0; i < r.length; i++) { if (i > 0) h = LAMBDA * h + (1 - LAMBDA) * r[i] * r[i]; out[i] = Math.sqrt(h * 252); }
    return out;
};
const rollingVol = (r, w) => r.map((_, i) => i < w ? NaN : Math.sqrt(mean(r.slice(i - w + 1, i + 1).map(x => x * x)) * 252));

// ── 回測核心 ──────────────────────────────────────────────────
// sigma[t]：t 收盤時可得的波動預測；rebalanceEvery：每幾天調整一次；band：權重變化小於此值就不調整
const run = (ret, sigma, { target = TARGET, maxLev = MAX_LEV, rebalanceEvery = 1, band = 0, cost = COST, constant = null } = {}) => {
    const n = ret.length;
    const daily = new Array(n).fill(0), weight = new Array(n).fill(0);
    let w = 0, turnover = 0, trades = 0;
    for (let t = WARMUP; t < n - 1; t++) {
        let next = w;
        if ((t - WARMUP) % rebalanceEvery === 0) {
            const want = constant ?? Math.min(target / sigma[t], maxLev);
            if (Math.abs(want - w) > band || w === 0) next = want;
        }
        const dw = Math.abs(next - w);
        if (dw > 1e-12) { turnover += dw; trades++; }
        w = next;
        weight[t] = w;
        daily[t + 1] = w * ret[t + 1] - dw * cost;   // t 收盤調整、t+1 收益
    }
    return { daily, weight, turnover, trades };
};

// 績效指標（只算交易期間）
const stats = (daily, weight, dates, extra = {}) => {
    const r = daily.slice(WARMUP + 1);
    const eq = [1]; r.forEach(x => eq.push(eq[eq.length - 1] * (1 + x)));
    let peak = 1, mdd = 0, ddStart = 0, worstStart = 0, worstEnd = 0, cur = 0;
    eq.forEach((v, i) => { if (v > peak) { peak = v; cur = i; } const dd = 1 - v / peak; if (dd > mdd) { mdd = dd; worstStart = cur; worstEnd = i; } });
    const years = r.length / 252;
    const cagr = Math.pow(eq[eq.length - 1], 1 / years) - 1;
    const vol = stdev(r) * Math.sqrt(252);
    const sharpe = mean(r) / stdev(r) * Math.sqrt(252);
    const down = r.filter(x => x < 0);
    const sortino = mean(r) / Math.sqrt(mean(down.map(x => x * x))) * Math.sqrt(252);
    // 月報酬
    const months = new Map();
    r.forEach((x, i) => { const m = dates[WARMUP + 1 + i].slice(0, 7); months.set(m, (months.get(m) ?? 1) * (1 + x)); });
    const mret = [...months.values()].map(v => v - 1);
    // 滾動 63 日年化波動：看風險是否穩定
    const roll = r.map((_, i) => i < 62 ? NaN : stdev(r.slice(i - 62, i + 1)) * Math.sqrt(252)).filter(Number.isFinite);
    const w = weight.slice(WARMUP, -1);
    return {
        totalReturn: eq[eq.length - 1] - 1, cagr, vol, sharpe, sortino, maxDD: mdd,
        calmar: mdd > 0 ? cagr / mdd : NaN,
        ddFrom: dates[WARMUP + worstStart], ddTo: dates[WARMUP + worstEnd],
        worstMonth: Math.min(...mret), bestMonth: Math.max(...mret), worstDay: Math.min(...r),
        rollVolMin: Math.min(...roll), rollVolMax: Math.max(...roll), rollVolSd: stdev(roll),
        avgWeight: mean(w), maxWeight: Math.max(...w), minWeight: Math.min(...w),
        ...extra,
    };
};

// 區塊拔靴法：夏普值差異（策略 − 基準）的 95% 信賴區間，保留報酬的自相關與波動群聚
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

// ── 執行 ──────────────────────────────────────────────────────
const dates = raw.dates;
const underlyings = {
    'SI=F': { name: '白銀期貨（SI=F 連續近月）', prices: raw.prices },
    'SLV':  { name: 'SLV 白銀 ETF', prices: raw.slvPrices },
};
const result = { createdAt: new Date().toISOString(), dataRange: { from: dates[WARMUP + 1], to: dates[dates.length - 1], days: dates.length - WARMUP - 1 },
    settings: { target: TARGET, maxLev: MAX_LEV, costBps: Math.round(COST * 1e6) / 100, lambda: LAMBDA, warmup: WARMUP }, underlyings: {} };

for (const [sym, u] of Object.entries(underlyings)) {
    const r = simpleRet(u.prices), lr = logRet(u.prices);
    const sig = ewmaVol(lr), sig22 = rollingVol(lr, 22).map((v, i) => Number.isFinite(v) ? v : sig[i]);
    const bh = run(r, sig, { constant: 1 });
    const vt = run(r, sig);
    const bhStats = stats(bh.daily, bh.weight, dates, { turnover: bh.turnover });
    // 同風險買進持有：把買進持有縮放到和策略相同的實現波動（事後縮放，只用來公平比較報酬/風險）
    const vtStats0 = stats(vt.daily, vt.weight, dates);
    const k = vtStats0.vol / bhStats.vol;
    const bhm = run(r, sig, { constant: k });
    const variants = [
        { key: 'vt',      name: `波動目標 ${TARGET * 100}%（EWMA，每日調整）`, res: vt },
        { key: 'vt_band', name: '同上，權重變化 > 10% 才調整', res: run(r, sig, { band: 0.10 }) },
        { key: 'vt_week', name: '同上，每 5 日調整一次', res: run(r, sig, { rebalanceEvery: 5 }) },
        { key: 'vt_22',   name: '波動目標，改用 22 日歷史波動', res: run(r, sig22) },
        { key: 'vt_lev2', name: '波動目標，槓桿上限 2 倍', res: run(r, sig, { maxLev: 2 }) },
        { key: 'bh',      name: '買進持有', res: bh },
        { key: 'bh_m',    name: `同風險買進持有（固定 ${(k * 100).toFixed(0)}% 部位）`, res: bhm },
    ];
    // 不同目標波動的敏感度
    const targets = [10, 15, 20, 25, 30, 40].map(tv => {
        const res = run(r, sig, { target: tv / 100 });
        return { target: tv, ...stats(res.daily, res.weight, dates, { turnover: res.turnover }) };
    });
    // 成本敏感度
    const costs = [0, 3, 10, 25].map(c => { const res = run(r, sig, { cost: c / 1e4 }); return { costBps: c, ...stats(res.daily, res.weight, dates) }; });
    // 逐年
    const years = [...new Set(dates.slice(WARMUP + 1).map(d => d.slice(0, 4)))].map(y => {
        const idx = dates.map((d, i) => [d, i]).filter(([d, i]) => i > WARMUP && d.startsWith(y)).map(([, i]) => i);
        const comp = (dl) => idx.reduce((a, i) => a * (1 + dl[i]), 1) - 1;
        const vol = (dl) => stdev(idx.map(i => dl[i])) * Math.sqrt(252);
        return { year: y, days: idx.length, vt: comp(vt.daily), bh: comp(bh.daily), bhm: comp(bhm.daily), vtVol: vol(vt.daily), bhVol: vol(bh.daily), avgW: mean(idx.map(i => vt.weight[i - 1])) };
    });
    // 圖表序列（每 2 天取樣一次以控制大小）
    const eqOf = (dl) => { const e = []; let v = 1; for (let i = WARMUP + 1; i < dl.length; i++) { v *= 1 + dl[i]; e.push(v); } return e; };
    const ddOf = (e) => { let pk = -Infinity; return e.map(v => { pk = Math.max(pk, v); return v / pk - 1; }); };
    const step = 2, pick = (a) => a.filter((_, i) => i % step === 0 || i === a.length - 1).map(v => typeof v === "number" && Number.isFinite(v) ? +v.toFixed(4) : v);
    const eVt = eqOf(vt.daily), eBh = eqOf(bh.daily), eBhm = eqOf(bhm.daily);
    const rollVol = (dl) => { const x = dl.slice(WARMUP + 1); return x.map((_, i) => i < 62 ? null : +(stdev(x.slice(i - 62, i + 1)) * Math.sqrt(252) * 100).toFixed(1)); };
    result.underlyings[sym] = {
        name: u.name, volMatchK: k,
        variants: variants.map(v => ({ key: v.key, name: v.name, ...stats(v.res.daily, v.res.weight, dates, { turnover: v.res.turnover, trades: v.res.trades }) })),
        sharpeTest: { vsBh: bootSharpeDiff(vt.daily, bh.daily) },
        // 買進持有最慘的一天：當天策略的部位與損失
        crash: (() => { let wi = WARMUP + 1; for (let i = WARMUP + 1; i < r.length; i++) if (r[i] < r[wi]) wi = i; return { date: dates[wi], ret: r[wi], weight: vt.weight[wi - 1], vtRet: vt.daily[wi] }; })(),
        targets, costs, years,
        series: {
            dates: pick(dates.slice(WARMUP + 1)),
            vt: pick(eVt), bh: pick(eBh), bhm: pick(eBhm),
            ddVt: pick(ddOf(eVt)), ddBh: pick(ddOf(eBh)), ddBhm: pick(ddOf(eBhm)),
            weight: pick(vt.weight.slice(WARMUP, -1)), sigma: pick(sig.slice(WARMUP, -1).map(v => v * 100)),
            rollVt: pick(rollVol(vt.daily)),
            rollBh: pick(rollVol(bh.daily)),
        },
    };
    const S = result.underlyings[sym];
    console.log(`── ${u.name} ──`);
    for (const v of S.variants) console.log(`  ${v.name.padEnd(30)} 年化 ${(v.cagr * 100).toFixed(1).padStart(6)}%  波動 ${(v.vol * 100).toFixed(1).padStart(5)}%  夏普 ${v.sharpe.toFixed(2)}  最大回撤 ${(v.maxDD * 100).toFixed(1)}%  平均部位 ${(v.avgWeight * 100).toFixed(0)}%  換手 ${v.turnover.toFixed(1)}`);
    console.log(`  夏普差（策略 − 買進持有）：${S.sharpeTest.vsBh.obs.toFixed(2)}（95% CI ${S.sharpeTest.vsBh.lo.toFixed(2)} ~ ${S.sharpeTest.vsBh.hi.toFixed(2)}）`);
}
mkdirSync(OUT, { recursive: true });
writeFileSync(path.join(OUT, 'voltarget.json'), JSON.stringify(result));
console.log('輸出到', path.relative(process.cwd(), path.join(OUT, 'voltarget.json')));

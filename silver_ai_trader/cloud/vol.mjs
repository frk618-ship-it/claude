#!/usr/bin/env node
// 波動率預測實驗：預測白銀未來 H 個交易日的已實現波動率（年化）
//
//   node train.mjs --config compare-features.config.json --dump-data runs/vol-data.json   # 先抓好資料
//   node vol.mjs --data runs/vol-data.json [--horizon 5] [--repeats 3] [--workers 4]
//
// 比較對象
//   計量基準（每期用訓練段估計，結果固定）：
//     隨機漫步  σ̂ = 過去 5 日已實現波動率
//     EWMA      RiskMetrics λ = 0.94
//     HAR       Corsi (2009)：log σ 對日、週、月三種尺度的過去波動做 OLS
//     HAR-X     HAR 再加上隱含波動率與不確定性指數
//   LSTM（每期重訓多次）：HAR 三項 / 原有變數 / 原有 + 不確定性 / 全部變數
//
// 評估：QLIKE（Patton 2011，對波動率代理雜訊穩健的損失）、波動率 RMSE、
//       相對訓練期平均的 R²、預測「波動會升高或降低」的準確率
// 輸出：output/vol-compare.json
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const arg = (name, def = null) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const t0 = Date.now();
const log = (...m) => console.log(`[${((Date.now() - t0) / 1000).toFixed(0).padStart(5)}s]`, ...m);

const H = parseInt(arg('--horizon', '5'), 10);
const P = H - 1;                                   // purge：目標期間重疊的樣本數
const CFG = {
    lookback: parseInt(arg('--lookback', '20'), 10), layers: parseInt(arg('--layers', '32'), 10),
    lr: parseFloat(arg('--lr', '0.001')), batchSize: parseInt(arg('--batch', '32'), 10),
    epochs: parseInt(arg('--epochs', '400'), 10), patience: parseInt(arg('--patience', '30'), 10),
    algorithm: arg('--algorithm', 'LSTM'),
};
const REPEATS = parseInt(arg('--repeats', '3'), 10);
const WORKERS = parseInt(arg('--workers', String(os.cpus().length)), 10);
const OUT = path.resolve(HERE, arg('--out', 'output'));
const MIN_VOL = 0.01;   // 年化 1%：避免 |r| = 0 的日子取對數變成 -∞

// ── 資料與特徵 ────────────────────────────────────────────────
const dump = JSON.parse(readFileSync(path.resolve(arg('--data') || arg('--worker-data') || 'runs/vol-data.json'), 'utf8'));
const raw = dump.raw;
const n = raw.prices.length;
const ret = raw.prices.map((p, i) => i === 0 ? 0 : Math.log(p / raw.prices[i - 1]));
const d = ret.map(r => r * r * 252);              // 每日年化變異數代理
const meanBack = (arr, i, w) => { let s = 0, c = 0; for (let j = Math.max(0, i - w + 1); j <= i; j++) { s += arr[j]; c++; } return s / c; };
const lv = (v) => Math.log(Math.max(Math.sqrt(v), MIN_VOL));
// HAR 三項（對數波動率）：日、週（5 日）、月（22 日）
raw.harD = d.map(v => lv(v));
raw.harW = d.map((_, i) => lv(meanBack(d, i, 5)));
raw.harM = d.map((_, i) => lv(meanBack(d, i, 22)));
// 目標：t 之後 H 個交易日的已實現波動率（對數）
const target = d.map((_, i) => {
    if (i + H >= n) return NaN;
    let s = 0; for (let k = 1; k <= H; k++) s += d[i + k];
    return lv(s / H);
});

const lit = new Map(dump.literature.map(f => [f.key, f]));
const HAR3 = ['harD', 'harW', 'harM'];
const UNC = ['epu', 'gpr', 'gprThreat', 'creditSpread', 'finStress', 'gvz'];
const usable = (k) => Array.isArray(raw[k === 'price' ? 'prices' : k]);
const original = ['price', ...Object.keys(dump.factors).filter(k => dump.factors[k] && !lit.has(k))].filter(usable);
const allKeys = ['price', ...Object.keys(dump.factors).filter(k => dump.factors[k])].filter(usable);
const uniq = (a) => [...new Set(a)];
const VARIANTS = [
    { key: 'lstm_har',  name: 'LSTM：HAR 三項',         desc: '只用日、週、月的過去波動（和 HAR 同樣的資訊）', features: HAR3 },
    { key: 'lstm_base', name: 'LSTM：原有變數',         desc: '原有變數 + HAR 三項', features: uniq([...original, ...HAR3]) },
    { key: 'lstm_unc',  name: 'LSTM：原有 + 不確定性',   desc: '再加上 EPU、GPR、GPR Threats、信用利差、金融壓力指數、黃金隱含波動率 GVZ', features: uniq([...original, ...HAR3, ...UNC.filter(usable)]) },
    { key: 'lstm_all',  name: 'LSTM：全部變數',         desc: '原有 + 兩批文獻變數 + HAR 三項', features: uniq([...allKeys, ...HAR3]) },
];
const series = (k) => raw[k === 'price' ? 'prices' : k];

// 樣本 s 對應時間 t = T0 + s：輸入是到 t 為止的視窗，目標是 t 之後 H 日的波動率
const T0 = Math.max(CFG.lookback - 1, 22);
const N = n - H - T0;
const tOf = (s) => T0 + s;

const splitHoldout = (m, frac = 0.15) => { const hold = Math.max(20, Math.floor(m * frac)); return { trainEnd: m - hold, holdStart: m - hold }; };
// 評估期間：4 段 walk-forward + 最終測試段（與先前的方向實驗相同的切法）
const periods = (() => {
    const sp = splitHoldout(N), k = 4, blk = Math.floor(sp.trainEnd / (k + 1)), out = [];
    for (let f = 0; f < k; f++) {
        const end = (f + 1) * blk;
        out.push({ label: `第 ${f + 1} 段`, end, testFrom: end, testTo: f === k - 1 ? sp.trainEnd - P : end + blk });
    }
    out.push({ label: '最終測試段', end: sp.trainEnd, testFrom: sp.holdStart, testTo: N });
    out.forEach(p => { p.from = raw.dates[tOf(p.testFrom) + H]; p.to = raw.dates[tOf(p.testTo - 1) + H]; });
    return out;
})();
// 訓練段 [0, trainTo)，其中最後一段當驗證（早停用）
const segs = (end) => {
    const trainTo = end - P;
    const inner = splitHoldout(trainTo);
    return { fitTo: inner.trainEnd - P, valFrom: inner.trainEnd, valTo: trainTo, trainTo };
};

// ── 線性代數（OLS / 嶺迴歸）──────────────────────────────────
const solve = (A, b) => {   // 高斯消去
    const m = A.length, M = A.map((r, i) => [...r, b[i]]);
    for (let c = 0; c < m; c++) {
        let p = c; for (let r = c + 1; r < m; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
        [M[c], M[p]] = [M[p], M[c]];
        for (let r = 0; r < m; r++) if (r !== c) { const f = M[r][c] / M[c][c]; for (let k = c; k <= m; k++) M[r][k] -= f * M[c][k]; }
    }
    return M.map((r, i) => r[m] / r[i]);
};
// 特徵先在訓練段標準化，再做嶺迴歸（λ 很小，只為數值穩定）
const ridge = (rows, y, lambda = 1e-4) => {
    const k = rows[0].length;
    const mu = Array.from({ length: k }, (_, j) => rows.reduce((s, r) => s + r[j], 0) / rows.length);
    const sd = Array.from({ length: k }, (_, j) => Math.sqrt(rows.reduce((s, r) => s + (r[j] - mu[j]) ** 2, 0) / rows.length) || 1);
    const Z = rows.map(r => [1, ...r.map((v, j) => (v - mu[j]) / sd[j])]);
    const A = Array.from({ length: k + 1 }, (_, i) => Array.from({ length: k + 1 }, (_, j) => Z.reduce((s, z) => s + z[i] * z[j], 0) + (i === j && i > 0 ? lambda * Z.length : 0)));
    const b = Array.from({ length: k + 1 }, (_, i) => Z.reduce((s, z, r) => s + z[i] * y[r], 0));
    const w = solve(A, b);
    return (r) => w[0] + r.reduce((s, v, j) => s + w[j + 1] * (v - mu[j]) / sd[j], 0);
};

// ── 評估指標 ──────────────────────────────────────────────────
// fc：{ vol: σ̂[], var: ĥ[] }，actual：σ[]
const metrics = (fc, from, to, trainMeanVol) => {
    let qlike = 0, se = 0, seMean = 0, dirHit = 0, dirN = 0;
    const m = to - from;
    for (let i = 0; i < m; i++) {
        const s = from + i, t = tOf(s);
        const a = Math.exp(target[t]), a2 = a * a;
        const h = Math.max(fc.var[i], 1e-8), v = fc.vol[i];
        qlike += a2 / h - Math.log(a2 / h) - 1;
        se += (v - a) ** 2; seMean += (trainMeanVol - a) ** 2;
        const now = Math.exp(raw.harW[t]);
        if (Math.abs(a - now) > 1e-9) { dirN++; if ((v > now) === (a > now)) dirHit++; }
    }
    return { QLIKE: qlike / m, RMSE: Math.sqrt(se / m) * 100, R2: 1 - se / seMean, dirAcc: dirN ? dirHit / dirN * 100 : NaN, n: m };
};
// 對數預測轉成波動率與變異數：用驗證段殘差變異數 s² 修正對數常態的偏誤
const fromLog = (yhat, s2) => ({ vol: yhat.map(y => Math.exp(y + s2 / 2)), var: yhat.map(y => Math.exp(2 * y + 2 * s2)) });

// ── 計量基準 ──────────────────────────────────────────────────
const baselines = (p) => {
    const g = segs(p.end);
    const tr = Array.from({ length: g.trainTo }, (_, s) => s);
    const te = Array.from({ length: p.testTo - p.testFrom }, (_, i) => p.testFrom + i);
    const trainMeanVol = tr.reduce((s, x) => s + Math.exp(target[tOf(x)]), 0) / tr.length;
    const out = {};
    // 隨機漫步：未來 = 過去 5 日
    out.rw = { vol: te.map(s => Math.exp(raw.harW[tOf(s)])), var: te.map(s => Math.exp(2 * raw.harW[tOf(s)])) };
    // EWMA（RiskMetrics）
    const lam = 0.94; const ew = new Array(n); ew[0] = d[1] || 1e-4;
    for (let i = 1; i < n; i++) ew[i] = lam * ew[i - 1] + (1 - lam) * d[i];
    out.ewma = { vol: te.map(s => Math.sqrt(ew[tOf(s)])), var: te.map(s => ew[tOf(s)]) };
    // HAR / HAR-X：log σ 的 OLS，殘差變異數用訓練段估計
    // 隱含波動率類（VIX、GVZ）取對數，和 log σ 同尺度
    const xv = (k, t) => ['vixPrices', 'gvz'].includes(k) ? Math.log(Math.max(series(k)[t], 1e-6)) : series(k)[t];
    const fitLog = (keys) => {
        const rows = tr.map(s => keys.map(k => xv(k, tOf(s))));
        const y = tr.map(s => target[tOf(s)]);
        const f = ridge(rows, y);
        const res = rows.map((r, i) => y[i] - f(r));
        const s2 = res.reduce((a, b) => a + b * b, 0) / res.length;
        return { fc: fromLog(te.map(s => f(keys.map(k => xv(k, tOf(s))))), s2), coef: keys };
    };
    out.har = fitLog(HAR3).fc;
    const xKeys = [...HAR3, 'vixPrices', ...UNC].filter(k => usable(k) && dump.factors[k] !== false);
    out.harx = fitLog(xKeys).fc;
    out.harxKeys = xKeys;
    return { fc: out, trainMeanVol };
};

// ── LSTM（子行程）────────────────────────────────────────────
const runLstm = async (job) => {
    const require = createRequire(import.meta.url);
    process.env.TF_CPP_MIN_LOG_LEVEL ??= '2';
    const tf = require('@tensorflow/tfjs-node');
    globalThis.tf = tf;
    const core = await import('../src/core.js');
    const p = periods[job.period], g = segs(p.end);
    const feats = job.features;
    // 每個特徵用「訓練段最後一個樣本的時間點」以前的資料做 min-max
    const lastT = tOf(g.trainTo - 1);
    const sc = feats.map(k => {
        const a = series(k); let lo = Infinity, hi = -Infinity;
        for (let i = 0; i <= lastT; i++) { if (a[i] < lo) lo = a[i]; if (a[i] > hi) hi = a[i]; }
        const r = hi - lo; return (v) => r ? (v - lo) / r : 0;
    });
    const cols = feats.map((k, j) => series(k).map(sc[j]));
    const win = (s) => { const t = tOf(s), w = []; for (let i = t - CFG.lookback + 1; i <= t; i++) w.push(cols.map(c => c[i])); return w; };
    const yTr = Array.from({ length: g.trainTo }, (_, s) => target[tOf(s)]);
    const mu = yTr.slice(0, g.fitTo).reduce((a, b) => a + b, 0) / g.fitTo;
    const sd = Math.sqrt(yTr.slice(0, g.fitTo).reduce((a, b) => a + (b - mu) ** 2, 0) / g.fitTo) || 1;
    const X = (from, to) => core.windowsToTensor(Array.from({ length: to - from }, (_, i) => win(from + i)));
    const Y = (from, to) => tf.tensor2d(Array.from({ length: to - from }, (_, i) => [(target[tOf(from + i)] - mu) / sd]));
    const model = core.createModel(CFG.algorithm, CFG.lookback, feats.length, { layers: CFG.layers, predTarget: 'logreturn', huberDelta: 1.0 });
    model.compile({ optimizer: tf.train.adam(CFG.lr), loss: core.resolveTargetSpec('logreturn', { huberDelta: 1.0 }).loss });
    const xT = X(0, g.fitTo), yT = Y(0, g.fitTo), xV = X(g.valFrom, g.valTo), yV = Y(g.valFrom, g.valTo);
    let best = Infinity, bestW = null, since = 0, bestEpoch = 0;
    await model.fit(xT, yT, {
        epochs: CFG.epochs, batchSize: CFG.batchSize, verbose: 0, shuffle: true, validationData: [xV, yV],
        callbacks: { onEpochEnd: async (ep, l) => {
            if (l.val_loss < best - 1e-7) { best = l.val_loss; bestEpoch = ep + 1; since = 0; bestW?.forEach(w => w.dispose()); bestW = model.getWeights().map(w => w.clone()); }
            else if (++since >= CFG.patience) model.stopTraining = true;
        } },
    });
    if (bestW) { model.setWeights(bestW); bestW.forEach(w => w.dispose()); }
    const pred = (from, to) => { const x = X(from, to); const o = model.predict(x); const v = Array.from(o.dataSync()).map(z => z * sd + mu); x.dispose(); o.dispose(); return v; };
    const vp = pred(g.valFrom, g.valTo);
    const s2 = vp.reduce((a, y, i) => a + (target[tOf(g.valFrom + i)] - y) ** 2, 0) / vp.length;
    const yhat = pred(p.testFrom, p.testTo);
    [xT, yT, xV, yV].forEach(t => t.dispose()); model.dispose();
    return { yhat, s2, bestEpoch };
};

if (arg('--worker')) {
    const job = JSON.parse(readFileSync(arg('--worker'), 'utf8'));
    try { process.stdout.write('RESULT ' + JSON.stringify(await runLstm(job)) + '\n'); }
    catch (e) { process.stdout.write('RESULT ' + JSON.stringify({ error: e.message }) + '\n'); }
    process.exit(0);
}

const runPool = async (jobs, onDone) => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'silver-vol-'));
    const results = new Array(jobs.length);
    let next = 0;
    const runOne = (i) => new Promise((resolve) => {
        const file = path.join(dir, `job-${i}.json`);
        writeFileSync(file, JSON.stringify(jobs[i]));
        const child = spawn(process.execPath, ['--no-warnings', fileURLToPath(import.meta.url), '--worker', file, '--worker-data', path.resolve(arg('--data')), ...args.filter((a, j) => !['--data', '--worker'].includes(a) && !['--data', '--worker'].includes(args[j - 1]))],
            { env: { ...process.env, TF_CPP_MIN_LOG_LEVEL: '2' }, stdio: ['ignore', 'pipe', 'pipe'] });
        let out = '', err = '';
        child.stdout.on('data', x => { out += x; });
        child.stderr.on('data', x => { err += x; });
        child.on('close', () => {
            const line = out.split('\n').reverse().find(l => l.startsWith('RESULT '));
            let r; try { r = line ? JSON.parse(line.slice(7)) : { error: err.trim().split('\n').pop() || '子行程沒有回傳結果' }; } catch (e) { r = { error: e.message }; }
            results[i] = r; onDone(i, r); resolve();
        });
    });
    const lane = async () => { while (next < jobs.length) await runOne(next++); };
    await Promise.all(Array.from({ length: Math.min(WORKERS, jobs.length) }, lane));
    rmSync(dir, { recursive: true, force: true });
    return results;
};

// ── 主流程 ────────────────────────────────────────────────────
const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
log(`資料：${raw.dates[0]} ~ ${raw.dates[n - 1]}，${n} 個交易日；預測未來 ${H} 日已實現波動率，樣本 ${N} 個`);
periods.forEach(p => log(`  ${p.label}：${p.from} ~ ${p.to}（${p.testTo - p.testFrom} 筆）`));
VARIANTS.forEach(v => log(`  ${v.name}：${v.features.length} 個特徵`));

const BASE_NAMES = { rw: '隨機漫步（過去 5 日）', ewma: 'EWMA（λ = 0.94）', har: 'HAR', harx: 'HAR-X（+ 隱含波動率、不確定性）' };
const base = periods.map(p => baselines(p));
log(`HAR-X 變數：${base[0].fc.harxKeys.join(', ')}`);

const jobs = [];
VARIANTS.forEach(v => { for (let rep = 0; rep < REPEATS; rep++) periods.forEach((_, pi) => jobs.push({ variant: v.key, rep, period: pi, features: v.features })); });
log(`LSTM：${VARIANTS.length} 組 × ${REPEATS} 次 × ${periods.length} 期 = ${jobs.length} 次訓練（${WORKERS} 個行程）`);
let done = 0;
const res = await runPool(jobs, (i, r) => {
    done++;
    const j = jobs[i], p = periods[j.period];
    if (r.error) return log(`  [${done}/${jobs.length}] ${j.variant} #${j.rep + 1} ${p.label} 失敗：${r.error}`);
    const m = metrics(fromLog(r.yhat, r.s2), p.testFrom, p.testTo, base[j.period].trainMeanVol);
    log(`  [${done}/${jobs.length}] ${j.variant} #${j.rep + 1} ${p.label}：QLIKE ${m.QLIKE.toFixed(3)}  RMSE ${m.RMSE.toFixed(2)}  R² ${m.R2.toFixed(3)}  升降 ${m.dirAcc.toFixed(1)}%`);
});

// 彙總：每個模型每期的指標（LSTM 取重訓的平均，另外算「重訓平均預測」的集成）
const rows = [];
periods.forEach((p, pi) => {
    const tm = base[pi].trainMeanVol;
    for (const k of ['rw', 'ewma', 'har', 'harx']) rows.push({ model: k, period: pi, ...metrics(base[pi].fc[k], p.testFrom, p.testTo, tm) });
    for (const v of VARIANTS) {
        const rs = res.filter((r, i) => r && !r.error && jobs[i].variant === v.key && jobs[i].period === pi);
        if (!rs.length) continue;
        const each = rs.map(r => metrics(fromLog(r.yhat, r.s2), p.testFrom, p.testTo, tm));
        const avg = (f) => mean(each.map(f));
        const sdv = (f) => { const a = each.map(f), m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / Math.max(1, a.length - 1)); };
        rows.push({ model: v.key, period: pi, runs: rs.length, QLIKE: avg(m => m.QLIKE), RMSE: avg(m => m.RMSE), R2: avg(m => m.R2), dirAcc: avg(m => m.dirAcc), QLIKEsd: sdv(m => m.QLIKE), RMSEsd: sdv(m => m.RMSE), n: each[0].n,
            runsDetail: each.map(m => ({ QLIKE: m.QLIKE, RMSE: m.RMSE, R2: m.R2, dirAcc: m.dirAcc })) });
        const ens = rs[0].yhat.map((_, i) => mean(rs.map(r => r.yhat[i])));
        rows.push({ model: v.key + '_ens', period: pi, ...metrics(fromLog(ens, mean(rs.map(r => r.s2))), p.testFrom, p.testTo, tm) });
    }
});
const models = [
    ...Object.entries(BASE_NAMES).map(([key, name]) => ({ key, name, kind: 'baseline' })),
    ...VARIANTS.map(v => ({ key: v.key, name: v.name, desc: v.desc, kind: 'lstm', features: v.features })),
    ...VARIANTS.map(v => ({ key: v.key + '_ens', name: v.name + '（' + REPEATS + ' 次集成）', kind: 'ensemble' })),
];
const summary = models.map(m => {
    const rs = rows.filter(r => r.model === m.key);
    if (!rs.length) return null;
    const harQ = periods.map((_, pi) => rows.find(r => r.model === 'har' && r.period === pi).QLIKE);
    return { ...m, QLIKE: mean(rs.map(r => r.QLIKE)), RMSE: mean(rs.map(r => r.RMSE)), R2: mean(rs.map(r => r.R2)), dirAcc: mean(rs.map(r => r.dirAcc)),
        beatsHar: rs.filter(r => r.QLIKE < harQ[r.period]).length };
}).filter(Boolean);
log('── 彙總（各期間平均）──');
summary.forEach(s => log(`  ${s.name.padEnd(24)} QLIKE ${s.QLIKE.toFixed(3)}  RMSE ${s.RMSE.toFixed(2)}  R² ${s.R2.toFixed(3)}  升降 ${s.dirAcc.toFixed(1)}%  勝過 HAR ${s.beatsHar}/${periods.length} 期`));

mkdirSync(OUT, { recursive: true });
// 圖表用：最終測試段的實際波動與各模型預測
const fp = periods.length - 1, P_ = periods[fp];
const pathOf = (fc) => fc.vol.map(v => +(v * 100).toFixed(2));
const finalRuns = (k) => res.filter((r, i) => r && !r.error && jobs[i].variant === k && jobs[i].period === fp);
const ensVol = (k) => { const rs = finalRuns(k); if (!rs.length) return null; const e = rs[0].yhat.map((_, i) => mean(rs.map(r => r.yhat[i]))); return pathOf(fromLog(e, mean(rs.map(r => r.s2)))); };
writeFileSync(path.join(OUT, 'vol-compare.json'), JSON.stringify({
    createdAt: new Date().toISOString(), durationSec: (Date.now() - t0) / 1000,
    dataRange: { from: raw.dates[0], to: raw.dates[n - 1], days: n }, horizon: H, repeats: REPEATS, config: CFG,
    excludedFactors: dump.excluded, harxKeys: base[0].fc.harxKeys,
    periods: periods.map(p => ({ label: p.label, from: p.from, to: p.to, n: p.testTo - p.testFrom })),
    models, summary, rows,
    finalPath: {
        dates: Array.from({ length: P_.testTo - P_.testFrom }, (_, i) => raw.dates[tOf(P_.testFrom + i)]),
        actual: Array.from({ length: P_.testTo - P_.testFrom }, (_, i) => +(Math.exp(target[tOf(P_.testFrom + i)]) * 100).toFixed(2)),
        rw: pathOf(base[fp].fc.rw), har: pathOf(base[fp].fc.har), harx: pathOf(base[fp].fc.harx),
        lstm: Object.fromEntries(VARIANTS.map(v => [v.key, ensVol(v.key)])),
    },
}, null, 1));
log(`輸出到 ${path.relative(process.cwd(), path.join(OUT, 'vol-compare.json'))}`);

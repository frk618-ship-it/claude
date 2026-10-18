#!/usr/bin/env node
// 每日（隔日）預測：2024 年起逐季滾動重訓，評估每個交易日的樣本外預測
//
//   node train.mjs --config compare-features.config.json --dump-data runs/vol-data.json   # 先抓好資料
//   node daily.mjs --data runs/vol-data.json [--eval-from 2024-01-01] [--repeats 3] [--workers 4]
//
// 做法
//   每季開始前，只用「該季第一天以前」的資料訓練，預測該季每一天的隔日報酬（每日一個樣本，不重疊）。
//   比較兩種訓練視窗：全部歷史（2016 起）、只用最近 3 年（756 個交易日）——檢驗近年行情改變時，舊資料是否有害。
//   每個設定重訓 N 次、取平均預測（集成），降低隨機起始值的影響。
//   最後用全部資料訓練，產生下一個交易日的預測。
// 輸出：output/daily.json
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

const CFG = {
    lookback: parseInt(arg('--lookback', '20'), 10), layers: parseInt(arg('--layers', '32'), 10),
    lr: parseFloat(arg('--lr', '0.001')), batchSize: parseInt(arg('--batch', '32'), 10),
    epochs: parseInt(arg('--epochs', '400'), 10), patience: parseInt(arg('--patience', '30'), 10),
    algorithm: arg('--algorithm', 'LSTM'), huberDelta: 1.0,
};
const EVAL_FROM = arg('--eval-from', '2024-01-01');
const REPEATS = parseInt(arg('--repeats', '3'), 10);
const WORKERS = parseInt(arg('--workers', String(os.cpus().length)), 10);
const RECENT = 756;   // 最近 3 年
const OUT = path.resolve(HERE, arg('--out', 'output'));
const DATA = path.resolve(HERE, arg('--data') || arg('--worker-data') || 'runs/vol-data.json');

// ── 資料與特徵 ────────────────────────────────────────────────
const dump = JSON.parse(readFileSync(DATA, 'utf8'));
const raw = dump.raw;
const n = raw.prices.length;
const lit = new Set(dump.literature.map(f => f.key));
const UNC = ['epu', 'gpr', 'gprThreat', 'creditSpread', 'finStress'];
const series = (k) => raw[k === 'price' ? 'prices' : k];
const usable = (k) => Array.isArray(series(k));
// 原有變數 + 不確定性變數（先前 5 日實驗中最好的一組）
const FEATURES = ['price', ...Object.keys(dump.factors).filter(k => dump.factors[k] && !lit.has(k)), ...UNC].filter(usable);
// 樣本 t：輸入是到 t 為止的視窗，目標是 t+1 的對數報酬
const logRet = raw.prices.map((p, i) => i === 0 ? 0 : Math.log(p / raw.prices[i - 1]));
const T0 = CFG.lookback - 1;
const lastT = n - 2;                 // 最後一個有目標的樣本
const targetOf = (t) => logRet[t + 1];

// 季度切分：每季第一個目標日 >= EVAL_FROM
const quarterOf = (d) => `${d.slice(0, 4)}Q${Math.floor((+d.slice(5, 7) - 1) / 3) + 1}`;
const folds = (() => {
    const out = [];
    for (let t = T0; t <= lastT; t++) {
        const d = raw.dates[t + 1];
        if (d < EVAL_FROM) continue;
        const q = quarterOf(d);
        if (!out.length || out[out.length - 1].q !== q) out.push({ q, from: t, to: t + 1 });
        else out[out.length - 1].to = t + 1;
    }
    return out;
})();
const VARIANTS = [
    { key: 'all',    name: '全部歷史訓練', desc: '每季重訓時使用 2016 年起的所有資料' },
    { key: 'recent', name: '近 3 年訓練',  desc: '每季重訓時只用最近 756 個交易日（約 3 年）' },
];

// ── 訓練（子行程）────────────────────────────────────────────
// job：{ variant, rep, trainFrom, trainTo（不含）, predFrom, predTo }，t 是樣本索引（時間點）
const runJob = async (job) => {
    const require = createRequire(import.meta.url);
    process.env.TF_CPP_MIN_LOG_LEVEL ??= '2';
    const tf = require('@tensorflow/tfjs-node');
    globalThis.tf = tf;
    const core = await import('../src/core.js');
    const { trainFrom, trainTo, predFrom, predTo } = job;
    // 正規化只用訓練段（到 trainTo-1 為止的時間點）
    const sc = FEATURES.map(k => {
        const a = series(k); let lo = Infinity, hi = -Infinity;
        for (let i = Math.max(0, trainFrom - CFG.lookback); i < trainTo; i++) { if (a[i] < lo) lo = a[i]; if (a[i] > hi) hi = a[i]; }
        const r = hi - lo; return (v) => r ? (v - lo) / r : 0;
    });
    const cols = FEATURES.map((k, j) => series(k).map(sc[j]));
    const win = (t) => { const w = []; for (let i = t - CFG.lookback + 1; i <= t; i++) w.push(cols.map(c => c[i])); return w; };
    // 訓練段最後 15% 當驗證（早停）
    const valFrom = trainTo - Math.max(40, Math.floor((trainTo - trainFrom) * 0.15));
    const ys = []; for (let t = trainFrom; t < valFrom; t++) ys.push(targetOf(t));
    const mu = ys.reduce((a, b) => a + b, 0) / ys.length;
    const sd = Math.sqrt(ys.reduce((a, b) => a + (b - mu) ** 2, 0) / ys.length) || 1;
    const X = (a, b) => core.windowsToTensor(Array.from({ length: b - a }, (_, i) => win(a + i)));
    const Y = (a, b) => tf.tensor2d(Array.from({ length: b - a }, (_, i) => [(targetOf(a + i) - mu) / sd]));
    const model = core.createModel(CFG.algorithm, CFG.lookback, FEATURES.length, { layers: CFG.layers, predTarget: 'logreturn', huberDelta: CFG.huberDelta });
    model.compile({ optimizer: tf.train.adam(CFG.lr), loss: core.resolveTargetSpec('logreturn', { huberDelta: CFG.huberDelta }).loss });
    const xT = X(trainFrom, valFrom), yT = Y(trainFrom, valFrom), xV = X(valFrom, trainTo), yV = Y(valFrom, trainTo);
    let best = Infinity, bestW = null, since = 0, bestEpoch = 0;
    await model.fit(xT, yT, {
        epochs: CFG.epochs, batchSize: CFG.batchSize, verbose: 0, shuffle: true, validationData: [xV, yV],
        callbacks: { onEpochEnd: async (ep, l) => {
            if (l.val_loss < best - 1e-7) { best = l.val_loss; bestEpoch = ep + 1; since = 0; bestW?.forEach(w => w.dispose()); bestW = model.getWeights().map(w => w.clone()); }
            else if (++since >= CFG.patience) model.stopTraining = true;
        } },
    });
    if (bestW) { model.setWeights(bestW); bestW.forEach(w => w.dispose()); }
    const x = X(predFrom, predTo), o = model.predict(x);
    const pred = Array.from(o.dataSync()).map(z => z * sd + mu);
    [xT, yT, xV, yV, x, o].forEach(t => t.dispose()); model.dispose();
    return { pred, bestEpoch };
};

if (arg('--worker')) {
    const job = JSON.parse(readFileSync(arg('--worker'), 'utf8'));
    try { process.stdout.write('RESULT ' + JSON.stringify(await runJob(job)) + '\n'); }
    catch (e) { process.stdout.write('RESULT ' + JSON.stringify({ error: e.message }) + '\n'); }
    process.exit(0);
}

const runPool = async (jobs, onDone) => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'silver-daily-'));
    const results = new Array(jobs.length);
    let next = 0;
    const passArgs = args.filter((a, j) => !['--data', '--worker'].includes(a) && !['--data', '--worker'].includes(args[j - 1]));
    const runOne = (i) => new Promise((resolve) => {
        const file = path.join(dir, `job-${i}.json`);
        writeFileSync(file, JSON.stringify(jobs[i]));
        const child = spawn(process.execPath, ['--no-warnings', fileURLToPath(import.meta.url), '--worker', file, '--worker-data', DATA, ...passArgs],
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
const trainStart = (v, to) => v === 'recent' ? Math.max(T0, to - RECENT) : T0;
log(`資料：${raw.dates[0]} ~ ${raw.dates[n - 1]}；評估 ${raw.dates[folds[0].from + 1]} ~ ${raw.dates[lastT + 1]}，${folds.length} 季、${folds.reduce((a, f) => a + f.to - f.from, 0)} 個每日樣本`);
log(`特徵 ${FEATURES.length} 個：${FEATURES.join(', ')}`);

const jobs = [];
VARIANTS.forEach(v => { for (let rep = 0; rep < REPEATS; rep++) folds.forEach((f, fi) => jobs.push({ kind: 'fold', variant: v.key, rep, fold: fi, trainFrom: trainStart(v.key, f.from), trainTo: f.from, predFrom: f.from, predTo: f.to })); });
// 最終模型：用全部資料訓練，預測「最新一天之後」的隔日報酬（樣本 t = n-1 沒有目標）
VARIANTS.forEach(v => { for (let rep = 0; rep < REPEATS; rep++) jobs.push({ kind: 'final', variant: v.key, rep, trainFrom: trainStart(v.key, lastT + 1), trainTo: lastT + 1, predFrom: n - 1, predTo: n }); });
log(`訓練：${VARIANTS.length} 種視窗 × ${REPEATS} 次 × (${folds.length} 季 + 1 個最終模型) = ${jobs.length} 次（${WORKERS} 個行程）`);
let done = 0;
const res = await runPool(jobs, (i, r) => {
    done++;
    const j = jobs[i];
    if (r.error) return log(`  [${done}/${jobs.length}] ${j.variant} #${j.rep + 1} ${j.kind === 'final' ? '最終' : folds[j.fold].q} 失敗：${r.error}`);
    if (j.kind === 'final') return log(`  [${done}/${jobs.length}] ${j.variant} #${j.rep + 1} 最終模型：明日預測報酬 ${(r.pred[0] * 100).toFixed(2)}%`);
    const f = folds[j.fold];
    const hit = r.pred.filter((p, k) => (p > 0) === (targetOf(f.from + k) > 0)).length;
    log(`  [${done}/${jobs.length}] ${j.variant} #${j.rep + 1} ${f.q}：DA ${(hit / r.pred.length * 100).toFixed(1)}%（${r.pred.length} 天，epoch ${r.bestEpoch}）`);
});

// 每日預測（集成 = 各次重訓平均）
const daily = { dates: [], prev: [], actual: [], actRet: [], sigma: [] };
const ewma = (() => { let h = mean(logRet.slice(1, 21).map(x => x * x)); return logRet.map((x, i) => { if (i > 0) h = 0.94 * h + 0.06 * x * x; return Math.sqrt(h * 252); }); })();
folds.forEach(f => { for (let t = f.from; t < f.to; t++) {
    daily.dates.push(raw.dates[t + 1]); daily.prev.push(raw.prices[t]); daily.actual.push(raw.prices[t + 1]);
    daily.actRet.push(targetOf(t)); daily.sigma.push(ewma[t]);
} });
const predOf = {}, runsDA = {};
VARIANTS.forEach(v => {
    const all = [];
    folds.forEach((f, fi) => {
        const rs = res.filter((r, i) => r && !r.error && jobs[i].kind === 'fold' && jobs[i].variant === v.key && jobs[i].fold === fi);
        for (let k = 0; k < f.to - f.from; k++) all.push(rs.length ? mean(rs.map(r => r.pred[k])) : NaN);
    });
    predOf[v.key] = all;
    // 單次重訓（不集成）的整體 DA，看隨機起始值造成的差異
    runsDA[v.key] = Array.from({ length: REPEATS }, (_, rep) => {
        let hit = 0, cnt = 0;
        folds.forEach((f, fi) => {
            const j = jobs.findIndex(x => x.kind === 'fold' && x.variant === v.key && x.rep === rep && x.fold === fi);
            const r = res[j]; if (!r || r.error) return;
            r.pred.forEach((p, k) => { cnt++; if ((p > 0) === (targetOf(f.from + k) > 0)) hit++; });
        });
        return cnt ? hit / cnt * 100 : NaN;
    });
});
const finalPred = Object.fromEntries(VARIANTS.map(v => {
    const rs = res.filter((r, i) => r && !r.error && jobs[i].kind === 'final' && jobs[i].variant === v.key).map(r => r.pred[0]);
    return [v.key, { mean: rs.length ? mean(rs) : NaN, runs: rs }];
}));

mkdirSync(OUT, { recursive: true });
writeFileSync(path.join(OUT, 'daily.json'), JSON.stringify({
    createdAt: new Date().toISOString(), durationSec: (Date.now() - t0) / 1000,
    dataRange: { from: raw.dates[0], to: raw.dates[n - 1], days: n }, evalFrom: EVAL_FROM,
    config: { ...CFG, repeats: REPEATS, recentWindow: RECENT, features: FEATURES }, excluded: dump.excluded,
    variants: VARIANTS, folds: folds.map(f => ({ q: f.q, from: raw.dates[f.from + 1], to: raw.dates[f.to], n: f.to - f.from })),
    daily, pred: predOf, runsDA,
    forecast: { lastDate: raw.dates[n - 1], lastPrice: raw.prices[n - 1], sigma: ewma[n - 1], byVariant: finalPred },
    recentPrices: { dates: raw.dates.slice(-760), prices: raw.prices.slice(-760) },
}));
log(`輸出到 ${path.relative(process.cwd(), path.join(OUT, 'daily.json'))}`);

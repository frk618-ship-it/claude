#!/usr/bin/env node
// ══════════════════════════════════════════════════════════════
// Silver AI Trader 雲端訓練
//
// 以原生 TensorFlow（@tensorflow/tfjs-node）訓練，與瀏覽器版共用 src/core.js：
// 特徵工程、正規化、時序切分、模型結構、評分方式完全一致。
// 訓練好的模型是 TF.js 格式，可直接在瀏覽器版「載入雲端模型」使用。
//
// 用法（在 cloud/ 目錄）：
//   npm install
//   node train.mjs                          從 Yahoo Finance 抓 8 個市場品種後訓練
//   node train.mjs --data 匯出檔.json       使用瀏覽器「匯出訓練資料」的完整資料（含 COT/TIPS 等）
//   node train.mjs --config my.json         覆寫參數（格式見 config.example.json）
//   node train.mjs --trials 20              先做 20 組超參數搜尋，再用最佳組合訓練
//   node train.mjs --out output             輸出目錄（預設 output/）
//   node train.mjs --workers 4              平行執行的行程數（預設 = CPU 核心數；用於參數搜尋與交叉驗證）
//   node train.mjs --horizon 5              預測 5 個交易日後的報酬（預設 1 = 隔日）
//
// 評估方式（比瀏覽器版更嚴謹）：
//   資料依時間切成 訓練 | 驗證 | 測試 三段。
//   驗證段只用來決定早停與超參數；測試段（= 瀏覽器版的保留集）完全不參與任何選擇，
//   報告上的準確率都來自測試段。
//   評估完成後，預設再以相同參數、相同 epoch 數用「全部資料」重訓一次作為輸出模型，
//   讓預測用上最新的資料（--no-refit 可關閉）。
// ══════════════════════════════════════════════════════════════
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { spawn } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

// Node 內建 fetch 預設不走 HTTPS_PROXY；在有代理的環境（如 Claude 雲端）自動以支援代理的設定重新啟動
if ((process.env.HTTPS_PROXY || process.env.https_proxy) && !process.env.NODE_USE_ENV_PROXY) {
    const r = spawnSync(process.execPath, ['--no-warnings', ...process.argv.slice(1)], {
        stdio: 'inherit', env: { ...process.env, NODE_USE_ENV_PROXY: '1' },
    });
    process.exit(r.status ?? 1);
}

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
process.env.TF_CPP_MIN_LOG_LEVEL ??= '2';
const tf = require('@tensorflow/tfjs-node');
globalThis.tf = tf;
const core = await import('../src/core.js');
const sources = await import('./sources.mjs');
const {
    MARKET_SYMBOLS, alignSymbolSeries, addDaysISO, deriveFeatures, filterDataByYears, checkDataQuality,
    createMultivariateDataset, createModel, resolveTargetSpec, buildTargetTensor, windowsToTensor,
    splitHoldout, scoreHoldout, calculateMAPE, decodePredictedPrices, simulateBacktest,
    runMultiStepPrediction, VALIDATION_MODE,
} = core;

// ── 參數 ──────────────────────────────────────────────────────
const args = process.argv.slice(2);
const arg = (name, def = null) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const flag = (name) => args.includes(name);

const DEFAULT_CONFIG = {
    // 與瀏覽器 DEFAULT_PARAMS 相同；epochs 是上限，實際由早停決定
    params: {
        lookback: 10, epochs: 400, layers: 32, lr: 0.001,
        futureDays: 3, historyYears: 2, batchSize: 32,
        directionalLambda: 0.3, huberDelta: 1.0,
        algorithm: 'BiGRU', validationMode: VALIDATION_MODE.NONE, kFolds: 5,
        // 預設預測對數報酬率：正規化範圍只來自訓練期間，價格創新高時「價格迴歸」必須外插、誤差暴增，
        // 報酬率沒有這個問題（瀏覽器版預設仍是價格迴歸）
        predTarget: 'logreturn',
        // 預測幾個交易日後的報酬（1 = 隔日）。大於 1 時固定預測對數報酬率
        horizon: 1,
    },
    patience: 30,                // 驗證 loss 連續幾個 epoch 沒進步就停
    factors: {
        slvPrices: true, slvVolume: true, slvVolumeRoc: true,
        tipsYield: true, rsiPrices: true, rsiPrices12: true,
        vixRoc: true, socialSentiment: true, fearGreed: true,
        goldPrices: true, oilPrices: true, dxyPrices: true,
        vixPrices: true, tnxPrices: true, copperPrices: true,
        silverInventory: true, goldSilverRatio: true, cotNetPosition: true,
        priceROC: true, ma5Silver: true, ma20Silver: true,
        maCross: true, macdLine: true,
        gtrend: true,
        // 文獻建議的額外輸入（見 core.js LITERATURE_FEATURES）
        ...Object.fromEntries(core.LITERATURE_FEATURES.map(f => [f.key, true])),
    },
    backtest: { initialCapital: 100000, leverage: 1.0, positionPct: 1.0, feeBps: 2, slippageBps: 1, threshold: 0.0, allowShort: true },
    search: {
        trials: 0, epochs: 80, patience: 15,
        space: {
            algorithm: ['BiGRU', 'LSTM', 'Transformer', 'TimesFM'],
            lookback: [5, 10, 15, 20, 30, 40, 60],
            layers: [16, 32, 48, 64],
            batchSize: [16, 32, 64],
            directionalLambda: [0.0, 0.1, 0.2, 0.3, 0.5, 0.7],
            huberDelta: [0.5, 1.0, 1.5, 2.0],
            lr: [0.0005, 0.001, 0.002, 0.003],
            // 價格迴歸在價格創新高時必須外插（正規化範圍只來自訓練期間），對數報酬率沒有這個問題
            predTarget: ['price', 'logreturn'],
        },
        factorKeepProb: 0.7,
        // 合理性門檻：驗證期間的價格誤差不得超過「明天 = 今天」的這個倍數，才進入排名。
        // 驗證期間只有幾十天，單看方向準確率挑最高分，選到的常是運氣好、但價格完全失準的模型。
        maxMapeVsNaive: 1.5,
    },
    refit: true,
    workers: null,   // null = CPU 核心數
};

const deepMerge = (a, b) => {
    if (!b || typeof b !== 'object' || Array.isArray(b)) return b ?? a;
    const out = { ...a };
    for (const k of Object.keys(b)) out[k] = (a && typeof a[k] === 'object' && !Array.isArray(a[k])) ? deepMerge(a[k], b[k]) : b[k];
    return out;
};
let config = DEFAULT_CONFIG;
if (arg('--config')) config = deepMerge(config, JSON.parse(readFileSync(arg('--config'), 'utf8')));
if (arg('--trials')) config.search.trials = parseInt(arg('--trials'), 10);
if (arg('--epochs')) config.params.epochs = parseInt(arg('--epochs'), 10);
if (arg('--horizon')) config.params.horizon = parseInt(arg('--horizon'), 10);
if (flag('--no-refit')) config.refit = false;
if (arg('--workers')) config.workers = parseInt(arg('--workers'), 10);
const OUT = path.resolve(HERE, arg('--out', 'output'));
const DATA_FILE = arg('--data');

const t0 = Date.now();
const log = (...m) => console.log(`[${((Date.now() - t0) / 1000).toFixed(0).padStart(4)}s]`, ...m);

// ── 資料 ──────────────────────────────────────────────────────
// 從 Yahoo Finance 抓日線（與瀏覽器版 Yahoo 備援相同的端點）。
// 用明確的起訖時間：range=max 會被 Yahoo 自動改成月線。多抓半年給 lookback 視窗用。
const fetchYahoo = async (symbol, years) => {
    const period2 = Math.floor(Date.now() / 1000);
    const period1 = period2 - Math.ceil((Math.max(years, 1) + 0.5) * 365.25 * 86400);
    for (let attempt = 0; attempt < 4; attempt++) {
        for (const host of ['query1', 'query2']) {
            const url = `https://${host}.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?period1=${period1}&period2=${period2}&interval=1d`;
            try {
                const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0 (silver-ai-trader cloud trainer)' } });
                if (r.status === 429) throw new Error('HTTP 429');
                if (!r.ok) throw new Error('HTTP ' + r.status);
                const j = await r.json();
                const res = j?.chart?.result?.[0];
                const q = res?.indicators?.quote?.[0];
                if (!res?.timestamp || !q?.close) throw new Error('格式不符');
                const volume = (q.volume && q.volume.length === res.timestamp.length) ? q.volume.map(v => v ?? 0) : res.timestamp.map(() => 0);
                return { timestamps: res.timestamp, close: q.close, volume };
            } catch (e) {
                log(`  ${symbol} @${host} 失敗：${e.message}`);
            }
        }
        await new Promise(r => setTimeout(r, 1500 * 2 ** attempt));
    }
    return null;
};

const loadData = async () => {
    if (DATA_FILE) {
        log(`讀取瀏覽器匯出的資料：${DATA_FILE}`);
        const exported = JSON.parse(readFileSync(DATA_FILE, 'utf8'));
        const raw = exported.data || exported;
        if (!Array.isArray(raw?.prices) || !Array.isArray(raw?.dates)) throw new Error('資料檔格式不符（缺少 dates / prices）');
        return { raw, source: 'browser-export', unavailable: {}, sourceStatus: [] };
    }
    log('從 Yahoo Finance 抓取 8 個市場品種…');
    const series = {};
    for (const s of MARKET_SYMBOLS) {
        series[s] = await fetchYahoo(s, config.params.historyYears);
        if (!series[s]) {
            if (s === 'SI=F') throw new Error('白銀期貨 (SI=F) 抓取失敗，無法訓練');
            log(`  ⚠️ ${s} 抓取失敗`);
        } else {
            log(`  ${s}: ${series[s].timestamps.length} 筆`);
        }
        await new Promise(r => setTimeout(r, 400));
    }
    const missing = MARKET_SYMBOLS.filter(s => !series[s]);
    // 抓不到的品種用白銀期貨的時間軸填中性值（並標記為模擬），由 checkDataQuality 排除相關因子
    for (const s of missing) series[s] = { ...series['SI=F'], close: series['SI=F'].close.map(() => 1), isMock: true };
    const base = alignSymbolSeries(series);

    // 輔助資料：五個來源平行抓取；抓不到的就排除對應因子（並在報告列出原因）
    log('抓取輔助資料（CFTC COT、美國財政部 TIPS、CNN Fear & Greed、Google Trends、StockTwits）…');
    const [cot, tips, fg, gt, st] = await Promise.allSettled([
        sources.fetchCOT(core),
        sources.fetchTIPS(core, base.dates),
        sources.fetchFearGreed(core, base.dates),
        sources.fetchGoogleTrends(core, base.dates),
        sources.fetchStockTwits(core),
    ]);
    const aux = { mockSymbols: missing };
    const unavailable = {};
    const sourceStatus = [];
    const note = (name, res, okDetail, factor, onOk) => {
        if (res.status === 'fulfilled') { onOk(res.value); sourceStatus.push({ name, ok: true, detail: okDetail(res.value), factor, firstDate: res.value.firstDate || null }); }
        else {
            const why = res.reason?.message || String(res.reason);
            if (factor) unavailable[factor] = `${name}：${why}`;
            sourceStatus.push({ name, ok: false, detail: why });
        }
        log(`  ${res.status === 'fulfilled' ? '✓' : '✗'} ${name}：${sourceStatus[sourceStatus.length - 1].detail}`);
    };
    note('CFTC COT', cot, v => `${v.source}，${v.map.size} 週`, 'cotNetPosition', v => { aux.cotDataMap = v.map; });
    note('美國財政部 TIPS', tips, v => `${v.points} 天（${v.coverage}）`, 'tipsYield', v => { aux.tipsYield = v.values; });
    note('CNN Fear & Greed', fg, v => `${v.points} 天，目前 ${v.currentScore.toFixed(1)}（${v.currentRating}）`, 'fearGreed', v => { aux.fearGreedData = v; });
    note('Google Trends', gt, v => `${v.points} 週`, 'gtrend', v => { aux.gtrend = v.values; });
    // StockTwits 只影響最後一天；歷史部分是價格動量代理，抓不到也不排除因子
    note('StockTwits', st, v => `多方 ${v.bullishPct.toFixed(0)}%（${v.totalTagged} 則）`, null, v => { aux.stockTwitsResult = v; });

    const raw = deriveFeatures(base, aux);

    // 文獻建議的額外輸入：Yahoo 另外 8 個品種 + 財政部名目 5 年期殖利率
    log(`抓取文獻建議的額外品種：${core.LITERATURE_SYMBOLS.join(', ')}、財政部名目殖利率…`);
    const extra = {};
    for (const s of core.LITERATURE_SYMBOLS) {
        extra[s] = await fetchYahoo(s, config.params.historyYears);
        log(`  ${extra[s] ? '✓' : '✗'} ${s}${extra[s] ? `：${extra[s].timestamps.length} 筆，${new Date(extra[s].timestamps[0] * 1000).toISOString().slice(0, 10)} 起` : ' 抓取失敗'}`);
        await new Promise(r => setTimeout(r, 400));
    }
    let nominal = null;
    try {
        const v = await sources.fetchTreasuryNominal5y(core, base.dates);
        nominal = v.values;
        sourceStatus.push({ name: '美國財政部名目殖利率', ok: true, detail: `${v.points} 天（${v.coverage}）`, factor: 'breakeven5y', firstDate: v.firstDate });
    } catch (e) {
        sourceStatus.push({ name: '美國財政部名目殖利率', ok: false, detail: e.message });
    }
    log(`  ${nominal ? '✓' : '✗'} 美國財政部名目殖利率：${sourceStatus[sourceStatus.length - 1].detail}`);
    // TIPS 失敗時 tipsYield 是填補值，損益兩平通膨率也不能用
    const { features, missing: litMissing } = core.deriveLiteratureFeatures(raw, extra, nominal && !unavailable.tipsYield ? nominal : null);
    Object.assign(raw, features);
    for (const [k, why] of Object.entries(litMissing)) unavailable[k] = why;
    // 晚於訓練期間開始才有資料的品種，交給主流程的涵蓋率檢查排除
    for (const lf of core.LITERATURE_FEATURES) {
        const ts = lf.needs.map(n => extra[n]?.timestamps?.[0]).filter(Boolean);
        if (ts.length) sourceStatus.push({ name: `Yahoo ${lf.needs.join('+')}（${lf.name}）`, ok: true, detail: '日線', factor: lf.key, firstDate: new Date(Math.max(...ts) * 1000).toISOString().slice(0, 10), literature: true });
    }

    // 第二批：不確定性與信用風險
    log('抓取不確定性與信用風險資料（EPU、GPR、FRED）…');
    const fredStart = addDaysISO(base.dates[0], -60);
    const [epu, gpr, baa, stl] = await Promise.allSettled([
        sources.fetchEPU(core), sources.fetchGPR(core),
        sources.fetchFred(core, 'BAA10Y', fredStart), sources.fetchFred(core, 'STLFSI4', fredStart),
    ]);
    const maps = {};
    const range = (v) => `${v.firstDate} ~ ${v.lastDate}`;
    note('經濟政策不確定性 EPU（policyuncertainty.com）', epu, v => `每日，${range(v)}`, 'epu', v => { maps.epu = v.map; });
    note('地緣政治風險 GPR（matteoiacoviello.com）', gpr, v => `每日，${range(v)}`, 'gpr', v => { maps.gpr = v.gpr; maps.gprThreat = v.threat; });
    if (gpr.status === 'fulfilled') sourceStatus.push({ name: '地緣政治威脅 GPR Threats', ok: true, detail: '同上', factor: 'gprThreat', firstDate: gpr.value.firstDate });
    else unavailable.gprThreat = unavailable.gpr;
    note('FRED BAA10Y 信用利差', baa, v => `每日，${range(v)}`, 'creditSpread', v => { maps.creditSpread = v.map; });
    note('FRED STLFSI4 金融壓力指數', stl, v => `每週，${range(v)}`, 'finStress', v => { maps.finStress = v.map; });
    Object.assign(raw, core.deriveUncertaintyFeatures(raw.dates, maps));
    return { raw, source: 'yahoo', unavailable, sourceStatus };
};

// ── 切分 ──────────────────────────────────────────────────────
// 訓練 | 驗證 | 測試。horizon > 1 時相鄰樣本的目標期間互相重疊，
// 每段尾端要丟掉 horizon-1 筆（purge），否則訓練標籤會用到驗證/測試期間的價格。
const segmentsFor = (ds, end = ds.split.trainEnd) => {
    const P = (ds.horizon || 1) - 1;
    const inner = splitHoldout(end);
    return { trainTo: inner.trainEnd - P, valFrom: inner.trainEnd, valTo: end - P, valid: inner.holdLen > P && inner.trainEnd - P > 0 };
};

// ── 訓練 ──────────────────────────────────────────────────────
const disposeAll = (arr) => arr.forEach(t => { try { t?.dispose?.(); } catch (_) {} });

// 在 [from, to) 訓練、[valFrom, valEnd) 驗證；早停並還原驗證 loss 最低的權重
const trainModel = async (ds, params, { from = 0, to, valFrom = to, valEnd, epochs, patience, quiet = false, onEpoch } = {}) => {
    const lookback = ds.X[0].length, nf = ds.X[0][0].length;
    const spec = resolveTargetSpec(params.predTarget, params);
    const model = createModel(params.algorithm, lookback, nf, params);
    model.compile({ optimizer: tf.train.adam(params.lr), loss: spec.loss });
    const slice = (a, b) => buildTargetTensor(ds.y.slice(a, b), ds.yDir.slice(a, b), params.predTarget, ds.yLogRet.slice(a, b));
    const xT = windowsToTensor(ds.X, from, to), yT = slice(from, to);
    const hasVal = valEnd > valFrom;
    const xV = hasVal ? windowsToTensor(ds.X, valFrom, valEnd) : null, yV = hasVal ? slice(valFrom, valEnd) : null;

    let best = Infinity, bestEpoch = 0, bestWeights = null, since = 0;
    const history = [];
    await model.fit(xT, yT, {
        epochs, batchSize: params.batchSize, verbose: 0,
        shuffle: false,   // DirectionalHuberLoss 依賴 batch 內相鄰樣本是相鄰交易日
        ...(hasVal ? { validationData: [xV, yV] } : {}),
        callbacks: {
            onEpochEnd: async (epoch, logs) => {
                const monitored = hasVal ? logs.val_loss : logs.loss;
                history.push({ epoch: epoch + 1, loss: logs.loss, val_loss: logs.val_loss ?? null });
                if (monitored < best - 1e-7) {
                    best = monitored; bestEpoch = epoch + 1; since = 0;
                    disposeAll(bestWeights || []);
                    bestWeights = model.getWeights().map(w => w.clone());
                } else if (++since >= patience) {
                    model.stopTraining = true;
                }
                onEpoch?.(epoch + 1, logs);
                if (!quiet && ((epoch + 1) % 25 === 0)) log(`    epoch ${epoch + 1}: loss ${logs.loss.toFixed(5)}${hasVal ? `  val_loss ${logs.val_loss.toFixed(5)}` : ''}`);
            },
        },
    });
    if (bestWeights) { model.setWeights(bestWeights); disposeAll(bestWeights); }
    disposeAll([xT, yT, xV, yV]);
    return { model, history, bestEpoch, bestLoss: best };
};

// 在 [from, to) 評估：回傳實際價、預測價、DA、MAPE
const evaluate = (model, ds, params, raw, from, to) => {
    const x = windowsToTensor(ds.X, from, to);
    const p = model.predict(x);
    const out = Array.from(p.dataSync());
    disposeAll([x, p]);
    const h = ds.horizon || 1;
    const prev = ds.prevPrices.slice(from, to);
    const pred = decodePredictedPrices(out, params.predTarget, ds.minMax, prev);
    const tIdx = (i) => ds.startIdx + from + i + h - 1;                 // 目標日的價格索引
    const actual = prev.map((_, i) => raw.prices[tIdx(i)]);
    const dates = prev.map((_, i) => raw.dates[tIdx(i)]);
    const prevH = prev.map((_, i) => raw.prices[ds.startIdx + from + i - 1 - h]);   // 基準日再往前 h 天（動能基準用）
    const s = scoreHoldout(actual, pred, prev);
    return { dates, actual, pred, prev, prevH, DA: s.holdoutDA, MAPE: s.holdoutMAPE, n: s.holdoutN };
};

// 比較基準：模型至少要贏過這些簡單規則才有意義
const baselines = ({ actual, prev, prevH }) => {
    const n = actual.length;
    let up = 0, mom = 0, momN = 0;
    for (let i = 0; i < n; i++) {
        if (actual[i] >= prev[i]) up++;
        // 動能：猜「接下來 h 天」和「過去 h 天」同方向
        if (Number.isFinite(prevH?.[i])) { momN++; if ((prev[i] >= prevH[i]) === (actual[i] >= prev[i])) mom++; }
    }
    return {
        randomWalkMAPE: calculateMAPE(actual, prev),          // 預測「之後 = 現在」
        alwaysUpDA: n ? (up / n) * 100 : NaN,                  // 永遠猜漲
        momentumDA: momN ? (mom / momN) * 100 : NaN,           // 猜跟前一段同方向
    };
};

const nextBusinessDays = (lastDate, n) => {
    const out = []; let t = Date.parse(lastDate + 'T00:00:00Z');
    while (out.length < n) {
        t += 86_400_000;
        const d = new Date(t).getUTCDay();
        if (d !== 0 && d !== 6) out.push(new Date(t).toISOString().slice(0, 10));
    }
    return out;
};

// ── 平行工作 ──────────────────────────────────────────────────
// RNN 在小 batch 下單一行程只吃得到約一個 CPU 核心；
// 參數搜尋的每組試驗、交叉驗證的每一折彼此獨立，分給多個子行程同時跑。
const runTrialJob = async (raw, job) => {
    const { tp, factors: tf_, epochs, patience } = job;
    const ts = Date.now();
    const ds = createMultivariateDataset(raw, tp.lookback, tf_, null, { horizon: tp.horizon });
    const sg = segmentsFor(ds);
    if (ds.split.holdLen <= 0 || !sg.valid) throw new Error('資料不足');
    const r = await trainModel(ds, tp, { to: sg.trainTo, valFrom: sg.valFrom, valEnd: sg.valTo, epochs, patience, quiet: true });
    const ev = evaluate(r.model, ds, tp, raw, sg.valFrom, sg.valTo);
    r.model.dispose();
    return { valDA: ev.DA, valMAPE: ev.MAPE, valNaiveMAPE: calculateMAPE(ev.actual, ev.prev), valLoss: r.bestLoss, bestEpoch: r.bestEpoch, sec: (Date.now() - ts) / 1000 };
};
const runFoldJob = async (raw, job) => {
    const { params, factors: f, fold, k, epochs, patience } = job;
    const ds = createMultivariateDataset(raw, params.lookback, f, null, { horizon: params.horizon });
    const P = ds.horizon - 1;
    const blk = Math.floor(ds.split.trainEnd / (k + 1));
    // 最後一折的測試也要在測試段之前結束（目標期間不跨進最終測試段）
    const trEnd = (fold + 1) * blk, teEnd = fold === k - 1 ? ds.split.trainEnd - P : trEnd + blk;
    const sg = segmentsFor(ds, trEnd);
    const r = await trainModel(ds, params, { to: sg.trainTo, valFrom: sg.valFrom, valEnd: sg.valTo, epochs, patience, quiet: true });
    const ev = evaluate(r.model, ds, params, raw, trEnd, teEnd);
    r.model.dispose();
    const at = (i) => raw.dates[ds.startIdx + i + P];
    return { fold: fold + 1, trainN: trEnd, testFrom: at(trEnd), testTo: at(teEnd - 1), DA: ev.DA, MAPE: ev.MAPE, bestEpoch: r.bestEpoch };
};

// loss 比較：同一模型結構、同一資料，只換訓練目標與 loss，在指定期間評估
//   job.period = { end: 訓練+驗證的結尾樣本索引, testFrom, testTo }
const runLossExpJob = async (raw, job) => {
    const { params, factors: f, period, epochs, patience } = job;
    const ts = Date.now();
    const ds = createMultivariateDataset(raw, params.lookback, f, null, { horizon: params.horizon });
    const H = ds.horizon;
    const sg = segmentsFor(ds, period.end);
    if (!sg.valid) throw new Error('資料不足');
    const r = await trainModel(ds, params, { to: sg.trainTo, valFrom: sg.valFrom, valEnd: sg.valTo, epochs, patience, quiet: true });
    const x = windowsToTensor(ds.X, period.testFrom, period.testTo);
    const p = r.model.predict(x);
    const out = Array.from(p.dataSync());
    disposeAll([x, p]); r.model.dispose();
    const n = period.testTo - period.testFrom;
    const prev = ds.prevPrices.slice(period.testFrom, period.testTo);
    const actual = prev.map((_, i) => raw.prices[ds.startIdx + period.testFrom + i + H - 1]);
    // 方向判斷：方向模型看機率，雙頭模型看方向頭，報酬模型看預測報酬的正負號
    const sig = (z) => 1 / (1 + Math.exp(-z));
    const predUp = prev.map((_, i) =>
        params.predTarget === 'direction' ? out[i] > 0.5
        : params.predTarget === 'dual_lr' ? sig(out[i * 2 + 1]) > 0.5
        : decodePredictedPrices([out[i]], params.predTarget, ds.minMax, [prev[i]])[0] >= prev[i]);
    const actualUp = actual.map((a, i) => a >= prev[i]);
    const DA = (predUp.filter((u, i) => u === actualUp[i]).length / n) * 100;
    // 價格誤差：只有能輸出報酬幅度的模型才有
    const pred = params.predTarget === 'direction' ? null : decodePredictedPrices(out, params.predTarget, ds.minMax, prev);
    const MAPE = pred ? calculateMAPE(actual, pred) : null;
    // 策略：每 H 天依預測方向做多或放空，持有 H 天（交易不重疊），不計成本
    let eq = 1;
    for (let t = 0; t < n; t += H) eq *= 1 + (predUp[t] ? 1 : -1) * (actual[t] / prev[t] - 1);
    let bh = 1;
    for (let t = 0; t < n; t += H) bh *= actual[t] / prev[t];
    return {
        DA, MAPE, naiveMAPE: calculateMAPE(actual, prev),
        alwaysUpDA: (actualUp.filter(Boolean).length / n) * 100,
        upShare: predUp.filter(Boolean).length / n,
        strategyReturn: eq - 1, buyHoldReturn: bh - 1,
        bestEpoch: r.bestEpoch, n, sec: (Date.now() - ts) / 1000,
    };
};

// 子行程模式：node train.mjs --worker 工作檔.json → 最後一行輸出 RESULT <json>
if (arg('--worker')) {
    const job = JSON.parse(readFileSync(arg('--worker'), 'utf8'));
    const raw = JSON.parse(readFileSync(job.dataPath, 'utf8'));
    try {
        const out = job.type === 'fold' ? await runFoldJob(raw, job)
                  : job.type === 'lossexp' ? await runLossExpJob(raw, job)
                  : await runTrialJob(raw, job);
        process.stdout.write('RESULT ' + JSON.stringify(out) + '\n');
        process.exit(0);
    } catch (e) {
        process.stdout.write('RESULT ' + JSON.stringify({ error: e.message }) + '\n');
        process.exit(0);
    }
}

// 以最多 workers 個子行程執行 jobs，onDone(i, result) 依完成順序回報
const runPool = async (jobs, workers, onDone) => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'silver-jobs-'));
    const results = new Array(jobs.length);
    let next = 0;
    const runOne = (i) => new Promise((resolve) => {
        const file = path.join(dir, `job-${i}.json`);
        writeFileSync(file, JSON.stringify(jobs[i]));
        const child = spawn(process.execPath, ['--no-warnings', fileURLToPath(import.meta.url), '--worker', file], {
            env: { ...process.env, TF_CPP_MIN_LOG_LEVEL: '2' }, stdio: ['ignore', 'pipe', 'pipe'],
        });
        let out = '', err = '';
        child.stdout.on('data', d => { out += d; });
        child.stderr.on('data', d => { err += d; });
        child.on('close', () => {
            const line = out.split('\n').reverse().find(l => l.startsWith('RESULT '));
            let r;
            try { r = line ? JSON.parse(line.slice(7)) : { error: (err.trim().split('\n').pop() || '子行程沒有回傳結果') }; }
            catch (e) { r = { error: e.message }; }
            results[i] = r; onDone?.(i, r); resolve();
        });
    });
    const lane = async () => { while (next < jobs.length) await runOne(next++); };
    await Promise.all(Array.from({ length: Math.min(workers, jobs.length) }, lane));
    rmSync(dir, { recursive: true, force: true });
    return results;
};

// ── loss 比較實驗 ─────────────────────────────────────────────
// node train.mjs --compare-loss [--config ...] [--repeats 3]
// 三種訓練目標 × 每種重複 N 次 × 5 個評估期間（4 段 walk-forward + 最終測試段）
const LOSS_VARIANTS = [
    { key: 'error',     name: '誤差導向',  desc: '對數報酬率 + Huber loss（讓預測報酬與實際報酬的差距最小）', predTarget: 'logreturn' },
    { key: 'direction', name: '方向導向',  desc: '漲跌方向 + 二元交叉熵（讓猜對漲跌的機率最大）',            predTarget: 'direction' },
    { key: 'hybrid',    name: '混合',      desc: '雙頭：報酬率 Huber + 0.5 × 方向交叉熵',                    predTarget: 'dual_lr', directionalLambda: 0.5 },
];
// 特徵比較：同一模型與訓練目標（誤差導向），只換輸入變數
const featureVariants = (factors) => {
    const batch = Object.fromEntries(core.LITERATURE_FEATURES.map(f => [f.key, f.batch || 1]));
    const on = (pred) => Object.fromEntries(Object.keys(factors).map(k => [k, !!factors[k] && pred(k)]));
    const n = (b) => core.LITERATURE_FEATURES.filter(f => (f.batch || 1) === b && factors[f.key]).length;
    return [
        { key: 'base', name: '原有變數',             desc: '目前模型使用的變數（不含文獻新增）', factors: on(k => !batch[k]) },
        { key: 'unc',  name: '原有 + 不確定性',       desc: `原有變數再加上 ${n(2)} 個不確定性與信用風險變數（EPU、GPR、信用利差、金融壓力）`, factors: on(k => batch[k] !== 1) },
        { key: 'all',  name: '原有 + 全部文獻變數',   desc: `原有變數 + 第一批 ${n(1)} 個（市場、利率、匯率、波動率、動能）+ 不確定性 ${n(2)} 個`, factors: on(() => true) },
    ];
};
const compareLoss = (...a) => compareVariants(LOSS_VARIANTS, 'loss-compare.json', ...a);
// --variants base,unc 只跑指定的組別（確認實驗用），輸出到 feature-confirm.json
const compareFeatures = (raw, factors, ...a) => {
    const pick = arg('--variants') ? arg('--variants').split(',') : null;
    const vs = featureVariants(factors).filter(v => !pick || pick.includes(v.key));
    return compareVariants(vs, pick ? 'feature-confirm.json' : 'feature-compare.json', raw, factors, ...a);
};
const compareVariants = async (VARIANTS, outName, raw, factors, excluded, sourceStatus, source, WORKERS, dataPath) => {
    const repeats = parseInt(arg('--repeats', '3'), 10);
    const base = { ...config.params, ...(config.compare || {}) };
    const H = Math.max(1, base.horizon || 1);
    const ds = createMultivariateDataset(raw, base.lookback, factors, null, { horizon: H });
    const P = H - 1, k = 4;
    const blk = Math.floor(ds.split.trainEnd / (k + 1));
    const at = (i) => raw.dates[ds.startIdx + i + P];
    const periods = [];
    for (let fold = 0; fold < k; fold++) {
        const end = (fold + 1) * blk, testTo = fold === k - 1 ? ds.split.trainEnd - P : end + blk;
        periods.push({ label: `第 ${fold + 1} 段`, end, testFrom: end, testTo });
    }
    periods.push({ label: '最終測試段', end: ds.split.trainEnd, testFrom: ds.split.holdStart, testTo: ds.X.length });
    periods.forEach(p => { p.from = at(p.testFrom); p.to = at(p.testTo - 1); });
    log(`比較：${VARIANTS.length} 種設定 × ${repeats} 次 × ${periods.length} 個期間 = ${VARIANTS.length * repeats * periods.length} 次訓練（${WORKERS} 個行程平行）`);
    log(`共同設定：${base.algorithm} LB=${base.lookback} L=${base.layers}，預測 ${H} 日，特徵 ${ds.numFeatures} 個`);
    periods.forEach(p => log(`  ${p.label}：${p.from} ~ ${p.to}（${p.testTo - p.testFrom} 筆）`));

    const jobs = [];
    for (const v of VARIANTS) for (let rep = 0; rep < repeats; rep++) periods.forEach((p, pi) => jobs.push({
        type: 'lossexp', dataPath, variant: v.key, rep, periodIdx: pi,
        params: { ...base, horizon: H, predTarget: v.predTarget || base.predTarget, ...(v.directionalLambda != null ? { directionalLambda: v.directionalLambda } : {}) },
        factors: v.factors || factors, period: p, epochs: base.epochs, patience: config.patience,
    }));
    let done = 0;
    const res = await runPool(jobs, WORKERS, (i, r) => {
        done++;
        const j = jobs[i];
        if (r.error) log(`  [${done}/${jobs.length}] ${j.variant} #${j.rep + 1} ${periods[j.periodIdx].label} 失敗：${r.error}`);
        else log(`  [${done}/${jobs.length}] ${j.variant} #${j.rep + 1} ${periods[j.periodIdx].label}：DA ${r.DA.toFixed(1)}%${r.MAPE != null ? `  MAPE ${r.MAPE.toFixed(2)}%` : ''}  猜漲 ${(r.upShare * 100).toFixed(0)}%  策略 ${(r.strategyReturn * 100).toFixed(1)}%`);
    });

    // 彙總：每個目標在每個期間取重複的平均
    const mean = (a) => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
    const sd = (a) => { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); };
    const summary = VARIANTS.map(v => {
        const perPeriod = periods.map((p, pi) => {
            const rs = res.filter((r, i) => r && !r.error && jobs[i].variant === v.key && jobs[i].periodIdx === pi);
            const pick = (f) => rs.map(f).filter(Number.isFinite);
            return {
                label: p.label, from: p.from, to: p.to, runs: rs.length,
                DA: mean(pick(r => r.DA)), DAsd: sd(pick(r => r.DA)),
                MAPE: mean(pick(r => r.MAPE)), naiveMAPE: mean(pick(r => r.naiveMAPE)),
                alwaysUpDA: mean(pick(r => r.alwaysUpDA)), upShare: mean(pick(r => r.upShare)),
                strategyReturn: mean(pick(r => r.strategyReturn)), buyHoldReturn: mean(pick(r => r.buyHoldReturn)),
                bestEpoch: mean(pick(r => r.bestEpoch)),
            };
        });
        const cols = (f) => perPeriod.map(f).filter(Number.isFinite);
        const vds = v.factors ? createMultivariateDataset(raw, base.lookback, v.factors, null, { horizon: H }) : ds;
        return { ...v, factors: undefined, features: vds.activeFeatures.map(f => f.name), perPeriod,
            avgDA: mean(cols(p => p.DA)), avgEdge: mean(cols(p => p.DA - Math.max(p.alwaysUpDA, 100 - p.alwaysUpDA))),
            avgMAPE: mean(cols(p => p.MAPE)), avgNaiveMAPE: mean(cols(p => p.naiveMAPE)),
            avgUpShare: mean(cols(p => p.upShare)), avgStrategy: mean(cols(p => p.strategyReturn)),
            periodsBeatingMajority: perPeriod.filter(p => p.DA > Math.max(p.alwaysUpDA, 100 - p.alwaysUpDA)).length };
    });
    log('── 彙總（各期間平均）──');
    for (const s of summary) log(`  ${s.name}：DA ${s.avgDA.toFixed(1)}%（比多數方向 ${s.avgEdge >= 0 ? '+' : ''}${s.avgEdge.toFixed(1)}）  MAPE ${s.avgMAPE != null ? s.avgMAPE.toFixed(2) + '%' : '—'}（不變 ${s.avgNaiveMAPE.toFixed(2)}%）  猜漲 ${(s.avgUpShare * 100).toFixed(0)}%  策略 ${(s.avgStrategy * 100).toFixed(1)}%  勝過多數方向 ${s.periodsBeatingMajority}/${periods.length} 期`);

    mkdirSync(OUT, { recursive: true });
    writeFileSync(path.join(OUT, outName), JSON.stringify({
        createdAt: new Date().toISOString(), durationSec: (Date.now() - t0) / 1000,
        dataSource: source, dataRange: { from: raw.dates[0], to: raw.dates[raw.dates.length - 1], days: raw.dates.length },
        excludedFactors: excluded, sources: sourceStatus,
        setup: { algorithm: base.algorithm, lookback: base.lookback, layers: base.layers, lr: base.lr, batchSize: base.batchSize, horizon: H, repeats, predTarget: base.predTarget, features: ds.activeFeatures.map(f => f.name) },
        literature: core.LITERATURE_FEATURES.map(f => ({ key: f.key, name: f.name, group: f.group, ref: f.ref, batch: f.batch || 1, used: !!factors[f.key], excluded: excluded.find(e => e.key === f.key)?.reason || null })),
        periods, summary,
        runs: res.map((r, i) => ({ variant: jobs[i].variant, rep: jobs[i].rep, period: jobs[i].periodIdx, ...r })),
    }, null, 1));
    log(`輸出到 ${path.join(path.relative(process.cwd(), OUT) || '.', outName)}`);
};

// ── 主流程 ────────────────────────────────────────────────────
const main = async () => {
    log(`TensorFlow backend：${tf.getBackend()}（${os.cpus().length} CPU）`);
    const { raw: rawAll, source, unavailable, sourceStatus } = await loadData();
    const raw = filterDataByYears(rawAll, config.params.historyYears);
    log(`資料：${raw.dates[0]} ~ ${raw.dates[raw.dates.length - 1]}，共 ${raw.dates.length} 個交易日（近 ${config.params.historyYears} 年）`);

    // 排除沒有真實資料的因子（雲端不互動，直接排除並記錄，而不是中止）
    let factors = { ...config.factors };
    const excluded = [];
    for (const [k, why] of Object.entries(unavailable)) if (factors[k]) { factors[k] = false; excluded.push({ key: k, reason: why }); }
    // 涵蓋率：輔助資料若晚於訓練期間開始才有，前段會被填成固定值，
    // 模型可能把「有沒有資料」當成訊號（等於學到「這是哪個年代」），因此排除
    const COVERAGE_SLACK_DAYS = 45;
    const startMs = Date.parse(raw.dates[0] + 'T00:00:00Z');
    for (const st of sourceStatus) {
        if (!st.ok || !st.factor || !st.firstDate || !factors[st.factor]) continue;
        const lagDays = (Date.parse(st.firstDate + 'T00:00:00Z') - startMs) / 86_400_000;
        if (lagDays > COVERAGE_SLACK_DAYS) {
            factors[st.factor] = false;
            const why = `${st.name}：只有 ${st.firstDate} 之後的資料，未涵蓋整個期間（從 ${raw.dates[0]} 起）`;
            excluded.push({ key: st.factor, reason: why });
            st.detail += `；${st.firstDate} 起，未涵蓋整個期間，不使用`;
        }
    }
    const dq = checkDataQuality(raw, factors);
    if (raw.dataQuality?.allMock) throw new Error('這份資料是瀏覽器的「模擬資料」，無法用於訓練。請在瀏覽器載入真實資料後再匯出。');
    if (dq.mockSelected.includes('price')) throw new Error(dq.reason);
    for (const k of dq.mockSelected) { factors[k] = false; excluded.push({ key: k, reason: '資料抓取失敗（模擬值）' }); }
    if (excluded.length) log(`排除因子：${excluded.map(e => e.key).join(', ')}`);

    // 子行程讀同一份資料
    const WORKERS = Math.max(1, config.workers || os.cpus().length);
    const dataDir = mkdtempSync(path.join(os.tmpdir(), 'silver-data-'));
    const dataPath = path.join(dataDir, 'data.json');
    writeFileSync(dataPath, JSON.stringify(raw));
    process.on('exit', () => { try { rmSync(dataDir, { recursive: true, force: true }); } catch (_) {} });

    // 只輸出整理好的資料（給 vol.mjs 等其他實驗使用）
    if (arg('--dump-data')) {
        writeFileSync(path.resolve(arg('--dump-data')), JSON.stringify({ raw, factors, excluded, sourceStatus, source,
            literature: core.LITERATURE_FEATURES.map(f => ({ key: f.key, name: f.name, group: f.group, ref: f.ref, batch: f.batch || 1 })) }));
        log(`資料輸出到 ${arg('--dump-data')}`);
        return;
    }
    if (flag('--compare-loss')) return compareLoss(raw, factors, excluded, sourceStatus, source, WORKERS, dataPath);
    if (flag('--compare-features')) return compareFeatures(raw, factors, excluded, sourceStatus, source, WORKERS, dataPath);

    // ── 超參數搜尋（只看驗證段，不碰測試段）──
    let params = { ...config.params };
    const H = Math.max(1, params.horizon || 1);
    params.horizon = H;
    if (H > 1) {
        // 多日報酬只支援對數報酬率：價格迴歸要外插、方向懲罰 loss 依賴「相鄰樣本 = 相鄰交易日」
        if (params.predTarget !== 'logreturn') log(`預測 ${H} 日報酬：目標固定為對數報酬率`);
        params.predTarget = 'logreturn';
        config.search.space = { ...config.search.space, predTarget: ['logreturn'] };
    }
    const search = { trials: [] };
    if (config.search.trials > 0) {
        log(`超參數搜尋：${config.search.trials} 組（每組最多 ${config.search.epochs} epochs）`);
        const sp = config.search.space;
        const pick = (a) => a[Math.floor(Math.random() * a.length)];
        const factorKeys = Object.keys(factors).filter(k => factors[k]);
        const jobs = Array.from({ length: config.search.trials }, () => {
            const tp = { ...params };
            for (const k of Object.keys(config.search.space)) tp[k] = pick(config.search.space[k]);
            const tf_ = Object.fromEntries(factorKeys.map(k => [k, Math.random() < config.search.factorKeepProb]));
            return { type: 'trial', dataPath, tp, factors: tf_, epochs: config.search.epochs, patience: config.search.patience };
        });
        const tSearch = Date.now();
        await runPool(jobs, WORKERS, (i, r) => {
            const { tp, factors: tf_ } = jobs[i];
            if (r.error) { log(`  #${i + 1} 失敗：${r.error}`); return; }
            search.trials.push({ params: Object.fromEntries(Object.keys(sp).map(k => [k, tp[k]])), factors: tf_, ...r });
            log(`  #${i + 1} ${tp.algorithm} LB=${tp.lookback} L=${tp.layers} → 驗證 DA ${r.valDA.toFixed(1)}%  MAPE ${r.valMAPE.toFixed(2)}%（${r.sec.toFixed(0)}s）`);
        });
        search.wallSec = (Date.now() - tSearch) / 1000;
        search.cpuSec = search.trials.reduce((a, t) => a + t.sec, 0);
        search.workers = WORKERS;
        log(`搜尋完成：${search.wallSec.toFixed(0)}s（${WORKERS} 個行程平行；逐一執行約需 ${search.cpuSec.toFixed(0)}s）`);
        const gate = config.search.maxMapeVsNaive;
        search.trials.forEach(t => { t.passed = Number.isFinite(t.valDA) && t.valMAPE <= gate * t.valNaiveMAPE; });
        search.gate = gate;
        let ok = search.trials.filter(t => t.passed);
        log(`通過價格合理性檢查（驗證 MAPE ≤ ${gate} × 明天=今天）：${ok.length} / ${search.trials.length} 組`);
        if (!ok.length) {
            log('⚠️ 沒有任何一組通過檢查，改用方向準確率最高的一組；請把結果當作參考');
            ok = search.trials.filter(t => Number.isFinite(t.valDA));
        }
        if (ok.length) {
            ok.sort((a, b) => (b.valDA - a.valDA) || (a.valLoss - b.valLoss));
            search.best = ok[0];
            params = { ...params, ...ok[0].params };
            factors = { ...factors, ...ok[0].factors };
            log(`最佳組合：${params.algorithm} LB=${params.lookback} L=${params.layers}（驗證 DA ${ok[0].valDA.toFixed(1)}%）`);
        }
    }

    // ── 資料集與切分 ──
    const ds = createMultivariateDataset(raw, params.lookback, factors, null, { horizon: H });
    if (!ds.X.length || ds.split.holdLen <= 0) throw new Error('資料不足以切出測試段');
    const sg = segmentsFor(ds);                           // 訓練段再切出驗證段，給早停用（含 purge 間隔）
    if (!sg.valid) throw new Error('資料不足以切出驗證段');
    const at = (i) => raw.dates[ds.startIdx + i + H - 1]; // 以「目標日」標示期間
    const split = {
        train: { n: sg.trainTo, from: at(0), to: at(sg.trainTo - 1) },
        val:   { n: sg.valTo - sg.valFrom, from: at(sg.valFrom), to: at(sg.valTo - 1) },
        test:  { n: ds.split.holdLen, from: at(ds.split.holdStart), to: at(ds.X.length - 1) },
        purge: H - 1,
    };
    log(`切分：訓練 ${split.train.n}｜驗證 ${split.val.n}｜測試 ${split.test.n}（${split.test.from} ~ ${split.test.to}）${H > 1 ? `，各段之間間隔 ${H - 1} 筆` : ''}`);
    log(`特徵 ${ds.numFeatures} 個：${ds.activeFeatures.map(f => f.key).join(', ')}`);

    // ── walk-forward 交叉驗證（選用）──
    const cv = [];
    if (params.validationMode === VALIDATION_MODE.K_FOLD && params.kFolds > 1) {
        const k = params.kFolds;
        log(`walk-forward 交叉驗證 ${k} 折（僅用測試段之前的資料，${WORKERS} 個行程平行）`);
        const jobs = Array.from({ length: k }, (_, fold) => ({ type: 'fold', dataPath, params, factors, fold, k, epochs: params.epochs, patience: config.patience }));
        const res = await runPool(jobs, WORKERS, (i, r) => {
            if (r.error) log(`  Fold ${i + 1} 失敗：${r.error}`);
            else log(`  Fold ${r.fold}: DA ${r.DA.toFixed(1)}%  MAPE ${r.MAPE.toFixed(2)}%`);
        });
        res.filter(r => r && !r.error).sort((a, b) => a.fold - b.fold).forEach(r => cv.push(r));
    }

    // ── 主訓練：訓練段訓練、驗證段早停，測試段完全不參與 ──
    log(`訓練 ${params.algorithm}（目標：${H > 1 ? `${H} 日` : ''}${params.predTarget}，最多 ${params.epochs} epochs，早停 ${config.patience}）`);
    const tTrain = Date.now();
    const main = await trainModel(ds, params, { to: sg.trainTo, valFrom: sg.valFrom, valEnd: sg.valTo, epochs: params.epochs, patience: config.patience });
    const trainSec = (Date.now() - tTrain) / 1000;
    log(`  完成：${main.history.length} epochs，最佳在第 ${main.bestEpoch} epoch（${trainSec.toFixed(1)}s，平均 ${(trainSec / main.history.length * 1000).toFixed(0)} ms/epoch）`);

    const test = evaluate(main.model, ds, params, raw, ds.split.holdStart, ds.X.length);
    const base = baselines(test);
    // 相鄰樣本的 H 日目標互相重疊，彼此不獨立；信賴區間要用有效樣本數（約 n / H）
    const effectiveN = Math.max(1, Math.floor(test.n / H));
    log(`測試段：DA ${test.DA.toFixed(2)}%  MAPE ${test.MAPE.toFixed(2)}%（永遠猜漲 DA ${base.alwaysUpDA.toFixed(1)}%，不變 MAPE ${base.randomWalkMAPE.toFixed(2)}%；有效樣本 ${effectiveN}）`);

    // 回測：每 H 天依預測做一次多/空，持有 H 天（交易不重疊）
    const tradeIdx = test.pred.map((_, t) => t).filter(t => t % H === 0);
    const bt = simulateBacktest({
        prices: raw.prices, dates: raw.dates,
        priceIndices: tradeIdx.map(t => ds.startIdx + ds.split.holdStart + t + H - 1),
        predPrices: tradeIdx.map(t => test.pred[t]), config: config.backtest, holdDays: H,
    });
    const p0 = test.prev[0], p1 = test.actual[test.actual.length - 1];
    const buyHold = p0 > 0 ? p1 / p0 - 1 : NaN;
    if (bt) log(`回測（測試段）：報酬 ${(bt.totalReturn * 100).toFixed(2)}%（買進持有 ${(buyHold * 100).toFixed(2)}%），MDD ${(bt.maxDD * 100).toFixed(2)}%，Sharpe ${bt.sharpe.toFixed(2)}`);

    // ── 重訓：同參數、同 epoch 數、全部資料，作為輸出與未來預測用的模型 ──
    let finalModel = main.model, refit = null;
    if (config.refit) {
        const epochs = Math.max(1, main.bestEpoch);
        log(`以全部資料重訓 ${epochs} epochs（輸出模型用）`);
        const r = await trainModel(ds, params, { to: ds.X.length, valFrom: ds.X.length, valEnd: ds.X.length, epochs, patience: Infinity, quiet: true });
        finalModel = r.model; main.model.dispose();
        refit = { epochs, samples: ds.X.length };
    }

    // ── 未來預測、全期間擬合、因子影響 ──
    const quietLog = () => {};
    const mp = await runMultiStepPrediction(finalModel, ds.X, ds.y, ds.minMax, ds.activeFeatures, ds.numFeatures, params.lookback, params.futureDays, quietLog, params.predTarget, ds.prevPrices, ds.latestWindow);
    const lastDate = raw.dates[raw.dates.length - 1];
    const lastPrice = raw.prices[raw.prices.length - 1];
    let forecast;
    if (H > 1) {
        // 直接預測：以最新視窗（結束在今天收盤）預測 H 個交易日後的價格
        const x = windowsToTensor([ds.latestWindow]);
        const p = finalModel.predict(x);
        const out = Array.from(p.dataSync());
        disposeAll([x, p]);
        forecast = { lastDate, lastPrice, horizon: H, dates: [nextBusinessDays(lastDate, H)[H - 1]],
                     prices: decodePredictedPrices(out, params.predTarget, ds.minMax, [lastPrice]), dirProbs: null };
    } else {
        forecast = { lastDate, lastPrice, horizon: 1, dates: nextBusinessDays(lastDate, params.futureDays),
                     prices: mp.futurePrices, dirProbs: mp.futureDirProbs?.length ? mp.futureDirProbs : null };
    }
    log(`預測（${lastDate} 收盤 ${lastPrice.toFixed(3)}）：${forecast.dates.map((d, i) => `${d} ${forecast.prices[i].toFixed(3)}`).join('，')}`);

    // ── 輸出 ──
    mkdirSync(OUT, { recursive: true });
    await finalModel.save('file://' + OUT);   // model.json + weights.bin（瀏覽器可直接載入）
    const browserConfig = {
        params, selectedFactors: factors,
        priceMinMax: { min: ds.minMax.price.min, max: ds.minMax.price.max },
        activeFeaturesConfig: ds.activeFeatures.map(f => ({ key: f.key, name: f.name, isExternal: f.isExternal, minMax: { min: f.minMax.min, max: f.minMax.max } })),
        finalNumFeatures: ds.numFeatures,
        cloud: { trainedAt: new Date().toISOString(), dataTo: lastDate, refit: !!refit, horizon: H },
    };
    writeFileSync(path.join(OUT, 'silver-config.json'), JSON.stringify(browserConfig, null, 2));

    const fittedPreds = params.predTarget === 'direction'
        ? null   // 純方向模型沒有價格可畫
        : mp.historyPreds;
    const results = {
        meta: {
            createdAt: new Date().toISOString(),
            durationSec: (Date.now() - t0) / 1000,
            runtime: { node: process.version, tfjs: tf.version.tfjs, backend: tf.getBackend(), cpus: os.cpus().length, cpuModel: os.cpus()[0]?.model },
            dataSource: source,
            dataRange: { from: raw.dates[0], to: lastDate, days: raw.dates.length },
            excludedFactors: excluded,
            sources: sourceStatus,
        },
        params, patience: config.patience,
        features: ds.activeFeatures.map(f => ({ key: f.key, name: f.name })),
        split,
        training: { history: main.history, bestEpoch: main.bestEpoch, bestValLoss: main.bestLoss, seconds: trainSec, msPerEpoch: trainSec / main.history.length * 1000, refit },
        cv, search: config.search.trials > 0 ? search : null,
        test: { DA: test.DA, MAPE: test.MAPE, n: test.n, effectiveN, horizon: H, baselines: base, dates: test.dates, actual: test.actual, pred: test.pred },
        backtest: bt ? { ...bt, buyHoldReturn: buyHold, buyHoldEntry: p0, holdDays: H, config: config.backtest } : null,
        forecast,
        fitted: {
            // 每筆預測對應的是「目標日」（H 天後）的價格
            dates: ds.prevPrices.map((_, i) => raw.dates[ds.startIdx + i + H - 1]),
            actual: ds.prevPrices.map((_, i) => raw.prices[ds.startIdx + i + H - 1]),
            pred: fittedPreds,
            testFrom: split.test.from,
        },
        influence: mp.influenceScores || null,
    };
    writeFileSync(path.join(OUT, 'results.json'), JSON.stringify(results));
    finalModel.dispose();
    log(`輸出到 ${path.relative(process.cwd(), OUT) || '.'}：model.json、weights.bin、silver-config.json、results.json`);
};

main().catch(e => { console.error('❌', e.message); process.exit(1); });

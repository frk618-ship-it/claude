// ══════════════════════════════════════════════════════════════
// Silver AI Trader 共用核心：資料特徵、資料集、模型、評估、預測
//
// 瀏覽器（src/app.jsx）與雲端訓練（cloud/train.mjs）共用同一份程式，
// 確保兩邊的特徵工程、正規化、時序切分、模型結構與評分方式完全一致。
// 不可使用 DOM / React / localStorage 等瀏覽器專屬 API。
//
// TensorFlow.js 由呼叫端提供：瀏覽器是 window.tf（CDN 載入），
// Node 是 @tensorflow/tfjs-node —— 載入本模組前必須先設定 globalThis.tf。
// ══════════════════════════════════════════════════════════════
const tf = globalThis.tf;
if (!tf) throw new Error('core.js：載入前必須先設定 globalThis.tf');

// 驗證模式
const VALIDATION_MODE = {
    NONE: 'NONE',
    K_FOLD: 'K_FOLD'
};

// 全局輔助函數：用於反標準化價格
const denormalizePrice = (val, minMax) => val * (minMax.price.max - minMax.price.min) + minMax.price.min;

// ==========================================
// 🎯 Directional Huber Loss — 核心 DA 提升 Loss
// 結合 Huber（穩健回歸）+ 方向懲罰（Direction Penalty）
// alpha: 方向懲罰權重 (0=純 Huber, 1=純方向懲罰)
// delta: Huber 分界點
// 原理：對於 batch 中的連續樣本 [i-1, i]，
//   若 yTrue 往上但 yPred < yTrue[i-1]，或 yTrue 往下但 yPred > yTrue[i-1]，
//   則施加額外懲罰，逼使模型預測正確方向
// ==========================================
const getDirectionalHuberLoss = (alpha = 0.3, delta = 1.0) => {
    return (yTrue, yPred) => {
        return tf.tidy(() => {
            // 基礎 Huber loss（對極端值穩健）
            const huber = tf.losses.huberLoss(yTrue, yPred, delta);

            const batchSize = yTrue.shape[0];
            if (batchSize <= 1 || alpha <= 0) return huber;

            // 取連續批次對：[0..n-2] vs [1..n-1]
            const truePrev  = tf.slice(yTrue, [0], [batchSize - 1]);   // y[i-1]
            const trueCurr  = tf.slice(yTrue, [1], [batchSize - 1]);   // y[i]   (actual)
            const predCurr  = tf.slice(yPred, [1], [batchSize - 1]);   // ŷ[i]   (predicted)

            // 實際方向向量（正=漲，負=跌）
            const actualDir   = tf.sub(trueCurr, truePrev);
            // 預測方向向量（相對於上一個真實值）
            const predictedDir = tf.sub(predCurr, truePrev);

            // 方向懲罰：relu(- actualDir * predictedDir)
            // 方向相同 → 乘積 > 0 → relu=0（無懲罰）
            // 方向相反 → 乘積 < 0 → relu>0（施加懲罰）
            const dotProduct  = tf.mul(actualDir, predictedDir);
            const dirPenalty  = tf.mean(tf.relu(tf.neg(dotProduct)));

            // 組合: (1-α) * Huber + α * DirectionPenalty
            const combined = tf.add(
                tf.mul(huber,      tf.scalar(1 - alpha)),
                tf.mul(dirPenalty, tf.scalar(alpha))
            );
            return combined;
        });
    };
};

// ==========================================
// Huber Loss（穩健回歸，對極端值較不敏感）
// ==========================================
const getHuberLoss = (delta = 1.0) => {
    return (yTrue, yPred) => {
        return tf.tidy(() => tf.losses.huberLoss(yTrue, yPred, delta));
    };
};

// ══════════════════════════════════════════════════════════════
// 雙頭損失（dual）：輸出 [price, dir_logit]
//   loss = Huber(price) + λ · BCE(sigmoid(dir_logit), dirLabel)
//   yTrue 形狀 [n,2]：欄0=正規化價格, 欄1=方向標籤(0/1)
// ══════════════════════════════════════════════════════════════
const getDualHeadLoss = (lambda = 0.5, delta = 1.0) => {
    return (yTrue, yPred) => tf.tidy(() => {
        const priceTrue = yTrue.slice([0, 0], [-1, 1]);
        const dirTrue   = yTrue.slice([0, 1], [-1, 1]);
        const pricepred = yPred.slice([0, 0], [-1, 1]);
        const dirLogit  = yPred.slice([0, 1], [-1, 1]);
        const priceLoss = tf.losses.huberLoss(priceTrue, pricepred, delta);
        const dirProb   = tf.sigmoid(dirLogit);
        const eps = tf.scalar(1e-7);
        const dirLoss = tf.neg(tf.mean(tf.add(
            tf.mul(dirTrue, tf.log(tf.add(dirProb, eps))),
            tf.mul(tf.sub(tf.scalar(1), dirTrue), tf.log(tf.add(tf.sub(tf.scalar(1), dirProb), eps)))
        )));
        return tf.add(priceLoss, tf.mul(tf.scalar(lambda), dirLoss));
    });
};

// 依預測目標回傳對應的 loss + 輸出單元數 + 輸出 activation
const resolveTargetSpec = (predTarget, params) => {
    const lambda = params.directionalLambda ?? 0.3;
    const delta  = params.huberDelta ?? 1.0;
    if (predTarget === 'direction') {
        return { units: 1, activation: 'sigmoid', loss: 'binaryCrossentropy', outputDim: 1 };
    }
    if (predTarget === 'dual') {
        return { units: 2, activation: 'linear', loss: getDualHeadLoss(lambda, delta), outputDim: 2 };
    }
    if (predTarget === 'logreturn') {
        // 目標本身已是報酬率 → 用純 Huber。
        // 不加方向懲罰項是刻意的：λ 的作用是拿價格精度換方向精度，
        // 而此模式的用途正是追求價格準確度。
        return { units: 1, activation: 'linear', loss: getHuberLoss(delta), outputDim: 1 };
    }
    // default: price
    return { units: 1, activation: 'linear', loss: getDirectionalHuberLoss(lambda, delta), outputDim: 1 };
};

// 依預測目標建立訓練目標張量
//   price     → [n,1] 正規化價格
//   direction → [n,1] 方向標籤 0/1
//   dual      → [n,2] [價格, 方向]
//   logreturn → [n,1] 標準化後的 ln(P_t/P_{t-1})
const buildTargetTensor = (yArr, yDirArr, predTarget, yLogRetArr) => {
    const n = yArr.length;
    if (predTarget === 'logreturn') {
        return tf.tensor(Float32Array.from((yLogRetArr || []).slice(0, n)), [n, 1]);
    }
    if (predTarget === 'direction') {
        return tf.tensor(Float32Array.from(yDirArr.slice(0, n)), [n, 1]);
    }
    if (predTarget === 'dual') {
        const flat = new Float32Array(n * 2);
        for (let i = 0; i < n; i++) { flat[i*2] = yArr[i]; flat[i*2+1] = yDirArr[i]; }
        return tf.tensor(flat, [n, 2]);
    }
    return tf.tensor(Float32Array.from(yArr.slice(0, n)), [n, 1]);
};



// ==========================================
// MAPE 指標計算函數
// ==========================================
const calculateMAPE = (actualPrices, predictedPrices) => {
    if (!actualPrices?.length || !predictedPrices?.length) return 0;
    const n = Math.min(actualPrices.length, predictedPrices.length);
    if (n === 0) return 0;
    let sumAbsPercentageError = 0;
    let count = 0;
    for (let i = 0; i < n; i++) {
        const actual = actualPrices[i];
        const predicted = predictedPrices[i];
        if (actual !== 0) {
            const absPercentageError = Math.abs((actual - predicted) / actual);
            sumAbsPercentageError += absPercentageError;
            count++;
        }
    }
    if (count === 0) return 0;
    return (sumAbsPercentageError / count) * 100;
};


// ==========================================
// 1. 核心指標計算 (RSI & ROC)
// ==========================================
const calculateRSI = (prices, period = 5) => { 
    if (prices.length < period) return new Array(prices.length).fill(50);
    let rsi = new Array(period).fill(50);
    let avgGain = 0, avgLoss = 0;
    for (let i = 1; i <= period; i++) {
        let change = prices[i] - prices[i-1];
        if (change >= 0) avgGain += change; else avgLoss -= change;
    }
    avgGain /= period; avgLoss /= period;
    if (avgLoss === 0) rsi[period] = 100; else rsi[period] = 100 - (100 / (1 + (avgGain / avgLoss)));
    for (let i = period + 1; i < prices.length; i++) {
        let change = prices[i] - prices[i-1];
        let currentGain = change > 0 ? change : 0;
        let currentLoss = change < 0 ? -change : 0;
        avgGain = (avgGain * (period - 1) + currentGain) / period;
        avgLoss = (avgLoss * (period - 1) + currentLoss) / period;
        if (avgLoss === 0) rsi.push(100); else rsi.push(100 - (100 / (1 + (avgGain / avgLoss))));
    }
    return prices.map((_, i) => i < period ? 50 : rsi[i]);
};

const calculateROC = (data, isDifference = false) => {
    const roc = [0]; 
    for (let i = 1; i < data.length; i++) {
        const prev = data[i-1];
        const curr = data[i];
        if (isDifference || prev === 0) {
            roc.push(curr - prev); 
        } else {
            const change = (curr - prev) / prev;
            if (!isFinite(change)) roc.push(0);
            else roc.push(change);
        }
    }
    return roc;
};

// ==========================================
// 計算移動平均線 (Simple Moving Average)
// ==========================================
const calculateMA = (prices, period) => {
    const ma = [];
    for (let i = 0; i < prices.length; i++) {
        if (i < period - 1) {
            // 資料不足時使用現有數據的平均
            const available = prices.slice(0, i + 1);
            ma.push(available.reduce((a, b) => a + b, 0) / available.length);
        } else {
            const window = prices.slice(i - period + 1, i + 1);
            ma.push(window.reduce((a, b) => a + b, 0) / period);
        }
    }
    return ma;
};

// ==========================================
// 計算 MACD 信號線差值 (fast - slow EMA normalized)
// ==========================================
const calculateEMA = (prices, period) => {
    const k = 2 / (period + 1);
    const ema = [prices[0]];
    for (let i = 1; i < prices.length; i++) {
        ema.push(prices[i] * k + ema[i-1] * (1 - k));
    }
    return ema;
};

// ══════════════════════════════════════════════════════════════
// 純函式決策邏輯 —— 與 IndexedDB 無關，可單獨驗證
// ══════════════════════════════════════════════════════════════

// 時序保留集切分：一律取「最後」一段，且永不打亂。
// 隨機切分會讓未來資料洩漏進訓練集，在時間序列上是嚴重錯誤。
function splitHoldout(n, frac = 0.15, minHold = 20) {
    if (!Number.isFinite(n) || n <= 0) return { trainEnd: 0, holdStart: 0, holdLen: 0 };
    let holdLen = Math.floor(n * frac);
    if (holdLen < minHold) holdLen = Math.min(minHold, Math.floor(n / 4));
    if (holdLen < 1) return { trainEnd: n, holdStart: n, holdLen: 0 };   // 資料太少就不切
    const trainEnd = n - holdLen;
    if (trainEnd < 30) return { trainEnd: n, holdStart: n, holdLen: 0 }; // 訓練集不足則放棄保留集
    return { trainEnd, holdStart: trainEnd, holdLen };
}

// 晉升規則：challenger 必須在「同一個保留集」上明確勝過 champion 才能上線。
// 沒有這道關卡，自動重訓會讓模型在無人察覺時逐步變差。
function shouldPromote(champion, challenger, opts = {}) {
    const daMargin   = opts.daMargin   ?? 1.0;   // 方向準確率至少要多 1 個百分點
    const mapeMargin = opts.mapeMargin ?? 0.0;
    const minHold    = opts.minHoldout ?? 20;

    if (!challenger || !Number.isFinite(challenger.holdoutDA)) {
        return { promote: false, reason: '挑戰者沒有有效的保留集分數' };
    }
    if ((challenger.holdoutN ?? 0) < minHold) {
        return { promote: false, reason: `保留集僅 ${challenger.holdoutN ?? 0} 筆，不足以判定（需 ${minHold} 筆）` };
    }
    if (!champion || !Number.isFinite(champion.holdoutDA)) {
        return { promote: true, reason: '目前沒有冠軍模型，挑戰者直接上任' };
    }

    const dDA = challenger.holdoutDA - champion.holdoutDA;
    if (dDA > daMargin) {
        return { promote: true, reason: `方向準確率 ${challenger.holdoutDA.toFixed(2)}% 勝過冠軍 ${champion.holdoutDA.toFixed(2)}%（+${dDA.toFixed(2)}）` };
    }
    // DA 打平時才看誤差，避免用價格誤差推翻方向表現
    if (Math.abs(dDA) <= daMargin) {
        const cM = champion.holdoutMAPE, gM = challenger.holdoutMAPE;
        if (Number.isFinite(cM) && Number.isFinite(gM) && (cM - gM) > mapeMargin) {
            return { promote: true, reason: `方向準確率相當，但誤差較低（${gM.toFixed(2)}% < ${cM.toFixed(2)}%）` };
        }
    }
    return { promote: false, reason: `未達晉升門檻（DA ${challenger.holdoutDA.toFixed(2)}% vs 冠軍 ${champion.holdoutDA.toFixed(2)}%）` };
}

// 由保留集的預測與實際值算出 DA / MAPE
function scoreHoldout(actualPrices, predPrices, prevPrices) {
    const n = Math.min(actualPrices.length, predPrices.length);
    if (n === 0) return { holdoutDA: NaN, holdoutMAPE: NaN, holdoutN: 0 };
    let correct = 0, valid = 0, mapeSum = 0, mapeN = 0;
    for (let i = 0; i < n; i++) {
        const a = actualPrices[i], p = predPrices[i];
        const prev = prevPrices ? prevPrices[i] : (i > 0 ? actualPrices[i - 1] : null);
        if (Number.isFinite(a) && Number.isFinite(p) && a !== 0) {
            mapeSum += Math.abs((a - p) / a) * 100; mapeN++;
        }
        if (Number.isFinite(prev) && Number.isFinite(a) && Number.isFinite(p)) {
            if ((a >= prev) === (p >= prev)) correct++;
            valid++;
        }
    }
    return {
        holdoutDA: valid ? (correct / valid) * 100 : NaN,
        holdoutMAPE: mapeN ? mapeSum / mapeN : NaN,
        holdoutN: n,
    };
}

// 把過去的預測與實際價格對帳。只結算目標日已有實際收盤價的紀錄。
function resolvePredictions(pending, priceByDate) {
    const resolved = [];
    for (const p of pending) {
        if (p.resolved) continue;
        const actual = priceByDate[p.targetDate];
        if (!Number.isFinite(actual)) continue;      // 目標日尚未到來或無資料
        const basis = p.basisPrice;
        const actualUp = Number.isFinite(basis) ? actual >= basis : null;
        const predUp   = Number.isFinite(basis) ? p.predPrice >= basis : (p.predDir === 1);
        resolved.push({
            ...p,
            resolved: 1,
            actualPrice: actual,
            actualUp: actualUp === null ? null : (actualUp ? 1 : 0),
            correct: actualUp === null ? null : ((actualUp === predUp) ? 1 : 0),
            absPctErr: actual !== 0 ? Math.abs((actual - p.predPrice) / actual) * 100 : null,
            resolvedAt: new Date().toISOString(),
        });
    }
    return resolved;
}

// 近期實測表現 —— 這是代理判斷「自己是否還準」的依據
function recentPerformance(resolvedPreds, window = 20) {
    const done = resolvedPreds
        .filter(p => p.resolved && p.correct !== null && p.correct !== undefined)
        .sort((a, b) => String(a.targetDate).localeCompare(String(b.targetDate)));
    const recent = done.slice(-window);
    if (recent.length === 0) return { n: 0, da: NaN, mape: NaN };
    const hits = recent.filter(p => p.correct === 1).length;
    const errs = recent.map(p => p.absPctErr).filter(Number.isFinite);
    return {
        n: recent.length,
        da: (hits / recent.length) * 100,
        mape: errs.length ? errs.reduce((a, b) => a + b, 0) / errs.length : NaN,
    };
}

// 依近期表現決定是否該重訓。門檻刻意保守：
// 樣本太少時不動作，避免拿雜訊當訊號。
function shouldRetrain(perf, opts = {}) {
    const minN = opts.minN ?? 10;
    const daFloor = opts.daFloor ?? 50;
    if (!perf || perf.n < minN) {
        return { retrain: false, reason: `實測樣本僅 ${perf?.n ?? 0} 筆，不足以判斷（需 ${minN} 筆）` };
    }
    if (Number.isFinite(perf.da) && perf.da < daFloor) {
        return { retrain: true, reason: `近 ${perf.n} 次方向準確率 ${perf.da.toFixed(1)}%，低於 ${daFloor}% 門檻` };
    }
    return { retrain: false, reason: `近 ${perf.n} 次方向準確率 ${perf.da.toFixed(1)}%，維持現任冠軍` };
}

/**
 * 📦 computeSLVHoldings: 白銀實物庫存代理
 * 方法: SLV_price / Silver_futures_price ≈ oz_per_share (實際約 0.93 oz/share)
 *   - 全球最大白銀 ETF，持倉約 3–5 億盎司實物 (LBMA 倫敦金庫)
 *   - oz_per_share 因 0.5%/年管理費緩慢下降
 *   - 比值短期偏離反映 premium/discount：正偏 = 市場需求 > NAV (創設壓力 = 庫存增)
 *   - 不需額外 API —— 全部基於既有 Yahoo Finance 真實數據
 * 回傳: oz_per_share × 1000 (數值約 920–1000，方便後續 Min-Max scale)
 */
const computeSLVHoldings = (slvPrices, silverFuturesPrices) => {
    return slvPrices.map((slv, i) => {
        const si = silverFuturesPrices[i];
        if (!si || si <= 0 || !slv || slv <= 0) return 930;
        return (slv / si) * 1000;
    });
};

// Google Trends 週資料 → 每日序列
// 每個點的時間是「該週的第一天」，數值卻是整週的搜尋熱度 ——
// 要等這週結束才知道。因此只能從「下一週」開始使用（第 7~13 天），
// 否則週一的特徵就含有週二到週六的資訊（前視偏差）。
// 尚未結束的當週（isPartial）數值還會變動，直接略過。
const alignWeeklyTrends = (timeline, dates, fallback = 50) => {
    const trendMap = new Map();
    for (const pt of timeline) {
        if (pt.isPartial) continue;
        const ms = parseInt(pt.time, 10) * 1000;
        if (!isFinite(ms)) continue;
        const val = pt.value?.[0] ?? fallback;
        for (let day = 7; day < 14; day++) {
            trendMap.set(new Date(ms + day * 86_400_000).toISOString().slice(0, 10), val);
        }
    }
    // 對齊交易日（向前填補）
    let last = fallback;
    return dates.map(d => {
        if (trendMap.has(d)) last = trendMap.get(d);
        return last;
    });
};

// ══════════════════════════════════════════════════════════════
// COT 週報 → 每日序列（依「公布時間」對齊，避免前視偏差）
//
// 報告的日期是部位統計的截至日（週二），CFTC 要到同週五美東 15:30 才公布，
// 當天的收盤價已經產生。遇到聯邦假日，公布會延到下週一。
// 因此一份報告最早只能從「截至日 + COT_RELEASE_LAG_DAYS 天」起使用：
// 7 天 = 下週二，假日延後公布的週也不會用到當時尚未公開的資訊。
// 第一份可用報告之前沒有資料，填 0（中性），不能拿之後的報告往回填。
// ══════════════════════════════════════════════════════════════
const COT_RELEASE_LAG_DAYS = 7;
const addDaysISO = (ymd, n) => new Date(Date.parse(ymd + 'T00:00:00Z') + n * 86_400_000).toISOString().slice(0, 10);
const alignCotToDates = (cotDataMap, dates) => {
    const reports = [...cotDataMap.entries()]
        .filter(([, v]) => v && Number.isFinite(v.net))
        .map(([asOf, v]) => ({ usableFrom: addDaysISO(asOf, COT_RELEASE_LAG_DAYS), net: v.net }))
        .sort((a, b) => a.usableFrom.localeCompare(b.usableFrom));
    let k = 0, last = 0;
    return dates.map(d => {
        while (k < reports.length && reports[k].usableFrom <= d) last = reports[k++].net;
        return last;
    });
};

// ══════════════════════════════════════════════════════════════
// 資料品質檢查：哪些因子其實是隨機模擬值
// ══════════════════════════════════════════════════════════════
const SYMBOL_FACTORS = {
    'SI=F':     ['price', 'goldSilverRatio', 'silverInventory'],
    'SLV':      ['slvPrices', 'slvVolume', 'slvVolumeRoc', 'silverInventory'],
    'GC=F':     ['goldPrices', 'goldSilverRatio'],
    'CL=F':     ['oilPrices'],
    'DX-Y.NYB': ['dxyPrices'],
    '^VIX':     ['vixPrices', 'vixRoc'],
    '^TNX':     ['tnxPrices'],
    'HG=F':     ['copperPrices'],
};
const mockFactorsFor = (mockSymbols, fearGreedMock) => {
    const set = new Set();
    (mockSymbols || []).forEach(s => (SYMBOL_FACTORS[s] || []).forEach(k => set.add(k)));
    if (fearGreedMock) set.add('fearGreed');
    return [...set];
};

// 回傳 { blocked, reason, mockSelected }。
//   - 白銀價格本身是模擬值 → 一律擋（目標值就是假的）
//   - 整份資料都是模擬 → 只有使用者明確選「模擬資料」示範時才放行
//   - 被勾選的因子是模擬值 → 擋，並列出要取消勾選的因子
const checkDataQuality = (data, selectedFactors) => {
    const q = data?.dataQuality;
    if (!q) return { blocked: false, reason: null, mockSelected: [] };   // 舊版快照沒有這個欄位
    if (q.allMock) {
        return q.explicitDemo
            ? { blocked: false, reason: '⚠️ 目前使用「模擬資料」示範模式，訓練結果沒有任何參考價值。', mockSelected: [] }
            : { blocked: true, reason: '市場資料載入失敗，畫面上是隨機模擬資料，已停止訓練。請重新載入真實資料。', mockSelected: [] };
    }
    const mf = q.mockFactors || [];
    if (mf.includes('price')) {
        return { blocked: true, reason: `白銀期貨 (SI=F) 抓取失敗，目前價格是隨機模擬值，已停止訓練。`, mockSelected: ['price'] };
    }
    const mockSelected = mf.filter(k => selectedFactors && selectedFactors[k]);
    if (mockSelected.length) {
        return { blocked: true, reason: `以下已勾選的因子抓取失敗、目前是模擬值：${mockSelected.join(', ')}。請取消勾選或重新載入資料後再訓練。`, mockSelected };
    }
    return { blocked: false, reason: null, mockSelected: [] };
};

// 保留最近 years 年（以日曆日期計算）。以前誤把 years×365 當成「交易日筆數」，
// 實際取到的期間約多出 45%（2 年變成約 2.9 年）。
const filterDataByYears = (data, years) => { 
    if (!data || !data.dates || data.dates.length === 0) return data;
    const lastMs = Date.parse(data.dates[data.dates.length - 1] + 'T00:00:00Z');
    const cutoff = new Date(lastMs - years * 365.25 * 86_400_000).toISOString().slice(0, 10);
    let startIndex = data.dates.findIndex(d => d >= cutoff);
    if (startIndex < 0) startIndex = 0;
    if (startIndex === 0 && data.dates.length <= 10) return data; 
    const filtered = {};
    Object.keys(data).forEach(key => {
        if (Array.isArray(data[key])) filtered[key] = data[key].slice(startIndex);
        else filtered[key] = data[key]; 
    });
    return filtered;
};


// ==========================================
// 3. TensorFlow 數據處理與模型構建
// ==========================================

// ══════════════════════════════════════════════════════════════
// 遮罩注意力層 (weightless)
//
// tfjs-layers 沒有內建 MultiHeadAttention。此層刻意「不帶權重」——
// Q/K/V 投影交給標準 tf.layers.dense（在 rank-3 上等同 TimeDistributed），
// 這層只負責注意力數學與因果遮罩。好處：
//   · 不需自行管理 addWeight，序列化/反序列化風險低
//   · 只用 rank-3 × rank-3 的 batched matMul，行為明確
// 「因果」= 第 t 個 patch 只能看到 ≤ t 的 patch，是 decoder-only 的核心，
// 也確保訓練時不會洩漏未來資訊。
// ══════════════════════════════════════════════════════════════
class MaskedAttention extends tf.layers.Layer {
    constructor(config) {
        super(config || {});
        this.dim = (config && config.dim) || 32;
        this._mask = null;
        this._maskP = -1;
    }
    computeOutputShape(inputShape) {
        const s = Array.isArray(inputShape[0]) ? inputShape[0] : inputShape;
        return [s[0], s[1], this.dim];
    }
    // 遮罩必須建立在 tidy 之外並以 tf.keep 保留，
    // 否則會被 model.predict/fit 內部的 tidy 回收掉。
    _ensureMask(P) {
        if (this._mask && this._maskP === P) return;
        if (this._mask) { try { this._mask.dispose(); } catch (_) {} }
        const m = [];
        for (let i = 0; i < P; i++) {
            const row = [];
            for (let j = 0; j < P; j++) row.push(j <= i ? 0 : -1e9);
            m.push(row);
        }
        this._mask = tf.keep(tf.tensor2d(m));
        this._maskP = P;
    }
    call(inputs) {
        const arr = Array.isArray(inputs) ? inputs : [inputs];
        const P = arr[0].shape[1];
        this._ensureMask(P);
        return tf.tidy(() => {
            const [q, k, v] = arr;
            let s = tf.matMul(q, k, false, true);        // [B,P,d]×[B,d,P] → [B,P,P]
            s = tf.div(s, Math.sqrt(this.dim));
            s = tf.add(s, this._mask);                    // 遮住未來
            return tf.matMul(tf.softmax(s, -1), v);       // [B,P,P]×[B,P,d] → [B,P,d]
        });
    }
    getConfig() { return Object.assign(super.getConfig(), { dim: this.dim }); }
    static get className() { return 'MaskedAttention'; }
}
try { tf.serialization.registerClass(MaskedAttention); } catch (_) {}

// 取序列最後一個 token —— decoder-only 的預測位置。
// 用它而非 globalAveragePooling，才符合「以最新 patch 預測下一步」的語意。
class LastToken extends tf.layers.Layer {
    constructor(config) { super(config || {}); }
    computeOutputShape(inputShape) {
        const s = Array.isArray(inputShape[0]) ? inputShape[0] : inputShape;
        return [s[0], s[2]];
    }
    call(inputs) {
        return tf.tidy(() => {
            const x = Array.isArray(inputs) ? inputs[0] : inputs;
            return x.slice([0, x.shape[1] - 1, 0], [-1, 1, -1]).squeeze([1]);
        });
    }
    static get className() { return 'LastToken'; }
}
try { tf.serialization.registerClass(LastToken); } catch (_) {}

// ══════════════════════════════════════════════════════════════
// TimesFM 架構 (patched decoder-only transformer)
//
// ⚠️ 這是 TimesFM 的「架構」，以你的資料從頭訓練，
//    不是 Google 發布的預訓練基礎模型權重
//    （那是 200M/500M 參數的 PyTorch/JAX 模型，無法在瀏覽器執行）。
//    因此沒有 zero-shot 能力，需與其他演算法一樣訓練。
//
// 論文核心要素，依瀏覽器算力縮小實作：
//   1. Input patching —— 時間軸切成不重疊 patch 作為 token，
//      比逐時間點當 token 更省算力，也更容易抓到區段型態
//   2. Residual block —— patch embedding 用帶線性跳接的 MLP
//   3. Stacked decoder layers —— 因果自注意力 + FFN
//   4. 取最後一個 patch 的表示解碼
// ══════════════════════════════════════════════════════════════
const createTimesFM = (inputTensor, lookback, finalNumFeatures, params) => {
    const dModel  = Math.min(params.layers, 64);   // shader uniform 安全上限
    const ffDim   = Math.min(dModel * 2, 128);
    const nLayers = 2;

    // patch 長度只取「能整除 lookback」的值，reshape 才合法；
    // 另要求至少切出 2 個 patch，否則整段變成單一 token，注意力就失去意義
    //（例如 lookback=7 若取 patchLen=7 只會得到 1 個 token）。
    // 都不滿足就退回 1：每個時間點自成一個 token，永遠合法。
    let patchLen = 1;
    for (let cand = Math.min(8, Math.floor(lookback / 2)); cand >= 2; cand--) {
        if (lookback % cand === 0) { patchLen = cand; break; }
    }
    const numPatches = lookback / patchLen;

    // [B, lookback, F] → [B, numPatches, patchLen×F]
    let x = tf.layers.reshape({ targetShape: [numPatches, patchLen * finalNumFeatures] }).apply(inputTensor);

    // ── 1. 輸入 residual block ──
    const inHidden = tf.layers.dense({ units: dModel, activation: 'relu' }).apply(x);
    const inProj   = tf.layers.dense({ units: dModel }).apply(inHidden);
    const inSkip   = tf.layers.dense({ units: dModel }).apply(x);
    x = tf.layers.add().apply([inProj, inSkip]);

    // ── 2. decoder layers ──
    for (let i = 0; i < nLayers; i++) {
        const q = tf.layers.dense({ units: dModel }).apply(x);
        const k = tf.layers.dense({ units: dModel }).apply(x);
        const v = tf.layers.dense({ units: dModel }).apply(x);
        const attn = new MaskedAttention({ dim: dModel }).apply([q, k, v]);
        x = tf.layers.add().apply([x, attn]);
        x = tf.layers.layerNormalization({ epsilon: 1e-6 }).apply(x);

        let ff = tf.layers.dense({ units: ffDim, activation: 'relu' }).apply(x);
        ff = tf.layers.dropout({ rate: 0.1 }).apply(ff);
        ff = tf.layers.dense({ units: dModel }).apply(ff);
        x = tf.layers.add().apply([x, ff]);
        x = tf.layers.layerNormalization({ epsilon: 1e-6 }).apply(x);
    }

    // ── 3. 取最後一個 patch ──
    x = new LastToken({}).apply(x);

    // ── 4. 輸出 residual block ──
    const outHidden = tf.layers.dense({ units: dModel, activation: 'relu' }).apply(x);
    const outProj   = tf.layers.dense({ units: dModel }).apply(outHidden);
    const outSkip   = tf.layers.dense({ units: dModel }).apply(x);
    return tf.layers.add().apply([outProj, outSkip]);
};

// Transformer 編碼器核心
const createTransformerEncoder = (inputTensor, lookback, finalNumFeatures, params) => {
    const numHeads = Math.min(8, Math.floor(params.layers / 16)); 
    // Cap ffDim: too large → MAX_FRAGMENT_UNIFORM_VECTORS exceeded → shader link failure
    const ffDim = Math.min(params.layers * 2, 128);  // was ×4, reduced to ×2, cap 128

    let x = tf.layers.dense({ units: params.layers, activation: 'relu' }).apply(inputTensor);

    let attentionOutput = tf.layers.conv1d({
        filters: params.layers,
        kernelSize: 3, 
        padding: 'same',
        activation: 'relu'
    }).apply(x);

    x = tf.layers.add().apply([x, attentionOutput]);
    x = tf.layers.layerNormalization({ epsilon: 1e-6 }).apply(x);

    let ffOutput = tf.layers.dense({ units: ffDim, activation: 'relu' }).apply(x);
    ffOutput = tf.layers.dropout({ rate: 0.1 }).apply(ffOutput);
    ffOutput = tf.layers.dense({ units: params.layers }).apply(ffOutput);

    x = tf.layers.add().apply([x, ffOutput]);
    x = tf.layers.layerNormalization({ epsilon: 1e-6 }).apply(x);

    let output = tf.layers.globalAveragePooling1d().apply(x); 

    return output; 
};


const createModel = (algorithm, lookback, finalNumFeatures, params) => {
    // 限制搜尋時的模型複雜度，防止 shader uniform 超限
    // WebGL MAX_FRAGMENT_UNIFORM_VECTORS = 1024 (mobile) ~ 4096 (desktop)
    // GRU/LSTM shader uniform 數 ≈ units² × 4 — 超過時 shader link 失敗
    const safeUnits = Math.min(params.layers, 64); // cap 64 for shader safety

    const inputShape = [lookback, finalNumFeatures];
    const inputTensor = tf.input({ shape: inputShape });
    let outputTensor;

    if (algorithm === 'BiGRU') {
        outputTensor = tf.layers.bidirectional({
            layer: tf.layers.gru({ units: safeUnits, returnSequences: true, recurrentInitializer: 'glorotUniform' }),
            inputShape: inputShape
        }).apply(inputTensor);
        outputTensor = tf.layers.dropout({ rate: 0.3 }).apply(outputTensor);
        outputTensor = tf.layers.bidirectional({
            layer: tf.layers.gru({ units: safeUnits, returnSequences: false, recurrentInitializer: 'glorotUniform' })
        }).apply(outputTensor);
    } else if (algorithm === 'LSTM') {
        outputTensor = tf.layers.lstm({
            units: safeUnits,
            returnSequences: true,
            inputShape: inputShape
        }).apply(inputTensor);
        outputTensor = tf.layers.dropout({ rate: 0.3 }).apply(outputTensor);
        outputTensor = tf.layers.lstm({
            units: safeUnits,
            returnSequences: false
        }).apply(outputTensor);
    } else if (algorithm === 'Transformer') {
        // Transformer with safeUnits
        const tParams = { ...params, layers: safeUnits };
        outputTensor = createTransformerEncoder(inputTensor, lookback, finalNumFeatures, tParams);
    } else if (algorithm === 'TimesFM') {
        const fmParams = { ...params, layers: safeUnits };
        outputTensor = createTimesFM(inputTensor, lookback, finalNumFeatures, fmParams);
    } else {
        throw new Error(`未知的演算法: ${algorithm}`);
    }

    // 依預測目標決定輸出層（價格迴歸 / 方向分類 / 雙頭）
    const _spec = resolveTargetSpec(params.predTarget || 'price', params);
    outputTensor = tf.layers.dense({ units: _spec.units, activation: _spec.activation }).apply(outputTensor);
    const model = tf.model({ inputs: inputTensor, outputs: outputTensor });
    return model;
};

// 把 X（樣本 × lookback × 特徵 的巢狀陣列）的 [from, to) 區段轉成 3D tensor。
// 先攤平成 Float32Array 再建 tensor：比 tf.tensor3d(巢狀陣列) 少掉形狀推斷與中間陣列，
// 全程式建 X tensor 都走這裡。
const windowsToTensor = (X, from = 0, to = X.length) => {
    const n = Math.max(0, to - from);
    const lb = X[from]?.length ?? 0;
    const nf = X[from]?.[0]?.length ?? 0;
    const flat = new Float32Array(n * lb * nf);
    let o = 0;
    for (let i = from; i < to; i++) {
        const win = X[i];
        for (let j = 0; j < lb; j++) {
            const row = win[j];
            for (let k = 0; k < nf; k++) flat[o++] = row[k];
        }
    }
    return tf.tensor3d(flat, [n, lb, nf]);
};

// opts.horizon：預測幾個交易日後（預設 1 = 隔日）。第 k 筆樣本的輸入是到 P[t] 為止的視窗，
// 目標是 P[t+horizon]（對數報酬率目標為 ln(P[t+horizon] / P[t])）。
const createMultivariateDataset = (data, lookback, selectedFactors, maxSamples = null, opts = {}) => {
    const horizon = Math.max(1, opts.horizon || 1);
    const { prices, slvPrices, slvVolume, slvVolumeRoc, goldPrices, oilPrices, dxyPrices, vixPrices, tnxPrices, copperPrices, silverInventory, goldSilverRatio, socialSentiment, rsiPrices, rsiPrices12, vixRoc, tipsYield, cotNetPosition,
            priceROC, ma5Silver, ma20Silver, maCross, macdLine, fearGreed, gtrend } = data;

    // ── 先決定時序切分，再做正規化 ──
    // min/max（與報酬率的 mean/std）只能用「訓練段」計算，再套用到保留集。
    // 若用全期間計算，保留集的價格區間會洩漏進訓練資料。
    // 保留集之後的值可能超出 [0,1]，這是正常的，代表價格突破訓練期間的範圍。
    // maxSamples: 只建最後 N 個視窗（搜尋用），省去建全量再 slice 的白工
    const startIdx = maxSamples ? Math.max(lookback, prices.length - maxSamples) : lookback;
    // 樣本數：最後 horizon-1 天沒有「未來目標」，不成樣本
    const split = splitHoldout(Math.max(0, prices.length - startIdx - (horizon - 1)));
    // 正規化只用到 startIdx+trainEnd 之前的價格。horizon > 1 時，
    // 呼叫端必須在訓練段尾端留 horizon-1 筆間隔（purge），訓練目標才不會超過這個範圍
    const fitEnd = Math.max(1, Math.min(prices.length, startIdx + split.trainEnd));

    const scale = (arr) => {
        let min = Infinity, max = -Infinity;
        const n = Math.min(fitEnd, arr.length);
        for (let i = 0; i < n; i++) {
            const v = arr[i];
            if (!Number.isFinite(v)) continue;
            if (v < min) min = v;
            if (v > max) max = v;
        }
        if (!Number.isFinite(min)) { min = 0; max = 0; }
        const range = max - min;
        return { scaled: arr.map(v => range === 0 ? 0 : (v - min) / range), min, max };
    };

    const pObj = scale(prices);
    const slvPObj = scale(slvPrices); 
    const slvVObj = scale(slvVolume); 
    const slvVRocObj = scale(slvVolumeRoc);
    const tipsObj = scale(tipsYield);  // 🏦 TIPS 5Y Real Yield 
    const rsiObj = scale(rsiPrices); 
    const rsi12Obj = scale(rsiPrices12); 
    const vrocObj = scale(vixRoc);
    const sObj = scale(socialSentiment);
    const fgArr = fearGreed || prices.map(() => 50);
    const fgObj = scale(fgArr);
    const gObj = scale(goldPrices);
    const oObj = scale(oilPrices);
    const dObj = scale(dxyPrices);
    const vObj = scale(vixPrices); 
    const tObj = scale(tnxPrices); 
    const cObj = scale(copperPrices); 
    const invObj = scale(silverInventory); // 📦 SLV Holdings proxy 
    const gsrObj = scale(goldSilverRatio); 
    const cotObj = scale(cotNetPosition); 
    // 🎯 動量/趨勢特徵 scaling
    const priceROCArr  = priceROC  || prices.map(() => 0);
    const ma5Arr       = ma5Silver || calculateMA(prices, 5);
    const ma20Arr      = ma20Silver|| calculateMA(prices, 20);
    const maCrossArr   = maCross   || ma5Arr.map((v, i) => ma20Arr[i] !== 0 ? (v - ma20Arr[i]) / ma20Arr[i] : 0);
    const macdArr      = macdLine  || prices.map(() => 0);
    const pRocObj   = scale(priceROCArr);
    const ma5Obj    = scale(ma5Arr);
    const ma20Obj   = scale(ma20Arr);
    const maCrossObj= scale(maCrossArr);
    const macdObj   = scale(macdArr);
    const gtrendArr  = gtrend || prices.map(() => 50);
    const gtrendObj = scale(gtrendArr);


    const allFeatures = [
        { key: 'price', scaled: pObj.scaled, name: '白銀期貨價格 (Lag)', isExternal: false, minMax: pObj, isSelected: true }, 
        { key: 'slvPrices', scaled: slvPObj.scaled, name: 'SLV ETF 收盤價', isExternal: true, minMax: slvPObj, isSelected: selectedFactors.slvPrices }, 
        { key: 'slvVolume', scaled: slvVObj.scaled, name: 'SLV ETF 成交量', isExternal: true, minMax: slvVObj, isSelected: selectedFactors.slvVolume }, 
        { key: 'slvVolumeRoc', scaled: slvVRocObj.scaled, name: 'SLV ETF 成交量 ROC', isExternal: true, minMax: slvVRocObj, isSelected: selectedFactors.slvVolumeRoc }, 
        { key: 'tipsYield', scaled: tipsObj.scaled, name: '實質利率 TIPS 5Y Real Yield (%)', isExternal: true, minMax: tipsObj, isSelected: selectedFactors.tipsYield }, 
        { key: 'rsiPrices', scaled: rsiObj.scaled, name: '白銀 RSI 指標 (5)', isExternal: true, minMax: rsiObj, isSelected: selectedFactors.rsiPrices }, 
        { key: 'rsiPrices12', scaled: rsi12Obj.scaled, name: '白銀 RSI 指標 (12)', isExternal: true, minMax: rsi12Obj, isSelected: selectedFactors.rsiPrices12 },
        { key: 'vixRoc', scaled: vrocObj.scaled, name: 'VIX 變動率 (ROC)', isExternal: true, minMax: vrocObj, isSelected: selectedFactors.vixRoc }, 
        { key: 'fearGreed', scaled: fgObj.scaled, name: 'CNN Fear & Greed (0-100)', isExternal: true, minMax: fgObj, isSelected: selectedFactors.fearGreed },
        { key: 'socialSentiment', scaled: sObj.scaled, name: 'StockTwits 白銀情緒', isExternal: true, minMax: sObj, isSelected: selectedFactors.socialSentiment },
        { key: 'goldPrices', scaled: gObj.scaled, name: '黃金價格 (GC=F)', isExternal: true, minMax: gObj, isSelected: selectedFactors.goldPrices },
        { key: 'oilPrices', scaled: oObj.scaled, name: '原油價格 (CL=F)', isExternal: true, minMax: oObj, isSelected: selectedFactors.oilPrices },
        { key: 'dxyPrices', scaled: dObj.scaled, name: '美元指數 (DXY)', isExternal: true, minMax: dObj, isSelected: selectedFactors.dxyPrices },
        { key: 'vixPrices', scaled: vObj.scaled, name: 'VIX 恐慌指數 (^VIX)', isExternal: true, minMax: vObj, isSelected: selectedFactors.vixPrices }, 
        { key: 'tnxPrices', scaled: tObj.scaled, name: '10年期利率 (^TNX)', isExternal: true, minMax: tObj, isSelected: selectedFactors.tnxPrices },
        { key: 'copperPrices', scaled: cObj.scaled, name: '銅期貨價格 (HG=F)', isExternal: true, minMax: cObj, isSelected: selectedFactors.copperPrices }, 
        { key: 'silverInventory', scaled: invObj.scaled, name: '白銀庫存代理 (SLV oz/share×1k)', isExternal: true, minMax: invObj, isSelected: selectedFactors.silverInventory }, 
        { key: 'goldSilverRatio', scaled: gsrObj.scaled, name: '黃金/白銀比', isExternal: true, minMax: gsrObj, isSelected: selectedFactors.goldSilverRatio }, 
        { key: 'cotNetPosition', scaled: cotObj.scaled, name: 'COT Managed Money Net', isExternal: true, minMax: cotObj, isSelected: selectedFactors.cotNetPosition }, 
        // 🎯 動量/趨勢特徵（DA 提升關鍵）
        { key: 'priceROC',   scaled: pRocObj.scaled,    name: '白銀日報酬率 (ROC)',    isExternal: true, minMax: pRocObj,    isSelected: selectedFactors.priceROC   },
        { key: 'ma5Silver',  scaled: ma5Obj.scaled,     name: '白銀 MA5',              isExternal: true, minMax: ma5Obj,     isSelected: selectedFactors.ma5Silver  },
        { key: 'ma20Silver', scaled: ma20Obj.scaled,    name: '白銀 MA20',             isExternal: true, minMax: ma20Obj,    isSelected: selectedFactors.ma20Silver },
        { key: 'maCross',    scaled: maCrossObj.scaled,  name: 'MA Cross (MA5-MA20)',   isExternal: true, minMax: maCrossObj, isSelected: selectedFactors.maCross    },
        { key: 'macdLine',   scaled: macdObj.scaled,     name: 'MACD 線 (12-26 EMA)',   isExternal: true, minMax: macdObj,    isSelected: selectedFactors.macdLine   },
        { key: 'gtrend',     scaled: gtrendObj.scaled,   name: 'Google Trends「silver」(0-100)', isExternal: true, minMax: gtrendObj, isSelected: selectedFactors.gtrend },
    ];

    const activeFeatures = allFeatures.filter(f => f.isSelected);
    const numFeatures = activeFeatures.length; 

    const X = [];
    const y = [];
    const yDir = [];        // 方向標籤：1=上漲, 0=下跌（相對前一日）
    const yLogRet = [];     // 對數報酬率目標
    const prevPrices = [];  // 每個樣本對應的前一日實際價格（重建價格用）

    if (prices.length <= lookback) {
        console.error("數據點不足以建立 Lookback 序列。");
        return { X: [], y: [], yDir: [], yLogRet: [], prevPrices: [],
                 minMax: { price: pObj, logRet: { mean: 0, std: 1 } },
                 allMinMax: {}, activeFeatures, numFeatures: 0,
                 split: { trainEnd: 0, holdStart: 0, holdLen: 0 }, startIdx, horizon, latestWindow: null };
    }

    const windowEndingAt = (last) => {   // 輸入視窗：索引 last-lookback+1 .. last
        const sequence = [];
        for (let j = last - lookback + 1; j <= last; j++) sequence.push(activeFeatures.map(f => f.scaled[j]));
        return sequence;
    };
    for(let i = startIdx; i + horizon - 1 < prices.length; i++) {
        const ti = i + horizon - 1;          // 目標價格的索引
        X.push(windowEndingAt(i - 1));
        y.push(pObj.scaled[ti]);
        // 方向標籤：目標日價格 vs 輸入視窗最後一天（用未正規化價格判斷）
        yDir.push(prices[ti] > prices[i - 1] ? 1 : 0);
        // 對數報酬率目標 r = ln(P_target / P_last)，以及重建價格所需的基準價格
        const pPrev = prices[i - 1], pNow = prices[ti];
        yLogRet.push((pPrev > 0 && pNow > 0) ? Math.log(pNow / pPrev) : 0);
        prevPrices.push(pPrev);
    }
    // 預測未來用的視窗：結束在最後一個交易日（訓練樣本的最後一個視窗少了這一天）
    const latestWindow = windowEndingAt(prices.length - 1);

    // 對數報酬率用 z-score 標準化：報酬率以 0 為中心且量級小（~±0.02），
    // 用 min-max 會被極端值主導，標準化才能讓 Huber 在合理尺度上運作。
    // 同樣只用訓練段計算統計量
    const lrFit  = yLogRet.slice(0, Math.max(1, (split.trainEnd || yLogRet.length) - (horizon - 1)));
    const lrMean = lrFit.length ? lrFit.reduce((s, v) => s + v, 0) / lrFit.length : 0;
    const lrVar  = lrFit.length ? lrFit.reduce((s, v) => s + (v - lrMean) ** 2, 0) / lrFit.length : 0;
    const lrStd  = Math.sqrt(lrVar) || 1;   // 全為常數時避免除以 0
    const yLogRetScaled = yLogRet.map(v => (v - lrMean) / lrStd);

    const allMinMax = {};
    activeFeatures.forEach(f => { allMinMax[f.key] = f.minMax; });

    // split：與正規化使用同一個切分點，呼叫端的保留集/驗證集必須用它
    return { X, y, yDir, yLogRet: yLogRetScaled, prevPrices,
             minMax: { price: pObj, logRet: { mean: lrMean, std: lrStd } },
             allMinMax, activeFeatures, numFeatures, split, startIdx, horizon, latestWindow };
};

// ... (calculateFactorInfluence, calculateDirectionalAccuracy, calculateKFoldDirectionalAccuracy, runMultiStepPrediction, exportModel remains the same) ...
const calculateFactorInfluence = async (model, lastInput, minMax, activeFeatures, finalNumFeatures) => {
    // ── 防護性校驗 ──
    if (!lastInput || !lastInput.length || !lastInput[0]?.length) {
        console.warn('[Influence] lastInput 無效，跳過分析');
        return {};
    }

    const actualNumFeatures = lastInput[0].length;
    const lookback          = lastInput.length;
    const perturbationDelta = 0.05;

    // activeFeatures 長度應與 actualNumFeatures 一致；若不一致則截斷或以 index label 補齊
    const safeName = (index) => {
        const f = Array.isArray(activeFeatures) ? activeFeatures[index] : undefined;
        return f?.name || `Feature_${index}`;
    };

    // 模型預期的輸入特徵數（從 model.layers[0] 取得）
    let modelExpectedFeatures = actualNumFeatures;
    try {
        const inputShape = model.layers[0].batchInputShape;
        if (inputShape && inputShape[2]) modelExpectedFeatures = inputShape[2];
    } catch(_) {}

    if (actualNumFeatures !== modelExpectedFeatures) {
        console.warn(`[Influence] 特徵數不符：X=${actualNumFeatures}, model=${modelExpectedFeatures}，跳過分析`);
        return null;
    }

    const influence = {};

    // ── 基準預測（帶 finally 確保 tensor 釋放）──
    let basePred = 0;
    {
        let bT, bP;
        try {
            bT = tf.tensor3d([lastInput], [1, lookback, actualNumFeatures]);
            bP = model.predict(bT);
            const bd = await bP.data();
            basePred = denormalizePrice(bd[0], minMax);
        } catch (e) {
            console.warn('[Influence] 基準預測失敗:', e?.message);
            return {};   // 基準都失敗就無從比較，早返回
        } finally {
            if (bT) tf.dispose(bT);
            if (bP) tf.dispose(bP);
        }
    }

    // ── 逐特徵擾動 ──
    for (let index = 1; index < actualNumFeatures; index++) {   // index 0 = price，跳過
        const featureName = safeName(index);
        let tP, pP, tM, pM;
        try {
            const origVal = lastInput[lookback - 1][index];

            // + delta
            const plus = lastInput.map((step, si) =>
                si === lookback - 1
                    ? step.map((v, fi) => fi === index ? Math.min(1.0, v + perturbationDelta) : v)
                    : [...step]
            );
            tP = tf.tensor3d([plus],  [1, lookback, actualNumFeatures]);
            pP = model.predict(tP);
            const dp = await pP.data();

            // - delta
            const minus = lastInput.map((step, si) =>
                si === lookback - 1
                    ? step.map((v, fi) => fi === index ? Math.max(0.0, v - perturbationDelta) : v)
                    : [...step]
            );
            tM = tf.tensor3d([minus], [1, lookback, actualNumFeatures]);
            pM = model.predict(tM);
            const dm = await pM.data();

            const predPlus  = denormalizePrice(dp[0], minMax);
            const predMinus = denormalizePrice(dm[0], minMax);
            influence[featureName] = (predPlus - predMinus) / 2;   // central difference

        } catch (e) {
            console.warn(`[Influence] 特徵 ${featureName} (idx=${index}) 擾動失敗:`, e?.message);
            influence[featureName] = 0;   // 失敗的特徵給 0，不中斷整個分析
        } finally {
            if (tP) tf.dispose(tP);
            if (pP) tf.dispose(pP);
            if (tM) tf.dispose(tM);
            if (pM) tf.dispose(pM);
        }

        // 每 8 個特徵讓 event loop 喘息，避免 UI 凍結
        if (index % 8 === 0) await new Promise(r => setTimeout(r, 0));
    }

    return influence;
};

const calculateDirectionalAccuracy = (fullPrices, lookback, predictedHistory) => {
    if (!predictedHistory || predictedHistory.length === 0) return 0;
    let correctDirectionCount = 0;
    let validCount = 0;
    for (let i = 0; i < predictedHistory.length; i++) {
        const prevIdx = lookback + i - 1;
        const curIdx  = lookback + i;
        if (prevIdx < 0 || curIdx >= fullPrices.length) continue;
        const actualPrevPrice    = fullPrices[prevIdx];
        const actualCurrentPrice = fullPrices[curIdx];
        const predictedCurrentPrice = predictedHistory[i];
        if (!Number.isFinite(actualPrevPrice) || !Number.isFinite(actualCurrentPrice) ||
            !Number.isFinite(predictedCurrentPrice)) continue;
        const actualDir    = (actualCurrentPrice - actualPrevPrice) >= 0;
        const predictedDir = (predictedCurrentPrice - actualPrevPrice) >= 0;
        if (actualDir === predictedDir) correctDirectionCount++;
        validCount++;
    }
    return validCount > 0 ? (correctDirectionCount / validCount) * 100 : 0;
};

const calculateKFoldDirectionalAccuracy = (testActuals, testPreds) => {
    if (testActuals.length <= 1 || testActuals.length !== testPreds.length) return 0;
    let correctDirectionCount = 0;
    for (let i = 1; i < testActuals.length; i++) {
        const actualChange = testActuals[i] - testActuals[i - 1];
        const predictedChange = testPreds[i] - testActuals[i - 1]; 

        const actualDir = actualChange >= 0; 
        const predictedDir = predictedChange >= 0; 
        if (actualDir === predictedDir) correctDirectionCount++;
    }
    return (correctDirectionCount / (testActuals.length - 1)) * 100;
};


// latestWindow：結束在最後一個交易日的輸入視窗（createMultivariateDataset 回傳）。
// 未提供時退回 X 的最後一筆 —— 那個視窗少了最新一天，第一步預測其實是「今天」。
const runMultiStepPrediction = async (model, X, y, minMax, activeFeatures, finalNumFeatures, lookback, futureDays, logsUpdater, predTarget = 'price', prevPrices = null, latestWindow = null) => {
    logsUpdater('正在計算歷史擬合路徑...');
    if (X.length === 0) {
        logsUpdater('錯誤: 歷史數據不足，無法進行預測。');
        return { historyPreds: [], futurePrices: [], influenceScores: null, actualHistory: [], dirProbs: [] };
    }

    // ── WebGL 記憶體防護：tensor 數量超過閾值時先做 backend reset ──
    const memBefore = tf.memory();
    if (memBefore.numTensors > 800) {
        logsUpdater(`⚠️ 偵測到殘留 tensor 過多 (${memBefore.numTensors})，正在清理...`);
        try { await tf.setBackend('webgl'); } catch(_) {}
    }

    const denormalizeLocal = (val) => denormalizePrice(val, minMax);
    const sig = (z) => 1 / (1 + Math.exp(-z));
    const stride = predTarget === 'dual' ? 2 : 1;  // dual 輸出 2 個單元
    // 對數報酬率：把標準化輸出還原為 r，再由前一日價格重建價格
    const lrStats  = (minMax && minMax.logRet) ? minMax.logRet : { mean: 0, std: 1 };
    const toLogRet = (z) => z * lrStats.std + lrStats.mean;
    const predLogRets = [];   // 供方向準確率直接由報酬率正負號判定
    // 反正規化價格 → [0,1] 尺度，迭代預測時要把重建價格寫回特徵 0
    const pMin = minMax.price.min, pMax = minMax.price.max;
    // 不夾到 [0,1]：正規化只用訓練段 min/max，近期價格超出範圍是正常的，夾住會讓預測卡在天花板
    const normPrice = (v) => (pMax - pMin) === 0 ? 0 : (v - pMin) / (pMax - pMin);

    // ── 1. 分批次歷史擬合（避免一次性大 tensor 造成 OOM）──
    const BATCH = 128; // 每批推理筆數，可視記憶體壓力調小
    const historyPreds = [];   // 價格（price/dual）或機率轉合成價（direction）
    const dirProbs = [];       // 方向機率（direction/dual）
    let batchErr = null;

    // Warmup（捕捉 WebGL context 遺失問題）
    try {
        const w = tf.zeros([1, lookback, finalNumFeatures]);
        const wp = model.predict(w);
        await wp.data();
        tf.dispose([w, wp]);   // dispose both input AND output
    } catch (e) {
        logsUpdater(`⚠️ 模型預熱失敗，嘗試繼續 (${e.message?.slice(0, 80)})`);
    }

    for (let start = 0; start < X.length; start += BATCH) {
        const chunk = X.slice(start, start + BATCH);
        let chunkTensor, predTensor;
        try {
            chunkTensor = windowsToTensor(X, start, Math.min(start + BATCH, X.length));
            predTensor  = model.predict(chunkTensor);
            const data  = await predTensor.data();
            for (let r = 0; r < chunk.length; r++) {
                if (predTarget === 'logreturn') {
                    // 由「前一日實際價格」重建，避免誤差沿著歷史累積
                    const rr = toLogRet(data[r]);
                    predLogRets.push(rr);
                    const idx  = start + r;
                    const base = (prevPrices && prevPrices[idx] != null)
                        ? prevPrices[idx]
                        : denormalizeLocal(y[idx]);
                    historyPreds.push(base * Math.exp(rr));
                } else if (predTarget === 'price') {
                    historyPreds.push(denormalizeLocal(data[r]));
                } else if (predTarget === 'direction') {
                    const p = data[r];           // sigmoid 機率
                    dirProbs.push(p);
                    historyPreds.push(p);        // 直接存機率（圖表以機率呈現）
                } else { // dual
                    historyPreds.push(denormalizeLocal(data[r * 2]));      // 單元0=價格
                    dirProbs.push(sig(data[r * 2 + 1]));                   // 單元1=方向 logit
                }
            }
        } catch (e) {
            batchErr = e;
            logsUpdater(`❌ 歷史擬合批次失敗 (起始=${start}): ${e.message?.slice(0, 120)}`);
            break;
        } finally {
            if (chunkTensor) tf.dispose(chunkTensor);
            if (predTensor)  tf.dispose(predTensor);
        }
        // 每 512 筆讓 UI 喘口氣
        if (start % 512 === 0 && start > 0) await new Promise(r => setTimeout(r, 0));
    }

    if (batchErr) {
        logsUpdater('歷史擬合中斷，已回傳部分結果。請嘗試重新執行或重新整理頁面。');
        // 仍回傳已收集到的部分結果，不讓整個預測流程崩潰
    } else {
        logsUpdater(`歷史擬合完成 (${historyPreds.length} 筆)。`);
    }

    // ── 2. 迭代未來預測 ──
    // 從 X 資料本身推導真實形狀（不依賴外部參數，避免不一致）
    const iterLookback = X[X.length - 1].length;            // 實際 lookback（行數）
    const iterFeatures = X[X.length - 1][0]?.length ?? 1;   // 實際特徵數（列數）
    logsUpdater(`正在執行 T+${futureDays} 日迭代預測（lookback=${iterLookback}, features=${iterFeatures}）...`);

    // deep-copy 最後一個 window，確保不污染原始 X
    let currentInput = (latestWindow || X[X.length - 1]).map(row => Float32Array.from(row));
    const futurePrices = [];
    const futureDirProbs = [];
    let lastScaled = currentInput[currentInput.length - 1][0]; // 最後一步的 price scaled 值

    for (let i = 0; i < futureDays; i++) {
        let inputTensor, outputTensor;
        try {
            // 用從資料推導的真實形狀建立 tensor
            const flat = [];
            for (const row of currentInput) for (const v of row) flat.push(v);
            inputTensor  = tf.tensor(flat, [1, iterLookback, iterFeatures]);
            outputTensor = model.predict(inputTensor);
            const out = await outputTensor.data();

            if (predTarget === 'logreturn') {
                // 複利式推進：P_{t+1} = P_t · exp(r)，並把新價格寫回視窗特徵 0
                const rr = toLogRet(out[0]);
                predLogRets.push(rr);
                const base = futurePrices.length
                    ? futurePrices[futurePrices.length - 1]
                    : (prevPrices && prevPrices.length ? denormalizeLocal(y[y.length - 1]) : denormalizeLocal(lastScaled));
                const np = base * Math.exp(rr);
                futurePrices.push(np);
                lastScaled = normPrice(np);
            } else if (predTarget === 'direction') {
                // 純方向：輸出為機率，無價格可迭代 → 以機率推一個微幅價格變化供圖表呈現
                const prob = out[0];
                futureDirProbs.push(prob);
                const stepPct = (prob - 0.5) * 0.02; // ±1% 視機率強度
                // 在價格空間推進，再轉回正規化值（正規化值乘比例不等於價格乘比例）
                lastScaled = normPrice(denormalizeLocal(lastScaled) * (1 + stepPct));
                futurePrices.push(denormalizeLocal(lastScaled));
            } else if (predTarget === 'dual') {
                lastScaled = out[0];                    // 單元0=價格
                futureDirProbs.push(sig(out[1]));       // 單元1=方向機率
                futurePrices.push(denormalizeLocal(lastScaled));
            } else {
                lastScaled = out[0];
                futurePrices.push(denormalizeLocal(lastScaled));
            }

            // 滑動視窗：把（合成/預測的）價格填入 feature 0
            const lastRow   = currentInput[currentInput.length - 1];
            const nextRow   = Float32Array.from(lastRow);
            nextRow[0]      = lastScaled;
            currentInput    = [...currentInput.slice(1), nextRow];

        } catch (e) {
            const msg = e?.message ?? String(e);
            logsUpdater(`⚠️ T+${i+1} 迭代失敗: ${msg.slice(0,100)}`);
            const lastRow = currentInput[currentInput.length - 1];
            const nextRow = Float32Array.from(lastRow);
            nextRow[0]    = lastScaled;
            currentInput  = [...currentInput.slice(1), nextRow];
            futurePrices.push(futurePrices[futurePrices.length - 1] ?? denormalizeLocal(lastScaled));
        } finally {
            if (inputTensor)  tf.dispose(inputTensor);
            if (outputTensor) tf.dispose(outputTensor);
        }
    }

    // ── 3. 因子敏感度分析 ──
    logsUpdater('正在進行因子敏感度分析...');
    let influenceScores = null;
    if (finalNumFeatures > 1) {
        try {
            influenceScores = await calculateFactorInfluence(
                model, latestWindow || X[X.length - 1], minMax, activeFeatures, finalNumFeatures
            );
        } catch (e) {
            logsUpdater(`⚠️ 因子敏感度分析失敗: ${e.message?.slice(0,80)}`);
        }
    }

    // ── 4. 實際歷史價格序列 ──
    const actualHistory = Array.isArray(y) && y.length > 0
        ? y.map(denormalizeLocal)
        : (activeFeatures.find(f => f.key === 'price')?.minMax?.scaled?.slice(lookback) ?? []).map(denormalizeLocal);

    const memAfter = tf.memory();
    console.log(`[Predict] tensors: ${memBefore.numTensors} → ${memAfter.numTensors}, bytes: ${(memAfter.numBytes/1024/1024).toFixed(1)} MB`);

    return { historyPreds, futurePrices, influenceScores, actualHistory, dirProbs, futureDirProbs, predLogRets };
};

// ══════════════════════════════════════════════════════════════
// 市場資料組裝（瀏覽器 fetchMarketData 與雲端訓練共用）
//   第一階段 alignSymbolSeries：以白銀期貨交易日為主軸，其他品種前向填補對齊
//   第二階段 deriveFeatures：加上輔助資料（COT/TIPS/Trends/F&G/StockTwits）與衍生特徵
// 每個品種的輸入格式：{ timestamps: 秒[], close: number[], volume: number[], isMock? }
// ══════════════════════════════════════════════════════════════
const MARKET_SYMBOLS = ["SI=F","SLV","GC=F","CL=F","DX-Y.NYB","^VIX","^TNX","HG=F"];

const alignSymbolSeries = (series) => {
    const silverFuturesData = series['SI=F'];
    const createLookup = (data) => {
        const map = new Map();
        data.timestamps.forEach((t, i) => {
            if (data.close[i] !== null) {
                const dateStr = new Date(t * 1000).toISOString().split('T')[0];
                map.set(dateStr, { close: data.close[i], volume: data.volume[i] });
            }
        });
        return map;
    };
    const slvMap = createLookup(series['SLV']);
    const goldMap = createLookup(series['GC=F']);
    const oilMap = createLookup(series['CL=F']);
    const dxyMap = createLookup(series['DX-Y.NYB']);
    const vixMap = createLookup(series['^VIX']);
    const tnxMap = createLookup(series['^TNX']);
    const copperMap = createLookup(series['HG=F']);

    const dates = [], prices = [], slvPrices = [], slvVolume = [], goldPrices = [], oilPrices = [];
    const dxyPrices = [], vixPrices = [], tnxPrices = [], copperPrices = [];
    let lastGold = 2000; let lastOil = 75; let lastDXY = 100; let lastVIX = 15; let lastTNX = 4.0; let lastCopper = 3.5;
    let lastSlvPrice = 22; let lastSlvVolume = 5000000;

    silverFuturesData.timestamps.forEach((t, i) => {
        if (silverFuturesData.close[i] === null) return;
        const dateStr = new Date(t * 1000).toISOString().split('T')[0];
        const gData = goldMap.get(dateStr)?.close; if (gData) lastGold = gData;
        const oData = oilMap.get(dateStr)?.close; if (oData) lastOil = oData;
        const dData = dxyMap.get(dateStr)?.close; if (dData) lastDXY = dData;
        const vData = vixMap.get(dateStr)?.close; if (vData) lastVIX = vData;
        const tData = tnxMap.get(dateStr)?.close; if (tData) lastTNX = tData;
        const cData = copperMap.get(dateStr)?.close; if (cData) lastCopper = cData;
        const slvDataPoint = slvMap.get(dateStr);
        if (slvDataPoint) {
            lastSlvPrice = slvDataPoint.close;
            if (slvDataPoint.volume !== null && slvDataPoint.volume !== undefined) lastSlvVolume = slvDataPoint.volume;
        }
        dates.push(dateStr);
        prices.push(silverFuturesData.close[i]);
        slvPrices.push(lastSlvPrice);
        slvVolume.push(lastSlvVolume);
        goldPrices.push(lastGold);
        oilPrices.push(lastOil);
        dxyPrices.push(lastDXY);
        vixPrices.push(lastVIX);
        tnxPrices.push(lastTNX);
        copperPrices.push(lastCopper);
    });
    return { dates, prices, slvPrices, slvVolume, goldPrices, oilPrices, dxyPrices, vixPrices, tnxPrices, copperPrices };
};

// aux：{ cotDataMap, tipsYield, gtrend, fearGreedData, stockTwitsResult, mockSymbols, dataCacheDate }
// 缺少的輔助資料以中性值補上，並標記為模擬值（訓練前由 checkDataQuality 擋下或排除）
const deriveFeatures = (base, aux = {}) => {
    const { dates, prices, slvPrices, slvVolume, goldPrices, oilPrices, dxyPrices, vixPrices, tnxPrices, copperPrices } = base;
    const cotDataMap = aux.cotDataMap || new Map();
    const tipsYield = aux.tipsYield || dates.map(() => 1.5);
    const gtrend = aux.gtrend || dates.map(() => 50);
    const fearGreedData = aux.fearGreedData || { values: dates.map(() => 50), source: 'mock', currentScore: 50, currentRating: 'Neutral' };
    const stockTwitsResult = aux.stockTwitsResult || { score: 50, source: 'mock', bullishPct: 50, bearishPct: 50, totalTagged: 0 };
    const missingReal = aux.mockSymbols || [];

    // COT：依公布時間對齊（見 alignCotToDates）
    const cotNetPositions = alignCotToDates(cotDataMap, dates);

    const goldSilverRatio = goldPrices.map((g, i) => g / prices[i]);
    const rsiPrices = calculateRSI(prices, 5);
    const rsiPrices12 = calculateRSI(prices, 12);
    const vixRoc = calculateROC(vixPrices);
    const slvVolumeRoc = calculateROC(slvVolume);

    // 📦 白銀庫存代理: SLV oz-per-share × 1000
    const silverInventory = computeSLVHoldings(slvPrices, prices);

    // 🎯 動量/趨勢特徵
    const priceROC    = calculateROC(prices);                       // 白銀日報酬率
    const ma5Silver   = calculateMA(prices, 5);                     // 5日均線
    const ma20Silver  = calculateMA(prices, 20);                    // 20日均線
    const maCross     = ma5Silver.map((v, i) =>                     // MA Cross: (MA5-MA20)/MA20
        ma20Silver[i] !== 0 ? (v - ma20Silver[i]) / ma20Silver[i] : 0
    );
    const ema12Silver = calculateEMA(prices, 12);
    const ema26Silver = calculateEMA(prices, 26);
    const macdLine    = ema12Silver.map((v, i) => v - ema26Silver[i]); // MACD 線

    // CNN Fear & Greed Index
    const fearGreedValues = fearGreedData.values;
    const fearGreedSource = fearGreedData.source;
    const fearGreedCurrent = fearGreedData.currentScore;
    const fearGreedRating  = fearGreedData.currentRating;

    // StockTwits 情緒序列
    // StockTwits 僅提供最新 ~30 則訊息，無歷史 API：
    //   - 歷史各天：以「價格動量」建構代理序列（只用到當天以前的價格）
    //   - 只有最後一天換成真實分數。不能往回混進過去幾天 ——
    //     那會讓過去的特徵含有「今天」才知道的情緒（前視偏差），而最近幾十天正好是測試期間
    const realSTScore = stockTwitsResult.score;        // 0–100
    const stSource    = stockTwitsResult.source;       // 'real' | 'mock'
    const socialSentiment = (() => {
        const n = prices.length;
        const arr = new Array(n);
        const momentum = prices.map((p, i) => i === 0 ? 0 : (p - prices[i-1]) / prices[i-1]);
        const smoothWin = 10;
        let base = 50;
        for (let i = 0; i < n; i++) {
            const start = Math.max(0, i - smoothWin + 1);
            const slice = momentum.slice(start, i + 1);
            const avgMom = slice.reduce((a, b) => a + b, 0) / slice.length;
            base = 50 + avgMom * 200;
            base = Math.max(10, Math.min(90, base));
            arr[i] = base;
        }
        if (n > 0 && stSource === 'real') arr[n - 1] = realSTScore;
        return arr;
    })();

    const last = (arr) => arr[arr.length - 1];
    const latestValues = {
        dataSource: `Real (Stooq/Yahoo, TIPS:Treasury, SLV Holdings, StockTwits:${stSource}, CFTC, F&G:${fearGreedSource})`,
        dataFetchedAt: new Date().toISOString(),
        dataCacheDate: aux.dataCacheDate || new Date().toLocaleString(),
        price: last(prices),
        slvPrices: last(slvPrices),
        slvVolume: last(slvVolume),
        tipsYield: last(tipsYield),
        rsiPrices: last(rsiPrices),
        rsiPrices12: last(rsiPrices12),
        vixRoc: last(vixRoc),
        socialSentiment: last(socialSentiment),
        stSource,
        stBullishPct: stockTwitsResult.bullishPct,
        stBearishPct: stockTwitsResult.bearishPct,
        stTotalTagged: stockTwitsResult.totalTagged,
        fearGreed: last(fearGreedValues),
        fearGreedSource,
        fearGreedCurrent,
        fearGreedRating,
        goldPrices: last(goldPrices),
        oilPrices: last(oilPrices),
        dxyPrices: last(dxyPrices),
        vixPrices: last(vixPrices),
        tnxPrices: last(tnxPrices),
        copperPrices: last(copperPrices),
        silverInventory: last(silverInventory),
        goldSilverRatio: last(goldSilverRatio),
        cotNetPosition: last(cotNetPositions),
        priceROC: last(priceROC),
        ma5Silver: last(ma5Silver),
        ma20Silver: last(ma20Silver),
        maCross: last(maCross),
        macdLine: last(macdLine),
        gtrend: last(gtrend),
    };

    return {
        dates, prices, slvPrices, slvVolume, slvVolumeRoc, goldPrices, oilPrices, dxyPrices, vixPrices, tnxPrices, copperPrices, silverInventory, goldSilverRatio,
        socialSentiment, fearGreed: fearGreedValues,
        rsiPrices, rsiPrices12, vixRoc, tipsYield,
        cotNetPosition: cotNetPositions,
        priceROC, ma5Silver, ma20Silver, maCross, macdLine,
        gtrend,
        latestValues,
        // 放在物件裡（非陣列），filterDataByYears 才不會把它當時間序列切掉
        dataQuality: {
            allMock: false,
            mockSymbols: missingReal,
            mockFactors: mockFactorsFor(missingReal, fearGreedSource === 'mock'),
        },
    };
};


// ══════════════════════════════════════════════════════════════
// 回測：逐日 1-step 預測 → 依預測報酬決定多/空/空手，扣手續費與滑價
//   priceIndices[t]：第 t 筆預測對應的價格索引 i（以 prices[i-1] 進場、prices[i] 出場）
//   predPrices[t]  ：模型對 prices[i] 的預測價格
// 有效筆數不足 5 筆時回傳 null
// ══════════════════════════════════════════════════════════════
const calcMaxDrawdown = (equity) => {
    let peak = equity[0] ?? 0;
    let maxDD = 0;
    for (const v of equity) {
        if (v > peak) peak = v;
        const dd = peak > 0 ? (peak - v) / peak : 0;
        if (dd > maxDD) maxDD = dd;
    }
    return maxDD;
};

const calcSharpe = (returns, periodsPerYear = 252) => {
    if (!returns || returns.length < 2) return 0;
    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const varr = returns.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (returns.length - 1);
    const std = Math.sqrt(varr);
    if (std === 0) return 0;
    return (mean / std) * Math.sqrt(periodsPerYear);
};

// holdDays：每筆交易持有幾天（以 prices[i-holdDays] 進場、prices[i] 出場）；預設 1
const simulateBacktest = ({ prices, dates, priceIndices, predPrices, config = {}, holdDays = 1 }) => {
    const initialCapital = Math.max(parseFloat(config.initialCapital) || 100000, 0);
    const leverage = Math.max(parseFloat(config.leverage) || 1.0, 0);
    const positionPct = Math.min(Math.max(parseFloat(config.positionPct) || 1.0, 0), 1);
    const feeBps = Math.max(parseFloat(config.feeBps) || 0, 0);
    const slippageBps = Math.max(parseFloat(config.slippageBps) || 0, 0);
    const threshold = Math.max(parseFloat(config.threshold) || 0, 0);
    const allowShort = !!config.allowShort;
    const oneWayCost = (feeBps + slippageBps) / 10000; // fraction per side

    let equity = initialCapital;
    const equityCurve = [];
    const dailyRets = [];
    const trades = [];
    let wins = 0;
    let tradeCount = 0;

    for (let t = 0; t < priceIndices.length; t++) {
        const i = priceIndices[t];
        if (i - holdDays < 0 || i >= prices.length) continue;
        const entry = prices[i - holdDays];
        const exit = prices[i];
        if (!(Number.isFinite(entry) && Number.isFinite(exit) && entry > 0)) continue;
        const predPrice = predPrices[t];
        if (!Number.isFinite(predPrice)) continue;
        const predRet = (predPrice - entry) / entry;

        let dir = 0;
        if (predRet > threshold) dir = 1;
        else if (allowShort && predRet < -threshold) dir = -1;

        // position sizing: notional = equity * positionPct * leverage
        const notional = equity * positionPct * leverage;
        const qty = dir !== 0 ? (notional / entry) * dir : 0;
        // costs: entry + exit
        const costs = Math.abs(qty) * entry * oneWayCost + Math.abs(qty) * exit * oneWayCost;
        const pnl = qty * (exit - entry) - costs;
        const prevEquity = equity;
        equity = equity + pnl;

        const r = prevEquity > 0 ? (equity / prevEquity - 1) : 0;
        equityCurve.push({ idx: i, equity, date: dates?.[i] ?? i });
        dailyRets.push(r);
        if (dir !== 0) {
            tradeCount += 1;
            if (pnl > 0) wins += 1;
            trades.push({ idx: i, date: dates?.[i] ?? i, dir, entry, exit, predPrice, predRet, pnl, equity });
        }
    }
    if (equityCurve.length < 5) return null;

    return {
        initialCapital,
        finalEquity: equity,
        totalReturn: (equity / initialCapital) - 1,
        maxDD: calcMaxDrawdown(equityCurve.map(d => d.equity)),
        sharpe: calcSharpe(dailyRets, 252 / holdDays),
        tradeCount,
        winRate: tradeCount > 0 ? wins / tradeCount : 0,
        equityCurve,
        trades,
    };
};


// ══════════════════════════════════════════════════════════════
// 模型輸出 → 預測價格（四種預測目標共用）
//   raw：model.predict 的扁平輸出（dual 為每筆 2 個值）
//   prevPrices[i]：第 i 筆樣本前一日的實際價格
// 與瀏覽器保留集評分、回測的解讀方式一致
// ══════════════════════════════════════════════════════════════
const decodePredictedPrices = (raw, predTarget, minMax, prevPrices) => {
    const n = predTarget === 'dual' ? raw.length / 2 : raw.length;
    const lrS = (minMax && minMax.logRet) ? minMax.logRet : { mean: 0, std: 1 };
    const out = new Array(n);
    for (let i = 0; i < n; i++) {
        const prev = prevPrices[i];
        if (predTarget === 'logreturn') out[i] = prev * Math.exp(raw[i] * lrS.std + lrS.mean);
        // 純方向：沒有價格，以機率偏離 0.5 的程度換成 ±1% 的預期變動，只用於判定方向
        else if (predTarget === 'direction') out[i] = prev * (1 + (raw[i] - 0.5) * 0.02);
        else if (predTarget === 'dual') out[i] = denormalizePrice(raw[i * 2], minMax);
        else out[i] = denormalizePrice(raw[i], minMax);
    }
    return out;
};


// ══════════════════════════════════════════════════════════════
// 輔助資料來源：網址與解析（瀏覽器與雲端共用；抓取方式各自實作）
// ══════════════════════════════════════════════════════════════

// 依日期前向填補對齊交易日；第一筆資料之前填 fallback
const alignMapToDates = (map, dates, fallback) => {
    let last = fallback;
    return dates.map(d => {
        if (map.has(d)) last = map.get(d);
        return Number.isFinite(last) ? last : fallback;
    });
};

// ── CFTC COT（Socrata API）──
const COT_API = {
    disagg: {
        base: 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json',
        // 這個資料集只有 report_date_as_yyyy_mm_dd（沒有 as_of_date_form_yyyy_mm_dd）；
        // 選取不存在的欄位會讓整個查詢失敗，過去因此一直退回 Legacy 資料集
        select: 'report_date_as_yyyy_mm_dd,market_and_exchange_names,m_money_positions_long_all,m_money_positions_short_all,open_interest_all',
        order: 'report_date_as_yyyy_mm_dd DESC',
    },
    legacy: {
        base: 'https://publicreporting.cftc.gov/resource/6dca-aqww.json',
        select: 'report_date_as_yyyy_mm_dd,market_and_exchange_names,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all',
        order: 'report_date_as_yyyy_mm_dd DESC',
    },
    silverWheres: [
        "upper(market_and_exchange_names) like '%SILVER%COMMODITY%EXCHANGE%INC%'",
        "upper(market_and_exchange_names) like '%SILVER%EXCHANGE%INC%'",
        "upper(market_and_exchange_names) like '%SILVER%'",
    ],
};
// 每週一筆；700 週約 13 年
const cotQueryUrl = (src, whereExpr, limit = 700) =>
    `${src.base}?$select=${src.select}&$where=${encodeURIComponent(whereExpr)}&$order=${encodeURIComponent(src.order)}&$limit=${limit}`;

// Disaggregated：Managed Money 淨部位；鍵為截至日（週二）
const parseCotDisagg = (records) => {
    const map = new Map();
    if (!Array.isArray(records)) return map;
    for (const r of records) {
        const raw = r.as_of_date_form_yyyy_mm_dd || r.report_date_as_yyyy_mm_dd;
        if (!raw) continue;
        const d   = String(raw).split('T')[0];
        const lng = parseFloat(r.m_money_positions_long_all);
        const sht = parseFloat(r.m_money_positions_short_all);
        if (!Number.isFinite(lng) || !Number.isFinite(sht)) continue;
        map.set(d, { net: lng - sht, long: lng, short: sht, openInterest: parseFloat(r.open_interest_all) || null, market: r.market_and_exchange_names || '' });
    }
    return map;
};

// Legacy：Non-Commercial 淨部位
const parseCotLegacy = (records) => {
    const map = new Map();
    if (!Array.isArray(records)) return map;
    for (const r of records) {
        const raw = r.report_date_as_yyyy_mm_dd || r.as_of_date_form_yyyy_mm_dd;
        if (!raw) continue;
        const d   = String(raw).split('T')[0];
        const lng = parseFloat(r.noncomm_positions_long_all);
        const sht = parseFloat(r.noncomm_positions_short_all);
        if (!Number.isFinite(lng) || !Number.isFinite(sht)) continue;
        map.set(d, { net: lng - sht, long: lng, short: sht, openInterest: parseFloat(r.open_interest_all) || null, market: r.market_and_exchange_names || '' });
    }
    return map;
};

// ── 美國財政部 5 年期 TIPS 實質殖利率（每年一份 XML）──
const treasuryRealYieldUrl = (year) =>
    'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value=' + year;
const parseTreasuryRealYieldXML = (xml) => {
    const map = new Map();
    const blocks = String(xml).match(/<m:properties>[\s\S]*?<\/m:properties>/g) || [];
    for (const b of blocks) {
        const dm = b.match(/<d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})/);
        // 財政部已把欄位從 BC_5YEAR 改名為 TC_5YEAR；兩種都接受
        const ym = b.match(/<d:(?:TC|BC)_5YEAR[^>]*>([\d.\-]+)<\/d:(?:TC|BC)_5YEAR>/);
        if (!dm || !ym) continue;
        const v = parseFloat(ym[1]);
        if (Number.isFinite(v)) map.set(dm[1], v);
    }
    return map;
};

// ── CNN Fear & Greed ──
const FEAR_GREED_URL = 'https://production.dataviz.cnn.io/index/fearandgreed/graphdata';
const parseFearGreed = (json) => {
    const currentScore  = parseFloat(json?.fear_and_greed?.score) || 50;
    const currentRating = json?.fear_and_greed?.rating || 'Neutral';
    const historical = json?.fear_and_greed_historical?.data || [];
    const map = new Map();
    historical.forEach(pt => {
        const ms = parseInt(pt.x, 10); if (!Number.isFinite(ms)) return;
        const d = new Date(ms);
        const ymd = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
        const val = parseFloat(pt.y); if (Number.isFinite(val)) map.set(ymd, val);
    });
    return { map, currentScore, currentRating };
};

// ── StockTwits（只有最新約 30 則訊息，沒有歷史）──
const STOCKTWITS_SYMBOLS = ['SLV', 'SI_F'];
const stockTwitsUrl = (sym) => `https://api.stocktwits.com/api/2/streams/symbol/${sym}.json`;
const countStockTwits = (data) => {
    if (data?.response?.status !== 200) throw new Error(`API status ${data?.response?.status}`);
    let bullish = 0, bearish = 0, tagged = 0;
    (data.messages || []).forEach(msg => {
        const s = msg?.entities?.sentiment?.basic;
        if (s === 'Bullish') { bullish++; tagged++; }
        else if (s === 'Bearish') { bearish++; tagged++; }
    });
    return { bullish, bearish, tagged, total: (data.messages || []).length };
};
// counts：每個 symbol 的 countStockTwits 結果（失敗為 null）
const summarizeStockTwits = (counts, symbols = STOCKTWITS_SYMBOLS) => {
    const valid = counts.filter(r => r && r.tagged > 0);
    if (valid.length === 0) return { score: 50, source: 'mock', bullishPct: 50, bearishPct: 50, totalTagged: 0, symbolsQueried: symbols };
    const totalBullish = valid.reduce((s, r) => s + r.bullish, 0);
    const totalTagged  = valid.reduce((s, r) => s + r.tagged, 0);
    const bullishPct   = totalTagged > 0 ? (totalBullish / totalTagged) * 100 : 50;
    return { score: bullishPct, source: 'real', bullishPct, bearishPct: 100 - bullishPct, totalTagged, symbolsQueried: symbols };
};

// ── Google Trends（兩段請求：explore 取 token → widgetdata 取週資料）──
const GOOGLE_TRENDS = { keyword: 'silver', geo: 'US' };
// 時間範圍必須是完整日期（YYYY-MM-DD YYYY-MM-DD）；只給年月（YYYY-MM）會被回 HTTP 400
const googleTrendsTimeRange = (dates) => `${dates[0] || '2021-01-01'} ${new Date().toISOString().slice(0, 10)}`;
const googleTrendsExploreUrl = (dates) => 'https://trends.google.com/trends/api/explore?' + new URLSearchParams({
    hl: 'en-US', tz: '-480',
    req: JSON.stringify({ comparisonItem: [{ keyword: GOOGLE_TRENDS.keyword, geo: GOOGLE_TRENDS.geo, time: googleTrendsTimeRange(dates) }], category: 0, property: '' }),
}).toString();
const googleTrendsWidgetUrl = (dates, token) => 'https://trends.google.com/trends/api/widgetdata/multiline?' + new URLSearchParams({
    hl: 'en-US', tz: '-480',
    req: JSON.stringify({ time: googleTrendsTimeRange(dates), resolution: 'WEEK', locale: 'en-US',
        comparisonItem: [{ geo: { country: GOOGLE_TRENDS.geo }, complexKeywordsRestriction: { keyword: [{ type: 'BROAD', value: GOOGLE_TRENDS.keyword }] } }],
        requestOptions: { property: '', backend: 'IZG', category: 0 } }),
    token,
}).toString();
// 回應前置 ")]}'"，去掉後才是 JSON
const parseGoogleTrendsJSON = (text) => { const i = String(text).indexOf('{'); return JSON.parse(i >= 0 ? text.slice(i) : text); };


export {
    alignMapToDates,
    COT_API,
    cotQueryUrl,
    parseCotDisagg,
    parseCotLegacy,
    treasuryRealYieldUrl,
    parseTreasuryRealYieldXML,
    FEAR_GREED_URL,
    parseFearGreed,
    STOCKTWITS_SYMBOLS,
    stockTwitsUrl,
    countStockTwits,
    summarizeStockTwits,
    GOOGLE_TRENDS,
    googleTrendsTimeRange,
    googleTrendsExploreUrl,
    googleTrendsWidgetUrl,
    parseGoogleTrendsJSON,
    decodePredictedPrices,
    calcMaxDrawdown,
    calcSharpe,
    simulateBacktest,
    MARKET_SYMBOLS,
    alignSymbolSeries,
    deriveFeatures,
    VALIDATION_MODE,
    denormalizePrice,
    getDirectionalHuberLoss,
    getHuberLoss,
    getDualHeadLoss,
    resolveTargetSpec,
    buildTargetTensor,
    calculateMAPE,
    calculateRSI,
    calculateROC,
    calculateMA,
    calculateEMA,
    splitHoldout,
    shouldPromote,
    scoreHoldout,
    resolvePredictions,
    recentPerformance,
    shouldRetrain,
    computeSLVHoldings,
    alignWeeklyTrends,
    COT_RELEASE_LAG_DAYS,
    addDaysISO,
    alignCotToDates,
    SYMBOL_FACTORS,
    mockFactorsFor,
    checkDataQuality,
    filterDataByYears,
    MaskedAttention,
    LastToken,
    createTimesFM,
    createTransformerEncoder,
    createModel,
    windowsToTensor,
    createMultivariateDataset,
    calculateFactorInfluence,
    calculateDirectionalAccuracy,
    calculateKFoldDirectionalAccuracy,
    runMultiStepPrediction,
};

import {
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
    alignSymbolSeries,
    deriveFeatures,
    MARKET_SYMBOLS,
    calculateKFoldDirectionalAccuracy,
    runMultiStepPrediction,
    simulateBacktest,
} from './core.js';

        const { useState, useEffect, useRef, useMemo, useCallback } = React;
        const tf = window.tf;

        // ── Lucide 圖示 ──
        // 圖示的 SVG 節點資料由 build.mjs 從 lucide 套件抽出（只含用到的圖示），
        // 放在 window.LUCIDE_ICONS。直接由 React 渲染 <svg>，
        // 不再用 lucide.createIcons() 事後改寫 DOM —— 那會和 React 的 reconciliation 打架。
        const toReactAttrs = (attrs) => {
            const out = {};
            for (const k in attrs) out[k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = attrs[k];
            return out;
        };
        const Icon = React.memo(({ name, className = '', style }) => {
            const nodes = (window.LUCIDE_ICONS || {})[name];
            if (!nodes) { console.warn('[Icon] 未內嵌的圖示:', name); return null; }
            return (
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                     className={`lucide lucide-${name} ${className}`} style={style} aria-hidden="true">
                    {nodes.map(([tag, attrs], i) => React.createElement(tag, { key: i, ...toReactAttrs(attrs) }))}
                </svg>
            );
        });

        // ==========================================
        // 核心模型設定
        // ==========================================
        const BEST_MODEL_FILENAME = 'best-slv-model-v19-disaggregated'; 
        // 模型權重存 IndexedDB：localStorage 只有約 5MB 且與快取共用，
        // BiGRU / Transformer 的權重很容易超過，存檔會失敗。
        const MODEL_URL = 'indexeddb://silver-model-v19'; 
        const BEST_MAPE_KEY = 'silver-best-mape-v19';
        const CHAMPION_URL  = 'indexeddb://silver-champion-v19';   // 冠軍權重另存，才能與挑戰者同場比較

        // 舊版把模型存在 localStorage —— 第一次開啟新版時搬到 IndexedDB 並刪除舊的，釋出空間
        const migrateModelStorage = async () => {
            try {
                const models = await tf.io.listModels();
                for (const url of [MODEL_URL, CHAMPION_URL]) {
                    const legacy = url.replace('indexeddb://', 'localstorage://');
                    if (!(legacy in models)) continue;
                    if (!(url in models)) await tf.io.copyModel(legacy, url);
                    await tf.io.removeModel(legacy);
                    console.log(`[Storage] 已將模型 ${legacy} 搬移到 ${url}`);
                }
            } catch (e) {
                console.warn('[Storage] 模型搬移失敗（不影響使用，只是舊模型需重新訓練）:', e?.message);
            }
        };
        const TRAIN_CONFIG_KEY = 'silver-train-config-v19'; 
        
        // 🎯 UPDATE: COT API URL (Disaggregated Futures Only)
        // 使用 Socrata Resource ID '72hh-3qpy' 對應 CFTC 的 Disaggregated Futures Only Reports
        // COT: API URLs defined inside fetchCOTData
        
        // 核心模型動作模式
        const RUN_MODE = {
            TRAIN_CONTINUE: 'TRAIN_CONTINUE',         // 重新訓練 (會忽略 IndexedDB 權重，從零開始訓練)
            TRAIN_RESUME: 'TRAIN_RESUME',             // 接續訓練 (從 IndexedDB 載入最佳模型繼續訓練)
            PREDICT_BEST: 'PREDICT_BEST',             // 僅預測 (從 IndexedDB 載入歷史最佳模型)
            PREDICT_UPLOADED: 'PREDICT_UPLOADED',     // 僅預測 (使用手動上傳的模型)
        };

        // ==========================================
        // 2. 數據獲取與處理
        // ==========================================
        // ==========================================
        // 🔄 ProxyRotator — 全域 CORS Proxy 輪替器
        //    - 維護多個備援 proxy，自動跳過近期失敗的
        //    - wrapUrl(url) 依序嘗試：直連 → proxy 1 → proxy 2 → ...
        // ==========================================
        // ══════════════════════════════════════════════════════════════
        // CORS proxy 清單（全程式共用這一份）
        //   · 公共 proxy 隨時可能限流或停止服務（thingproxy.freeboard.io 已停止，已移除）
        //   · 可自架 proxy（見 cors-proxy-worker.js），在畫面「資料代理」設定網址後排在最優先
        //     格式為網址前綴，程式會在後面接上 encodeURIComponent(目標網址)，
        //     例如 https://silver-proxy.<帳號>.workers.dev/?url=
        // ══════════════════════════════════════════════════════════════
        const CUSTOM_PROXY_KEY = 'silver-custom-proxy';
        const getCustomProxy = () => {
            try { return (localStorage.getItem(CUSTOM_PROXY_KEY) || '').trim(); } catch (_) { return ''; }
        };
        const PUBLIC_PROXIES = [
            { id: 'corsproxy.io',  wrap: u => `https://corsproxy.io/?${encodeURIComponent(u)}` },
            { id: 'allorigins',    wrap: u => `https://api.allorigins.win/raw?url=${encodeURIComponent(u)}` },
            { id: 'codetabs',      wrap: u => `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(u)}` },
            { id: 'corsproxy.dev', wrap: u => `https://corsproxy.dev/?${encodeURIComponent(u)}` },
        ];
        // 每次呼叫都重新讀設定，修改自架 proxy 後下次載入資料就生效，不必重新整理
        const proxyEntries = (order = null) => {
            const pub = order ? order.map(id => PUBLIC_PROXIES.find(p => p.id === id)).filter(Boolean) : PUBLIC_PROXIES;
            const custom = getCustomProxy();
            return custom ? [{ id: 'custom', wrap: u => custom + encodeURIComponent(u) }, ...pub] : pub;
        };
        // 給 raceText 使用：回傳包裝函式陣列
        const corsProxies = (order = null) => proxyEntries(order).map(p => p.wrap);

        const ProxyRotator = (() => {
            // 記錄各 proxy 最近失敗時間（ms）；COOLDOWN 內不再選用
            const failTs  = new Map();
            const COOLDOWN = 90_000;   // 90 秒冷卻

            // 標記 proxy 失敗
            const markFail = (id) => { failTs.set(id, Date.now()); };

            // 回傳「目前可用的 proxy 清單」：直連（Yahoo v7/COT Socrata 可能有 CORS，但試一下無妨）+ 各 proxy
            const available = () => {
                const now = Date.now();
                return [{ id: 'direct', wrap: null }, ...proxyEntries()]
                    .filter(({ id }) => now - (failTs.get(id) || 0) > COOLDOWN);
            };

            // 競速取得：所有可用 proxy 錯開 STAGGER 毫秒依序「發動」，誰先回來就用誰。
            //
            // 為何不逐一等待：一個無法連線的 proxy 要等滿 timeout 才換下一個，
            // 三個 proxy 就是三倍的等待。實測「前兩個逾時、第三個成功」的典型情況，
            // 逐一要 30 秒，競速只要 2 秒；而第一個就成功時兩者一樣快，競速零代價。
            //
            // 錯開發動（而非全部同時）是為了：第一個若正常，後面根本不會發出去，
            // 不會平白增加流量或觸發對方限流。
            const STAGGER_MS = 250;

            const fetchAny = (url, opts = {}) => {
                const { timeoutMs = 7000, headers = {}, skipDirect = false } = opts;
                const pool = available().filter(({ id }) => !(skipDirect && id === 'direct'));
                if (pool.length === 0) return Promise.reject(new Error('沒有可用的 proxy（全部在冷卻中）'));

                return new Promise((resolve, reject) => {
                    let settled = false, failed = 0;
                    const ctrls = [];
                    const timers = [];

                    // 注意：勝出者的 controller 必須排除在中止之外。
                    // fetchAny 回傳的是 Response 物件，呼叫端之後才讀 body；
                    // 若把贏家一起 abort，body 串流會被砍斷，
                    // 呼叫端的 .text()/.json() 會拋出 "The user aborted a request"。
                    const cleanup = (winner) => {
                        timers.forEach(clearTimeout);
                        ctrls.forEach(ct => { if (ct !== winner) { try { ct.abort(); } catch (_) {} } });
                    };

                    pool.forEach(({ id, wrap }, pi) => {
                        timers.push(setTimeout(async () => {
                            if (settled) return;            // 已有人勝出就不再發動
                            const wrapped = wrap ? wrap(url) : url;
                            const ctrl = new AbortController();
                            ctrls.push(ctrl);
                            const tid = setTimeout(() => { try { ctrl.abort(); } catch (_) {} }, timeoutMs);
                            timers.push(tid);
                            try {
                                const resp = await fetch(wrapped, { signal: ctrl.signal, headers });
                                clearTimeout(tid);
                                if (resp.status === 429 || resp.status === 503) {
                                    markFail(id);
                                    throw new Error(`HTTP ${resp.status} (rate limited)`);
                                }
                                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                                if (!settled) { settled = true; cleanup(ctrl); resolve(resp); }
                            } catch (e) {
                                clearTimeout(tid);
                                const msg = e?.message || '';
                                if (msg.includes('rate limit') || msg.includes('429') || msg.includes('503')) markFail(id);
                                if (++failed >= pool.length && !settled) {
                                    settled = true; cleanup(null);
                                    reject(new Error(`所有 proxy 都失敗: ${url.slice(0, 80)}`));
                                }
                            }
                        }, pi * STAGGER_MS));
                    });
                });
            };

            return { fetchAny, markFail, available, STAGGER_MS };
        })();

        // ==========================================
        // 💾 MarketDataCache — 以 localStorage 快取市場數據
        //    TTL：Yahoo EOD 數據 6 小時，其他 1 小時
        // ==========================================
        // ══════════════════════════════════════════════════════════════
        // AgentStore —— 代理的持久化記憶（IndexedDB）
        //
        // 三個 object store：
        //   runs        每次訓練/搜尋/預測的執行紀錄
        //   models      模型註冊表，含 champion / challenger 狀態
        //   predictions 預測日誌，事後與實際價格比對
        //
        // 用 IndexedDB 而非 localStorage：預測紀錄會逐日累積，
        // localStorage 的 5MB 上限與同步阻塞都不適合。
        // ══════════════════════════════════════════════════════════════
        const AgentStore = (() => {
            const DB_NAME = 'silver-agent-v1';
            const VERSION = 2;   // v2 新增 snapshot（市場資料快照，供「立即顯示再背景更新」）
            let _db = null;

            const open = () => new Promise((resolve, reject) => {
                if (_db) return resolve(_db);
                if (typeof indexedDB === 'undefined') return reject(new Error('IndexedDB 不可用'));
                const req = indexedDB.open(DB_NAME, VERSION);
                req.onupgradeneeded = (e) => {
                    const db = e.target.result;
                    if (!db.objectStoreNames.contains('runs')) {
                        const s = db.createObjectStore('runs', { keyPath: 'id', autoIncrement: true });
                        s.createIndex('ts', 'ts');
                    }
                    if (!db.objectStoreNames.contains('models')) {
                        const s = db.createObjectStore('models', { keyPath: 'id', autoIncrement: true });
                        s.createIndex('status', 'status');
                        s.createIndex('ts', 'ts');
                    }
                    if (!db.objectStoreNames.contains('predictions')) {
                        const s = db.createObjectStore('predictions', { keyPath: 'id', autoIncrement: true });
                        s.createIndex('targetDate', 'targetDate');
                        s.createIndex('resolved', 'resolved');
                    }
                    // 市場資料快照：固定 key='latest'，只保留最新一份。
                    // 用 IndexedDB 而非 localStorage —— 快照約 600KB，
                    // localStorage 的同步 JSON 解析會卡住主執行緒，正好抵消「立即顯示」的意義。
                    if (!db.objectStoreNames.contains('snapshot')) {
                        db.createObjectStore('snapshot', { keyPath: 'key' });
                    }
                };
                req.onsuccess = () => { _db = req.result; resolve(_db); };
                req.onerror = () => reject(req.error);
            });

            const tx = async (store, mode, fn) => {
                const db = await open();
                return new Promise((resolve, reject) => {
                    const t = db.transaction(store, mode);
                    const s = t.objectStore(store);
                    let out;
                    try { out = fn(s); } catch (e) { return reject(e); }
                    // 注意：IDBRequest 在查無資料時 result 為 undefined。
                    // 舊寫法會在這種情況回傳 IDBRequest 本身，讓「有沒有快照」的判斷永遠為真。
                    t.oncomplete = () => resolve(
                        (out && typeof out === 'object' && 'result' in out) ? out.result : out
                    );
                    t.onerror = () => reject(t.error);
                });
            };

            const get  = (store, key) => tx(store, 'readonly',  s => s.get(key));
            const add  = (store, obj) => tx(store, 'readwrite', s => s.add(obj));
            const put  = (store, obj) => tx(store, 'readwrite', s => s.put(obj));
            const all  = (store)      => tx(store, 'readonly',  s => s.getAll());
            const clear= (store)      => tx(store, 'readwrite', s => s.clear());

            return { open, get, add, put, all, clear, tx };
        })();


        const MarketDataCache = (() => {
            // 每次都抓最新資料；這份快取只在所有來源都失敗時當備援（稍舊的真實資料勝過隨機模擬值）。
            // 啟動時的「立即顯示」由 IndexedDB 快照負責，不靠這裡。
            const PREFIX = 'slv_cache_v2_';

            const get = (key) => {
                try {
                    const raw = localStorage.getItem(PREFIX + key);
                    if (!raw) return null;
                    const obj = JSON.parse(raw);
                    return obj; // { ts, data }
                } catch(e) { return null; }
            };

            const set = (key, data) => {
                try {
                    localStorage.setItem(PREFIX + key, JSON.stringify({ ts: Date.now(), data }));
                } catch(e) { console.warn('[Cache] localStorage write failed:', e?.message); }
            };

            const getData = (key) => { const e = get(key); return e ? e.data : null; };

            const clear = () => {
                Object.keys(localStorage)
                    .filter(k => k.startsWith(PREFIX))
                    .forEach(k => localStorage.removeItem(k));
                // 也清舊版 prefix（v1）
                Object.keys(localStorage)
                    .filter(k => k.startsWith('slv_cache_v1_'))
                    .forEach(k => localStorage.removeItem(k));
            };

            // 取得快取存入時間的日期字串（顯示用）
            const getCacheDate = (key) => {
                const e = get(key);
                if (!e) return null;
                return new Date(e.ts).toLocaleString();
            };

            return { set, getData, clear, getCacheDate };
        })();

        // ══════════════════════════════════════════════════════════════
        // WebGL 後端守護：偵測 shader 失敗並自動復原
        // "Failed to link vertex and fragment shaders" 發生時：
        //   1. 強制重設 WebGL context
        //   2. 若仍失敗，降級到 CPU backend
        // ══════════════════════════════════════════════════════════════
        const resetWebGLBackend = async () => {
            console.warn('[WebGL] Resetting backend due to context loss / shader failure');
            try {
                tf.engine().endScope();
            } catch(_) {}
            try {
                await tf.setBackend('webgl');
                await tf.ready();
                console.log('[WebGL] Backend reset OK, tensors:', tf.memory().numTensors);
                return true;
            } catch(e) {
                console.warn('[WebGL] Reset failed, falling back to CPU:', e?.message?.slice(0,60));
                try {
                    await tf.setBackend('cpu');
                    await tf.ready();
                    console.log('[CPU] Fallback backend ready');
                    return false; // false = degraded to CPU
                } catch(e2) {
                    console.error('[CPU] Fallback also failed:', e2?.message?.slice(0,60));
                    return false;
                }
            }
        };

        const isShaderError = (e) => {
            const msg = (e?.message || String(e)).toLowerCase();
            return msg.includes('shader') || msg.includes('link') ||
                   msg.includes('webgl') || msg.includes('context') ||
                   msg.includes('program') || msg.includes('compile');
        };

        // ══════════════════════════════════════════════════════════════
        // 🌙 背景安全 yield
        // tf.nextFrame() = requestAnimationFrame → 隱藏分頁時完全凍結
        // safeYield() 在背景時改用 setTimeout（節流至 ~1Hz 但持續執行）
        // 搭配既有的 CPU backend 切換，實現完整背景訓練
        // ══════════════════════════════════════════════════════════════
        const safeYield = () => document.hidden
            ? new Promise(r => setTimeout(r, 0))
            : tf.nextFrame();

        // raceText: 多個候選網址同時競速（錯開發動），取最先成功的文字內容。
        // 給那些有自己 proxy 清單、沒走 ProxyRotator 的來源使用。
        const raceText = (urls, { timeoutMs = 7000, stagger = 250 } = {}) => {
            if (!urls || urls.length === 0) return Promise.resolve(null);
            return new Promise((resolve) => {
                let settled = false, failed = 0;
                const ctrls = [], timers = [];
                const done = (val) => { if (settled) return; settled = true;
                    timers.forEach(clearTimeout);
                    ctrls.forEach(ct => { try { ct.abort(); } catch (_) {} });
                    resolve(val); };
                urls.forEach((u, i) => {
                    timers.push(setTimeout(async () => {
                        if (settled) return;
                        const ctrl = new AbortController(); ctrls.push(ctrl);
                        const tid = setTimeout(() => { try { ctrl.abort(); } catch (_) {} }, timeoutMs);
                        timers.push(tid);
                        try {
                            const r = await fetch(u, { signal: ctrl.signal });
                            clearTimeout(tid);
                            if (!r.ok) throw new Error('HTTP ' + r.status);
                            done(await r.text());
                        } catch (e) {
                            clearTimeout(tid);
                            if (++failed >= urls.length) done(null);   // 全失敗 → null，呼叫端決定備援
                        }
                    }, i * stagger));
                });
            });
        };


        // ══════════════════════════════════════════════════════════════
        // CNN Fear & Greed Index 真實抓取
        // 來源：CNN Business 非公開 DataViz API
        // 回傳：{ values: number[], source: 'real'|'mock',
        //         currentScore: number, currentRating: string }
        //
        // API 回傳 fear_and_greed_historical.data：
        //   [{x: unixMs, y: score(0-100)}, ...]  每日一筆，數年歷史
        // ==========================================
        const fetchFearGreedIndex = async (dates) => {
            const CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata";
            const CACHE_KEY = 'feargreed';

            const alignToDates = (map, fallback = 50) => {
                const out = []; let last = fallback;
                for (const d of dates) { if (map.has(d)) last = map.get(d); out.push(Number.isFinite(last) ? last : fallback); }
                return out;
            };
            const mockFallback = (currentScore = 50) => {
                const out = []; let v = currentScore;
                for (let i = 0; i < dates.length; i++) { v = Math.max(5, Math.min(95, v + (Math.random() - 0.5) * 3)); out.push(v); }
                return { values: out, source: "mock", currentScore, currentRating: "Neutral" };
            };
            if (!Array.isArray(dates) || dates.length === 0) return mockFallback();

            try {
                const resp = await ProxyRotator.fetchAny(CNN_URL, { timeoutMs: 10000, headers: { Accept: 'application/json' } });
                const json = await resp.json();
                const currentScore  = parseFloat(json?.fear_and_greed?.score) || 50;
                const currentRating = json?.fear_and_greed?.rating || "Neutral";
                const historical = json?.fear_and_greed_historical?.data || [];
                if (historical.length === 0) throw new Error("Empty historical data");
                const fgMap = new Map();
                historical.forEach(pt => {
                    const ms = parseInt(pt.x, 10); if (!Number.isFinite(ms)) return;
                    const d = new Date(ms);
                    const ymd = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
                    const val = parseFloat(pt.y); if (Number.isFinite(val)) fgMap.set(ymd, val);
                });
                // 存快取
                const mapObj = {}; fgMap.forEach((v, k) => { mapObj[k] = v; });
                MarketDataCache.set(CACHE_KEY, { map: mapObj, score: currentScore, rating: currentRating });
                console.log(`[F&G] OK: ${fgMap.size} pts, score=${currentScore.toFixed(1)}`);
                return { values: alignToDates(fgMap, currentScore), source: "real", currentScore, currentRating };
            } catch (e) {
                console.warn("[F&G] All proxies failed:", e.message);
                // 備援：上次成功抓到的歷史序列（只缺最近幾天，向前填補）
                const cached = MarketDataCache.getData(CACHE_KEY);
                if (cached?.map) {
                    console.warn(`[F&G] 使用 ${MarketDataCache.getCacheDate(CACHE_KEY)} 的快取資料`);
                    const fgMap = new Map(Object.entries(cached.map));
                    return { values: alignToDates(fgMap, cached.score), source: "real", currentScore: cached.score, currentRating: cached.rating };
                }
                return mockFallback();
            }
        };

        // ==========================================
        // StockTwits 真實情緒抓取（強化版）
        // 同時查詢 SLV + SI_F（白銀期貨），取加權平均
        // 回傳：{ score: number(0-100), source: 'real'|'mock',
        //         bullishPct: number, bearishPct: number,
        //         totalTagged: number, symbolsQueried: string[] }
        // ==========================================
        const fetchStockTwitsSentimentReal = async () => {
            const symbols = ["SLV", "SI_F"];
            // 不快取：StockTwits 只提供「最新約 30 則」的當下情緒，舊快照沒有參考價值

            const querySymbol = async (sym) => {
                const url = `https://api.stocktwits.com/api/2/streams/symbol/${sym}.json`;
                try {
                    const resp = await ProxyRotator.fetchAny(url, { timeoutMs: 8000, skipDirect: true });
                    const data = await resp.json();
                    if (data?.response?.status !== 200) throw new Error(`API status ${data?.response?.status}`);
                    let bullish = 0, bearish = 0, tagged = 0;
                    (data.messages || []).forEach(msg => {
                        const s = msg?.entities?.sentiment?.basic;
                        if (s === "Bullish") { bullish++; tagged++; }
                        else if (s === "Bearish") { bearish++; tagged++; }
                    });
                    return { bullish, bearish, tagged, total: (data.messages || []).length };
                } catch (e) {
                    console.warn(`[StockTwits] ${sym} failed:`, e.message);
                    return null;
                }
            };

            // 並行查詢所有 symbol
            const results = await Promise.all(symbols.map(querySymbol));
            const valid = results.filter(r => r !== null && r.tagged > 0);

            if (valid.length === 0) {
                console.warn("[StockTwits] All symbols failed or no tagged messages.");
                return { score: 50, source: "mock", bullishPct: 50, bearishPct: 50, totalTagged: 0, symbolsQueried: symbols };
            }

            // 合計所有有效 symbol 的標記訊息
            const totalBullish = valid.reduce((s, r) => s + r.bullish, 0);
            const totalTagged  = valid.reduce((s, r) => s + r.tagged, 0);
            const bullishPct   = totalTagged > 0 ? (totalBullish / totalTagged) * 100 : 50;
            const bearishPct   = 100 - bullishPct;
            const score        = bullishPct;

            console.log(`[StockTwits] Real score: ${score.toFixed(1)}% bullish (${totalTagged} tagged msgs from ${valid.length}/${symbols.length} symbols)`);
            const result = { score, source: "real", bullishPct, bearishPct, totalTagged, symbolsQueried: symbols };
            return result;
        };
        
        /**
         * 🏦 fetchTIPSYield: 實質利率 (5Y TIPS Par Real Yield)
         * 來源①: US Treasury XML API — daily_treasury_real_yield_curve
         *   https://home.treasury.gov/.../xml?data=daily_treasury_real_yield_curve&field_tdr_date_value=YYYY
         *   XML 欄位: d:BC_5YEAR (5年期 TIPS 實質殖利率, %)
         * 來源②: corsproxy.io / allorigins.win 代理
         * 備援: 以 0.5% 填補 (TIPS 資料始於 2004)
         */
        const fetchTIPSYield = async (dates) => {
            const TREASURY_BASE = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml';
            const CORS_PROXIES = corsProxies();
            const parseXML = (xml) => {
                const map = new Map();
                const blocks = xml.match(/<m:properties>[\s\S]*?<\/m:properties>/g) || [];
                for (const b of blocks) {
                    const dm = b.match(/<d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})/);
                    const ym = b.match(/<d:BC_5YEAR[^>]*>([\d.\-]+)<\/d:BC_5YEAR>/);
                    if (!dm || !ym) continue;
                    const v = parseFloat(ym[1]);
                    if (Number.isFinite(v)) map.set(dm[1], v);
                }
                return map;
            };
            const years = [...new Set(dates.map(d => d.slice(0, 4)))].sort();
            const tipsMap = new Map();
            // 各年度資料彼此獨立 → 平行抓取。原本 5~6 年逐年串行，
            // 每年最差要等到 proxy 逾時，是整個載入流程最長的單一環節。
            const perYear = await Promise.all(years.map(async (year) => {
                const baseUrl = TREASURY_BASE + '?data=daily_treasury_real_yield_curve&field_tdr_date_value=' + year;
                // 直連與各 proxy 一起競速，不再逐一等到逾時
                const xml = await raceText([baseUrl, ...CORS_PROXIES.map(p => p(baseUrl))], { timeoutMs: 7000 });
                if (xml) {
                    try {
                        const m = parseXML(xml);
                        if (m.size > 0) { console.log('[TIPS] ' + year + ': ' + m.size + ' records'); return m; }
                    } catch(e) { console.warn('[TIPS] ' + year + ' 解析失敗:', e?.message?.slice(0, 60)); }
                }
                return null;
            }));
            // 依年份順序合併，確保後續前向填補的結果與串行版本一致
            perYear.forEach(m => { if (m) m.forEach((v, k) => tipsMap.set(k, v)); });
            let lastKnown = null;
            const result = dates.map(d => {
                if (tipsMap.has(d)) lastKnown = tipsMap.get(d);
                return lastKnown !== null ? lastKnown : 0.5;
            });
            console.log('[TIPS] series length=' + result.length + ', latest=' + result[result.length-1]);
            return result;
        };


        /**
         * 🎯 NEW: 獲取 CFTC COT Disaggregated 數據
         * 來源: CFTC SODA API (Disaggregated Futures Only)
         * 篩選: Silver on COMEX
         */
        
        /**
         * 🧩 更穩定版：獲取 CFTC COT Disaggregated Futures Only（Managed Money 淨部位）
         * - 資料源：CFTC publicreporting（Socrata / SODA）
         * - 主要鍵：as_of_date_form_yyyy_mm_dd（注意：不是 report_date_as_yyyy_mm_dd）
         * - 主要欄位：m_money_positions_long_all / m_money_positions_short_all / open_interest_all
         * - 先直連 Socrata（通常支援 CORS），失敗再走 proxy
         * - 具備：重試 / 指數退避 / 多種 market filter fallback / 輕量 $select / 偵錯輸出
         *
         * 欄位與日期格式參考：Socrata dataset 欄位定義（As_of_Date_Form_YYYY-MM-DD）。 
         */
        /**
         * 📈 fetchGoogleTrends: Google Trends「silver」搜尋熱度 (0-100)
         *
         * 流程（兩步驟）：
         *  Step 1: POST /trends/api/explore → 取得 TIMESERIES widget token
         *  Step 2: GET  /trends/api/widgetdata/multiline?token=... → 取得週度時序資料
         *
         * 回應格式前置 ")]}'" 需剝除後再 JSON.parse
         * 回傳: number[] (長度 = dates.length, 以前向填補對齊每日)
         * 數值代表: 0=低熱度, 100=搜尋高峰（相對值）
         *
         * CORS: Google Trends 不允許直接 browser fetch → 必須透過 proxy
         *       若所有 proxy 均失敗 → 以 50 填充（中性值）
         */

        const fetchGoogleTrends = async (dates) => {
            const KEYWORD   = 'silver';
            const GEO       = 'US';
            // 從最早日期到今天，Google Trends 最多 5 年
            const startDate = dates[0] ? dates[0].slice(0, 7) : '2020-01';
            const endDate   = new Date().toISOString().slice(0, 7);
            const TIME_STR  = `${startDate} ${endDate}`;

            const EXPLORE_URL = 'https://trends.google.com/trends/api/explore';
            const WIDGET_URL  = 'https://trends.google.com/trends/api/widgetdata/multiline';

            // Google Trends 的 proxy 清單（allorigins 對 text 較可靠）
            const GT_PROXIES = corsProxies(['allorigins', 'corsproxy.io', 'codetabs']);

            const stripPrefix = (text) => {
                // Google Trends API 回應前置 ")]}'"
                const idx = text.indexOf('{');
                return idx >= 0 ? text.slice(idx) : text;
            };

            // Google Trends 要兩段請求（取 token → 取資料），逐一試 proxy 會把等待加倍。
            // 改競速後最差情況從 3×14s×2 ≈ 84 秒降到約 13 秒。
            const fetchViaProxies = (url, ms = 7000) =>
                raceText(GT_PROXIES.map(fn => fn(url)), { timeoutMs: ms });

            // Step 1: explore → get token
            const exploreParams = new URLSearchParams({
                hl: 'en-US', tz: '-480',
                req: JSON.stringify({
                    comparisonItem: [{ keyword: KEYWORD, geo: GEO, time: TIME_STR }],
                    category: 0, property: ''
                })
            });
            const exploreUrl = EXPLORE_URL + '?' + exploreParams.toString();

            let token = null;
            try {
                const raw = await fetchViaProxies(exploreUrl);
                if (!raw) throw new Error('no response from explore');
                const json = JSON.parse(stripPrefix(raw));
                const widget = (json.widgets || []).find(w => w.id === 'TIMESERIES');
                if (!widget?.token) throw new Error('TIMESERIES widget not found');
                token = widget.token;
                console.log('[GTrend] token obtained:', token.slice(0, 20) + '...');
            } catch(e) {
                console.warn('[GTrend] explore step failed:', e?.message?.slice(0, 80));
                return new Array(dates.length).fill(50); // neutral fallback
            }

            // Step 2: widgetdata → get interest over time
            const widgetParams = new URLSearchParams({
                hl: 'en-US', tz: '-480',
                req: JSON.stringify({ time: TIME_STR, resolution: 'WEEK', locale: 'en-US',
                    comparisonItem: [{ geo: { country: GEO }, complexKeywordsRestriction: { keyword: [{ type: 'BROAD', value: KEYWORD }] } }],
                    requestOptions: { property: '', backend: 'IZG', category: 0 }
                }),
                token,
            });
            const widgetUrl = WIDGET_URL + '?' + widgetParams.toString();

            try {
                const raw2 = await fetchViaProxies(widgetUrl);
                if (!raw2) throw new Error('no response from widgetdata');
                const json2 = JSON.parse(stripPrefix(raw2));
                const timeline = json2?.default?.timelineData || [];
                if (timeline.length === 0) throw new Error('empty timeline data');

                const result = alignWeeklyTrends(timeline, dates);
                console.log(`[GTrend] OK: ${timeline.length} weekly pts, latest=${result[result.length-1]}`);
                return result;
            } catch(e) {
                console.warn('[GTrend] widgetdata step failed:', e?.message?.slice(0, 80));
                return new Array(dates.length).fill(50);
            }
        };

                const fetchCOTData = async (onStatus) => {
            // ==========================================
            // 3-tier COT Data Strategy:
            // 1) CFTC Disaggregated Futures API (Managed Money) - fixed URL encoding
            // 2) CFTC Legacy Futures API (Non-Commercial) - fallback
            // 3) CFTC HTML Report (current week only) - last resort
            // ==========================================
            const DISAG_BASE  = 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json';
            const LEGACY_BASE = 'https://publicreporting.cftc.gov/resource/6dca-aqww.json';
            const HTML_URL    = 'https://www.cftc.gov/dea/futures/deacmxsf.htm';

            const CORS_PROXIES = corsProxies();

            const emitStatus = (patch) => {
                try { if (typeof onStatus === 'function') onStatus({ updatedAt: new Date().toISOString(), ...patch }); } catch(_) {}
            };
            const sleep = ms => new Promise(r => setTimeout(r, ms));

            const fetchT = (url, ms = 12000) => {
                const ctrl = new AbortController();
                const tid = setTimeout(() => ctrl.abort(), ms);
                return fetch(url, { signal: ctrl.signal }).finally(() => clearTimeout(tid));
            };

            const fetchJSON = async (url, retries = 2) => {
                for (let i = 0; i <= retries; i++) {
                    try {
                        const r = await fetchT(url);
                        if (!r.ok) throw new Error(`HTTP ${r.status}`);
                        return await r.json();
                    } catch(e) {
                        if (i < retries) await sleep(700 * (i + 1));
                        else throw e;
                    }
                }
            };

            // Build SoQL URL: $where value is encodeURIComponent'd (fixes wildcard % encoding)
            const buildUrl = (base, select, whereExpr, orderExpr, limit) =>
                `${base}?$select=${select}&$where=${encodeURIComponent(whereExpr)}&$order=${encodeURIComponent(orderExpr)}&$limit=${limit}`;

            const parseDisagg = (records) => {
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

            const parseLegacy = (records) => {
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

            const successReturn = (map, source, phase) => {
                const keys = [...map.keys()].sort();
                const latestDate = keys[keys.length - 1];
                const latest = map.get(latestDate);
                emitStatus({ state: 'ok', source, records: map.size, latestDate, latestNet: latest?.net ?? null, phase });
                console.log(`[COT] OK ${source}: ${map.size} records, latest=${latestDate}, net=${latest?.net}`);
                return map;
            };

            emitStatus({ state: 'loading', source: null, records: 0, latestDate: null, latestNet: null, message: null, phase: 'start' });

            const SILVER_WHERES = [
                "upper(market_and_exchange_names) like '%SILVER%COMMODITY%EXCHANGE%INC%'",
                "upper(market_and_exchange_names) like '%SILVER%EXCHANGE%INC%'",
                "upper(market_and_exchange_names) like '%SILVER%'",
            ];

            // ─── Source 1: Disaggregated Futures (Managed Money) ───
            const DISAG_SEL = 'as_of_date_form_yyyy_mm_dd,market_and_exchange_names,m_money_positions_long_all,m_money_positions_short_all,open_interest_all';
            for (let wi = 0; wi < SILVER_WHERES.length; wi++) {
                const base = buildUrl(DISAG_BASE, DISAG_SEL, SILVER_WHERES[wi], 'as_of_date_form_yyyy_mm_dd DESC', 300);
                // 直連與各 proxy 競速，不再逐一等到逾時（最差從 5×14s 降為一次逾時）
                emitStatus({ state: 'loading', phase: `disag_w${wi+1}` });
                const txt = await raceText([base, ...CORS_PROXIES.map(p => p(base))], { timeoutMs: 7000 });
                if (txt) {
                    try {
                        const map = parseDisagg(JSON.parse(txt));
                        if (map.size > 0) return successReturn(map, 'Disagg', 'disag_ok');
                    } catch(e) { console.warn(`[COT] Disagg w${wi+1} 解析失敗:`, e?.message?.slice(0,80)); }
                }
            }

            // ─── Source 2: Legacy Futures (Non-Commercial) ───
            const LEGACY_SEL = 'report_date_as_yyyy_mm_dd,market_and_exchange_names,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all';
            for (let wi = 0; wi < SILVER_WHERES.length; wi++) {
                const base = buildUrl(LEGACY_BASE, LEGACY_SEL, SILVER_WHERES[wi], 'report_date_as_yyyy_mm_dd DESC', 300);
                emitStatus({ state: 'loading', phase: `legacy_w${wi+1}` });
                const txt = await raceText([base, ...CORS_PROXIES.map(p => p(base))], { timeoutMs: 7000 });
                if (txt) {
                    try {
                        const map = parseLegacy(JSON.parse(txt));
                        if (map.size > 0) return successReturn(map, 'Legacy', 'legacy_ok');
                    } catch(e) { console.warn(`[COT] Legacy w${wi+1} 解析失敗:`, e?.message?.slice(0,80)); }
                }
            }

            // ─── Source 3: CFTC HTML Text Report (current week only) ───
            // URL: https://www.cftc.gov/dea/futures/deacmxsf.htm
            // Format: fixed-width text, NON-COMMERCIAL long/short in COMMITMENTS line
            emitStatus({ state: 'loading', phase: 'html', message: 'Parsing CFTC HTML report...' });
            const htmlUrls = [HTML_URL, ...CORS_PROXIES.map(p => p(HTML_URL))];
            for (let ai = 0; ai < 1; ai++) {
                try {
                    const text = await raceText(htmlUrls, { timeoutMs: 7000 });
                    if (!text) continue;
                    const sidx = text.indexOf('SILVER - COMMODITY EXCHANGE INC');
                    if (sidx === -1) { console.warn('[COT] HTML: SILVER section not found'); continue; }

                    const sec = text.substring(sidx, sidx + 2000);

                    // Date: "POSITIONS AS OF MM/DD/YY"
                    const dm = sec.match(/POSITIONS AS OF\s+(\d{2})\/(\d{2})\/(\d{2,4})/);
                    if (!dm) continue;
                    const yr  = dm[3].length === 2 ? (parseInt(dm[3]) < 50 ? '20'+dm[3] : '19'+dm[3]) : dm[3];
                    const dateStr = `${yr}-${dm[1]}-${dm[2]}`;

                    // COMMITMENTS row: NON-COMMERCIAL LONG  SHORT  SPREADS ...
                    const ci = sec.indexOf('COMMITMENTS');
                    if (ci === -1) continue;
                    const lm = sec.substring(ci + 12).match(/^\s*([\d,]+)\s+([\d,]+)/m);
                    if (!lm) continue;

                    const long  = parseInt(lm[1].replace(/,/g,''));
                    const short = parseInt(lm[2].replace(/,/g,''));
                    if (!Number.isFinite(long) || !Number.isFinite(short)) continue;

                    const map = new Map();
                    map.set(dateStr, { net: long - short, long, short, openInterest: null, market: 'SILVER - COMMODITY EXCHANGE INC.' });
                    console.log(`[COT] HTML parsed: date=${dateStr}, net=${long-short}`);
                    emitStatus({ state: 'ok', source: 'HTML Report (current week)', records: 1, latestDate: dateStr, latestNet: long - short, phase: 'html_ok', message: 'Only latest week available; history forward-filled' });
                    return map;

                } catch(e) { console.warn(`[COT] HTML a${ai+1}:`, e?.message?.slice(0,80)); }
            }

            // ─── All failed ───
            console.warn('[COT] All sources failed.');
            emitStatus({ state: 'error', source: null, records: 0, latestDate: null, latestNet: null, message: 'All COT sources failed, using mock data', phase: 'failed_all' });
            return new Map();
        };

        const fetchMarketData = async (cotStatusCb) => { 
            const DATA_RANGE = "5y";
            try { if (typeof cotStatusCb === "function") cotStatusCb({ state: "loading", source: null, records: 0, latestDate: null, latestNet: null, message: null, phase: "marketdata_start", updatedAt: new Date().toISOString() }); } catch (_) {}

            // ══════════════════════════════════════════════════════════
            // 市場數據三層取得策略
            //  Layer 1: Stooq.com CSV  — 無 proxy、無 rate limit、CORS = *
            //           URL: https://stooq.com/q/d/l/?s={symbol}&i=d
            //           回傳: Date,Open,High,Low,Close,Volume (CSV)
            //  Layer 2: Yahoo Finance v8 JSON (query1/query2)
            //           透過 ProxyRotator 輪替，proxy 失敗自動切換
            //  Layer 3: localStorage 快取 —— 只在前兩層都失敗時使用（上次成功抓到的真實資料）
            // ══════════════════════════════════════════════════════════

            // Stooq symbol 對照表
            const STOOQ_MAP = {
                'SI=F':     'si=f',
                'SLV':      'slv.us',
                'GC=F':     'gc=f',
                'CL=F':     'cl=f',
                'DX-Y.NYB': 'dx-y.nyb',
                '^VIX':     '^vix',
                '^TNX':     '^tnx',
                'HG=F':     'hg=f',
            };

            // Stooq CSV 解析 → { timestamps, close, volume }
            const parseStooqCSV = (csv, symbol) => {
                const lines = csv.trim().split('\n');
                if (lines.length < 2) throw new Error('Stooq: empty CSV');
                // header: Date,Open,High,Low,Close,Volume
                const hdr = lines[0].toLowerCase().split(',');
                const di = hdr.indexOf('date');
                const ci = hdr.indexOf('close');
                const vi = hdr.indexOf('volume');
                if (di < 0 || ci < 0) throw new Error('Stooq: missing columns');

                // Stooq returns newest-first → reverse to oldest-first
                const rows = lines.slice(1).reverse();
                const now = Date.now();
                const fiveYearsAgo = now - DATA_RANGE_MS;
                const timestamps = [], close = [], volume = [];
                for (const row of rows) {
                    const cols = row.split(',');
                    if (cols.length < 3) continue;
                    // Date: YYYY-MM-DD
                    const ts = new Date(cols[di] + 'T00:00:00Z').getTime() / 1000;
                    if (!isFinite(ts) || ts * 1000 < fiveYearsAgo) continue;
                    const c = parseFloat(cols[ci]);
                    if (!isFinite(c) || c <= 0) continue;
                    timestamps.push(ts);
                    close.push(c);
                    volume.push(vi >= 0 ? (parseFloat(cols[vi]) || 0) : 0);
                }
                if (timestamps.length < 30) throw new Error(`Stooq: too few rows (${timestamps.length})`);
                console.log(`[Stooq] ${symbol} OK: ${timestamps.length} pts`);
                return { timestamps, close, volume };
            };

            // Layer 1: Stooq direct (no proxy needed)
            const fetchStooq = async (symbol) => {
                const stooqSym = STOOQ_MAP[symbol];
                if (!stooqSym) return null;
                const url = `https://stooq.com/q/d/l/?s=${encodeURIComponent(stooqSym)}&i=d`;
                try {
                    const ctrl = new AbortController();
                    const tid = setTimeout(() => ctrl.abort(), 12000);
                    const resp = await fetch(url, { signal: ctrl.signal });
                    clearTimeout(tid);
                    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                    const csv = await resp.text();
                    return parseStooqCSV(csv, symbol);
                } catch (e) {
                    console.warn(`[Stooq] ${symbol} failed:`, e.message?.slice(0, 80));
                    return null;
                }
            };

            // Layer 2: Yahoo v8 via ProxyRotator
            const fetchYahooViaProxy = async (symbol) => {
                const urls = [
                    `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?range=${DATA_RANGE}&interval=1d`,
                    `https://query2.finance.yahoo.com/v8/finance/chart/${symbol}?range=${DATA_RANGE}&interval=1d`,
                ];
                for (const url of urls) {
                    try {
                        const response = await ProxyRotator.fetchAny(url, { timeoutMs: 15000 });
                        const data = await response.json();
                        if (!data.chart?.result?.length) continue;
                        const result = data.chart.result[0];
                        const ind = result.indicators.quote[0];
                        const ts  = result.timestamp;
                        if (!ind.close || !ts) continue;
                        let vol = ind.volume;
                        if (!vol || vol.length !== ts.length) vol = new Array(ind.close.length).fill(0);
                        else vol = vol.map(v => v === null ? 0 : v);
                        console.log(`[Yahoo] ${symbol} OK via proxy (${ts.length} pts)`);
                        return { timestamps: ts, close: ind.close, volume: vol };
                    } catch (e) {
                        console.warn(`[Yahoo] ${symbol} proxy failed:`, e.message?.slice(0, 80));
                    }
                }
                return null;
            };

            // ── 主要抓取流程：快取 → Stooq → Yahoo proxy ──
            const DATA_RANGE_MS = 5 * 365.25 * 24 * 3600 * 1000;

            // 產生佔位用的 mock 時序數據（當所有外部 API 均失敗時確保程式繼續運作）
            const makeMockSeries = (symbol, n = 1260) => {
                const SEEDS = { 'SI=F':28,'SLV':22,'GC=F':2000,'CL=F':75,'DX-Y.NYB':103,'^VIX':18,'^TNX':4.2,'HG=F':3.8 };
                let v = SEEDS[symbol] ?? 50;
                const now = Date.now() / 1000;
                const DAY = 86400;
                const timestamps = [], close = [], volume = [];
                for (let i = n - 1; i >= 0; i--) {
                    v = Math.max(v * 0.5, v * (1 + (Math.random() - 0.499) * 0.012));
                    timestamps.push(Math.round(now - i * DAY * 1.4));
                    close.push(parseFloat(v.toFixed(4)));
                    volume.push(Math.round(1e6 + Math.random() * 5e6));
                }
                console.warn(`[Mock] Using synthetic series for ${symbol}`);
                return { timestamps, close, volume, isMock: true };
            };

            // Stooq 直連。Stooq 有 CORS 且無流量限制，
            // 因此這一段可以安全平行 —— 先前的逐一 + 150ms 是為了保護會限流的 proxy，
            // 套用在 Stooq 上只是白白多等。成功的結果寫入快取，供之後失敗時備援。
            const fetchSymbolFast = async (symbol) => {
                const cacheKey = `sym_${symbol}`;
                const result = await fetchStooq(symbol);
                if (result) MarketDataCache.set(cacheKey, result);
                return result;
            };

            const yahooSymbols = MARKET_SYMBOLS;

            // 第一輪：八個代碼同時抓，耗時 ≈ 最慢的那一個，而非八個相加
            const t0 = performance.now();
            const yahooResults = await Promise.all(yahooSymbols.map(s => fetchSymbolFast(s)));
            console.log(`[Fetch] Stooq 平行取得 ${yahooResults.filter(Boolean).length}/${yahooSymbols.length} 檔，耗時 ${((performance.now()-t0)/1000).toFixed(1)}s`);

            // 第二輪：只有失敗的才走 proxy，這段仍逐一並保留間隔（proxy 會限流）
            for (let i = 0; i < yahooSymbols.length; i++) {
                if (yahooResults[i]) continue;
                const sym = yahooSymbols[i];
                let r = await fetchYahooViaProxy(sym);
                if (!r) {
                    const stale = MarketDataCache.getData(`sym_${sym}`);
                    if (stale) { console.warn(`[Cache] ${sym} using stale cache`); r = stale; }
                }
                if (r) MarketDataCache.set(`sym_${sym}`, r);
                yahooResults[i] = r;
                await new Promise(res => setTimeout(res, 150));
            }

            // 核心品種（SI=F, SLV, GC=F）若全部失敗則用 mock，其餘也補 mock
            const [silverFuturesData, slvEtfData, goldData, oilData, dxyData, vixData, tnxData, copperData]
                = yahooResults.map((r, i) => r ?? makeMockSeries(yahooSymbols[i]));

            // 四個外部來源彼此獨立 —— 在此同時「發動」，稍後才各自 await。
            // COT 在建立 dates 的迴圈中就要用到，TIPS/GTrend 則需要 dates，
            // 因此兩組的 await 時機不同，但網路請求是重疊進行的。
            const tAux = performance.now();
            const stockTwitsPromise = fetchStockTwitsSentimentReal();
            const cotPromise        = fetchCOTData(cotStatusCb);

            // 這兩個要等 dates 建好才能發，先留著函式參考
            let tipsPromise = null, gtrendPromise = null;

            const [stockTwitsResult, cotDataMap] = await Promise.all([stockTwitsPromise, cotPromise]);

            // 抓不到的品種已由 makeMockSeries 補成隨機漫步。這裡記下是哪些，
            // 訓練前由 checkDataQuality 擋下 —— 用隨機資料訓練出的模型只是在學雜訊。
            const missingReal = yahooSymbols.filter((s, i) =>
                [silverFuturesData, slvEtfData, goldData, oilData, dxyData, vixData, tnxData, copperData][i]?.isMock);
            if (missingReal.length) console.warn('[Market] Using mock for:', missingReal.join(', '));

            // 第一階段：以白銀期貨的交易日為主軸，對齊其他品種（純計算，與雲端訓練共用）
            const base = alignSymbolSeries({
                'SI=F': silverFuturesData, 'SLV': slvEtfData, 'GC=F': goldData, 'CL=F': oilData,
                'DX-Y.NYB': dxyData, '^VIX': vixData, '^TNX': tnxData, 'HG=F': copperData,
            });
            const { dates } = base;

            // dates 已就緒 → TIPS 與 Google Trends 同時發出並一起等待
            tipsPromise   = fetchTIPSYield(dates);       // 🏦 TIPS 5Y 實質利率
            gtrendPromise = fetchGoogleTrends(dates);    // 📈 Google Trends「silver」搜尋熱度
            const fgPromise = fetchFearGreedIndex(dates); // 😨 CNN Fear & Greed（同樣只依賴 dates）

            // 三者都設上限：任何一個來源慢到超過預算就放棄它、用中性備援值，
            // 不讓單一來源卡住整個載入。這些是輔助因子，缺一個不影響核心流程。
            const AUX_BUDGET_MS = 20000;
            const withBudget = (promise, fallback, label) => Promise.race([
                promise,
                new Promise(res => setTimeout(() => {
                    console.warn(`[Fetch] ${label} 超過 ${AUX_BUDGET_MS / 1000}s 預算，改用備援值`);
                    res(fallback);
                }, AUX_BUDGET_MS))
            ]).catch(() => fallback);

            const [tipsYield, gtrend, fearGreedData] = await Promise.all([
                withBudget(tipsPromise,   dates.map(() => 1.5), 'TIPS'),
                withBudget(gtrendPromise, dates.map(() => 50),  'Google Trends'),
                withBudget(fgPromise,
                    { values: dates.map(() => 50), source: 'mock', currentScore: 50, currentRating: 'Neutral' },
                    'Fear & Greed'),
            ]);
            console.log(`[Fetch] 輔助因子完成，耗時 ${((performance.now()-tAux)/1000).toFixed(1)}s`);

            // 第二階段：衍生特徵與資料品質標記（純計算，與雲端訓練共用）
            return deriveFeatures(base, {
                cotDataMap, tipsYield, gtrend, fearGreedData, stockTwitsResult,
                mockSymbols: missingReal,
                dataCacheDate: MarketDataCache.getCacheDate(`sym_SI=F`) || new Date().toLocaleString(),
            });
        };


        const exportModel = async (logsUpdater) => {
            logsUpdater('正在嘗試從瀏覽器儲存區載入最佳模型...');
            try {
                const model = await tf.loadLayersModel(MODEL_URL);
                logsUpdater('模型載入成功。正在準備下載...');
                
                // 使用 TF.js 內建的 save 函式觸發下載
                await model.save(`downloads://${BEST_MODEL_FILENAME}`);
                
                logsUpdater(`模型 (${BEST_MODEL_FILENAME}.json/.bin) 下載成功!`);
                logsUpdater('模型下載成功! 請在瀏覽器彈出的儲存視窗中，**手動選擇此 HTML 檔案所在的資料夾**作為儲存路徑。');

                tf.dispose(model);
            } catch (e) {
                logsUpdater(`下載錯誤: 未找到模型或瀏覽器不支援下載。請確認已訓練並儲存模型。錯誤詳情: ${e.message}`);
                console.error("Model export failed:", e);
            }
        };


        // ==========================================
        // 6. React UI 組件
        // ==========================================
        
        const FactorSelector = React.memo(({ selectedFactors, onChange }) => {
            const factors = [
                { key: 'slvPrices', name: 'SLV ETF 價格', color: 'text-indigo-400' }, 
                { key: 'slvVolume', name: 'SLV ETF 成交量', color: 'text-teal-400' }, 
                { key: 'slvVolumeRoc', name: 'SLV 成交量 ROC', color: 'text-cyan-400' }, 
                { key: 'tipsYield', name: 'TIPS 5Y 實質利率 (%)', color: 'text-orange-400' }, 
                { key: 'rsiPrices', name: '白銀 RSI 指標 (5)', color: 'text-fuchsia-400' }, 
                { key: 'rsiPrices12', name: '白銀 RSI 指標 (12)', color: 'text-pink-400' }, 
                { key: 'vixRoc', name: 'VIX 變動率 (ROC)', color: 'text-yellow-400' }, 
                { key: 'fearGreed',      name: 'CNN Fear & Greed (0-100)',  color: 'text-orange-400' },
                { key: 'socialSentiment',name: 'StockTwits 白銀情緒 (SLV+SI_F)', color: 'text-red-400' },
                { key: 'goldPrices', name: '黃金價格 (GC=F)', color: 'text-purple-400' },
                { key: 'oilPrices', name: '原油價格 (CL=F)', color: 'text-lime-400' },
                { key: 'dxyPrices', name: '美元指數 (DXY)', color: 'text-blue-400' },
                { key: 'vixPrices', name: 'VIX 恐慌指數 (^VIX)', color: 'text-gray-400' },
                { key: 'tnxPrices', name: '10年期利率 (^TNX)', color: 'text-amber-400' }, 
                { key: 'copperPrices', name: '銅期貨價格 (HG=F)', color: 'text-stone-400' }, 
                { key: 'silverInventory', name: '白銀庫存 SLV oz/share×1k', color: 'text-sky-400' },
                { key: 'gtrend',         name: 'Google Trends silver (0-100)', color: 'text-green-400' }, 
                { key: 'goldSilverRatio', name: '黃金/白銀比', color: 'text-violet-400' }, 
                { key: 'cotNetPosition', name: 'COT Managed Money Net', color: 'text-indigo-400' }, 
                // 🎯 動量/趨勢特徵
                { key: 'priceROC',   name: '白銀日報酬率 (ROC)', color: 'text-rose-300' },
                { key: 'ma5Silver',  name: '白銀 MA5',           color: 'text-green-300' },
                { key: 'ma20Silver', name: '白銀 MA20',          color: 'text-green-500' },
                { key: 'maCross',    name: 'MA Cross (MA5-MA20)',color: 'text-yellow-300' },
                { key: 'macdLine',   name: 'MACD 線 (12-26 EMA)',color: 'text-orange-300' },
            ];
            
            return (
                <div className="space-y-3">
                    {factors.map(factor => (
                        <div key={factor.key} className="flex items-center justify-between">
                            <label htmlFor={`factor-${factor.key}`} className={`text-sm ${factor.color} font-medium flex-1`}>
                                {factor.name}
                            </label>
                            <label className="toggle-switch">
                                <input 
                                    id={`factor-${factor.key}`}
                                    type="checkbox" 
                                    checked={selectedFactors[factor.key]}
                                    onChange={() => onChange(factor.key)}
                                />
                                <span className="slider"></span>
                            </label>
                        </div>
                    ))}
                </div>
            );
        });

        const LossChart = React.memo(({ lossHistory, bestLoss }) => {
            const chartRef = useRef(null);

            useEffect(() => {
                if (!chartRef.current || lossHistory.length === 0) return;

                const epochs = Array.from({ length: lossHistory.length }, (_, i) => i + 1);
                const minLoss = lossHistory.length > 0 ? Math.min(...lossHistory) : bestLoss;
                const minIdx = lossHistory.indexOf(minLoss) + 1; 

                const traces = [
                    { x: epochs, y: lossHistory, mode: 'lines', name: 'Training Loss', line: { color: '#8b5cf6', width: 2 }, marker: { size: 2 } },
                    { x: [minIdx], y: [minLoss], mode: 'markers', name: 'Best Loss', marker: { color: '#22c55e', size: 9, symbol: 'diamond' } }
                ];
                
                const layout = {
                    paper_bgcolor: 'rgba(0,0,0,0)',
                    plot_bgcolor: 'rgba(0,0,0,0)',
                    margin: { t: 20, r: 20, b: 35, l: 45 },
                    xaxis: { title: 'Epoch', showgrid: false, tickfont: { color: '#a1a1aa' }, linecolor: '#3f3f46' },
                    yaxis: { title: 'Loss', showgrid: true, gridcolor: '#27272a', zerolinecolor: '#3f3f46', tickfont: { color: '#a1a1aa' } },
                    legend: { x: 0, y: 1.2, orientation: 'h', font: { color: '#e4e4e7' }, bgcolor: 'rgba(0,0,0,0)' },
                    height: 220,
                    hovermode: 'x unified'
                };

                Plotly.newPlot(chartRef.current, traces, layout, { displayModeBar: false, responsive: true });
            }, [lossHistory, bestLoss]);

            return <div ref={chartRef} className="w-full h-[220px]"></div>;
        });

        const ChartComponent = React.memo(({ data, prediction, lookback, futureDays }) => {
            const chartRef = useRef(null);
            
            const commonLayout = {
                paper_bgcolor: 'rgba(0,0,0,0)',
                plot_bgcolor: 'rgba(0,0,0,0)',
                margin: { t: 20, r: 20, b: 80, l: 45 },
                xaxis: { 
                    title: '日期', 
                    showgrid: false, 
                    tickfont: { color: '#a1a1aa' }, 
                    linecolor: '#3f3f46', 
                    rangeslider: { visible: true, bgcolor: 'rgba(255,255,255,0.05)' } 
                },
                yaxis: { title: '價格 ($)', showgrid: true, gridcolor: '#27272a', zerolinecolor: '#3f3f46', tickfont: { color: '#a1a1aa' } },
                legend: { x: 0, y: 1.0, orientation: 'h', font: { color: '#e4e4e7' }, bgcolor: 'rgba(0,0,0,0)' },
                height: 400,
                hovermode: 'x unified'
            };

            useEffect(() => {
                if (!chartRef.current || !data) return;

                const traces = [];

                // 1. 歷史實際價格
                traces.push({
                    x: data.dates,
                    y: data.prices,
                    mode: 'lines',
                    name: '實際價格 (SI=F)',
                    line: { color: '#2dd4bf', width: 2 },
                });

                // 2. 歷史 RSI
                traces.push({
                    x: data.dates,
                    y: data.cotNetPosition, // 使用 cotNetPosition
                    mode: 'lines',
                    name: 'cotNetPosition',
                    line: { color: '#f97316', width: 1, dash: 'dot' },
                    yaxis: 'y2', 
                    visible: 'legendonly' 
                });

                if (prediction && prediction.historyPreds && prediction.historyPreds.length > 0) {
                    const startIdx = data.dates.length - prediction.historyPreds.length;
                    
                    // 3. 歷史擬合預測
                    const historyDates = data.dates.slice(startIdx);
                    traces.push({
                        x: historyDates,
                        y: prediction.historyPreds,
                        mode: 'lines+markers',
                        name: '歷史擬合預測',
                        line: { color: '#fde047', width: 2, dash: 'dash' },
                        marker: { color: '#fde047', size: 4, symbol: 'dot' },
                        type: 'scatter'
                    });

                    const lastDate = new Date(data.dates[data.dates.length-1]);
                    const futureDates = [];
                    for(let i = 1; i <= prediction.futurePrices.length; i++) {
                        const d = new Date(lastDate);
                        d.setDate(d.getDate() + i);
                        futureDates.push(d.toISOString().split('T')[0]);
                    }

                    // 4. 未來預測 (線+點)
                    traces.push({
                        x: [data.dates[data.dates.length - 1], ...futureDates],
                        y: [data.prices[data.prices.length - 1], ...prediction.futurePrices],
                        mode: 'lines+markers',
                        marker: { color: '#8b5cf6', size: 6, symbol: 'circle' },
                        line: { color: '#8b5cf6', width: 2, dash: 'solid' },
                        name: `未來 ${futureDays} 日預測`
                    });
                }

                const layout = {
                    ...commonLayout,
                    yaxis: { ...commonLayout.yaxis, title: '價格 ($)' },
                    yaxis2: { 
                        title: 'RSI 指數', 
                        overlaying: 'y', 
                        side: 'right', 
                        showgrid: false, 
                        range: [0, 100],
                        tickfont: { color: '#a1a1aa' } 
                    },
                    height: 400
                };
                
                Plotly.newPlot(chartRef.current, traces, layout, {displayModeBar: false, responsive: true});
            }, [data, prediction, lookback, futureDays]);

            return <div ref={chartRef} className="w-full h-[400px]"></div>;
        });

        const FactorInfluence = React.memo(({ influenceScores, numFeatures }) => {
            if (!influenceScores) return null;

            const entries = Object.entries(influenceScores).sort(([, a], [, b]) => Math.abs(b) - Math.abs(a));
            const maxAbsImpact = Math.max(...entries.map(([, impact]) => Math.abs(impact))) || 1;

            return (
                <div className="glass-panel p-4 rounded-xl shadow-2xl">
                    <h3 className="text-xl font-bold mb-4 text-yellow-400 flex items-center">
                        <Icon name="zap" className="w-5 h-5 mr-2 text-yellow-400" />
                        因子影響力分析 (T+1日)
                    </h3>
                    <div className="space-y-3">
                        {entries.map(([name, impact]) => {
                            const normalizedImpact = (Math.abs(impact) / maxAbsImpact) * 100;
                            const isPositive = impact >= 0;
                            const barColor = isPositive ? 'bg-emerald-600' : 'bg-rose-600';
                            
                            return (
                                <div key={name} className="space-y-1">
                                    <div className="flex justify-between items-center text-xs">
                                        <span className="text-zinc-400">{name}</span>
                                        <span className={`font-mono font-bold ${isPositive ? 'text-emerald-400' : 'text-rose-400'}`}>
                                            {impact.toFixed(4)}
                                        </span>
                                    </div>
                                    <div className="w-full bg-zinc-700 h-2 rounded-full">
                                        <div 
                                            className={`h-full rounded-full transition-all duration-300 ${barColor}`} 
                                            style={{ 
                                                width: `${normalizedImpact}%`,
                                                marginLeft: isPositive ? 0 : `${100 - normalizedImpact}%` 
                                            }}
                                        ></div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            );
        });
        
        const FactorStatusPanel = React.memo(({ latestData, activeFactors }) => {
            if (!latestData) return null;

            // StockTwits 標籤
            const stSrc = latestData.stSource || 'mock';
            const stBadge = stSrc === 'real' ? '✅ Real' : '⚠️ Mock';
            const stLabel = `StockTwits 白銀情緒 (${stBadge})`;
            const stColor = stSrc === 'real' ? 'text-red-400' : 'text-yellow-500';

            // Fear & Greed 標籤
            const fgSrc = latestData.fearGreedSource || 'mock';
            const fgBadge = fgSrc === 'real' ? '✅ Real' : '⚠️ Mock';
            const fgLabel = `CNN Fear & Greed (${fgBadge})`;
            const fgColor = fgSrc === 'real' ? 'text-orange-400' : 'text-yellow-500';

            const factorList = [
                { key: 'price',          name: '白銀期貨價格',          format: p => p.toFixed(4), color: 'text-teal-400' }, 
                { key: 'slvPrices',      name: 'SLV ETF 收盤價',       format: p => p.toFixed(4), color: 'text-indigo-400' }, 
                { key: 'goldPrices',     name: '黃金價格 (GC=F)',       format: p => p.toFixed(2), color: 'text-purple-400' },
                { key: 'dxyPrices',      name: '美元指數 (DXY)',        format: p => p.toFixed(2), color: 'text-blue-400' },
                { key: 'tnxPrices',      name: '10年期利率 (^TNX)',     format: p => p.toFixed(3), color: 'text-amber-400' },
                { key: 'fearGreed',      name: fgLabel,                 format: p => p.toFixed(1), color: fgColor },
                { key: 'socialSentiment',name: stLabel,                 format: p => p.toFixed(1), color: stColor },
                { key: 'tipsYield',      name: 'TIPS 5Y 實質利率 (%)', format: p => p.toFixed(2), color: 'text-orange-400' }, 
                { key: 'vixPrices',      name: 'VIX 恐慌指數',          format: p => p.toFixed(2), color: 'text-gray-400' },
                { key: 'goldSilverRatio',name: '黃金/白銀比',           format: p => p.toFixed(2), color: 'text-violet-400' }, 
                { key: 'cotNetPosition', name: 'COT Managed Money Net', format: p => p.toFixed(0), color: 'text-indigo-400' }, 
            ].filter(f => f.key === 'price' || activeFactors[f.key]);

            factorList.sort((a, b) => (a.key === 'price' ? -1 : 1));

            // Google Trends 熱度解讀
            const gtrendVal = latestData.gtrend ?? null;
            const gtrendLabel = gtrendVal === null ? null
                : gtrendVal >= 75 ? { text: '熱搜', cls: 'text-red-400 bg-red-900/30 border-red-500/40' }
                : gtrendVal >= 50 ? { text: '升溫', cls: 'text-yellow-400 bg-yellow-900/30 border-yellow-500/40' }
                : gtrendVal >= 25 ? { text: '正常', cls: 'text-zinc-300 bg-zinc-700/40 border-zinc-500/40' }
                :                   { text: '低熱', cls: 'text-zinc-500 bg-zinc-800/40 border-zinc-700/40' };

            return (
                <div className="glass-panel p-4 rounded-xl flex flex-col space-y-4">
                    <h2 className="text-lg font-bold text-white border-b border-zinc-700 pb-2 flex items-center">
                        <Icon name="activity" className="w-5 h-5 mr-2 text-white" />
                        即時因子狀態
                    </h2>
                    <div className="flex justify-between items-center text-sm font-mono p-2 bg-zinc-700 rounded-lg">
                        <span className="font-bold text-zinc-300">數據來源:</span>
                        <span className={latestData.dataSource.includes('Real') ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold animate-pulse-slow'}>
                            {latestData.dataSource}
                        </span>
                    </div>

                    {/* Google Trends 專屬區塊 */}
                    {activeFactors.gtrend && gtrendVal !== null && (
                        <div className="p-3 rounded-xl border bg-zinc-900/50 space-y-2" style={{borderColor:'rgba(34,197,94,0.3)'}}>
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-1.5">
                                    <Icon name="trending-up" className="w-4 h-4 text-green-400" />
                                    <span className="text-sm font-semibold text-green-300">Google Trends「silver」</span>
                                    <span className="text-xs text-zinc-500">週度相對熱度</span>
                                </div>
                                <span className={`text-xs px-2 py-0.5 rounded-full border font-semibold ${gtrendLabel.cls}`}>
                                    {gtrendLabel.text}
                                </span>
                            </div>
                            {/* 進度條視覺化 */}
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-zinc-500 w-4">0</span>
                                <div className="flex-1 h-3 bg-zinc-800 rounded-full overflow-hidden">
                                    <div
                                        className="h-full rounded-full transition-all duration-700"
                                        style={{
                                            width: `${Math.min(100, gtrendVal)}%`,
                                            background: gtrendVal >= 75
                                                ? 'linear-gradient(to right,#f97316,#ef4444)'
                                                : gtrendVal >= 50
                                                    ? 'linear-gradient(to right,#eab308,#f97316)'
                                                    : 'linear-gradient(to right,#22c55e,#16a34a)'
                                        }}
                                    ></div>
                                </div>
                                <span className="text-xs text-zinc-500 w-7">100</span>
                                <span className="font-mono text-base font-bold text-white w-8 text-right">{Math.round(gtrendVal)}</span>
                            </div>
                            <p className="text-xs text-zinc-600">
                                數值 = 該期間最高點的相對比例（100 = 歷史最高搜尋量）
                            </p>
                        </div>
                    )}

                    <div className="flex-1 overflow-y-auto max-h-[400px] pr-2 space-y-3">
                        {factorList.map(factor => (
                            <div key={factor.key} className="flex justify-between items-center pb-2 border-b border-zinc-800">
                                <span className={`text-sm font-medium ${factor.color}`}>{factor.name}</span>
                                <span className="font-mono text-lg text-white font-semibold">
                                    {factor.format(latestData[factor.key] ?? 0)}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            );
        });

        // COT 資料來源狀態
        const CotStatusPanel = React.memo(({ cotStatus }) => {
            const [showCotDebug, setShowCotDebug] = useState(false);
            return (
                <>
                {/* COT 資料狀態 */}
                <div className="glass-panel p-4 rounded-xl shadow-accent-900/20 flex flex-col space-y-3">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-bold text-accent flex items-center">
                            <Icon name="activity" className="w-5 h-5 mr-2" />
                            COT 淨部位資料狀態
                        </h2>
                        <span className={`text-xs px-2 py-1 rounded-full border ${
                            cotStatus.state === "ok" ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10" :
                            cotStatus.state === "loading" ? "border-amber-500/40 text-amber-300 bg-amber-500/10" :
                            cotStatus.state === "error" ? "border-red-500/40 text-red-300 bg-red-500/10" :
                            "border-zinc-600 text-zinc-300 bg-zinc-800/30"
                        }`}>
                            {cotStatus.state === "ok" ? "已連線" : cotStatus.state === "loading" ? "載入中" : cotStatus.state === "error" ? "失敗" : "未啟動"}
                        </span>
                    </div>

                    <div className="grid grid-cols-2 gap-3 text-sm">
                        <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                            <div className="text-xs text-zinc-400 mb-1">來源</div>
                            <div className="font-semibold text-white">{cotStatus.source || "-"}</div>
                        </div>
                        <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                            <div className="text-xs text-zinc-400 mb-1">筆數</div>
                            <div className="font-semibold text-white">{Number.isFinite(cotStatus.records) ? cotStatus.records : 0}</div>
                        </div>
                        <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                            <div className="text-xs text-zinc-400 mb-1">最新報告日</div>
                            <div className="font-semibold text-white">{cotStatus.latestDate || "-"}</div>
                        </div>
                        <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                            <div className="text-xs text-zinc-400 mb-1">最新淨部位</div>
                            <div className="font-semibold text-white">
                                {Number.isFinite(cotStatus.latestNet) ? cotStatus.latestNet.toLocaleString() : "-"}
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center justify-between text-xs text-zinc-400">
                        <div>
                            <span className="mr-2">phase:</span>
                            <span className="text-zinc-300">{cotStatus.phase || "-"}</span>
                        </div>
                        <div className="text-zinc-500">
                            {cotStatus.updatedAt ? new Date(cotStatus.updatedAt).toLocaleString() : ""}
                        </div>
                    </div>

                    {cotStatus.state === "error" && (
                        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-200 text-sm">
                            {cotStatus.message || "COT 來源連線失敗。"}
                            <div className="mt-2">
                                <button
                                    onClick={() => setShowCotDebug(v => !v)}
                                    className="text-xs underline text-red-200/90 hover:text-red-100"
                                >
                                    {showCotDebug ? "隱藏" : "顯示"} 偵錯提示
                                </button>
                            </div>
                            {showCotDebug && (
                                <div className="mt-2 text-xs text-red-100/90 space-y-1">
                                    <div>1) 請打開瀏覽器 DevTools Console 查看 [COT] 訊息。</div>
                                    <div>2) 若公司網路擋 Socrata，程式會自動改走 proxy；仍失敗請更換網路。</div>
                                    <div>3) 若你改過查詢條件，請確認日期欄位是 as_of_date_form_yyyy_mm_dd。</div>
                                </div>
                            )}
                        </div>
                    )}
                </div>
                </>
            );
        });

        // 自架 CORS 代理設定（狀態只在此面板內使用）
        const ProxySettingsPanel = React.memo(({ logsUpdater }) => {
            const [customProxyInput, setCustomProxyInput] = useState(getCustomProxy);
            const [customProxySaved, setCustomProxySaved] = useState(getCustomProxy);
            return (
                <>
                {/* 🌐 資料代理設定 */}
                <div className="glass-panel p-4 rounded-xl flex flex-col space-y-3">
                    <h2 className="text-lg font-bold text-sky-300 flex items-center">
                        <Icon name="globe" className="w-5 h-5 mr-2" />
                        資料代理（CORS Proxy）
                    </h2>
                    <div className="text-xs text-zinc-400 leading-relaxed">
                        瀏覽器無法直接讀取部分資料源（Yahoo、CFTC、Treasury 等），需經過代理。
                        公共代理常限流或停止服務；建議自架（見 <span className="font-mono">cors-proxy-worker.js</span>），
                        在此填入網址前綴後會優先使用，公共代理作為備援。
                    </div>
                    <input
                        type="url"
                        value={customProxyInput}
                        onChange={e => setCustomProxyInput(e.target.value)}
                        placeholder="https://silver-proxy.你的帳號.workers.dev/?url="
                        className="w-full px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-sm font-mono text-zinc-200"
                    />
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => {
                                const v = customProxyInput.trim();
                                if (v && !/^https:\/\//i.test(v)) {
                                    logsUpdater('⚠️ 代理網址必須以 https:// 開頭。');
                                    return;
                                }
                                try {
                                    if (v) localStorage.setItem(CUSTOM_PROXY_KEY, v);
                                    else localStorage.removeItem(CUSTOM_PROXY_KEY);
                                } catch (_) {}
                                setCustomProxySaved(v);
                                logsUpdater(v ? `已設定自架代理：${v}（下次載入資料生效）` : '已清除自架代理，只使用公共代理。');
                            }}
                            disabled={customProxyInput.trim() === customProxySaved}
                            className="px-3 py-1 rounded bg-sky-700/60 hover:bg-sky-600/60 text-white text-xs transition disabled:opacity-40"
                        >
                            儲存
                        </button>
                        <span className="text-xs text-zinc-500">
                            {customProxySaved ? '目前：自架代理優先' : '目前：僅公共代理'}
                        </span>
                    </div>
                </div>
                </>
            );
        });

        // StockTwits 情緒來源狀態
        const StockTwitsPanel = React.memo(({ stockTwitsStatus, loadData, isLoadingData }) => {
            return (
                <>
                {/* 📊 StockTwits 情緒狀態 */}
                <div className="glass-panel p-4 rounded-xl shadow-accent-900/20 flex flex-col space-y-3">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-bold text-red-400 flex items-center">
                            <Icon name="users" className="w-5 h-5 mr-2" />
                            StockTwits 白銀情緒
                        </h2>
                        <span className={`text-xs px-2 py-1 rounded-full border ${
                            stockTwitsStatus.source === 'real' ? 'border-emerald-500/40 text-emerald-300 bg-emerald-500/10' :
                            stockTwitsStatus.source === 'idle' ? 'border-zinc-600 text-zinc-300 bg-zinc-800/30' :
                                                                  'border-yellow-500/40 text-yellow-300 bg-yellow-500/10'
                        }`}>
                            {stockTwitsStatus.source === 'real' ? '✅ Real' : stockTwitsStatus.source === 'idle' ? '未啟動' : '⚠️ Mock'}
                        </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-sm">
                        <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-center">
                            <div className="text-xs text-zinc-400">多方 %</div>
                            <div className="text-lg font-bold text-emerald-400">{stockTwitsStatus.bullishPct !== null ? stockTwitsStatus.bullishPct.toFixed(1) : '—'}%</div>
                        </div>
                        <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-center">
                            <div className="text-xs text-zinc-400">空方 %</div>
                            <div className="text-lg font-bold text-rose-400">{stockTwitsStatus.bearishPct !== null ? stockTwitsStatus.bearishPct.toFixed(1) : '—'}%</div>
                        </div>
                        <div className="p-2 rounded-lg bg-zinc-900/40 border border-zinc-800 text-center">
                            <div className="text-xs text-zinc-400">標記則數</div>
                            <div className="text-lg font-bold text-white">{stockTwitsStatus.totalTagged ?? '—'}</div>
                        </div>
                    </div>
                    <div className="text-xs text-zinc-500 leading-relaxed">
                        {stockTwitsStatus.source === 'real'
                            ? '🟢 即時抓取 StockTwits SLV + SI_F 訊息流，統計已標記情緒訊息之多空比例。'
                            : stockTwitsStatus.source === 'idle' ? '⬜ 尚未載入。'
                            : '🟡 StockTwits API 不可用，使用價格動量代理情緒。'}
                    </div>
                    {stockTwitsStatus.source === 'mock' && (
                        <button onClick={() => loadData(false)} disabled={isLoadingData}
                            className="px-3 py-1 rounded bg-red-700/60 hover:bg-red-600/60 text-white text-xs transition w-fit">
                            {isLoadingData ? '載入中...' : '重試'}
                        </button>
                    )}
                    <div className="text-xs text-zinc-600">{stockTwitsStatus.updatedAt ? new Date(stockTwitsStatus.updatedAt).toLocaleString() : ''}</div>
                </div>
                </>
            );
        });

        // CNN Fear & Greed 來源狀態
        const FearGreedPanel = React.memo(({ fearGreedStatus, loadData, isLoadingData }) => {
            return (
                <>
                {/* 😨 CNN Fear & Greed 狀態 */}
                <div className="glass-panel p-4 rounded-xl shadow-accent-900/20 flex flex-col space-y-3">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-bold text-orange-400 flex items-center">
                            <Icon name="gauge" className="w-5 h-5 mr-2" />
                            CNN Fear & Greed Index
                        </h2>
                        <span className={`text-xs px-2 py-1 rounded-full border ${
                            fearGreedStatus.source === 'real' ? 'border-emerald-500/40 text-emerald-300 bg-emerald-500/10' :
                            fearGreedStatus.source === 'idle' ? 'border-zinc-600 text-zinc-300 bg-zinc-800/30' :
                                                                 'border-yellow-500/40 text-yellow-300 bg-yellow-500/10'
                        }`}>
                            {fearGreedStatus.source === 'real' ? '✅ Real' : fearGreedStatus.source === 'idle' ? '未啟動' : '⚠️ Mock'}
                        </span>
                    </div>
                    {/* 儀表指針視覺化 */}
                    {fearGreedStatus.currentScore !== null && (
                        <div className="space-y-2">
                            <div className="flex justify-between text-xs text-zinc-400">
                                <span>極度恐慌</span><span>恐懼</span><span>中立</span><span>貪婪</span><span>極度貪婪</span>
                            </div>
                            <div className="relative h-3 rounded-full overflow-hidden"
                                 style={{background: 'linear-gradient(to right, #ef4444, #f97316, #eab308, #22c55e, #16a34a)'}}>
                                <div className="absolute top-0 w-2 h-full bg-white rounded-full shadow-lg transition-all duration-500"
                                     style={{left: `calc(${Math.min(100, Math.max(0, fearGreedStatus.currentScore))}% - 4px)`}}></div>
                            </div>
                            <div className="flex justify-between items-center">
                                <span className="text-2xl font-bold font-mono text-white">{fearGreedStatus.currentScore?.toFixed(1)}</span>
                                <span className={`text-sm font-semibold px-2 py-1 rounded ${
                                    fearGreedStatus.currentScore >= 75 ? 'bg-green-700 text-white' :
                                    fearGreedStatus.currentScore >= 55 ? 'bg-green-800/60 text-green-300' :
                                    fearGreedStatus.currentScore >= 45 ? 'bg-zinc-700 text-zinc-300' :
                                    fearGreedStatus.currentScore >= 25 ? 'bg-red-800/60 text-red-300' :
                                    'bg-red-700 text-white'
                                }`}>{fearGreedStatus.currentRating}</span>
                            </div>
                        </div>
                    )}
                    <div className="text-xs text-zinc-500 leading-relaxed">
                        {fearGreedStatus.source === 'real'
                            ? '🟢 即時抓取 CNN Business DataViz API 含多年歷史數據，0=極度恐慌，100=極度貪婪。'
                            : fearGreedStatus.source === 'idle' ? '⬜ 尚未載入。'
                            : '🟡 CNN API 不可用，使用低變異模擬值代替。'}
                    </div>
                    {fearGreedStatus.source === 'mock' && (
                        <button onClick={() => loadData(false)} disabled={isLoadingData}
                            className="px-3 py-1 rounded bg-orange-700/60 hover:bg-orange-600/60 text-white text-xs transition w-fit">
                            {isLoadingData ? '載入中...' : '重試'}
                        </button>
                    )}
                    <div className="text-xs text-zinc-600">{fearGreedStatus.updatedAt ? new Date(fearGreedStatus.updatedAt).toLocaleString() : ''}</div>
                </div>
                </>
            );
        });

        // 回測設定與結果（權益曲線由 App 的 effect 以 Plotly 繪到 #backtestEquityChart）
        const BacktestPanel = React.memo(({ backtestConfig, setBacktestConfig, backtestResult, runBacktest }) => {
            return (
                <>
                {/* 回測模擬 (Backtest) */}
                <div className="glass-panel p-4 rounded-xl shadow-accent-900/20 flex flex-col space-y-4">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-bold text-accent flex items-center">
                            <Icon name="trending-up" className="w-5 h-5 mr-2" />
                            回測模擬 (Backtest)
                        </h2>
                        <span className="text-xs px-2 py-1 rounded-full border border-zinc-600 text-zinc-300 bg-zinc-800/30">
                            使用模型逐筆預測 1-step
                        </span>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <div>
                            <label className="text-xs text-zinc-400">測試區間比例 (最後 N%)</label>
                            <input
                                type="number"
                                step="0.05"
                                min="0.05"
                                max="0.8"
                                value={backtestConfig.testRatio}
                                onChange={(e)=>setBacktestConfig(p=>({...p, testRatio: e.target.value}))}
                                className="w-full mt-1 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-zinc-400">初始資金</label>
                            <input
                                type="number"
                                step="1000"
                                value={backtestConfig.initialCapital}
                                onChange={(e)=>setBacktestConfig(p=>({...p, initialCapital: e.target.value}))}
                                className="w-full mt-1 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-zinc-400">槓桿</label>
                            <input
                                type="number"
                                step="0.5"
                                min="0"
                                value={backtestConfig.leverage}
                                onChange={(e)=>setBacktestConfig(p=>({...p, leverage: e.target.value}))}
                                className="w-full mt-1 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-zinc-400">每次使用資金比例</label>
                            <input
                                type="number"
                                step="0.1"
                                min="0"
                                max="1"
                                value={backtestConfig.positionPct}
                                onChange={(e)=>setBacktestConfig(p=>({...p, positionPct: e.target.value}))}
                                className="w-full mt-1 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100"
                            />
                        </div>

                        <div>
                            <label className="text-xs text-zinc-400">單邊手續費 (bps)</label>
                            <input
                                type="number"
                                step="1"
                                min="0"
                                value={backtestConfig.feeBps}
                                onChange={(e)=>setBacktestConfig(p=>({...p, feeBps: e.target.value}))}
                                className="w-full mt-1 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-zinc-400">單邊滑價 (bps)</label>
                            <input
                                type="number"
                                step="1"
                                min="0"
                                value={backtestConfig.slippageBps}
                                onChange={(e)=>setBacktestConfig(p=>({...p, slippageBps: e.target.value}))}
                                className="w-full mt-1 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-zinc-400">進場門檻 (預測報酬率)</label>
                            <input
                                type="number"
                                step="0.001"
                                min="0"
                                value={backtestConfig.threshold}
                                onChange={(e)=>setBacktestConfig(p=>({...p, threshold: e.target.value}))}
                                className="w-full mt-1 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div className="flex items-end">
                            <label className="inline-flex items-center gap-2 text-sm text-zinc-200 select-none">
                                <input
                                    type="checkbox"
                                    checked={!!backtestConfig.allowShort}
                                    onChange={(e)=>setBacktestConfig(p=>({...p, allowShort: e.target.checked}))}
                                />
                                允許放空
                            </label>
                        </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={runBacktest}
                            className="px-4 py-2 rounded-lg bg-accent/20 border border-accent/40 text-accent hover:bg-accent/30 transition"
                        >
                            執行回測
                        </button>
                        <div className="text-xs text-zinc-400 flex items-center">
                            ※ 需先完成「訓練/載入模型」，回測採用測試區間逐筆 1-step 預測與交易模擬
                        </div>
                    </div>

                    {backtestResult && (
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                            <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                                <div className="text-xs text-zinc-400">總報酬</div>
                                <div className="text-lg font-bold text-white">{(backtestResult.totalReturn*100).toFixed(2)}%</div>
                            </div>
                            <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                                <div className="text-xs text-zinc-400">期末資產</div>
                                <div className="text-lg font-bold text-white">{backtestResult.finalEquity.toFixed(2)}</div>
                            </div>
                            <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                                <div className="text-xs text-zinc-400">最大回撤</div>
                                <div className="text-lg font-bold text-white">{(backtestResult.maxDD*100).toFixed(2)}%</div>
                            </div>
                            <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                                <div className="text-xs text-zinc-400">Sharpe</div>
                                <div className="text-lg font-bold text-white">{backtestResult.sharpe.toFixed(2)}</div>
                            </div>
                            <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800">
                                <div className="text-xs text-zinc-400">交易/勝率</div>
                                <div className="text-lg font-bold text-white">{backtestResult.tradeCount} / {(backtestResult.winRate*100).toFixed(1)}%</div>
                            </div>
                        </div>
                    )}

                    <div className="rounded-xl overflow-hidden border border-zinc-800 bg-zinc-950/30 p-2">
                        <div id="backtestEquityChart" style={{width:"100%", height:"320px"}}></div>
                    </div>
                </div>
                </>
            );
        });

        // 代理記憶：冠軍模型、實測戰績、晉升歷程
        const AgentMemoryPanel = React.memo(({ agentModels, agentRuns, agentPerf, setAgentTick, logsUpdater }) => {
            const [agentOpen, setAgentOpen]   = useState(false);
            return (
                <>
                {/* ══ 代理記憶：冠軍模型、實測戰績、晉升歷程 ══ */}
                <div className="glass-panel mb-4">
                    <button onClick={() => setAgentOpen(o => !o)}
                        className="w-full flex items-center gap-2 px-5 py-3 text-left">
                        <Icon name="brain-circuit" className="w-5 h-5" style={{color:'var(--metal-dim)'}} />
                        <span className="font-semibold text-base" style={{color:'var(--metal-bright)'}}>代理記憶</span>
                        {(() => {
                            const champ = agentModels.find(m => m.status === 'champion');
                            return champ ? (
                                <span className="text-xs px-2 py-0.5 rounded-full"
                                      style={{background:'var(--up-soft)', color:'var(--up)', border:'1px solid rgba(63,185,80,0.3)'}}>
                                    冠軍 {champ.algorithm} · 保留集 DA {Number.isFinite(champ.holdoutDA) ? champ.holdoutDA.toFixed(1) : '—'}%
                                </span>
                            ) : (
                                <span className="text-xs" style={{color:'var(--metal-faint)'}}>尚無冠軍模型</span>
                            );
                        })()}
                        <Icon name={agentOpen ? 'chevron-up' : 'chevron-down'} className="w-4 h-4 ml-auto" style={{color:'var(--metal-dim)'}} />
                    </button>

                    {agentOpen && (
                        <div className="border-t px-5 py-4 space-y-5" style={{borderColor:'var(--edge)'}}>

                            {/* 實測戰績 —— 訓練指標是回看的，這裡才是真實紀錄 */}
                            <div>
                                <div className="flex items-baseline justify-between mb-2">
                                    <span className="text-sm font-semibold" style={{color:'var(--metal-bright)'}}>實測戰績</span>
                                    <span className="text-xs" style={{color:'var(--metal-faint)'}}>過去預測 vs 實際收盤</span>
                                </div>
                                {agentPerf.n === 0 ? (
                                    <p className="text-xs" style={{color:'var(--metal-faint)'}}>
                                        尚無已到期的預測可對帳。每次預測都會記錄下來，等目標日收盤後自動比對。
                                    </p>
                                ) : (
                                    <div className="flex gap-6">
                                        <div>
                                            <div className="text-xs" style={{color:'var(--metal-faint)'}}>方向命中率</div>
                                            <div className="tabular text-xl font-semibold"
                                                 style={{color: agentPerf.da >= 55 ? 'var(--up)' : agentPerf.da < 50 ? 'var(--down)' : 'var(--metal-bright)'}}>
                                                {agentPerf.da.toFixed(1)}%
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-xs" style={{color:'var(--metal-faint)'}}>平均誤差</div>
                                            <div className="tabular text-xl font-semibold" style={{color:'var(--metal-bright)'}}>
                                                {Number.isFinite(agentPerf.mape) ? agentPerf.mape.toFixed(2) + '%' : '—'}
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-xs" style={{color:'var(--metal-faint)'}}>樣本</div>
                                            <div className="tabular text-xl font-semibold" style={{color:'var(--metal)'}}>{agentPerf.n}</div>
                                        </div>
                                    </div>
                                )}
                                {(() => {
                                    const rec = shouldRetrain(agentPerf);
                                    return (
                                        <p className="text-xs mt-2" style={{color: rec.retrain ? 'var(--warn)' : 'var(--metal-faint)'}}>
                                            {rec.retrain ? '⚠️ ' : ''}{rec.reason}
                                        </p>
                                    );
                                })()}
                            </div>

                            {/* 模型註冊表 */}
                            <div>
                                <div className="text-sm font-semibold mb-2" style={{color:'var(--metal-bright)'}}>模型註冊表</div>
                                {agentModels.length === 0 ? (
                                    <p className="text-xs" style={{color:'var(--metal-faint)'}}>還沒有訓練紀錄。</p>
                                ) : (
                                    <div className="overflow-x-auto">
                                        <table className="w-full text-xs">
                                            <thead>
                                                <tr style={{color:'var(--metal-faint)'}}>
                                                    <th className="text-left font-normal pb-1.5">時間</th>
                                                    <th className="text-left font-normal pb-1.5">演算法</th>
                                                    <th className="text-right font-normal pb-1.5">保留集 DA</th>
                                                    <th className="text-right font-normal pb-1.5">保留集誤差</th>
                                                    <th className="text-right font-normal pb-1.5 pl-3">狀態</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                            {agentModels.slice(0, 10).map((m) => (
                                                <tr key={m.id} className="border-t" style={{borderColor:'var(--edge)'}}>
                                                    <td className="py-1.5 tabular" style={{color:'var(--metal-faint)'}}>
                                                        {new Date(m.ts).toLocaleDateString()} {new Date(m.ts).toLocaleTimeString().slice(0,5)}
                                                    </td>
                                                    <td className="py-1.5" style={{color:'var(--metal)'}}>{m.algorithm}</td>
                                                    <td className="py-1.5 text-right tabular" style={{color:'var(--metal-bright)'}}>
                                                        {Number.isFinite(m.holdoutDA) ? m.holdoutDA.toFixed(2) + '%' : '—'}
                                                    </td>
                                                    <td className="py-1.5 text-right tabular" style={{color:'var(--metal)'}}>
                                                        {Number.isFinite(m.holdoutMAPE) ? m.holdoutMAPE.toFixed(2) + '%' : '—'}
                                                    </td>
                                                    <td className="py-1.5 text-right pl-3">
                                                        <span className="px-1.5 py-0.5 rounded" style={
                                                            m.status === 'champion' ? {background:'var(--up-soft)', color:'var(--up)'}
                                                            : m.status === 'retired' ? {color:'var(--metal-faint)'}
                                                            : {color:'var(--metal-dim)'}
                                                        }>
                                                            {m.status === 'champion' ? '冠軍' : m.status === 'retired' ? '退役' : '挑戰者'}
                                                        </span>
                                                    </td>
                                                </tr>
                                            ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>

                            {/* 執行日誌 */}
                            <div>
                                <div className="text-sm font-semibold mb-2" style={{color:'var(--metal-bright)'}}>晉升歷程</div>
                                {agentRuns.length === 0 ? (
                                    <p className="text-xs" style={{color:'var(--metal-faint)'}}>尚無執行紀錄。</p>
                                ) : (
                                    <ul className="space-y-1.5">
                                        {agentRuns.slice(0, 6).map(r => (
                                            <li key={r.id} className="text-xs flex gap-2">
                                                <span className="tabular shrink-0" style={{color:'var(--metal-faint)'}}>
                                                    {new Date(r.ts).toLocaleDateString()}
                                                </span>
                                                <span style={{color: r.promoted ? 'var(--up)' : 'var(--metal-dim)'}}>
                                                    {r.promoted ? '🏆' : '🛡️'} {r.note}
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>

                            <button onClick={async () => {
                                if (!confirm('確定清除所有代理記憶？模型註冊表、預測日誌與戰績都會消失，且無法復原。')) return;
                                try {
                                    await AgentStore.clear('models');
                                    await AgentStore.clear('runs');
                                    await AgentStore.clear('predictions');
                                    try { await tf.io.removeModel(CHAMPION_URL); } catch(_) {}
                                    setAgentTick(t => t + 1);
                                    logsUpdater('代理記憶已清除。');
                                } catch (e) { logsUpdater('清除失敗: ' + e?.message); }
                            }}
                                className="text-xs px-3 py-1.5 rounded-lg transition-colors"
                                style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal-dim)'}}>
                                清除代理記憶
                            </button>
                        </div>
                    )}
                </div>
                </>
            );
        });

        // 模型與訓練參數設定
        const ModelParamsPanel = React.memo(({ params, handleParamChange }) => {
            return (
                <>
                {/* 模型參數設定 */}
                <div className="glass-panel p-4 rounded-xl flex flex-col space-y-4">
                    <h2 className="text-lg font-bold text-accent border-b border-zinc-700 pb-2 flex items-center">
                        <Icon name="sliders" className="w-5 h-5 mr-2" />
                        模型與訓練參數
                    </h2>

                    {/* Algorithm Selector */}
                    <div className="flex flex-wrap gap-2">
                        <button onClick={() => handleParamChange('algorithm', 'BiGRU')} className={`flex-1 px-3 py-2 text-sm font-semibold rounded-lg transition-colors ${params.algorithm === 'BiGRU' ? 'bg-accent-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>Bi-GRU</button>
                        <button onClick={() => handleParamChange('algorithm', 'LSTM')} className={`flex-1 px-3 py-2 text-sm font-semibold rounded-lg transition-colors ${params.algorithm === 'LSTM' ? 'bg-accent-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>LSTM</button>
                        <button onClick={() => handleParamChange('algorithm', 'Transformer')} className={`flex-1 px-3 py-2 text-sm font-semibold rounded-lg transition-colors ${params.algorithm === 'Transformer' ? 'bg-amber-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>Transformer 🎯</button>
                        <button onClick={() => handleParamChange('algorithm', 'TimesFM')} className={`flex-1 px-3 py-2 text-sm font-semibold rounded-lg transition-colors ${params.algorithm === 'TimesFM' ? 'bg-sky-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>TimesFM</button>
                    </div>

                    {params.algorithm === 'TimesFM' && (
                        <p className="text-xs mt-2 px-2 py-1.5 rounded-lg" style={{color:'var(--metal-dim)', background:'var(--surface-2)', border:'1px solid var(--edge)'}}>
                            採用 TimesFM 的<strong style={{color:'var(--metal-bright)'}}>架構</strong>（patch 切分 + 因果注意力的 decoder-only transformer），
                            以你的資料從頭訓練。這不是 Google 發布的預訓練基礎模型權重
                            （200M/500M 參數、僅有 PyTorch/JAX 版本，無法在瀏覽器執行），因此沒有 zero-shot 能力。
                        </p>
                    )}

                    {/* Prediction Target Selector */}
                    <div className="space-y-1 pt-2 border-t border-zinc-800">
                        <label className="text-xs text-zinc-400 block mb-1 flex items-center gap-1">
                            <Icon name="target" className="w-3 h-3" />預測目標
                        </label>
                        <div className="flex flex-wrap gap-2">
                            <button onClick={() => handleParamChange('predTarget', 'price')} className={`flex-1 px-2 py-2 text-xs font-semibold rounded-lg transition-colors ${(params.predTarget||'price') === 'price' ? 'bg-teal-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>價格迴歸</button>
                            <button onClick={() => handleParamChange('predTarget', 'direction')} className={`flex-1 px-2 py-2 text-xs font-semibold rounded-lg transition-colors ${params.predTarget === 'direction' ? 'bg-rose-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>方向分類 🎯</button>
                            <button onClick={() => handleParamChange('predTarget', 'dual')} className={`flex-1 px-2 py-2 text-xs font-semibold rounded-lg transition-colors ${params.predTarget === 'dual' ? 'bg-violet-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>雙頭 (價格+方向)</button>
                            <button onClick={() => handleParamChange('predTarget', 'logreturn')} className={`flex-1 px-2 py-2 text-xs font-semibold rounded-lg transition-colors ${params.predTarget === 'logreturn' ? 'bg-emerald-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>對數報酬率 📈</button>
                        </div>
                        <p className="text-xs text-zinc-500 mt-1">
                            {(params.predTarget||'price') === 'price' && '迴歸預測下一日價格，方向由價格變化推導。'}
                            {params.predTarget === 'direction' && '直接預測「漲/跌」二元分類，最佳化方向準確率 (DA)。輸出為上漲機率，不提供價格數值。'}
                            {params.predTarget === 'dual' && '同時輸出價格與方向機率：價格供圖表/MAPE，方向頭專注提升 DA。'}
                            {params.predTarget === 'logreturn' && '預測 ln(今日/昨日) 而非價格本身。報酬率接近平穩，模型無法靠「明天≈今天」取巧；且絕對誤差近似相對誤差，與 MAPE 指標對齊。用純 Huber，不加方向懲罰。'}
                        </p>
                    </div>

                    {/* Cross-Validation Mode */}
                    <div className="space-y-1 pt-2 border-t border-zinc-800">
                        <label className="text-xs text-zinc-400 block mb-1">驗證模式</label>
                        <div className="flex justify-around space-x-2">
                            <button onClick={() => handleParamChange('validationMode', VALIDATION_MODE.NONE)} className={`flex-1 px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors ${params.validationMode === VALIDATION_MODE.NONE ? 'bg-indigo-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>無 (標準訓練)</button>
                            <button onClick={() => handleParamChange('validationMode', VALIDATION_MODE.K_FOLD)} className={`flex-1 px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors ${params.validationMode === VALIDATION_MODE.K_FOLD ? 'bg-indigo-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}>K-Fold 驗證</button>
                        </div>
                    </div>

                    {params.validationMode === VALIDATION_MODE.K_FOLD && (
                        <div className="space-y-1">
                            <div className="flex justify-between text-xs text-zinc-400">
                                <span>K-Fold 數量 (K)</span>
                                <span className="font-mono text-cyan-400 font-bold">{params.kFolds}</span>
                            </div>
                            <input type="range" min="2" max="10" value={params.kFolds} step="1" onChange={e => handleParamChange('kFolds', parseInt(e.target.value))} className="custom-range w-full" />
                        </div>
                    )}

                    {/* Loss Lambda / Directional Weight */}
                    <div className="space-y-1 pt-2 border-t border-zinc-800">
                        <div className="flex justify-between text-xs text-zinc-400">
                            <span>🎯 方向懲罰權重 (α) — 0=純Huber，0.3推薦</span>
                            <span className="font-mono text-pink-400 font-bold">{params.directionalLambda.toFixed(1)}</span>
                        </div>
                        <input type="range" min="0.0" max="1.0" value={params.directionalLambda} step="0.1" onChange={e => handleParamChange('directionalLambda', parseFloat(e.target.value))} className="custom-range w-full" />
                        <p className="text-xs text-zinc-600">α 越高模型越重視方向正確性，α 越低越精準預測價格水準</p>
                    </div>

                    {/* Lookback */}
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-zinc-400">
                            <span>Lookback (天數)</span>
                            <span className="font-mono text-accent font-bold">{params.lookback}</span>
                        </div>
                        <input type="range" min="5" max="120" value={params.lookback} step="1" onChange={e => handleParamChange('lookback', parseInt(e.target.value))} className="custom-range w-full" />
                    </div>

                    {/* Layers */}
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-zinc-400">
                            <span>單層單元數 (Units/Filters)</span>
                            <span className="font-mono text-accent font-bold">{params.layers}</span>
                        </div>
                        <input type="range" min="16" max="128" value={params.layers} step="16" onChange={e => handleParamChange('layers', parseInt(e.target.value))} className="custom-range w-full" />
                    </div>

                    {/* Epochs */}
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-zinc-400">
                            <span>訓練週期 (Epochs)</span>
                            <span className="font-mono text-accent font-bold">{params.epochs}</span>
                        </div>
                        <input type="range" min="10" max="1000" value={params.epochs} step="10" onChange={e => handleParamChange('epochs', parseInt(e.target.value))} className="custom-range w-full" />
                    </div>

                    {/* Batch Size */}
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-zinc-400">
                            <span>Batch Size</span>
                            <span className="font-mono text-accent font-bold">{params.batchSize}</span>
                        </div>
                        <input type="range" min="8" max="128" value={params.batchSize} step="8" onChange={e => handleParamChange('batchSize', parseInt(e.target.value))} className="custom-range w-full" />
                    </div>

                    {/* Future Days */}
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-zinc-400">
                            <span>預測天數 (T+N)</span>
                            <span className="font-mono text-accent font-bold">{params.futureDays}</span>
                        </div>
                        <input type="range" min="1" max="10" value={params.futureDays} step="1" onChange={e => handleParamChange('futureDays', parseInt(e.target.value))} className="custom-range w-full" />
                    </div>

                    {/* History Years */}
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-zinc-400">
                            <span>歷史數據年限 (Years)</span>
                            <span className="font-mono text-accent font-bold">{params.historyYears.toFixed(1)}</span>
                        </div>
                        <input type="range" min="0.5" max="5.0" value={params.historyYears} step="0.5" onChange={e => handleParamChange('historyYears', parseFloat(e.target.value))} className="custom-range w-full" />
                    </div>

                </div>
                </>
            );
        });

        const App = () => {
            const [rawData, setRawData] = useState(null);
            const [fetchError, setFetchError] = useState(null);
            // 預設 false：沒有自動載入，開頁先進入「尚未載入」的進站畫面
            const [isLoadingData, setIsLoadingData] = useState(false);
            const [loadElapsed, setLoadElapsed] = useState(0);   // 載入已經過秒數
            // 代理記憶狀態
            const [agentTick, setAgentTick]   = useState(0);     // 寫入後觸發面板重讀
            const [agentModels, setAgentModels] = useState([]);
            const [agentRuns, setAgentRuns]   = useState([]);
            const [agentPerf, setAgentPerf]   = useState({ n: 0, da: NaN, mape: NaN });
                        // ── 先顯示快取、背景更新 ──
            const [snapshotAt, setSnapshotAt]   = useState(null);   // 目前畫面資料的快照時間（null = 即時資料）
            const [isRefreshing, setIsRefreshing] = useState(false);// 背景更新進行中
            const [hasSnapshot, setHasSnapshot] = useState(false);  // 是否存在可用快照（決定進站畫面的按鈕）
            const pendingFreshRef = useRef(null);                   // 訓練中先暫存新資料，結束後再換
            const [cotStatus, setCotStatus] = useState({
                state: "idle",
                source: null,
                records: 0,
                latestDate: null,
                latestNet: null,
                message: null,
                phase: null,
                updatedAt: null
            });
                                                const [stockTwitsStatus, setStockTwitsStatus] = useState({ source: 'idle', bullishPct: null, bearishPct: null, totalTagged: 0, updatedAt: null });
            const [fearGreedStatus, setFearGreedStatus] = useState({ source: 'idle', currentScore: null, currentRating: null, updatedAt: null });

            const modelRef = useRef(null);
            const trainConfigRef = useRef(null);

            // ─────────────────────────────────────────────────────────────
            // 🔑 歷史最佳設定自動還原
            // 在所有 useState 初始化前，讀取 LocalStorage 中的訓練設定。
            // 若存在，則以歷史最佳設定作為初始值，確保 UI 一載入即與模型吻合。
            // ─────────────────────────────────────────────────────────────
            const DEFAULT_FACTORS = {
                slvPrices: true, slvVolume: true, slvVolumeRoc: true,
                tipsYield: true, rsiPrices: true, rsiPrices12: true,
                vixRoc: true, socialSentiment: true, fearGreed: true,
                goldPrices: true, oilPrices: true, dxyPrices: true,
                vixPrices: true, tnxPrices: true, copperPrices: true,
                silverInventory: true, goldSilverRatio: true, cotNetPosition: true,
                priceROC: true, ma5Silver: true, ma20Silver: true,
                maCross: true, macdLine: true,
                gtrend: true,
            };
            const DEFAULT_PARAMS = {
                lookback: 10, epochs: 400, layers: 32, lr: 0.001,
                futureDays: 3, historyYears: 2, batchSize: 32,
                directionalLambda: 0.3, huberDelta: 1.0,
                algorithm: 'BiGRU', validationMode: VALIDATION_MODE.NONE, kFolds: 5,
                predTarget: 'price',  // 'price' | 'direction' | 'dual'
            };

            const loadSavedConfig = () => {
                try {
                    const str = localStorage.getItem(TRAIN_CONFIG_KEY);
                    if (!str) return null;
                    const cfg = JSON.parse(str);
                    if (!cfg || !cfg.params || !cfg.selectedFactors) return null;
                    return cfg;
                } catch (e) {
                    console.warn('[InitRestore] Failed to parse saved config:', e);
                    return null;
                }
            };
            const savedConfig = loadSavedConfig();
            const hasSavedModel = savedConfig !== null;

            // 以歷史最佳設定（若有）作為 useState 初始值
            const initParams          = hasSavedModel ? { ...DEFAULT_PARAMS,  ...savedConfig.params          } : DEFAULT_PARAMS;
            const initSelectedFactors = hasSavedModel ? { ...DEFAULT_FACTORS, ...savedConfig.selectedFactors } : DEFAULT_FACTORS;
            const initNumFeatures     = hasSavedModel
                ? (savedConfig.finalNumFeatures ?? (1 + Object.values(savedConfig.selectedFactors).filter(Boolean).length))
                : (1 + Object.values(DEFAULT_FACTORS).filter(Boolean).length);
            const initRunMode         = hasSavedModel ? RUN_MODE.PREDICT_BEST : RUN_MODE.TRAIN_RESUME;

            const [restoredFromSaved] = useState(hasSavedModel);
            const [runMode, setRunMode] = useState(initRunMode);
            const [uploadStatus, setUploadStatus] = useState({ type: 'info', message: '請上傳 .json 和 .bin 檔案' });
            const [latestFactorData, setLatestFactorData] = useState(null);

            const [modelState, setModelState] = useState({
                isTraining: false,
                progress: 0,
                currentEpoch: 0,
                totalEpochs: 0,
                loss: 'N/A',
                directionalAccuracy: 'N/A',
                mape: 'N/A',
                kFoldMape: 'N/A',
                kFoldDa: 'N/A',
                logs: hasSavedModel
                    ? [`✅ 已自動還原歷史最佳設定 (演算法:${savedConfig.params.algorithm}, Lookback:${savedConfig.params.lookback}, 特徵數:${initNumFeatures})`, '啟動中...']
                    : ['啟動中...'],
            });

            const [currentBestLoss, setCurrentBestLoss] = useState(parseFloat(localStorage.getItem(BEST_MAPE_KEY)) || 100);
            const [bestLossValue, setBestLossValue] = useState(null);

            const [params, setParams] = useState(initParams);
            const [selectedFactors, setSelectedFactors] = useState(initSelectedFactors);
            const [numFeatures, setNumFeatures] = useState(initNumFeatures);

            // 🔑 mount 時還原 trainConfigRef，讓 PREDICT_BEST 免重新點擊即可直接預測
            useEffect(() => {
                if (!hasSavedModel) return;
                try {
                    trainConfigRef.current = {
                        minMax: { price: savedConfig.priceMinMax },
                        activeFeatures: savedConfig.activeFeaturesConfig.map(f => ({ ...f, minMax: f.minMax })),
                        finalNumFeatures: savedConfig.finalNumFeatures,
                        lookback: savedConfig.params.lookback,
                        algorithm: savedConfig.params.algorithm,
                    };
                    console.log('[InitRestore] trainConfigRef restored:', trainConfigRef.current);
                } catch (e) {
                    console.warn('[InitRestore] Failed to restore trainConfigRef:', e);
                }
            // eslint-disable-next-line react-hooks/exhaustive-deps
            }, []); 

            // ── 自動訓練觸發：一鍵流程在套用最佳參數後自動觸發完整訓練 ──
            const trainModelRef = useRef(null);
            useEffect(() => { trainModelRef.current = trainModel; }); // no deps = always up-to-date
            useEffect(() => {
                if (!autoTrainTriggerRef.current) return;
                autoTrainTriggerRef.current = false;
                // 100ms 確保 React 已完成 re-render，trainModelRef.current 含最新 isTraining=false closure
                const tid = setTimeout(() => {
                    const fn = trainModelRef.current;
                    if (typeof fn === 'function') {
                        console.log('[AutoFlow] 觸發 trainModel');
                        fn();
                    }
                }, 100);
                return () => clearTimeout(tid);
            // eslint-disable-next-line react-hooks/exhaustive-deps
            }, [params, selectedFactors]);

            const [trainingResults, setTrainingResults] = useState({ lossHistory: [] });
            const [prediction, setPrediction] = useState(null);

            // ── AI 自動搜尋代理 ──
            const agentRunningRef = useRef(false);
            const agentStopRef    = useRef(false);
            const [agentState, setAgentState] = useState({
                running: false,
                currentTrial: 0,
                totalTrials: 0,
                strategy: 'random',   // 'random' | 'grid' | 'bayesian'
                searchEpochs: 60,     // quick-eval epochs per trial
                maxTrials: 20,
                metric: 'loss',       // 'loss' | 'da'
                trials: [],           // [{params, factors, loss, da, trialIdx}]
                bestTrial: null,
                log: [],
            });
            const [agentPanelOpen, setAgentPanelOpen] = useState(false);
            const [bgEnabled,    setBgEnabled]    = useState(false);
            const [notifyEnabled,setNotifyEnabled]= useState(false);
            const [bgStatus,     setBgStatus]     = useState('idle'); // idle | active | paused | done

            // ── 一鍵智慧流程 ──
            const autoFlowStopRef    = useRef(false);
            const autoTrainTriggerRef = useRef(false);
            const [autoFlowPhase,  setAutoFlowPhase]  = useState('idle'); // 'idle'|'searching'|'applying'|'training'|'done'
            const [autoFlowTrials, setAutoFlowTrials] = useState(10);
            const [autoFlowEpochs, setAutoFlowEpochs] = useState(30);

            // ── 偵測訓練結束 → 將一鍵流程推進到 done（必須放在 autoFlowPhase 宣告之後）──
            useEffect(() => {
                if (autoFlowPhase === 'training' && !modelState.isTraining) {
                    setAutoFlowPhase('done');
                    if (document.hidden) {
                        window.SilverNotify.send('🎉 一鍵智慧流程完成', '訓練+預測已完成，請返回查看結果', 'slv-autoflow-done');
                    }
                }
            }, [modelState.isTraining, autoFlowPhase]);

            // ── 同步 window._trainingActive 給背景執行邏輯 ──
            useEffect(() => {
                // WakeLock、靜音保活、心跳、背景切換 backend 都由 SilverBackground 統一管理
                window.SilverBackground.setTraining(modelState.isTraining);
                if (modelState.isTraining) setBgStatus('active');
                else setBgStatus(prev => prev === 'active' ? 'done' : prev);
            }, [modelState.isTraining]);
            // ===== Backtest Simulation UI =====
            const [backtestConfig, setBacktestConfig] = useState({
                testRatio: 0.3,          // 使用最後 30% 做回測
                initialCapital: 100000,  // 初始資金
                leverage: 1.0,           // 槓桿
                positionPct: 1.0,        // 每次使用資金比例 (0~1)
                feeBps: 2,               // 單邊手續費 (bps)
                slippageBps: 1,          // 單邊滑價 (bps)
                threshold: 0.0,          // 進場門檻：預測報酬率絕對值 > threshold 才進場
                allowShort: true,        // 允許放空
            });
            const [backtestResult, setBacktestResult] = useState(null);

            
            const logsUpdater = useCallback((msg) => {
                setModelState(prev => ({ ...prev, logs: [msg, ...prev.logs.slice(0, 4)] }));
            }, [])

            // ── 每個 epoch 的畫面更新批次化 ──
            // 每個 epoch 都 setState 會讓整頁（含 Plotly loss 圖）重繪，GPU 上一秒可能有幾十個 epoch。
            // 改為先排進佇列，最多每 EPOCH_UI_INTERVAL_MS 套用一次（React 會把這幾個 setState 合併成一次重繪）。
            const EPOCH_UI_INTERVAL_MS = 250;
            const epochUiRef = useRef({ losses: [], patches: [], timer: null, last: 0 });
            const flushEpochUi = useCallback(() => {
                const q = epochUiRef.current;
                if (q.timer) { clearTimeout(q.timer); q.timer = null; }
                q.last = performance.now();
                const { losses, patches } = q;
                q.losses = []; q.patches = [];
                if (losses.length) {
                    setTrainingResults(prev => ({ ...prev, lossHistory: [...prev.lossHistory, ...losses] }));
                    const minL = Math.min(...losses);
                    setBestLossValue(prev => (prev === null || minL < prev) ? minL : prev);
                }
                if (patches.length) setModelState(prev => patches.reduce((st, f) => f(st), prev));
            }, []);
            // loss：加入 loss 曲線；patch：modelState 的函式型更新
            const queueEpochUi = useCallback(({ loss, patch } = {}) => {
                const q = epochUiRef.current;
                if (loss !== undefined) q.losses.push(loss);
                if (patch) q.patches.push(patch);
                const wait = EPOCH_UI_INTERVAL_MS - (performance.now() - q.last);
                if (wait <= 0) flushEpochUi();
                else if (!q.timer) q.timer = setTimeout(flushEpochUi, wait);
            }, [flushEpochUi]);

            // ===== Backtest Engine =====
            const runBacktest = useCallback(async () => {
                try {
                    if (!rawData || !rawData.prices || rawData.prices.length < 50) {
                        setBacktestResult(null);
                        logsUpdater("回測失敗：無可用價格資料。");
                        return;
                    }
                    if (!modelRef.current) {
                        setBacktestResult(null);
                        logsUpdater("回測失敗：尚未載入/訓練模型（modelRef.current 為空）。");
                        return;
                    }

                    logsUpdater("回測：建立資料集...");
                    // 必須與訓練用同一份資料（marketData = 依「歷史年數」篩選後），
                    // 正規化參數與保留集切分點才會一致
                    // （marketData 在此 callback 之後才宣告，不能放進 deps，所以這裡用相同方式重算）
                    const btData = filterDataByYears(rawData, params.historyYears);
                    const ds = createMultivariateDataset(btData, params.lookback, selectedFactors);
                    const { X, minMax, prevPrices: btPrev, split: btSplit, startIdx: btStart } = ds;
                    const prices = btData.prices;

                    if (!X || X.length < 30) {
                        setBacktestResult(null);
                        logsUpdater("回測失敗：序列資料不足（lookback 太大或資料太少）。");
                        return;
                    }

                    // 回測只能用模型「沒看過」的區段 = 保留集。
                    // 設定的測試比例若涵蓋到訓練區段，就截到保留集起點，否則績效是樣本內、會被高估。
                    const testRatio = Math.min(Math.max(parseFloat(backtestConfig.testRatio) || 0.3, 0.05), 0.8);
                    const requestedSplit = Math.floor(X.length * (1 - testRatio));
                    const holdStart = (btSplit && btSplit.holdLen > 0) ? btSplit.holdStart : X.length;
                    const splitIdx = Math.max(requestedSplit, holdStart);
                    if (splitIdx >= X.length) {
                        setBacktestResult(null);
                        logsUpdater("回測失敗：沒有保留集（資料太少），無法做樣本外回測。");
                        return;
                    }
                    if (splitIdx > requestedSplit) {
                        logsUpdater(`回測：測試比例 ${(testRatio*100).toFixed(0)}% 會涵蓋訓練過的資料，已改為只用保留集（最後 ${X.length - splitIdx} 筆）。`);
                    }
                    const testX = X.slice(splitIdx);

                    // 依預測目標把模型輸出換回價格；與 runMultiStepPrediction 的解讀一致
                    const predTarget = params.predTarget || 'price';
                    const outDim = modelRef.current.outputs?.[0]?.shape?.slice(-1)[0] ?? 1;
                    const expectedDim = predTarget === 'dual' ? 2 : 1;
                    if (outDim !== expectedDim) {
                        setBacktestResult(null);
                        logsUpdater(`回測失敗：目前模型輸出 ${outDim} 維，與預測目標「${predTarget}」不符，請先以此目標重新訓練。`);
                        return;
                    }
                    const lrS = minMax.logRet || { mean: 0, std: 1 };
                    const toPredPrice = (raw, t) => {
                        const prev = btPrev[splitIdx + t];
                        if (predTarget === 'logreturn') return prev * Math.exp(raw[t] * lrS.std + lrS.mean);
                        // 純方向：沒有價格，以機率偏離 0.5 的程度換成預期報酬（±1%），只用於決定多空
                        if (predTarget === 'direction') return prev * (1 + (raw[t] - 0.5) * 0.02);
                        if (predTarget === 'dual') return denormalizePrice(raw[t * 2], minMax);
                        return denormalizePrice(raw[t], minMax);
                    };

                    logsUpdater(`回測：預測測試集（共 ${testX.length} 筆，目標=${predTarget}）...`);

                    // Batch predict for speed
                    const xTensor = windowsToTensor(testX);
                    const predTensor = modelRef.current.predict(xTensor);
                    const predScaled = await predTensor.data();
                    tf.dispose([xTensor, predTensor]);

                    const result = simulateBacktest({
                        prices,
                        dates: btData.dates,
                        // 第 t 筆測試樣本預測的是 prices[startIdx + splitIdx + t]（只用到前一天以前的資料）
                        priceIndices: testX.map((_, t) => btStart + splitIdx + t),
                        predPrices: testX.map((_, t) => toPredPrice(predScaled, t)),
                        config: backtestConfig,
                    });
                    if (!result) {
                        setBacktestResult(null);
                        logsUpdater("回測失敗：有效回測筆數不足（可能資料對齊問題）。");
                        return;
                    }
                    const { tradeCount, totalReturn, maxDD, sharpe } = result;
                    setBacktestResult({ ...result, config: { ...backtestConfig, testRatio } });

                    logsUpdater(`回測完成：交易 ${tradeCount} 次，總報酬 ${(totalReturn*100).toFixed(2)}%，MDD ${(maxDD*100).toFixed(2)}%，Sharpe ${sharpe.toFixed(2)}`);
                } catch (e) {
                    console.error(e);
                    setBacktestResult(null);
                    logsUpdater(`回測失敗：${e.message || e}`);
                }
            }, [rawData, params.lookback, params.historyYears, params.predTarget, selectedFactors, backtestConfig, logsUpdater]);

            // 當 backtestResult 變動時畫 Equity 曲線
            useEffect(() => {
                if (!backtestResult || !backtestResult.equityCurve) return;
                const elId = "backtestEquityChart";
                const el = document.getElementById(elId);
                if (!el) return;

                const x = backtestResult.equityCurve.map(d => d.date);
                const y = backtestResult.equityCurve.map(d => d.equity);

                const trace = {
                    x, y,
                    type: "scatter",
                    mode: "lines",
                    name: "Equity"
                };

                const layout = {
                    title: "Backtest Equity Curve",
                    margin: { l: 50, r: 20, t: 40, b: 40 },
                    xaxis: { title: "Date" },
                    yaxis: { title: "Equity" },
                    paper_bgcolor: "rgba(0,0,0,0)",
                    plot_bgcolor: "rgba(0,0,0,0)",
                    font: { color: "#e4e4e7" }
                };

                Plotly.newPlot(elId, [trace], layout, {displayModeBar: false, responsive: true});
            }, [backtestResult]);
;

            const marketData = useMemo(() => {
                if (!rawData) return null;
                const filtered = filterDataByYears(rawData, params.historyYears);
                if (filtered.dates && filtered.dates.length > 0) {
                    logsUpdater(`數據範圍篩選: ${filtered.dates[0]} ~ ${filtered.dates[filtered.dates.length-1]} (共 ${filtered.dates.length} 筆)`);
                }
                return filtered;
            }, [rawData, params.historyYears, logsUpdater]);

            const mockMarketData = useCallback(() => { 
                const dates = [];
                const prices = [];
                const slvVolumes = [];
                const goldPrices = [];
                const oilPrices = [];
                const dxyPrices = []; 
                const vixPrices = []; 
                const tnxPrices = []; 
                const copperPrices = []; 
                const silverInventory = [];
                const slvPrices = [];
                const cotNetPositions = []; 

                let lastPrice = 25;
                let lastNetPos = 50000; 
                const numDays = 5 * 365;
                for (let i = 0; i < numDays; i++) {
                    const change = (Math.random() - 0.5) * 0.2;
                    lastPrice = lastPrice + change;
                    prices.push(Math.max(10, Math.min(50, lastPrice))); 

                    const lastGold = goldPrices[goldPrices.length - 1] || 2000;
                    goldPrices.push(lastGold + (Math.random() - 0.5) * 2 + change * 10);
                    const lastOil = oilPrices[oilPrices.length - 1] || 75;
                    oilPrices.push(lastOil + (Math.random() - 0.5) * 0.5 + change);
                    const lastDXY = dxyPrices[dxyPrices.length - 1] || 100;
                    dxyPrices.push(lastDXY + (Math.random() - 0.5) * 0.1 - change * 0.2);
                    const lastVIX = vixPrices[vixPrices.length - 1] || 15;
                    vixPrices.push(Math.max(10, lastVIX + (Math.random() - 0.5) * 1)); 
                    const lastTNX = tnxPrices[tnxPrices.length - 1] || 4.0;
                    tnxPrices.push(Math.max(1.0, lastTNX + (Math.random() - 0.5) * 0.05));
                    const lastCopper = copperPrices[copperPrices.length - 1] || 3.5;
                    copperPrices.push(lastCopper + (Math.random() - 0.5) * 0.1 + change * 0.5);
                    // silverInventory mock: slow oz/share decay
                    const mockOzSh = 0.930 - (i / prices.length) * 0.004;
                    silverInventory.push(mockOzSh * 1000 + (Math.random() - 0.5) * 1.5);
                    const lastSlvPrice = slvPrices[slvPrices.length - 1] || 22;
                    slvPrices.push(lastSlvPrice + change * 0.8);
                    const lastSlvVolume = slvVolumes[slvVolumes.length - 1] || 5000000;
                    slvVolumes.push(Math.max(1000000, lastSlvVolume + (Math.random() - 0.5) * 500000));
                    
                    // MOCK COT CALCULATION
                    const netChange = (Math.random() - 0.5) * 5000 + change * 5000; 
                    lastNetPos = lastNetPos + netChange;
                    cotNetPositions.push(Math.max(-50000, Math.min(200000, lastNetPos))); 

                    const nextDate = new Date();
                    nextDate.setDate(nextDate.getDate() - numDays + i);
                    dates.push(nextDate.toISOString().split('T')[0]);
                }
                const socialSentiment = (() => {
                    // mock: 以價格動量建構，與真實路徑邏輯相同
                    const arr = new Array(prices.length);
                    const momentum = prices.map((p, i) => i === 0 ? 0 : (p - prices[i-1]) / prices[i-1]);
                    let base = 50;
                    for (let i = 0; i < prices.length; i++) {
                        const start = Math.max(0, i - 10 + 1);
                        const slice = momentum.slice(start, i + 1);
                        const avgMom = slice.reduce((a, b) => a + b, 0) / slice.length;
                        base = Math.max(10, Math.min(90, 50 + avgMom * 200));
                        arr[i] = base;
                    }
                    return arr;
                })();
                // Mock CNN Fear & Greed（穩定隨機遊走）
                const fearGreed = (() => {
                    const arr = [];
                    let v = 50;
                    for (let i = 0; i < prices.length; i++) {
                        v = Math.max(5, Math.min(95, v + (Math.random() - 0.5) * 3));
                        arr.push(v);
                    }
                    return arr;
                })();
                const goldSilverRatio = goldPrices.map((g, i) => g / prices[i]);
                const rsiPrices = calculateRSI(prices, 5); 
                const rsiPrices12 = calculateRSI(prices, 12); 
                const vixRoc = calculateROC(vixPrices);
                const tipsYield = dates.map(d => {
                    const yr = new Date(d).getFullYear();
                    if (yr < 2022) return 0.3;
                    if (yr < 2023) return 0.8;
                    if (yr < 2024) return 2.2;
                    if (yr < 2025) return 1.9;
                    return 1.6;
                });
                    const slvVolumeRoc = calculateROC(slvVolumes);
                // 🎯 動量特徵
                const priceROC    = calculateROC(prices);
                const ma5Silver   = calculateMA(prices, 5);
                const ma20Silver  = calculateMA(prices, 20);
                const maCross     = ma5Silver.map((v, i) => ma20Silver[i] !== 0 ? (v - ma20Silver[i]) / ma20Silver[i] : 0);
                const ema12       = calculateEMA(prices, 12);
                const ema26       = calculateEMA(prices, 26);
                const macdLine    = ema12.map((v, i) => v - ema26[i]);
                // 🎃 Mock Google Trends（穩定隨機遊走 0-100）
                const gtrend = (() => { let v=50; return prices.map(() => { v=Math.max(5,Math.min(95,v+(Math.random()-0.5)*4)); return Math.round(v); }); })();
                
                // --- 提取最新的模擬數據和來源標籤 ---
                const latestValues = {
                    dataSource: 'Mock (隨機模擬值)',
                    stSource: 'mock',
                    stBullishPct: 50,
                    stBearishPct: 50,
                    stTotalTagged: 0,
                    fearGreedSource: 'mock',
                    fearGreedCurrent: fearGreed[fearGreed.length - 1],
                    fearGreedRating: 'Neutral',
                    price: prices[prices.length - 1],
                    slvPrices: slvPrices[slvPrices.length - 1],
                    slvVolume: slvVolumes[slvVolumes.length - 1],
                    tipsYield: tipsYield[tipsYield.length - 1],
                    rsiPrices: rsiPrices[rsiPrices.length - 1],
                    rsiPrices12: rsiPrices12[rsiPrices12.length - 1],
                    vixRoc: vixRoc[vixRoc.length - 1],
                    socialSentiment: socialSentiment[socialSentiment.length - 1],
                    fearGreed: fearGreed[fearGreed.length - 1],
                    goldPrices: goldPrices[goldPrices.length - 1],
                    oilPrices: oilPrices[oilPrices.length - 1],
                    dxyPrices: dxyPrices[dxyPrices.length - 1],
                    vixPrices: vixPrices[vixPrices.length - 1],
                    tnxPrices: tnxPrices[tnxPrices.length - 1],
                    copperPrices: copperPrices[copperPrices.length - 1],
                    silverInventory: silverInventory[silverInventory.length - 1],
                    goldSilverRatio: goldSilverRatio[goldSilverRatio.length - 1],
                    cotNetPosition: cotNetPositions[cotNetPositions.length - 1], 
                    // 🎯 動量特徵
                    priceROC: priceROC[priceROC.length - 1],
                    ma5Silver: ma5Silver[ma5Silver.length - 1],
                    ma20Silver: ma20Silver[ma20Silver.length - 1],
                    maCross: maCross[maCross.length - 1],
                    macdLine: macdLine[macdLine.length - 1],
                    gtrend: gtrend[gtrend.length - 1],
                };

                return { 
                    dates, prices, slvPrices, slvVolume: slvVolumes, slvVolumeRoc, goldPrices, oilPrices, dxyPrices, vixPrices, tnxPrices, copperPrices, silverInventory, goldSilverRatio,
                    socialSentiment, fearGreed,
                    rsiPrices, rsiPrices12, vixRoc, tipsYield, 
                    cotNetPosition: cotNetPositions,
                    // 🎯 動量特徵
                    priceROC, ma5Silver, ma20Silver, maCross, macdLine,
                    gtrend,
                    latestValues,
                    dataQuality: { allMock: true, explicitDemo: false, mockSymbols: [], mockFactors: [] },
                };
            }, []);

            // 市場資料到位後，把過去的預測與實際收盤價對帳。
            // 這是代理唯一能知道「自己實際準不準」的途徑 ——
            // 訓練指標是回看的，只有事後對帳才是真實戰績。
            useEffect(() => {
                if (!marketData) return;
                let cancelled = false;
                (async () => {
                    try {
                        const priceByDate = {};
                        marketData.dates.forEach((d, i) => { priceByDate[d] = marketData.prices[i]; });
                        const all = await AgentStore.all('predictions');
                        const newly = resolvePredictions(all, priceByDate);
                        for (const r of newly) await AgentStore.put('predictions', r);
                        if (cancelled) return;
                        const after = newly.length ? await AgentStore.all('predictions') : all;
                        setAgentPerf(recentPerformance(after, 20));
                        if (newly.length) console.log(`[Agent] 已對帳 ${newly.length} 筆預測`);
                        const [ms, rs] = await Promise.all([AgentStore.all('models'), AgentStore.all('runs')]);
                        if (cancelled) return;
                        setAgentModels(ms.sort((a, b) => String(b.ts).localeCompare(String(a.ts))));
                        setAgentRuns(rs.sort((a, b) => String(b.ts).localeCompare(String(a.ts))));
                    } catch (e) { console.warn('[Agent] 對帳失敗:', e?.message); }
                })();
                return () => { cancelled = true; };
            }, [marketData, agentTick]);

            // 載入期間計時，供載入畫面顯示已等待時間與退出選項
            useEffect(() => {
                if (!isLoadingData) { setLoadElapsed(0); return; }
                const t0 = Date.now();
                const id = setInterval(() => setLoadElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
                return () => clearInterval(id);
            }, [isLoadingData]);

            // 套用一份資料集到畫面（快取或即時共用）
            const applyData = useCallback((data, { fromSnapshot = false, snapTs = null } = {}) => {
                setRawData(data);
                setLatestFactorData(data.latestValues);
                const lv = data.latestValues || {};
                setStockTwitsStatus({
                    source: lv.stSource || 'mock',
                    bullishPct: lv.stBullishPct, bearishPct: lv.stBearishPct,
                    totalTagged: lv.stTotalTagged, updatedAt: new Date().toISOString()
                });
                setFearGreedStatus({
                    source: lv.fearGreedSource || 'mock',
                    currentScore: lv.fearGreedCurrent, currentRating: lv.fearGreedRating,
                    updatedAt: new Date().toISOString()
                });
                setSnapshotAt(fromSnapshot ? snapTs : null);
            }, []);

            // 立即以快照開場，同時在背景抓取即時資料。
            // 「立即」的前提是不經過 JSON 解析 —— 快照存在 IndexedDB，
            // 用結構化複製讀出，不會卡住主執行緒。
            const loadFromSnapshotThenRefresh = useCallback(async () => {
                setFetchError(null);
                let shown = false;
                try {
                    const snap = await AgentStore.get('snapshot', 'latest');
                    if (snap && snap.data && Array.isArray(snap.data.prices) && snap.data.prices.length > 0) {
                        applyData(snap.data, { fromSnapshot: true, snapTs: snap.ts });
                        shown = true;
                        logsUpdater(`已載入 ${new Date(snap.ts).toLocaleString()} 的快照（${snap.data.prices.length} 筆），背景更新中…`);
                    }
                } catch (e) { console.warn('[Snapshot] 讀取失敗:', e?.message); }

                // 沒有可用快照 → 退回一般載入流程（顯示載入畫面）
                if (!shown) { await loadDataRef.current(false); return; }

                // 有快照 → 背景更新，畫面不中斷
                loadDataRef.current(false, { background: true });
            }, [applyData, logsUpdater]);

            const loadData = useCallback(async (useMock = false, opts = {}) => {
                const background = !!opts.background;
                if (background) setIsRefreshing(true);
                else { setIsLoadingData(true); setLoadElapsed(0); }
                setFetchError(null);
                logsUpdater(background
                    ? '背景更新市場數據中…'
                    : '開始載入市場數據 (Yahoo / StockTwits / CFTC / CNN F&G)...');
                try {
                    const data = useMock ? mockMarketData() : await fetchMarketData(setCotStatus);
                    // 使用者自己按「模擬資料」才算示範模式；載入失敗自動退回的模擬資料不算
                    if (useMock && data.dataQuality) data.dataQuality.explicitDemo = true;

                    // 訓練進行中不要抽換資料 —— 先暫存，等訓練結束再套用。
                    // 否則畫面上的指標會對應到已經不存在的那份資料。
                    if (background && window._trainingActive) {
                        pendingFreshRef.current = data;
                        logsUpdater('新資料已就緒，將在訓練結束後套用。');
                    } else {
                        applyData(data);
                    }

                    if (!useMock) {
                        // 存成快照供下次立即開場
                        AgentStore.put('snapshot', { key: 'latest', ts: new Date().toISOString(), data })
                            .then(() => setHasSnapshot(true))
                            .catch(e => console.warn('[Snapshot] 寫入失敗:', e?.message));
                    }

                    const lv = data.latestValues;
                    const stLabel = lv.stSource === 'real' ? '✅ Real' : '⚠️ Mock';
                    const fgLabel = lv.fearGreedSource === 'real' ? '✅ Real' : '⚠️ Mock';
                    logsUpdater(`數據載入完成 (${data.prices.length} 筆) | StockTwits: ${stLabel} | F&G: ${fgLabel} (${lv.fearGreedCurrent?.toFixed(1)} ${lv.fearGreedRating})`);
                    const dq = data.dataQuality;
                    if (dq && !dq.allMock && dq.mockSymbols?.length) {
                        logsUpdater(`⚠️ 以下品種抓取失敗，已用隨機模擬值補上：${dq.mockSymbols.join(', ')}。相關因子（${dq.mockFactors.join(', ')}）勾選時將無法訓練。`);
                    }
                } catch (e) {
                    if (background) {
                        // 背景更新失敗 → 保留畫面上的快照，不要換成模擬資料。
                        // 使用者手上仍是真實（只是稍舊）的資料，比換成假資料好。
                        logsUpdater(`⚠️ 背景更新失敗：${e.message}。畫面維持先前的快照資料。`);
                    } else {
                        setFetchError(e.message);
                        logsUpdater(`數據載入失敗: ${e.message}。自動切換到模擬數據。`);
                        const mockData = mockMarketData();
                        applyData(mockData);
                    }
                } finally {
                    if (background) setIsRefreshing(false);
                    else setIsLoadingData(false);
                }
            }, [logsUpdater, mockMarketData, applyData]);

            // loadData 會被 loadFromSnapshotThenRefresh 呼叫，但宣告在它之後，
            // 用 ref 轉接避免互相引用造成的順序問題。
            const loadDataRef = useRef(null);
            useEffect(() => { loadDataRef.current = loadData; });

            // 訓練結束後，套用訓練期間擱置的新資料
            useEffect(() => {
                if (modelState.isTraining) return;
                if (!pendingFreshRef.current) return;
                const d = pendingFreshRef.current;
                pendingFreshRef.current = null;
                applyData(d);
                logsUpdater('訓練已結束，已套用背景更新取得的最新資料。');
            }, [modelState.isTraining, applyData, logsUpdater]);

            // 開頁先確認有沒有可用快照，決定進站畫面要顯示哪一種按鈕
            useEffect(() => {
                let cancelled = false;
                (async () => {
                    try {
                        const snap = await AgentStore.get('snapshot', 'latest');
                        if (!cancelled && snap && snap.data) setHasSnapshot(true);
                    } catch (e) { console.warn('[Snapshot] 探測失敗:', e?.message); }
                })();
                return () => { cancelled = true; };
            }, []);

            useEffect(() => {
                // 不在載入時自動抓取數據 —— 先讓介面出現，由使用者按下按鈕再取得。
                // 這樣開啟頁面是即時的，也不會在使用者還沒決定前就打外部 API。
                window.SilverNotify.init().then(ok => {
                    if (ok) setNotifyEnabled(true);
                });
            }, []);

            const handleParamChange = useCallback((key, value) => {
                setParams(prev => ({ ...prev, [key]: value }));
                if (key === 'historyYears' && rawData) {
                    // Force marketData re-computation and log update
                    const filtered = filterDataByYears(rawData, value);
                    logsUpdater(`數據範圍篩選: ${filtered.dates[0]} ~ ${filtered.dates[filtered.dates.length-1]} (共 ${filtered.dates.length} 筆)`);
                }
            }, [rawData, logsUpdater]);

            const handleFactorChange = useCallback((key) => {
                setSelectedFactors(prev => {
                    const newFactors = { ...prev, [key]: !prev[key] };
                    const newNumFeatures = 1 + Object.values(newFactors).filter(v => v).length;
                    setNumFeatures(newNumFeatures);
                    logsUpdater(`特徵數量更新為: ${newNumFeatures}`);
                    return newFactors;
                });
            }, [logsUpdater]);

            const handleUploadModel = async (event) => {
                if (modelState.isTraining) return;

                const files = event.target.files;
                if (!files || files.length !== 2) {
                    setUploadStatus({ type: 'error', message: '請同時上傳模型檔案 (.json) 和 .bin 檔案' });
                    return;
                }
                setModelState(prev => ({ ...prev, isTraining: true }));
                setPrediction(null);
                logsUpdater('正在載入外部模型...');
                setUploadStatus({ type: 'loading', message: '正在載入模型...' });
                
                const jsonFile = Array.from(files).find(f => f.name.endsWith('.json'));
                const binFile = Array.from(files).find(f => f.name.endsWith('.bin'));

                if (!jsonFile || !binFile) {
                    setUploadStatus({ type: 'error', message: '請確保檔案包含 .json 和 .bin 文件。' });
                    setModelState(prev => ({ ...prev, isTraining: false }));
                    return;
                }

                try {
                    const model = await tf.loadLayersModel(tf.io.browserFiles([jsonFile, binFile]));
                    modelRef.current = model;
                    logsUpdater('模型載入成功。正在嘗試載入訓練配置...');

                    // 嘗試從 LocalStorage 載入歷史訓練配置
                    const savedConfigStr = localStorage.getItem(TRAIN_CONFIG_KEY);
                    if (savedConfigStr) {
                        const config = JSON.parse(savedConfigStr);
                        // 自動同步訓練時的參數
                        setParams(config.params);
                        setSelectedFactors(config.selectedFactors);
                        const newNumFeatures = 1 + Object.values(config.selectedFactors).filter(v => v).length;
                        setNumFeatures(newNumFeatures);
                        
                        trainConfigRef.current = {
                            minMax: { price: config.priceMinMax },
                            activeFeatures: config.activeFeaturesConfig.map(f => ({ ...f, minMax: f.minMax })),
                            finalNumFeatures: config.finalNumFeatures,
                            lookback: config.params.lookback,
                            algorithm: config.params.algorithm,
                        };
                        logsUpdater(`外部模型載入成功。已自動載入並設定訓練時的參數 (演算法: ${config.params.algorithm}) 及因子配置。`);
                        setUploadStatus({ type: 'success', message: `外部模型載入成功 (${config.params.algorithm})。` });
                    } else {
                        // 如果沒有歷史配置，則根據當前 UI 參數建立臨時配置
                        logsUpdater('警告: 未找到歷史訓練配置。將根據當前 UI 參數建立臨時配置。');
                        const { minMax, activeFeatures, numFeatures: finalFeatures } = createMultivariateDataset(marketData, params.lookback, selectedFactors);
                        trainConfigRef.current = {
                            minMax, activeFeatures, finalNumFeatures: finalFeatures, lookback: params.lookback, algorithm: params.algorithm
                        };
                        setUploadStatus({ type: 'warning', message: `外部模型載入成功。建議手動配置參數。` });
                    }
                    setRunMode(RUN_MODE.PREDICT_UPLOADED);

                } catch (e) {
                    logsUpdater(`載入模型失敗: ${e.message}`);
                    setUploadStatus({ type: 'error', message: `載入模型失敗: ${e.message}` });
                    if (modelRef.current) { tf.dispose(modelRef.current); modelRef.current = null; }
                } finally {
                    setModelState(prev => ({ ...prev, isTraining: false }));
                }
            };
            
            // 清除歷史最佳紀錄
            const handleClearBest = useCallback(async () => {
                if (!confirm("確定要清除瀏覽器儲存區中的『歷史最佳模型』及其紀錄嗎？此操作不可逆。")) return;

                logsUpdater('正在清除瀏覽器儲存區中的歷史最佳模型紀錄...');
                
                // 1. 清除 Loss 紀錄
                localStorage.removeItem(BEST_MAPE_KEY);
                localStorage.removeItem(TRAIN_CONFIG_KEY);
                setCurrentBestLoss(100); 
                
                // 2. 清除儲存的模型權重 (TensorFlow.js IndexedDB 模型)
                try {
                    await tf.io.removeModel(MODEL_URL);
                    logsUpdater('歷史最佳模型及權重已成功清除！');
                } catch (e) {
                    logsUpdater(`警告: 嘗試清除模型權重失敗，可能模型不存在。錯誤詳情: ${e.message}`);
                }
                
                // 3. 重設 UI 狀態
                setPrediction(null);
                setTrainingResults({ lossHistory: [] });
                setBestLossValue(null);
                if (modelRef.current) {
                    tf.dispose(modelRef.current);
                    modelRef.current = null;
                }
                setModelState(prev => ({ ...prev, directionalAccuracy: 'N/A', loss: 'N/A', mape: 'N/A', kFoldMape: 'N/A', kFoldDa: 'N/A' })); 

            }, [logsUpdater]);

            const trainModel = useCallback(async () => {
                if (!marketData || modelState.isTraining) return;
                
                setModelState(prev => ({ 
                    ...prev, 
                    isTraining: true, 
                    progress: 0,
                    currentEpoch: 0,
                    totalEpochs: params.epochs,
                    loss: 'N/A', 
                    directionalAccuracy: 'N/A', 
                    mape: 'N/A',
                    kFoldMape: 'N/A', 
                    kFoldDa: 'N/A'    
                }));
                setPrediction(null);
                setTrainingResults({ lossHistory: [] });
                
                try { // ── 外層防護：任何非預期錯誤都能復位 isTraining ──

                // ── 資料品質：被勾選的因子若是隨機模擬值，訓練只會學到雜訊 ──
                const _dq = checkDataQuality(marketData, selectedFactors);
                if (_dq.reason) logsUpdater(`${_dq.blocked ? '🛑 ' : ''}${_dq.reason}`);
                if (_dq.blocked && runMode.includes('TRAIN')) {
                    setModelState(prev => ({ ...prev, isTraining: false }));
                    return;
                }

                logsUpdater('開始準備數據集...');
                const { X, y, yDir, yLogRet, prevPrices, minMax, allMinMax, activeFeatures, numFeatures: finalNumFeatures, split } = createMultivariateDataset(marketData, params.lookback, selectedFactors);

                // ── 時序保留集：最後一段完全不參與訓練，之後用它公平比較挑戰者與現任冠軍。
                //    絕不隨機切分 —— 那會讓未來資料洩漏進訓練集。
                //    必須與 createMultivariateDataset 的正規化切分點一致（直接用它回傳的 split）。
                //    必須宣告在這一層：訓練分支與後面的保留集評分/代理裁決都要用到，
                //    放進 if (TRAIN) 內會變成區塊作用域，外面存取就會 ReferenceError。
                const _hs = split || splitHoldout(X.length);

                if (X.length === 0) {
                    logsUpdater('錯誤: 數據集生成失敗或數據點不足。');
                    setModelState(prev => ({ ...prev, isTraining: false }));
                    return;
                }

                // 訓練 Loss 歷史記錄
                let historyLoss = [];

                const buildFullConfig = () => ({
                    params: params, selectedFactors: selectedFactors,
                    priceMinMax: { min: minMax.price.min, max: minMax.price.max },
                    activeFeaturesConfig: activeFeatures.map(f => ({ key: f.key, name: f.name, isExternal: f.isExternal, minMax: { min: f.minMax.min, max: f.minMax.max } })),
                    finalNumFeatures: finalNumFeatures,
                });

                // === K-Fold（walk-forward）數據分割邏輯 ===
                // 只在保留集之前的區段做交叉驗證；每一折只用「測試折之前」的資料訓練。
                // 一般 K-Fold 會拿測試折之後（未來）的資料訓練，在時間序列上是資料洩漏。
                const kfEnd = _hs.holdLen > 0 ? _hs.trainEnd : X.length;
                const kfBlock = Math.floor(kfEnd / ((params.kFolds || 1) + 1));
                let foldSize = kfBlock;
                let foldResults = [];
                let trainModelInstance = null;
                let finalModel = null;
                // 各次訓練的 loss 不能互相比較（不同演算法、目標、資料區間），
                // 所以「最佳」只在本次訓練內比較；跨次比較交給保留集上的冠軍/挑戰者機制。
                let runBestLoss = Infinity;
                let savedThisRun = false;

                // 預測目標必須在 kfSpec 之前宣告，否則 TDZ（ReferenceError）或被轉譯成 undefined
                const _kfTarget = params.predTarget || 'price';
                const _kfOk = _kfTarget === 'price' || _kfTarget === 'logreturn';
                const kfSpec = resolveTargetSpec(_kfTarget, params);
                const trainAndEvaluate = async (trainX, trainY, trainYLR, testX, testY, testYLR, testPrev, foldIdx) => {
                    if (trainModelInstance) tf.dispose(trainModelInstance); // 清理上一次 Fold 的模型
                    trainModelInstance = createModel(params.algorithm, params.lookback, finalNumFeatures, params);
                    // loss 與訓練目標必須與 predTarget 一致，
                    // 否則折內以價格訓練、折外卻把輸出當報酬率解讀，結果會靜默錯誤
                    trainModelInstance.compile({
                        optimizer: tf.train.adam(params.lr),
                        loss: kfSpec.loss,
                        metrics: ['mape'],
                    });

                    logsUpdater(`Fold ${foldIdx + 1}/${params.kFolds}: 訓練開始 (訓練集大小: ${trainX.length}, 驗證集大小: ${testX.length})`);
                    
                    const toTargetTensor = (Y, YLR) => _kfTarget === 'logreturn'
                        ? tf.tensor(Float32Array.from(YLR), [YLR.length, 1])
                        : tf.tensor2d(Y, [Y.length, 1]);
                    const foldTensorX = windowsToTensor(trainX);
                    const foldTensorY = toTargetTensor(trainY, trainYLR);
                    // 驗證集 = 本折的測試區段（在訓練區段之後），用來決定存哪個 epoch
                    const foldValX = windowsToTensor(testX);
                    const foldValY = toTargetTensor(testY, testYLR);
                    const isLastFold = foldIdx === params.kFolds - 1;

                    let foldHistoryLoss = [];

                    const foldCheckpointCallback = {
                        onEpochEnd: async (epoch, logs) => {
                            const currentLoss = logs.loss;
                            const valLoss = Number.isFinite(logs.val_loss) ? logs.val_loss : currentLoss;
                            foldHistoryLoss.push(currentLoss);

                            if (foldIdx === 0) {
                                // 圖表顯示第一折的訓練 loss，讓使用者馬上看到進度
                                historyLoss.push(currentLoss);
                                queueEpochUi({ loss: currentLoss });
                            }

                            // 最後一折用到最多歷史資料，作為最終模型；依驗證 loss 存最佳 epoch
                            if (isLastFold && valLoss < runBestLoss) {
                                runBestLoss = valLoss;
                                savedThisRun = true;
                                const lossToSave = valLoss.toFixed(8);
                                localStorage.setItem(BEST_MAPE_KEY, lossToSave);
                                setCurrentBestLoss(valLoss);

                                await trainModelInstance.save(MODEL_URL);
                                localStorage.setItem(TRAIN_CONFIG_KEY, JSON.stringify(buildFullConfig()));

                                queueEpochUi({ patch: prev => ({
                                    ...prev,
                                    loss: lossToSave,
                                    logs: [`Fold ${foldIdx + 1} Epoch ${epoch+1}: val_loss ${lossToSave} - **本次最佳，已儲存模型**`, ...prev.logs.filter(log => !log.startsWith('Epoch') && !log.startsWith('新的歷史最佳')).slice(0, 4)],
                                }) });
                            }

                            queueEpochUi({ patch: prev => ({ ...prev, progress: ((foldIdx / params.kFolds) * 100) + (((epoch + 1) / params.epochs) * (100 / params.kFolds)), currentEpoch: epoch + 1, totalEpochs: params.epochs }) });
                            await safeYield();
                        }
                    };

                    await trainModelInstance.fit(foldTensorX, foldTensorY, {
                        epochs: params.epochs,
                        batchSize: params.batchSize,
                        // 不打亂：DirectionalHuberLoss 依賴 batch 內相鄰樣本是相鄰交易日
                        shuffle: false,
                        validationData: [foldValX, foldValY],
                        callbacks: [foldCheckpointCallback]
                    });

                    tf.dispose([foldTensorX, foldTensorY, foldValX, foldValY]);
                    flushEpochUi();
                    logsUpdater(`Fold ${foldIdx + 1}/${params.kFolds}: 訓練完成，正在評估...`);
                    
                    // 評估
                    const testTensorX = windowsToTensor(testX);
                    const predTensor = trainModelInstance.predict(testTensorX);
                    const predData = await predTensor.data();
                    tf.dispose([testTensorX, predTensor]);

                    const testActuals = testY.map(val => denormalizePrice(val, minMax));
                    let testPreds, mape, da;
                    if (_kfTarget === 'logreturn') {
                        const st = minMax.logRet || { mean: 0, std: 1 };
                        const rs = Array.from(predData).map(z => z * st.std + st.mean);
                        // 由該樣本的前一日實際價格重建，誤差不沿折內累積
                        testPreds = rs.map((r, i) => (testPrev[i] ?? testActuals[i]) * Math.exp(r));
                        mape = calculateMAPE(testActuals, testPreds);
                        // 方向直接看報酬率正負號
                        let ok = 0;
                        for (let i = 0; i < rs.length; i++) {
                            const actualUp = testActuals[i] > (testPrev[i] ?? testActuals[i]);
                            if ((rs[i] > 0) === actualUp) ok++;
                        }
                        da = rs.length ? (ok / rs.length) * 100 : 0;
                    } else {
                        testPreds = Array.from(predData).map(val => denormalizePrice(val, minMax));
                        mape = calculateMAPE(testActuals, testPreds);
                        da = calculateKFoldDirectionalAccuracy(testActuals, testPreds);
                    }

                    foldResults.push({ mape, da });
                    logsUpdater(`Fold ${foldIdx + 1}/${params.kFolds} 評估結果: MAPE: ${mape.toFixed(4)}%，DA: ${da.toFixed(2)}%`);

                    // 總進度更新
                    setModelState(prev => ({ ...prev, progress: ((foldIdx + 1) / params.kFolds) * 100 }));
                };
                
                // ===================================
                // 1. K-Fold 驗證模式
                // ===================================
                if (runMode.includes('TRAIN') && params.validationMode === VALIDATION_MODE.K_FOLD && !_kfOk) {
                    logsUpdater(`⚠️ K-Fold 交叉驗證目前僅支援「價格迴歸」與「對數報酬率」目標，已改用標準訓練流程。`);
                }
                if (runMode.includes('TRAIN') && params.validationMode === VALIDATION_MODE.K_FOLD && params.kFolds > 1 && foldSize >= 10 && _kfOk) {
                    logsUpdater(`開始執行 ${params.kFolds}-Fold walk-forward 交叉驗證（僅用保留集之前的 ${kfEnd} 筆，保留集 ${_hs.holdLen} 筆不參與）...`);

                    // 區段切成 k+1 塊：第 i 折以塊 0..i 訓練、塊 i+1 測試（擴張視窗）
                    for (let i = 0; i < params.kFolds; i++) {
                        const trainEndIdx = (i + 1) * kfBlock;
                        const testEndIdx  = i === params.kFolds - 1 ? kfEnd : trainEndIdx + kfBlock;

                        const trainX  = X.slice(0, trainEndIdx);
                        const trainY  = y.slice(0, trainEndIdx);
                        const trainYLR= yLogRet.slice(0, trainEndIdx);
                        const testX   = X.slice(trainEndIdx, testEndIdx);
                        const testY   = y.slice(trainEndIdx, testEndIdx);
                        const testYLR = yLogRet.slice(trainEndIdx, testEndIdx);
                        const testPrev= prevPrices.slice(trainEndIdx, testEndIdx);

                        if (trainX.length === 0 || testX.length === 0) {
                            logsUpdater(`Fold ${i + 1} 數據集劃分不足，跳過此 Fold。`);
                            continue;
                        }

                        await trainAndEvaluate(trainX, trainY, trainYLR, testX, testY, testYLR, testPrev, i);
                    }
                    
                    // 計算平均結果
                    const avgMape = foldResults.reduce((sum, res) => sum + res.mape, 0) / (foldResults.length || 1);
                    const avgDa = foldResults.reduce((sum, res) => sum + res.da, 0) / (foldResults.length || 1);
                    
                    logsUpdater(`K-Fold 驗證完成。平均 MAPE: ${avgMape.toFixed(4)}%，平均 DA: ${avgDa.toFixed(2)}%`);

                    setModelState(prev => ({ ...prev, kFoldMape: avgMape.toFixed(2), kFoldDa: avgDa.toFixed(2) }));
                    
                    // 只載入「本次」存下的模型；本次沒存成功就用最後一折訓練完的模型，
                    // 不能載入 localStorage 裡可能是別次、別種設定留下的舊模型
                    let reloaded = null;
                    if (savedThisRun) {
                        try { reloaded = await tf.loadLayersModel(MODEL_URL); } catch(e) { reloaded = null; }
                    }
                    if (reloaded) {
                        finalModel = reloaded;
                        tf.dispose(trainModelInstance);
                    } else {
                        logsUpdater('警告: K-Fold 模式下無法重新載入本次最佳模型，改用最後一折訓練完成的模型。');
                        finalModel = trainModelInstance;
                    }
                    trainModelInstance = null;
                    modelRef.current = finalModel;
                } 
                
                // ===================================
                // 2. 標準訓練/預測模式 (原始邏輯)
                // ===================================
                else {
                    let modelToUse = null;
                    let resumedFromSaved = false;   // 接續訓練且沿用了已存模型的權重

                    // 1. 決定模型來源
                    if (runMode === RUN_MODE.TRAIN_CONTINUE) {
                        modelToUse = createModel(params.algorithm, params.lookback, finalNumFeatures, params);
                        logsUpdater(`已建立新的 ${params.algorithm} 模型並從零開始訓練。`);
                        // 重設儲存的 best loss，讓第一個 epoch 必定能存檔（避免舊 loss 擋住新模型）
                        localStorage.setItem(BEST_MAPE_KEY, '100');
                        setCurrentBestLoss(100);
                        setBestLossValue(null); 
                    } else if (runMode === RUN_MODE.TRAIN_RESUME) {
                        try {
                            modelToUse = await tf.loadLayersModel(MODEL_URL);
                            resumedFromSaved = true;
                            logsUpdater(`成功載入瀏覽器儲存區歷史最佳模型，將在其基礎上繼續訓練 (儲存 Loss: ${currentBestLoss.toFixed(8)})。`);
                            setBestLossValue(null);
                            const savedConfigStr = localStorage.getItem(TRAIN_CONFIG_KEY);
                            if (savedConfigStr) {
                                const config = JSON.parse(savedConfigStr);
                                const currentActiveFeatures = activeFeatures.map(f => ({ ...f, minMax: allMinMax[f.key] }));
                                const savedFactorKeys = Object.keys(config.selectedFactors).filter(k => k in selectedFactors && config.selectedFactors[k]).sort().join(',');
                                const currentFactorKeys = Object.keys(selectedFactors).filter(k => selectedFactors[k]).sort().join(',');
                                if (config.params.algorithm !== params.algorithm || config.params.lookback !== params.lookback || config.params.layers !== params.layers || savedFactorKeys !== currentFactorKeys) {
                                    tf.dispose(modelToUse);
                                    modelToUse = createModel(params.algorithm, params.lookback, finalNumFeatures, params);
                                    resumedFromSaved = false;
                                    logsUpdater('警告: 訓練參數或因子配置與儲存模型不一致，將重新建立模型並從零開始訓練。');
                                } else if ((config.params.predTarget || 'price') !== (params.predTarget || 'price')) {
                                    // 輸出層與 loss 都不同，沿用舊權重只會從錯的起點出發
                                    tf.dispose(modelToUse);
                                    modelToUse = createModel(params.algorithm, params.lookback, finalNumFeatures, params);
                                    resumedFromSaved = false;
                                    logsUpdater('警告: 預測目標與儲存模型不同，將重新建立模型並從零開始訓練。');
                                }
                                trainConfigRef.current = { minMax: { price: minMax.price }, activeFeatures: currentActiveFeatures, finalNumFeatures: finalNumFeatures, lookback: params.lookback, algorithm: params.algorithm };
                            }
                        } catch (e) {
                            modelToUse = createModel(params.algorithm, params.lookback, finalNumFeatures, params);
                            logsUpdater(`錯誤:瀏覽器儲存區中未找到歷史最佳模型，已切換為從零開始訓練。`);
                        }
                    } else if (runMode === RUN_MODE.PREDICT_BEST) {
                        modelToUse = modelRef.current; 
                        if (!modelToUse || (modelRef.current && trainConfigRef.current.algorithm !== params.algorithm)) {
                            try {
                                const loadedModel = await tf.loadLayersModel(MODEL_URL);
                                modelToUse = loadedModel;
                                logsUpdater(`成功載入瀏覽器儲存區歷史最佳模型 (儲存 Loss: ${currentBestLoss.toFixed(8)})。`);
                                const savedConfigStr = localStorage.getItem(TRAIN_CONFIG_KEY);
                                if (savedConfigStr) {
                                    const config = JSON.parse(savedConfigStr);
                                    trainConfigRef.current = { minMax: { price: config.priceMinMax }, activeFeatures: config.activeFeaturesConfig.map(f => ({ ...f, minMax: f.minMax })), finalNumFeatures: config.finalNumFeatures, lookback: config.params.lookback, algorithm: config.params.algorithm };
                                    setParams(prev => ({ ...prev, lookback: config.params.lookback, layers: config.params.layers, algorithm: config.params.algorithm }));
                                    setSelectedFactors(config.selectedFactors);
                                    setNumFeatures(config.finalNumFeatures);
                                }
                            } catch (e) {
                                logsUpdater('錯誤:瀏覽器儲存區中未找到歷史最佳模型，無法進行預測。');
                                setModelState(prev => ({ ...prev, isTraining: false }));
                                return;
                            }
                        } else {
                            logsUpdater('使用當前記憶體中的歷史最佳模型進行預測。');
                        }
                        modelRef.current = modelToUse; 
                    } else if (runMode === RUN_MODE.PREDICT_UPLOADED) {
                        modelToUse = modelRef.current;
                        if (!modelToUse) { logsUpdater('錯誤: 未上傳模型，無法進行預測。'); setModelState(prev => ({ ...prev, isTraining: false })); return; }
                        if (!trainConfigRef.current) {
                            const { minMax, activeFeatures, numFeatures: finalFeatures } = createMultivariateDataset(marketData, params.lookback, selectedFactors);
                            trainConfigRef.current = { minMax, activeFeatures, finalNumFeatures: finalFeatures, lookback: params.lookback, algorithm: params.algorithm };
                        }
                        logsUpdater('使用手動上傳的模型進行預測。');
                    } else {
                        logsUpdater('錯誤: 未選擇有效的運行模式。');
                        setModelState(prev => ({ ...prev, isTraining: false }));
                        return;
                    }
                    
                    finalModel = modelToUse;

                    // 2. 編譯模型 (訓練模式才需要)
                    if (runMode.includes('TRAIN')) {
                        const _pt = params.predTarget || 'price';
                        const _spec = resolveTargetSpec(_pt, params);
                        const _targetName = _pt === 'direction' ? '方向分類 (BCE)'
                            : _pt === 'dual' ? '雙頭 (價格+方向)'
                            : _pt === 'logreturn' ? `對數報酬率 (純 Huber, δ=${params.huberDelta ?? 1.0})`
                            : `DirectionalHuber (α=${params.directionalLambda}, δ=${params.huberDelta ?? 1.0})`;
                        modelToUse.compile({
                            optimizer: tf.train.adam(params.lr),
                            loss: _spec.loss,
                            metrics: _pt === 'direction' ? ['accuracy'] : ['mae'],
                        });
                        logsUpdater(`模型編譯完成 (演算法: ${params.algorithm}, 目標: ${_targetName}, LR: ${params.lr})`);
                    
                        const _trainX = _hs.holdLen > 0 ? X.slice(0, _hs.trainEnd) : X;
                        const _trainY = _hs.holdLen > 0 ? y.slice(0, _hs.trainEnd) : y;
                        const _trainYDir = _hs.holdLen > 0 ? yDir.slice(0, _hs.trainEnd) : yDir;
                        const _trainYLR  = _hs.holdLen > 0 ? yLogRet.slice(0, _hs.trainEnd) : yLogRet;
                        if (_hs.holdLen > 0) {
                            logsUpdater(`保留集：最後 ${_hs.holdLen} 筆不參與訓練，用於冠軍/挑戰者比較。`);
                        } else {
                            logsUpdater(`資料量不足以切出保留集，本次訓練不進行冠軍比較。`);
                        }
                        const tensorX = windowsToTensor(_trainX);
                        const tensorY = buildTargetTensor(_trainY, _trainYDir, _pt, _trainYLR);

                        // 驗證集 = 保留集。存檔依驗證 loss，而不是訓練 loss（訓練 loss 只會越練越低，挑不出過擬合）
                        let valX = null, valY = null;
                        if (_hs.holdLen > 0) {
                            valX = windowsToTensor(X, _hs.holdStart);
                            valY = buildTargetTensor(y.slice(_hs.holdStart), yDir.slice(_hs.holdStart), _pt, yLogRet.slice(_hs.holdStart));
                        }
                        const lossLabel = valX ? 'val_loss' : 'loss';

                        // 接續訓練：以「載入模型目前的驗證 loss」為起點，練不贏就不覆蓋原模型
                        if (resumedFromSaved && valX) {
                            try {
                                const ev = modelToUse.evaluate(valX, valY, { batchSize: params.batchSize });
                                const evT = Array.isArray(ev) ? ev[0] : ev;
                                const v0 = (await evT.data())[0];
                                tf.dispose(ev);
                                if (Number.isFinite(v0)) {
                                    runBestLoss = v0;
                                    logsUpdater(`接續訓練起點 val_loss: ${v0.toFixed(6)}（需低於此值才會覆蓋已存模型）`);
                                }
                            } catch (e) { console.warn('[Resume] 初始驗證 loss 計算失敗:', e?.message); }
                        }

                        const checkpointCallback = {
                            onEpochEnd: async (epoch, logs) => {
                                const currentLoss = logs.loss;
                                const monitored = (valX && Number.isFinite(logs.val_loss)) ? logs.val_loss : currentLoss;
                                const progress = ((epoch + 1) / params.epochs) * 100;
                                historyLoss.push(currentLoss);
                                queueEpochUi({ loss: currentLoss });

                                if (monitored < runBestLoss) {
                                    runBestLoss = monitored;
                                    savedThisRun = true;
                                    const lossToSave = monitored.toFixed(8);
                                    localStorage.setItem(BEST_MAPE_KEY, lossToSave);
                                    setCurrentBestLoss(monitored);
                                    await modelToUse.save(MODEL_URL);
                                    localStorage.setItem(TRAIN_CONFIG_KEY, JSON.stringify(buildFullConfig()));

                                    queueEpochUi({ patch: prev => ({
                                        ...prev,
                                        progress,
                                        loss: lossToSave,
                                        logs: [`Epoch ${epoch+1}: ${lossLabel} ${lossToSave} - **本次最佳，已儲存模型**`, ...prev.logs.filter(log => !log.startsWith('Epoch') && !log.startsWith('新的歷史最佳')).slice(0, 4)],
                                        directionalAccuracy: 'N/A',
                                        mape: 'N/A'
                                    }) });
                                } else {
                                    queueEpochUi({ patch: prev => ({
                                        ...prev,
                                        progress,
                                        loss: currentLoss.toFixed(4),
                                        logs: [`Epoch ${epoch+1}: loss ${currentLoss.toFixed(4)}${valX ? ` / val_loss ${monitored.toFixed(4)}` : ''}`, ...prev.logs.filter(log => !log.startsWith('Epoch') && !log.startsWith('正在儲存最佳模型')).slice(0, 4)]
                                    }) });
                                }
                                await safeYield();
                            }
                        };

                        await modelToUse.fit(tensorX, tensorY, {
                            epochs: params.epochs,
                            batchSize: params.batchSize,
                            // 不打亂：DirectionalHuberLoss 依賴 batch 內相鄰樣本是相鄰交易日
                            shuffle: false,
                            ...(valX ? { validationData: [valX, valY] } : {}),
                            callbacks: [checkpointCallback]
                        });
                        if (valX) tf.dispose([valX, valY]);
                        flushEpochUi();

                        logsUpdater(`訓練完成，重新載入瀏覽器儲存區中的最佳模型進行最終預測...`);

                        try {
                            // 本次沒有存檔、也不是接續訓練 → localStorage 裡的是別次的模型，不能拿來用
                            if (!savedThisRun && !resumedFromSaved) throw new Error('本次未儲存模型');
                            const reloadedModel = await tf.loadLayersModel(MODEL_URL);
                            // ⚠️ 驗證形狀：reloaded model 的 lookback/features 必須與 X 一致
                            const rShape = reloadedModel.layers[0]?.batchInputShape;
                            const xLB = X[0]?.length ?? params.lookback;
                            const xFeat = X[0]?.[0]?.length ?? finalNumFeatures;
                            const shapeMismatch = rShape && (rShape[1] !== xLB || rShape[2] !== xFeat);
                            if (shapeMismatch) {
                                logsUpdater(`⚠️ 重載模型形狀不符 (模型:${rShape[1]}×${rShape[2]}, X:${xLB}×${xFeat})，使用當前訓練完成的模型。`);
                                tf.dispose(reloadedModel);
                                finalModel = modelToUse;
                            } else {
                                tf.dispose(modelToUse);
                                finalModel = reloadedModel;
                            }
                        } catch(e) {
                            logsUpdater('警告: 無法重新載入瀏覽器儲存區中的最佳模型，使用當前訓練結束的模型進行預測。');
                            finalModel = modelToUse;
                        }
                        
                        tf.dispose([tensorX, tensorY]);

                        localStorage.setItem(TRAIN_CONFIG_KEY, JSON.stringify(buildFullConfig()));
                        // ⚠️ 同步更新 trainConfigRef，確保後續預測使用正確的 minMax / lookback / numFeatures
                        trainConfigRef.current = {
                            minMax: { price: minMax.price },
                            activeFeatures: activeFeatures.map(f => ({ ...f, minMax: { min: f.minMax.min, max: f.minMax.max } })),
                            finalNumFeatures: finalNumFeatures,
                            lookback: params.lookback,
                            algorithm: params.algorithm,
                        };
                        modelRef.current = finalModel; 
                    } 
                    
                }

                // 3. 執行預測與指標計算
                // 使用 FRESH 值（剛從 createMultivariateDataset 取得）
                // 不使用 trainConfigRef 的舊值——trainConfigRef 只在 PREDICT_BEST/UPLOADED 時需要
                const predMinMax         = minMax;          // fresh — 與 X、y 同一次 scale
                const predNumFeatures    = finalNumFeatures; // fresh — 與 X shape 一致
                const predLookback       = params.lookback;  // fresh — 與 X shape 一致
                const _predTarget        = params.predTarget || 'price';
                logsUpdater(`開始執行 T+${params.futureDays} 日預測（目標=${_predTarget}, 特徵=${predNumFeatures}, lookback=${predLookback}）...`);
                const { historyPreds, futurePrices, influenceScores, actualHistory, dirProbs, futureDirProbs, predLogRets } = await runMultiStepPrediction(
                    finalModel,
                    X,
                    y,
                    predMinMax,
                    activeFeatures,        // fresh — 含 .minMax.scaled，influence 分析用
                    predNumFeatures,
                    predLookback,
                    params.futureDays,
                    logsUpdater,
                    _predTarget,
                    prevPrices
                );

                // ══ 保留集評分：挑戰者與冠軍都用「同一段」資料評，比較才公平 ══
                const evalOnHoldout = async (mdl) => {
                    if (!mdl || _hs.holdLen <= 0) return { holdoutDA: NaN, holdoutMAPE: NaN, holdoutN: 0 };
                    let hx = null, hp = null;
                    try {
                        const hX = X.slice(_hs.holdStart);
                        hx = windowsToTensor(X, _hs.holdStart);
                        hp = mdl.predict(hx);
                        const raw = await hp.data();
                        const stride = _predTarget === 'dual' ? 2 : 1;
                        const lrS = (minMax && minMax.logRet) ? minMax.logRet : { mean: 0, std: 1 };
                        const actual = [], preds = [], prevs = [];
                        for (let i = 0; i < hX.length; i++) {
                            const gi = _hs.holdStart + i;
                            actual.push(denormalizePrice(y[gi], minMax));
                            prevs.push(prevPrices[gi]);
                            if (_predTarget === 'logreturn') {
                                preds.push(prevPrices[gi] * Math.exp(raw[i] * lrS.std + lrS.mean));
                            } else if (_predTarget === 'direction') {
                                // 只有方向：用機率合成一個相對前值的價格，供方向判定
                                preds.push(prevPrices[gi] * (raw[i] > 0.5 ? 1.0001 : 0.9999));
                            } else {
                                preds.push(denormalizePrice(raw[i * stride], minMax));
                            }
                        }
                        return scoreHoldout(actual, preds, prevs);
                    } catch (e) {
                        console.warn('[Holdout] 評分失敗:', e?.message);
                        return { holdoutDA: NaN, holdoutMAPE: NaN, holdoutN: 0 };
                    } finally {
                        if (hp) try { tf.dispose(hp); } catch(_){}
                        if (hx) try { tf.dispose(hx); } catch(_){}
                    }
                };

                // ── 指標計算 ──
                let directionalAccuracy, calculatedMAPE;
                if (_predTarget === 'logreturn') {
                    // 方向直接看預測報酬率的正負號，不必再從價格差回推
                    const rs = predLogRets || [];
                    let ok = 0, n = 0;
                    for (let i = 0; i < rs.length && i < yDir.length; i++) {
                        if ((rs[i] > 0 ? 1 : 0) === yDir[i]) ok++;
                        n++;
                    }
                    directionalAccuracy = (n > 0 ? (ok / n) * 100 : 0).toFixed(2);
                    // MAPE 用重建後的價格計算，與其他模式可直接比較
                    const ov = Math.min(historyPreds.length, actualHistory.length);
                    calculatedMAPE = (ov > 0
                        ? calculateMAPE(actualHistory.slice(0, ov), historyPreds.slice(0, ov))
                        : NaN).toFixed(2);
                } else if (_predTarget === 'direction' || _predTarget === 'dual') {
                    // 直接用方向機率對比實際方向計算 DA（最準確）
                    const probs = dirProbs || [];
                    const fullP = marketData.prices;
                    const offset = fullP.length - probs.length;
                    let correct = 0, valid = 0;
                    for (let i = 0; i < probs.length; i++) {
                        const pi = offset + i;
                        if (pi <= 0 || pi >= fullP.length) continue;
                        const actualUp = fullP[pi] > fullP[pi - 1];
                        const predUp   = probs[i] > 0.5;
                        if (actualUp === predUp) correct++;
                        valid++;
                    }
                    directionalAccuracy = (valid > 0 ? (correct / valid) * 100 : 0).toFixed(2);
                    // dual 仍有價格可算 MAPE；direction 無價格 → 標記 N/A
                    if (_predTarget === 'dual') {
                        const overlapLen = Math.min(historyPreds.length, actualHistory.length);
                        calculatedMAPE = calculateMAPE(actualHistory.slice(0, overlapLen), historyPreds.slice(0, overlapLen)).toFixed(2);
                    } else {
                        calculatedMAPE = 'N/A';
                    }
                } else {
                    // 價格迴歸：從預測價格序列推導方向
                    const overlapLen = Math.min(historyPreds.length, actualHistory.length);
                    const hpTrimmed  = historyPreds.slice(0, overlapLen);
                    const ahTrimmed  = actualHistory.slice(0, overlapLen);
                    directionalAccuracy = calculateDirectionalAccuracy(
                        marketData.prices, predLookback, hpTrimmed
                    ).toFixed(2);
                    calculatedMAPE = (overlapLen > 0 ? calculateMAPE(ahTrimmed, hpTrimmed) : NaN).toFixed(2);
                }
                
                logsUpdater(`指標計算完成。DA: ${directionalAccuracy}%，MAPE: ${calculatedMAPE}%。`);
                logsUpdater(`T+${params.futureDays} 日預測價格: ${futurePrices.map(p => p.toFixed(2)).join(', ')}`);

                // 背景執行通知
                if (document.hidden) {
                    window.SilverNotify.send(
                        '✅ 白銀 AI 訓練完成',
                        `DA ${directionalAccuracy}%  MAPE ${calculatedMAPE}%  T+1: $${futurePrices[0]?.toFixed(2)}`,
                        'slv-train-done'
                    );
                }

                setPrediction({
                    historyPreds,
                    futurePrices,
                    directionalAccuracy,
                    influenceScores,
                    mape: calculatedMAPE,
                    futureDirProbs,          // 方向信心度（direction / dual 模式）
                    predTarget: _predTarget,
                });

                // ══════════════════════════════════════════════════════
                // 代理：保留集評分 → 冠軍/挑戰者裁決 → 寫入紀錄
                // ══════════════════════════════════════════════════════
                try {
                    const challengerScore = await evalOnHoldout(finalModel);

                    // 冠軍在「同一段保留集」上重新評分。
                    // 直接比對舊紀錄的分數是不對的 —— 保留集會隨新資料改變。
                    let championScore = null, championMeta = null;
                    try {
                        const models = await AgentStore.all('models');
                        championMeta = models.find(m => m.status === 'champion') || null;
                        if (championMeta && _hs.holdLen > 0) {
                            const champModel = await tf.loadLayersModel(CHAMPION_URL);
                            const s = champModel.layers[0]?.batchInputShape;
                            const shapeOk = !s || (s[1] === (X[0]?.length) && s[2] === (X[0]?.[0]?.length));
                            const sameTarget = (championMeta.predTarget || 'price') === _predTarget;
                            if (shapeOk && sameTarget) {
                                championScore = await evalOnHoldout(champModel);
                            } else {
                                logsUpdater('現任冠軍的輸入形狀或預測目標已不同，無法在同一保留集比較，視為無冠軍。');
                                championMeta = null;
                            }
                            tf.dispose(champModel);
                        }
                    } catch (e) {
                        console.warn('[Agent] 載入冠軍失敗:', e?.message);
                        championMeta = null;
                    }

                    const verdict = shouldPromote(
                        championScore ? { ...championMeta, ...championScore } : null,
                        { ...challengerScore }
                    );

                    if (runMode.includes('TRAIN') && _hs.holdLen > 0) {
                        logsUpdater(`保留集評分 — 挑戰者 DA ${Number.isFinite(challengerScore.holdoutDA) ? challengerScore.holdoutDA.toFixed(2) + '%' : 'N/A'}` +
                            (championScore ? `，冠軍 DA ${championScore.holdoutDA.toFixed(2)}%` : '（目前無冠軍）'));
                        logsUpdater(verdict.promote ? `🏆 晉升為新冠軍：${verdict.reason}` : `🛡️ 維持現任冠軍：${verdict.reason}`);
                    }

                    // 純預測不產生新模型，不該寫進註冊表或觸發晉升，
                    // 否則每按一次「執行預測」就多一筆假的挑戰者。
                    const _didTrain = runMode.includes('TRAIN');

                    const modelRecord = {
                        ts: new Date().toISOString(),
                        algorithm: params.algorithm,
                        predTarget: _predTarget,
                        lookback: params.lookback,
                        layers: params.layers,
                        epochs: params.epochs,
                        holdoutDA: challengerScore.holdoutDA,
                        holdoutMAPE: challengerScore.holdoutMAPE,
                        holdoutN: challengerScore.holdoutN,
                        inSampleDA: parseFloat(directionalAccuracy),
                        inSampleMAPE: parseFloat(calculatedMAPE),
                        status: verdict.promote ? 'champion' : 'challenger',
                        reason: verdict.reason,
                    };

                    if (_didTrain && verdict.promote) {
                        // 舊冠軍退役，新冠軍權重另存一份，之後才有得比
                        const models = await AgentStore.all('models');
                        for (const m of models.filter(m => m.status === 'champion')) {
                            await AgentStore.put('models', { ...m, status: 'retired' });
                        }
                        try { await finalModel.save(CHAMPION_URL); } catch (e) { console.warn('[Agent] 冠軍權重儲存失敗:', e?.message); }
                    }
                    if (_didTrain) await AgentStore.add('models', modelRecord);

                    await AgentStore.add('runs', {
                        ts: new Date().toISOString(),
                        type: runMode.includes('TRAIN') ? 'train' : 'predict',
                        algorithm: params.algorithm,
                        predTarget: _predTarget,
                        da: parseFloat(directionalAccuracy),
                        mape: parseFloat(calculatedMAPE),
                        holdoutDA: challengerScore.holdoutDA,
                        promoted: verdict.promote ? 1 : 0,
                        note: verdict.reason,
                    });

                    // ── 預測日誌：記下每一天的判斷，日後與實際收盤價對帳 ──
                    const lastDate = marketData.dates[marketData.dates.length - 1];
                    const basis = marketData.prices[marketData.prices.length - 1];
                    for (let i = 0; i < futurePrices.length; i++) {
                        const d = new Date(lastDate + 'T00:00:00Z');
                        let added = 0;
                        while (added < i + 1) {            // 只算交易日（略過週末）
                            d.setUTCDate(d.getUTCDate() + 1);
                            const wd = d.getUTCDay();
                            if (wd !== 0 && wd !== 6) added++;
                        }
                        await AgentStore.add('predictions', {
                            madeAt: new Date().toISOString(),
                            basisDate: lastDate,
                            basisPrice: basis,
                            targetDate: d.toISOString().slice(0, 10),
                            horizon: i + 1,
                            predPrice: futurePrices[i],
                            predDir: futurePrices[i] >= basis ? 1 : 0,
                            confidence: (futureDirProbs && futureDirProbs[i] != null)
                                ? Math.abs(futureDirProbs[i] - 0.5) * 200 : null,
                            algorithm: params.algorithm,
                            predTarget: _predTarget,
                            resolved: 0,
                        });
                    }
                    setAgentTick(t => t + 1);   // 通知面板重新讀取
                } catch (e) {
                    // 完整錯誤進 console（含堆疊），面板只顯示可讀摘要。
                    // 之前只印 message，ReferenceError 這類問題看不出是哪個變數。
                    console.error('[Agent] 紀錄寫入失敗:', e);
                    const _nm = e?.name ? e.name + ': ' : '';
                    logsUpdater(`⚠️ 代理紀錄寫入失敗（不影響預測結果）：${_nm}${(e?.message ?? String(e)).slice(0, 120)}　詳見瀏覽器主控台`);
                }
                
                setModelState(prev => ({ 
                    ...prev, 
                    isTraining: false, 
                    directionalAccuracy: directionalAccuracy, 
                    mape: calculatedMAPE 
                }));
                if (finalModel !== modelRef.current) { tf.dispose(finalModel); }

                } catch (outerErr) {
                    const errMsg = outerErr?.message ?? String(outerErr);
                    logsUpdater(`❌ 執行失敗: ${errMsg.slice(0,120)}`);
                    console.error('[trainModel] outer catch:', outerErr);
                    if (isShaderError(outerErr)) {
                        logsUpdater('🔄 偵測到 WebGL shader 錯誤，正在重設 GPU context...');
                        await resetWebGLBackend();
                        logsUpdater('重設完成，請重新執行訓練。');
                    }
                    setModelState(prev => ({ ...prev, isTraining: false, progress: 0 }));
                } // ── end outer try-catch ──

            }, [params, selectedFactors, marketData, modelState.isTraining, runMode, logsUpdater, queueEpochUi, flushEpochUi, currentBestLoss, latestFactorData]); 

            // ══════════════════════════════════════════════════════════════
            // AI 代理：自動搜尋最佳超參數組合
            // ══════════════════════════════════════════════════════════════
            const runAgentSearch = useCallback(async () => {
                if (!marketData || agentRunningRef.current) return;
                // 只檢查核心資料（不帶 selectedFactors）；個別模擬因子在 runTrial 裡排除
                const _dqS = checkDataQuality(marketData, null);
                if (_dqS.blocked) { logsUpdater(`🛑 ${_dqS.reason}`); return; }
                const _mockFactors = marketData.dataQuality?.mockFactors || [];

                agentRunningRef.current = true;
                agentStopRef.current    = false;

                const cfg = agentState;
                const SEARCH_EPOCHS = cfg.searchEpochs;
                const MAX_TRIALS    = cfg.maxTrials;
                const METRIC        = cfg.metric;   // 'loss' | 'da'
                const STRATEGY      = cfg.strategy;

                // ── 搜尋空間定義 ──
                const SPACE = {
                    algorithm:         ['BiGRU', 'LSTM', 'Transformer', 'TimesFM'],
                    lookback:          [5, 10, 15, 20, 30, 40, 60],
                    layers:            [16, 32, 48, 64, 96, 128],
                    batchSize:         [16, 32, 64],
                    directionalLambda: [0.0, 0.1, 0.2, 0.3, 0.5, 0.7],
                    huberDelta:        [0.5, 1.0, 1.5, 2.0],
                    lr:                [0.0005, 0.001, 0.002, 0.003],
                };
                // 因子搜尋空間：對每個可關閉的因子以 70% 機率開啟
                const FACTOR_KEYS = Object.keys(params).length > 0
                    ? ['slvPrices','slvVolume','slvVolumeRoc','tipsYield','rsiPrices','rsiPrices12',
                       'vixRoc','socialSentiment','fearGreed','goldPrices','oilPrices','dxyPrices',
                       'vixPrices','tnxPrices','copperPrices','silverInventory','goldSilverRatio',
                       'cotNetPosition','priceROC','ma5Silver','ma20Silver','maCross','macdLine']
                    : [];

                const randChoice = arr => arr[Math.floor(Math.random() * arr.length)];

                // ── 試驗樣本產生器 ──
                const generateTrial = (trialIdx, prevTrials) => {
                    if (STRATEGY === 'grid') {
                        // 展開固定網格（小版：3×3×2×2 = 36種）
                        const gridParams = [
                            {algorithm:'BiGRU',     lookback:10, layers:32, directionalLambda:0.3, batchSize:32, lr:0.001, huberDelta:1.0},
                            {algorithm:'BiGRU',     lookback:20, layers:64, directionalLambda:0.3, batchSize:32, lr:0.001, huberDelta:1.0},
                            {algorithm:'BiGRU',     lookback:10, layers:32, directionalLambda:0.1, batchSize:32, lr:0.001, huberDelta:1.0},
                            {algorithm:'BiGRU',     lookback:30, layers:64, directionalLambda:0.5, batchSize:16, lr:0.001, huberDelta:1.0},
                            {algorithm:'LSTM',      lookback:10, layers:32, directionalLambda:0.3, batchSize:32, lr:0.001, huberDelta:1.0},
                            {algorithm:'LSTM',      lookback:20, layers:64, directionalLambda:0.3, batchSize:32, lr:0.001, huberDelta:1.0},
                            {algorithm:'LSTM',      lookback:10, layers:48, directionalLambda:0.2, batchSize:32, lr:0.002, huberDelta:1.5},
                            {algorithm:'Transformer',lookback:10, layers:64, directionalLambda:0.3, batchSize:32, lr:0.001, huberDelta:1.0},
                            {algorithm:'Transformer',lookback:20, layers:96, directionalLambda:0.2, batchSize:32, lr:0.001, huberDelta:1.0},
                        ];
                        const p = gridParams[trialIdx % gridParams.length];
                        return { trialParams: { ...params, ...p, epochs: SEARCH_EPOCHS }, trialFactors: selectedFactors };
                    } else if (STRATEGY === 'bayesian' && prevTrials.length >= 4) {
                        // 簡化貝葉斯：加權隨機採樣，傾向重複前 3 名的近鄰
                        const sorted = [...prevTrials].sort((a, b) =>
                            METRIC === 'da' ? (b.da || 0) - (a.da || 0) : (a.loss || 999) - (b.loss || 999)
                        );
                        const best = sorted[0];
                        // 以最佳為基礎，對每個維度以 60% 機率繼承，40% 機率隨機
                        const tp = {};
                        for (const [k, arr] of Object.entries(SPACE)) {
                            tp[k] = Math.random() < 0.6 ? best.params[k] : randChoice(arr);
                        }
                        // 因子：70% 繼承最佳，30% 隨機化
                        const tf2 = {};
                        for (const fk of FACTOR_KEYS) {
                            tf2[fk] = Math.random() < 0.7 ? best.factors[fk] : Math.random() > 0.3;
                        }
                        return { trialParams: { ...params, ...tp, epochs: SEARCH_EPOCHS }, trialFactors: tf2 };
                    } else {
                        // 純隨機採樣
                        const tp = {};
                        for (const [k, arr] of Object.entries(SPACE)) {
                            tp[k] = randChoice(arr);
                        }
                        const tf2 = {};
                        for (const fk of FACTOR_KEYS) {
                            tf2[fk] = Math.random() > 0.3;   // 70% 機率開啟
                        }
                        return { trialParams: { ...params, ...tp, epochs: SEARCH_EPOCHS }, trialFactors: tf2 };
                    }
                };

                // ── 單次快速訓練 + 評分 ──
                // ── 每 N 次試驗清理 WebGL 狀態，防止 Adam 動量張量累積 OOM ──
                const TRIAL_GC_INTERVAL = 8;
                let trialsSinceGC = 0;

                // 剪枝參考：歷史最佳 trial 的 loss（跨 trial 共享）
                let bestTrialLossSoFar = Infinity;

                const runTrial = async (trialParams, trialFactors) => {
                    // 模擬值因子不參與搜尋（直接改 trialFactors，最佳組合套用時才不會帶到它們）
                    _mockFactors.forEach(k => { if (k in trialFactors) trialFactors[k] = false; });
                    const memPre = tf.memory();
                    if (memPre.numTensors > 1500) {
                        await safeYield();
                        await new Promise(r => setTimeout(r, 200));
                    }
                    const SEARCH_MAX_SAMPLES = 520;
                    // maxSamples 直接在資料層截斷 → 不再建全量視窗後 slice（省 60% JS 建構時間）
                    const ds = createMultivariateDataset(marketData, trialParams.lookback, trialFactors, SEARCH_MAX_SAMPLES);
                    if (!ds.X || ds.X.length < 30 || ds.numFeatures === 0) return null;
                    // 時序切分：前段訓練、最後一段驗證。排名只看驗證段，
                    // 否則等於挑「最會背訓練資料」的組合。
                    const sp = ds.split;
                    if (!sp || sp.holdLen < 10) return null;
                    const rawX   = ds.X;
                    const rawY   = ds.y;
                    const minMax = ds.minMax;
                    const n      = sp.trainEnd;              // 訓練樣本數
                    const actualLB   = rawX[0]?.length       ?? trialParams.lookback;
                    const actualFeat = rawX[0]?.[0]?.length  ?? ds.numFeatures;
                    const flatten = (from, to) =>
                        [windowsToTensor(rawX, from, to), tf.tensor2d(Float32Array.from(rawY.slice(from, to)), [to - from, 1])];
                    let model = null, tX = null, tY = null, vX = null, vY = null, predsT = null;
                    let finalLoss = 9999;
                    try {
                        model = createModel(trialParams.algorithm, actualLB, actualFeat, trialParams);
                        model.compile({
                            optimizer: tf.train.adam(trialParams.lr),
                            loss: getDirectionalHuberLoss(trialParams.directionalLambda, trialParams.huberDelta ?? 1.0),
                        });
                        [tX, tY] = flatten(0, n);
                        [vX, vY] = flatten(sp.holdStart, rawX.length);

                        // 早停 + 剪枝狀態（都看驗證 loss）
                        let bestEpochLoss = Infinity, sinceImprove = 0;
                        const PATIENCE = 8;

                        await model.fit(tX, tY, {
                            epochs: SEARCH_EPOCHS,
                            batchSize: 128,   // 搜尋固定 128：GPU dispatch 次數減半
                            verbose: 0,
                            // 不打亂：DirectionalHuberLoss 依賴 batch 內相鄰樣本是相鄰交易日
                            shuffle: false,
                            validationData: [vX, vY],
                            callbacks: { onEpochEnd: async (ep, logs) => {
                                finalLoss = logs?.val_loss ?? logs?.loss ?? finalLoss;
                                // ── 早停：PATIENCE epochs 無改善 → 提前結束 ──
                                if (finalLoss < bestEpochLoss - 1e-6) { bestEpochLoss = finalLoss; sinceImprove = 0; }
                                else sinceImprove++;
                                if (sinceImprove >= PATIENCE) { model.stopTraining = true; }
                                // ── 剪枝：明顯劣於歷史最佳 → 中止，別浪費算力 ──
                                if (ep >= 10 && bestTrialLossSoFar < Infinity && finalLoss > bestTrialLossSoFar * 4) {
                                    model.stopTraining = true;
                                }
                                // ── yield 頻率：每 5 epochs 一次（原本每 epoch，rAF 16ms×epochs 純浪費）──
                                if (ep % 5 === 4) await safeYield();
                            }}
                        });
                        // 更新跨 trial 剪枝參考
                        if (finalLoss < bestTrialLossSoFar) bestTrialLossSoFar = finalLoss;
                        tf.dispose([tX, tY]); tX = null; tY = null;
                        predsT = model.predict(vX);
                        const predArr = await predsT.data();
                        tf.dispose(predsT); predsT = null;
                        tf.dispose([vX, vY]); vX = null; vY = null;
                        // 驗證段 DA：直接用資料集的 prevPrices 對齊
                        // （原本用 offset+lookback 推算索引，會錯開 lookback 天）
                        let correct = 0, valid = 0;
                        for (let i = 0; i < predArr.length; i++) {
                            const gi = sp.holdStart + i;
                            const prevP = ds.prevPrices[gi];
                            const actP  = denormalizePrice(rawY[gi], minMax);
                            const predP = denormalizePrice(predArr[i], minMax);
                            if (!Number.isFinite(prevP) || !Number.isFinite(actP) || !Number.isFinite(predP)) continue;
                            if ((actP >= prevP) === (predP >= prevP)) correct++;
                            valid++;
                        }
                        const daFinal = valid > 0 ? (correct / valid) * 100 : 0;
                        return { loss: finalLoss, da: daFinal };
                    } catch (e) {
                        console.warn('[Trial] error:', e?.message?.slice(0, 120));
                        if (isShaderError(e)) {
                            console.warn('[Trial] Shader error detected — resetting WebGL backend');
                            await resetWebGLBackend();
                        }
                        return null;
                    } finally {
                        if (predsT) try { tf.dispose(predsT); } catch(_){}
                        if (tY)     try { tf.dispose(tY);     } catch(_){}
                        if (tX)     try { tf.dispose(tX);     } catch(_){}
                        if (vY)     try { tf.dispose(vY);     } catch(_){}
                        if (vX)     try { tf.dispose(vX);     } catch(_){}
                        if (model)  try { tf.dispose(model);  } catch(_){}
                        await safeYield();
                    }
                };

                setAgentState(prev => ({ ...prev, running: true, trials: [], bestTrial: null, log: ['🤖 代理啟動，開始搜尋...'], currentTrial: 0, totalTrials: MAX_TRIALS }));
                setModelState(prev => ({ ...prev, isTraining: true }));

                const allTrials = [];
                let bestScore = METRIC === 'da' ? -Infinity : Infinity;
                let bestTrial = null;

                for (let t = 0; t < MAX_TRIALS; t++) {
                    if (agentStopRef.current) {
                        setAgentState(prev => ({ ...prev, log: [`⛔ 使用者中斷，已完成 ${t} / ${MAX_TRIALS} 試驗`, ...prev.log] }));
                        break;
                    }

                    // 每 TRIAL_GC_INTERVAL 次記憶體診斷與自動停止保護
                    trialsSinceGC++;
                    if (trialsSinceGC >= TRIAL_GC_INTERVAL) {
                        trialsSinceGC = 0;
                        await safeYield();
                        await new Promise(r => setTimeout(r, 100));
                        const mem = tf.memory();
                        const mbUsed = (mem.numBytes / 1e6).toFixed(1);
                        console.log(`[Agent] #${t+1}: tensors=${mem.numTensors}, MB=${mbUsed}`);
                        setAgentState(prev => ({ ...prev, log: [`🔍 記憶體: ${mem.numTensors} tensors, ${mbUsed}MB`, ...prev.log.slice(0,19)] }));
                        if (mem.numTensors > 2000 || parseFloat(mbUsed) > 800) {
                            setAgentState(prev => ({ ...prev, log: [`🛑 記憶體超限，自動停止防止崩潰`, ...prev.log.slice(0,19)] }));
                            agentStopRef.current = true;
                        }
                    }

                    const { trialParams, trialFactors } = generateTrial(t, allTrials);
                    const activeCount = Object.values(trialFactors).filter(Boolean).length;
                    const trialLabel  = `#${t+1} ${trialParams.algorithm} LB=${trialParams.lookback} L=${trialParams.layers} α=${trialParams.directionalLambda} 因子=${activeCount+1}`;

                    setAgentState(prev => ({ ...prev, currentTrial: t + 1,
                        log: [`⚙️ 試驗 ${trialLabel}`, ...prev.log.slice(0, 19)] }));

                    try {
                        const result = await runTrial(trialParams, trialFactors);
                        if (!result) {
                            setAgentState(prev => ({ ...prev, log: [`⚠️ #${t+1} 數據不足，跳過`, ...prev.log.slice(0,19)] }));
                            continue;
                        }

                        const trialRecord = { trialIdx: t+1, params: trialParams, factors: trialFactors, loss: result.loss, da: result.da, label: trialLabel };
                        allTrials.push(trialRecord);

                        const score = METRIC === 'da' ? result.da : result.loss;
                        const isBest = METRIC === 'da' ? score > bestScore : score < bestScore;

                        if (isBest) {
                            bestScore = score;
                            bestTrial = trialRecord;
                        }

                        setAgentState(prev => ({
                            ...prev,
                            trials: [...allTrials],
                            bestTrial,
                            log: [`✅ #${t+1} Loss=${result.loss.toFixed(4)} DA=${result.da.toFixed(1)}% ${isBest ? '⭐ 新最佳!' : ''}`,
                                  ...prev.log.slice(0, 19)]
                        }));

                    } catch(e) {
                        const em = e?.message?.slice(0,80) ?? String(e).slice(0,80);
                        setAgentState(prev => ({ ...prev, log: [`❌ #${t+1} 錯誤: ${em}`, ...prev.log.slice(0,19)] }));
                        if (isShaderError(e)) {
                            setAgentState(prev => ({ ...prev, log: ['⚠️ WebGL shader 錯誤，正在重設...', ...prev.log.slice(0,19)] }));
                            await resetWebGLBackend();
                        }
                    }

                    await new Promise(r => setTimeout(r, 300)); // 讓 GPU scheduler 喘息
                }

                // ── 完成：若有最佳試驗，提示套用 ──
                const doneMsg = bestTrial
                    ? `🏆 搜尋完成！最佳 #${bestTrial.trialIdx}: ${METRIC==='da'?'DA='+bestTrial.da.toFixed(1)+'%':'Loss='+bestTrial.loss.toFixed(4)} — 點擊「套用最佳參數」以套用`
                    : '🔍 搜尋完成，無有效試驗結果。';

                // 🔔 背景分頁時發送完成通知
                if (document.hidden) {
                    window.SilverNotify.send(
                        '🤖 AI 超參數搜尋完成',
                        bestTrial ? `最佳 DA=${bestTrial.da.toFixed(1)}%，返回查看排行榜` : '搜尋結束，無有效結果',
                        'slv-agent-done'
                    );
                }

                setAgentState(prev => ({ ...prev, running: false, log: [doneMsg, ...prev.log.slice(0,19)] }));
                setModelState(prev => ({ ...prev, isTraining: false }));
                agentRunningRef.current = false;

            }, [marketData, agentState, params, selectedFactors, createMultivariateDataset]);

            // 套用代理找到的最佳參數
            const applyAgentBest = useCallback(() => {
                if (!agentState.bestTrial) return;
                const { params: bp, factors: bf } = agentState.bestTrial;
                setParams(prev => ({ ...prev, ...bp, epochs: prev.epochs }));  // 保留完整 epochs
                setSelectedFactors(prev => ({ ...prev, ...bf }));
                setRunMode(RUN_MODE.TRAIN_CONTINUE);
                setAgentState(prev => ({ ...prev, log: ['✅ 最佳參數已套用至 UI，請確認後點擊「重新訓練」', ...prev.log.slice(0,19)] }));
            }, [agentState.bestTrial]);

            // ═══════════════════════════════════════════════════════
            // 🚀 一鍵智慧最佳化並預測
            // 流程: AI 快速搜尋最佳超參數 → 套用最佳 → 完整訓練 → 預測
            // ═══════════════════════════════════════════════════════
            const runAutoFullPipeline = useCallback(async () => {
                if (!marketData || modelState.isTraining || agentRunningRef.current) return;
                const _dqA = checkDataQuality(marketData, null);
                if (_dqA.blocked) { logsUpdater(`🛑 ${_dqA.reason}`); return; }
                const _mockFactorsA = marketData.dataQuality?.mockFactors || [];
                autoFlowStopRef.current  = false;
                agentRunningRef.current  = true;

                const TRIALS      = autoFlowTrials;
                const QUICK_EP    = autoFlowEpochs;
                const ALL_KEYS    = ['slvPrices','slvVolume','slvVolumeRoc','tipsYield','gtrend',
                                     'rsiPrices','rsiPrices12','vixRoc','fearGreed',
                                     'socialSentiment','goldPrices','oilPrices','dxyPrices',
                                     'vixPrices','tnxPrices','copperPrices','silverInventory',
                                     'goldSilverRatio','cotNetPosition',
                                     'priceROC','ma5Silver','ma20Silver','maCross','macdLine'];
                const ALGOS   = ['BiGRU','LSTM','Transformer','TimesFM'];
                const LOOKBACKS = [5,10,15,20,30];
                const LAYERS_OPT = [32,48,64,96];
                const BATCH_OPT  = [16,32,64];
                const LAMBDA_OPT = [0.1,0.2,0.3,0.5];
                const LR_OPT     = [0.001,0.002];
                const pick = arr => arr[Math.floor(Math.random() * arr.length)];

                setAutoFlowPhase('searching');
                // 搜尋開始時重設 localStorage best loss，確保後面完整訓練能正常存檔
                localStorage.setItem(BEST_MAPE_KEY, '100');
                setCurrentBestLoss(100);
                setModelState(prev => ({ ...prev, isTraining: true, progress: 0, logs: ['🔍 一鍵流程啟動：AI 快速超參數搜尋中...', ...prev.logs.slice(0,3)] }));

                let bestTrial  = null;
                let bestMetric = -Infinity;

                for (let t = 0; t < TRIALS; t++) {
                    if (autoFlowStopRef.current) break;
                    const trialParams = {
                        algorithm: pick(ALGOS), lookback: pick(LOOKBACKS),
                        layers: pick(LAYERS_OPT), batchSize: pick(BATCH_OPT),
                        directionalLambda: pick(LAMBDA_OPT), lr: pick(LR_OPT),
                        huberDelta: 1.0, validationMode: VALIDATION_MODE.NONE, kFolds: 1,
                    };
                    const trialFactors = {};
                    ALL_KEYS.forEach(k => { trialFactors[k] = !_mockFactorsA.includes(k) && Math.random() < 0.70; });

                    // Pre-trial memory check
                    const memAF = tf.memory();
                    if (memAF.numTensors > 2500) { logsUpdater('🛑 記憶體超限，停止'); autoFlowStopRef.current = true; break; }

                    const MAX_S = 520;
                    const dsAF = createMultivariateDataset(marketData, trialParams.lookback, trialFactors, MAX_S);
                    if (!dsAF.X || dsAF.X.length < 20 || dsAF.numFeatures === 0) continue;
                    // 時序切分：前段訓練、最後一段驗證，排名只看驗證段 DA
                    const spAF = dsAF.split;
                    if (!spAF || spAF.holdLen < 10) continue;
                    const afX  = dsAF.X;
                    const afY  = dsAF.y;
                    const afLB = afX[0]?.length ?? trialParams.lookback;
                    const afFt = afX[0]?.[0]?.length ?? dsAF.numFeatures;
                    const flatAFRange = (from, to) =>
                        [windowsToTensor(afX, from, to), tf.tensor2d(Float32Array.from(afY.slice(from, to)), [to - from, 1])];
                    let tM = null, xT = null, yT = null, xV = null, yV = null, pT = null;
                    try {
                        [xT, yT] = flatAFRange(0, spAF.trainEnd);
                        [xV, yV] = flatAFRange(spAF.holdStart, afX.length);
                        tM = createModel(trialParams.algorithm, afLB, afFt, trialParams);
                        tM.compile({ optimizer: tf.train.adam(trialParams.lr), loss: getDirectionalHuberLoss(trialParams.directionalLambda, trialParams.huberDelta) });
                        let bestL = Infinity, noImp = 0;
                        await tM.fit(xT, yT, { epochs: QUICK_EP, batchSize: 128, verbose: 0,
                            // 不打亂：DirectionalHuberLoss 依賴 batch 內相鄰樣本是相鄰交易日
                            shuffle: false,
                            validationData: [xV, yV],
                            callbacks: { onEpochEnd: async (ep, logs) => {
                                const L = logs?.val_loss ?? logs?.loss ?? Infinity;
                                if (L < bestL - 1e-6) { bestL = L; noImp = 0; } else noImp++;
                                if (noImp >= 6) tM.stopTraining = true;      // 早停（看驗證 loss）
                                if (ep % 5 === 4) await safeYield();          // 每5輪 yield
                            } } });
                        tf.dispose([xT, yT]); xT = null; yT = null;
                        pT = tM.predict(xV);
                        const pd = await pT.data();
                        tf.dispose(pT); pT = null; tf.dispose([xV, yV]); xV = null; yV = null;
                        // 驗證段 DA，以每個樣本的前一日實際價格為基準
                        let ok = 0, valid = 0;
                        for (let i = 0; i < pd.length; i++) {
                            const gi = spAF.holdStart + i;
                            const prevP = dsAF.prevPrices[gi];
                            const actP  = denormalizePrice(afY[gi], dsAF.minMax);
                            const predP = denormalizePrice(pd[i], dsAF.minMax);
                            if (!Number.isFinite(prevP)) continue;
                            if ((actP >= prevP) === (predP >= prevP)) ok++;
                            valid++;
                        }
                        const da = valid ? (ok / valid) * 100 : 0;
                        if (da > bestMetric) { bestMetric = da; bestTrial = { params: trialParams, factors: trialFactors, da, loss: 9999, trialIdx: t }; }
                        logsUpdater(`  ✓ #${t+1}/${TRIALS} ${trialParams.algorithm} LB=${trialParams.lookback}: DA=${da.toFixed(1)}%`);
                    } catch(e) {
                        const errMsg = e?.message?.slice(0,80) ?? String(e).slice(0,80);
                        logsUpdater(`  ✗ #${t+1}: ${errMsg}`);
                        if (isShaderError(e)) {
                            logsUpdater('⚠️ WebGL shader 錯誤，正在重設 GPU context...');
                            await resetWebGLBackend();
                        }
                    } finally {
                        if (pT) try { tf.dispose(pT); } catch(_){}
                        if (yT) try { tf.dispose(yT); } catch(_){}
                        if (xT) try { tf.dispose(xT); } catch(_){}
                        if (yV) try { tf.dispose(yV); } catch(_){}
                        if (xV) try { tf.dispose(xV); } catch(_){}
                        if (tM) try { tf.dispose(tM); } catch(_){}
                    }
                    setModelState(prev => ({ ...prev, progress: ((t+1)/TRIALS)*40 }));
                    await new Promise(r => setTimeout(r, 150)); // 讓 GPU scheduler 喘息
                }

                agentRunningRef.current = false;

                if (!bestTrial || autoFlowStopRef.current) {
                    setAutoFlowPhase('idle');
                    setModelState(prev => ({ ...prev, isTraining: false, progress: 0 }));
                    logsUpdater('⚠️ 一鍵流程已停止');
                    return;
                }

                // ── 套用最佳參數 ──
                setAutoFlowPhase('applying');
                logsUpdater(`✅ 最佳超參數: ${bestTrial.params.algorithm} LB=${bestTrial.params.lookback} DA=${bestTrial.da.toFixed(1)}%`);
                const fullBestParams = { ...params, ...bestTrial.params, epochs: 500 };

                // ⚠️ 關鍵修復：先重設 isTraining=false，否則 trainModel 第一行 guard 永遠 return
                // 搜尋階段開始時已設 isTraining:true，必須在觸發訓練前還原
                setModelState(prev => ({ ...prev, isTraining: false, progress: 0 }));
                setParams(fullBestParams);
                setSelectedFactors(prev => ({ ...prev, ...bestTrial.factors }));
                setRunMode(RUN_MODE.TRAIN_CONTINUE);

                // 等待 React 批次 re-render 完成，確保新 trainModel closure 已取得 isTraining=false
                await new Promise(r => setTimeout(r, 50));

                // ── 直接呼叫 trainModel（不依賴 useEffect，更可靠）──
                setAutoFlowPhase('training');
                logsUpdater('🏋️ 套用完成，啟動完整訓練（500 epochs）...');
                autoTrainTriggerRef.current = true;
                // 額外觸發 params 變動，確保 useEffect 偵測到
                setParams(p => ({ ...p }));

            }, [marketData, modelState.isTraining, autoFlowTrials, autoFlowEpochs, params, logsUpdater]);

            const stopAutoFlow = useCallback(() => {
                autoFlowStopRef.current  = true;
                agentStopRef.current     = true;
                agentRunningRef.current  = false;
                setAutoFlowPhase('idle');
                setModelState(prev => ({ ...prev, isTraining: false, progress: 0 }));
                logsUpdater('⛔ 一鍵流程已手動中止');
            }, [logsUpdater]);

            const getButtonText = () => {
                if (modelState.isTraining) {
                    if (runMode.includes('TRAIN')) return `訓練中... ${modelState.progress.toFixed(1)}%`;
                    return '預測中...';
                }
                switch (runMode) {
                    case RUN_MODE.TRAIN_CONTINUE: return '重新訓練模型';
                    case RUN_MODE.TRAIN_RESUME: return '接續訓練 (從最佳權重)';
                    case RUN_MODE.PREDICT_BEST: return `執行預測 T+${params.futureDays} 日 (使用歷史最佳)`;
                    case RUN_MODE.PREDICT_UPLOADED: return `執行預測 T+${params.futureDays} 日 (使用手動模型)`;
                    default: return '選擇模式並執行動作';
                }
            };

            if (fetchError) {
                return (
                    <div className="h-screen w-full flex items-center justify-center text-center p-8 bg-red-900/10 text-red-400">
                        <div className="glass-panel p-8 rounded-xl max-w-lg space-y-4">
                            <Icon name="alert-octagon" className="w-12 h-12 mx-auto text-red-500" />
                            <h2 className="text-xl font-semibold" style={{color:'var(--metal-bright)'}}>市場數據取得失敗</h2>
                            <p className="text-sm font-mono break-all" style={{color:'var(--down)'}}>{fetchError}</p>
                            <p className="text-sm" style={{color:'var(--metal-dim)'}}>
                                資料來源可能暫時無法連線，或代理伺服器達到使用上限。系統已切換為模擬數據以維持運作。
                                重試通常可解決；若持續失敗，清除快取後再試一次。
                            </p>
                            <div className="flex flex-col gap-2 items-center">
                                <button onClick={() => loadData(false)} disabled={isLoadingData} className="px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2"
                                    style={{background:'var(--metal-bright)', color:'#0B1014'}}>
                                    <Icon name="refresh-cw" className={`w-4 h-4 ${isLoadingData ? 'animate-spin' : ''}`} />重新載入數據
                                </button>
                                <button onClick={() => { MarketDataCache.clear(); loadData(false); }} disabled={isLoadingData} className="px-4 py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-2"
                                    style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal)'}}>
                                    <Icon name="trash-2" className="w-3 h-3" />清除快取並重新取得
                                </button>
                            </div>
                        </div>
                    </div>
                );
            }

            // 尚未載入且沒有在載入中 —— 進站畫面：先看到介面，由使用者決定何時取得資料
            if (!marketData && !isLoadingData) {
                return (
                    <div className="min-h-screen w-full flex items-center justify-center p-6" style={{background:'var(--ground)'}}>
                        <div className="w-full max-w-md">
                            <div className="flex items-center gap-3 mb-6">
                                <div className="flex items-center justify-center w-11 h-11 rounded-lg shrink-0"
                                     style={{background:'linear-gradient(160deg,#E8F0F5,#8FA5B3)', color:'#0B1014'}}>
                                    <span className="font-mono font-bold">Ag</span>
                                </div>
                                <div>
                                    <h1 className="text-lg font-semibold leading-tight" style={{color:'var(--metal-bright)'}}>白銀期貨預測</h1>
                                    <p className="text-xs leading-tight" style={{color:'var(--metal-faint)'}}>多因子深度學習 · 方向與價格預測</p>
                                </div>
                            </div>

                            <div className="glass-panel p-5">
                                <p className="text-sm mb-1" style={{color:'var(--metal)'}}>尚未載入市場資料</p>
                                <p className="text-xs mb-5 leading-relaxed" style={{color:'var(--metal-faint)'}}>
                                    {hasSnapshot
                                        ? '上次的資料仍在，可以立即開始操作，最新行情會在背景取得，到了就自動換上。'
                                        : '需要白銀、黃金、美元指數、TIPS 實質利率、COT 持倉等 20 餘項因子的歷史資料才能訓練模型。來源為 Stooq 與 Yahoo Finance，視網路狀況約需數十秒。'}
                                </p>

                                {hasSnapshot ? (
                                    <>
                                        <button onClick={loadFromSnapshotThenRefresh}
                                            className="w-full py-3 rounded-lg font-semibold text-sm flex items-center justify-center gap-2 transition-colors"
                                            style={{background:'var(--metal-bright)', color:'#0B1014'}}>
                                            <Icon name="zap" className="w-4 h-4" />
                                            立即開始（先用上次資料）
                                        </button>
                                        <button onClick={() => loadData(false)}
                                            className="w-full mt-2 py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-1.5"
                                            style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal)'}}>
                                            <Icon name="download" className="w-3 h-3" />等待最新資料再開始
                                        </button>
                                    </>
                                ) : (
                                    <button onClick={() => loadData(false)}
                                        className="w-full py-3 rounded-lg font-semibold text-sm flex items-center justify-center gap-2 transition-colors"
                                        style={{background:'var(--metal-bright)', color:'#0B1014'}}>
                                        <Icon name="download" className="w-4 h-4" />
                                        讀取歷史資料
                                    </button>
                                )}

                                <div className="flex gap-2 mt-2">
                                    <button onClick={() => { MarketDataCache.clear();
                                            AgentStore.tx('snapshot','readwrite', s => s.delete('latest'))
                                                .catch(()=>{}).finally(()=> setHasSnapshot(false));
                                            loadData(false); }}
                                        className="flex-1 py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-1.5"
                                        style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal)'}}>
                                        <Icon name="trash-2" className="w-3 h-3" />清除快取後讀取
                                    </button>
                                    <button onClick={() => loadData(true)}
                                        className="flex-1 py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-1.5"
                                        style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal)'}}>
                                        <Icon name="flask-conical" className="w-3 h-3" />用模擬資料試用
                                    </button>
                                </div>

                                <p className="text-xs mt-3" style={{color:'var(--metal-faint)'}}>
                                    模擬資料僅供操作介面，預測結果不具參考價值。
                                </p>
                            </div>
                        </div>
                    </div>
                );
            }

            if (!marketData && isLoadingData) {
                return (
                    <div className="h-screen w-full flex items-center justify-center flex-col gap-4">
                        <div className="flex items-center justify-center w-12 h-12 rounded-lg"
                             style={{background:'linear-gradient(160deg,#E8F0F5,#8FA5B3)', color:'#0B1014'}}>
                            <span className="font-mono font-bold">Ag</span>
                        </div>
                        <div className="text-center">
                            <p className="text-sm" style={{color:'var(--metal)'}}>正在取得市場數據</p>
                            <p className="text-xs mt-1" style={{color:'var(--metal-faint)'}}>白銀、黃金、美元指數與 20 餘項因子的五年歷史</p>
                        </div>
                        <div className="w-40 h-0.5 rounded-full overflow-hidden" style={{background:'var(--surface-3)'}}>
                            <div className="h-full w-1/3 rounded-full animate-pulse-slow" style={{background:'var(--metal-bright)'}}></div>
                        </div>
                        {loadElapsed >= 20 && (
                            <p className="text-xs" style={{color:'var(--metal-faint)'}}>
                                已等待 <span className="tabular">{loadElapsed}</span> 秒
                            </p>
                        )}
                        {/* 等太久時給一條退路，而不是讓人只能重整頁面 */}
                        {loadElapsed >= 20 && (
                            <button onClick={() => setIsLoadingData(false)}
                                className="text-xs px-3 py-1.5 rounded-lg transition-colors"
                                style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal-dim)'}}>
                                停止等待，返回
                            </button>
                        )}
                    </div>
                );
            }
            
            return (
                <div id="app" className="min-h-screen pb-12" style={{background:'var(--ground)', color:'var(--metal)'}}>
                    <header className="sticky top-0 z-10 border-b" style={{background:'rgba(11,16,20,0.88)', backdropFilter:'blur(14px)', borderColor:'var(--edge)'}}>
                        <div className="container mx-auto px-4 py-3 flex items-center justify-between gap-4">
                            {/* 品牌：純度鋼印 + 標題 */}
                            <div className="flex items-center gap-3 min-w-0">
                                <div className="flex items-center justify-center w-9 h-9 rounded-md shrink-0"
                                     style={{background:'linear-gradient(160deg,#E8F0F5,#8FA5B3)', color:'#0B1014'}}>
                                    <span className="font-mono font-bold text-sm">Ag</span>
                                </div>
                                <div className="min-w-0">
                                    <h1 className="text-base font-semibold leading-tight truncate" style={{color:'var(--metal-bright)'}}>
                                        白銀期貨預測
                                    </h1>
                                    <p className="text-xs leading-tight truncate" style={{color:'var(--metal-faint)'}}>
                                        {numFeatures} 項因子 · {params.algorithm}
                                    </p>
                                </div>
                            </div>

                            {/* 即時狀態：只在有事發生時出現 */}
                            <div className="flex items-center gap-2 shrink-0">
                                {modelState.isTraining && (
                                    <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md"
                                          style={{color:'var(--warn)', background:'rgba(217,164,65,0.1)', border:'1px solid rgba(217,164,65,0.28)'}}>
                                        <Icon name="loader-2" className="w-3 h-3 animate-spin" />
                                        <span className="tabular">{modelState.currentEpoch}/{modelState.totalEpochs}</span>
                                    </span>
                                )}
                                {!modelState.isTraining && modelState.directionalAccuracy !== 'N/A' && (
                                    <span className="hidden sm:flex items-baseline gap-1.5 text-xs px-2.5 py-1 rounded-md"
                                          style={{background:'var(--surface-2)', border:'1px solid var(--edge)'}}>
                                        <span style={{color:'var(--metal-faint)'}}>方向準確率</span>
                                        <span className="tabular font-semibold" style={{color:'var(--metal-bright)'}}>{modelState.directionalAccuracy}%</span>
                                    </span>
                                )}
                                {/* 資料新鮮度：只在「不是即時資料」時出現，平時不佔版面 */}
                                {isRefreshing && (
                                    <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md"
                                          style={{color:'var(--warn)', background:'rgba(217,164,65,0.1)', border:'1px solid rgba(217,164,65,0.28)'}}>
                                        <Icon name="refresh-cw" className="w-3 h-3 animate-spin" />
                                        <span className="hidden sm:inline">更新中</span>
                                    </span>
                                )}
                                {!isRefreshing && snapshotAt && (
                                    <span className="hidden sm:flex items-baseline gap-1.5 text-xs px-2.5 py-1 rounded-md"
                                          title={`資料擷取於 ${new Date(snapshotAt).toLocaleString()}`}
                                          style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal-faint)'}}>
                                        快照 {new Date(snapshotAt).toLocaleDateString()}
                                    </span>
                                )}

                                {/* 重新讀取歷史資料：主畫面隨時可用 */}
                                <button onClick={() => loadData(false, { background: !!rawData })}
                                    disabled={isLoadingData || isRefreshing || modelState.isTraining}
                                    title="重新讀取歷史資料"
                                    className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md transition-colors disabled:opacity-40"
                                    style={{background:'var(--surface-2)', border:'1px solid var(--edge)', color:'var(--metal)'}}>
                                    <Icon name="refresh-cw" className={`w-3.5 h-3.5 ${isLoadingData ? 'animate-spin' : ''}`} />
                                    <span className="hidden sm:inline">{isLoadingData ? '讀取中' : '重新讀取'}</span>
                                </button>
                                <span className="assay-mark hidden md:inline">{APP_VERSION}</span>
                            </div>
                        </div>
                    </header>
                    
                    <div className="container mx-auto p-4">

                        {/* ══════════════════════════════════════════════════════
                            🎯 逐日預測結果 Hero — 永遠置頂顯示
                            預測後立即出現，未預測時顯示等待提示
                        ══════════════════════════════════════════════════════ */}
                        {(() => {
                            const lastPrice = marketData?.prices?.[marketData.prices.length - 1];
                            const hasPred   = prediction && Array.isArray(prediction.futurePrices) && prediction.futurePrices.length > 0;

                            /* 計算每日方向與漲跌幅 */
                            const dayCards = hasPred ? prediction.futurePrices.map((price, idx) => {
                                const prevPrice = idx === 0 ? lastPrice : prediction.futurePrices[idx - 1];
                                const pct       = prevPrice ? ((price - prevPrice) / prevPrice) * 100 : 0;
                                const isUp      = pct >= 0;
                                return { price, pct, isUp, label: `T+${idx + 1}` };
                            }) : [];

                            const head = dayCards[0];
                            const rest = dayCards.slice(1);
                            const probs = hasPred && Array.isArray(prediction.futureDirProbs) ? prediction.futureDirProbs : [];
                            const daNum = hasPred ? parseFloat(prediction.directionalAccuracy) : NaN;

                            return (
                                <div className="glass-panel mb-4 overflow-hidden">

                                    {/* ── 量測列：基準與模型品質 ── */}
                                    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 py-2.5 border-b" style={{borderColor:'var(--edge)'}}>
                                        <div className="flex items-baseline gap-2">
                                            <span className="text-xs" style={{color:'var(--metal-faint)'}}>最新收盤</span>
                                            <span className="tabular text-sm" style={{color:'var(--metal-bright)'}}>
                                                {lastPrice ? '$' + lastPrice.toFixed(4) : '—'}
                                            </span>
                                        </div>
                                        {hasPred && (
                                            <div className="flex items-baseline gap-5 text-xs">
                                                <span className="flex items-baseline gap-1.5">
                                                    <span style={{color:'var(--metal-faint)'}}>方向準確率</span>
                                                    <span className="tabular font-semibold" style={{color: daNum >= 55 ? 'var(--up)' : 'var(--metal-bright)'}}>
                                                        {prediction.directionalAccuracy}%
                                                    </span>
                                                </span>
                                                <span className="flex items-baseline gap-1.5">
                                                    <span style={{color:'var(--metal-faint)'}}>平均誤差</span>
                                                    <span className="tabular font-semibold" style={{color:'var(--metal)'}}>{prediction.mape}%</span>
                                                </span>
                                                {modelState.kFoldMape !== 'N/A' && (
                                                    <span className="flex items-baseline gap-1.5">
                                                        <span style={{color:'var(--metal-faint)'}}>交叉驗證</span>
                                                        <span className="tabular font-semibold" style={{color:'var(--metal)'}}>{modelState.kFoldDa}%</span>
                                                    </span>
                                                )}
                                            </div>
                                        )}
                                    </div>

                                    {hasPred ? (
                                        <div className="assay-settle flex flex-col lg:flex-row">

                                            {/* ── 主判讀：明日方向。這是整個介面存在的理由 ── */}
                                            <div className="px-6 py-6 lg:w-[340px] lg:shrink-0 border-b lg:border-b-0 lg:border-r"
                                                 style={{borderColor:'var(--edge)', background: head.isUp ? 'var(--up-soft)' : 'var(--down-soft)'}}>
                                                <div className="flex items-baseline gap-2 mb-3">
                                                    <span className="text-xs" style={{color:'var(--metal-faint)'}}>明日 (T+1) 判讀</span>
                                                </div>
                                                <div className="flex items-center gap-3">
                                                    <Icon name={head.isUp ? 'trending-up' : 'trending-down'} className="w-9 h-9 shrink-0" style={{color: head.isUp ? 'var(--up)' : 'var(--down)'}} />
                                                    <div>
                                                        <div className="text-3xl font-semibold leading-none" style={{color: head.isUp ? 'var(--up)' : 'var(--down)'}}>
                                                            {head.isUp ? '看漲' : '看跌'}
                                                        </div>
                                                        <div className="tabular text-sm mt-1.5" style={{color: head.isUp ? 'var(--up)' : 'var(--down)'}}>
                                                            {head.isUp ? '+' : ''}{head.pct.toFixed(2)}%
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="tabular text-2xl mt-4" style={{color:'var(--metal-bright)'}}>
                                                    ${head.price.toFixed(4)}
                                                </div>
                                                {probs.length > 0 && (
                                                    <div className="mt-4">
                                                        <div className="flex justify-between text-xs mb-1.5">
                                                            <span style={{color:'var(--metal-faint)'}}>模型信心</span>
                                                            <span className="tabular" style={{color:'var(--metal)'}}>
                                                                {(Math.abs(probs[0] - 0.5) * 200).toFixed(0)}%
                                                            </span>
                                                        </div>
                                                        <div className="h-1 rounded-full overflow-hidden" style={{background:'var(--surface-3)'}}>
                                                            <div className="h-full rounded-full" style={{
                                                                width: (Math.abs(probs[0] - 0.5) * 200).toFixed(0) + '%',
                                                                background: head.isUp ? 'var(--up)' : 'var(--down)'
                                                            }}></div>
                                                        </div>
                                                    </div>
                                                )}
                                            </div>

                                            {/* ── 後續日：報價帶，數值右對齊便於縱向比較 ── */}
                                            <div className="flex-1 min-w-0">
                                                {rest.length === 0 ? (
                                                    <div className="h-full flex items-center px-6 py-6 text-sm" style={{color:'var(--metal-faint)'}}>
                                                        將「預測天數」調高可看到更長的路徑
                                                    </div>
                                                ) : (
                                                    <table className="w-full text-sm">
                                                        <tbody>
                                                        {rest.map(({ price, pct, isUp, label }, i) => (
                                                            <tr key={label} className="border-b last:border-b-0" style={{borderColor:'var(--edge)'}}>
                                                                <td className="pl-6 pr-3 py-3" style={{width:'56px', color:'var(--metal-faint)'}}>
                                                                    <span className="tabular text-xs">{label}</span>
                                                                </td>
                                                                <td className="py-3" style={{width:'56px'}}>
                                                                    <span className="inline-flex items-center gap-1.5" style={{color: isUp ? 'var(--up)' : 'var(--down)'}}>
                                                                        <Icon name={isUp ? 'arrow-up-right' : 'arrow-down-right'} className="w-3.5 h-3.5" />
                                                                        <span className="text-xs">{isUp ? '漲' : '跌'}</span>
                                                                    </span>
                                                                </td>
                                                                {/* 彈性間隔：把數值全部推到右側成為一個可縱向比較的數字欄 */}
                                                                <td className="py-3" style={{width:'100%'}}></td>
                                                                <td className="py-3 pr-4 text-right tabular text-xs" style={{whiteSpace:'nowrap', color: isUp ? 'var(--up)' : 'var(--down)'}}>
                                                                    {isUp ? '+' : ''}{pct.toFixed(2)}%
                                                                </td>
                                                                <td className="py-3 text-right tabular" style={{whiteSpace:'nowrap', color:'var(--metal-bright)'}}>
                                                                    ${price.toFixed(4)}
                                                                </td>
                                                                <td className="py-3 pl-4 pr-6" style={{width:'72px'}}>
                                                                    {probs.length > i + 1 && (
                                                                        <div className="h-1 rounded-full overflow-hidden" style={{background:'var(--surface-3)'}}>
                                                                            <div className="h-full rounded-full" style={{
                                                                                width: (Math.abs(probs[i+1] - 0.5) * 200).toFixed(0) + '%',
                                                                                background: isUp ? 'var(--up)' : 'var(--down)'
                                                                            }}></div>
                                                                        </div>
                                                                    )}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                        </tbody>
                                                    </table>
                                                )}
                                            </div>
                                        </div>
                                    ) : (
                                        /* 空狀態是一份邀請，不是一句抱歉 */
                                        <div className="px-6 py-10 text-center">
                                            <p className="text-sm mb-1" style={{color:'var(--metal)'}}>
                                                尚未有預測結果
                                            </p>
                                            <p className="text-xs" style={{color:'var(--metal-faint)'}}>
                                                在下方按「一鍵智慧流程」，系統會自動搜尋最佳參數、訓練並產出明日判讀
                                            </p>
                                        </div>
                                    )}
                                </div>
                            );
                        })()}

                        {/* 🔑 歷史最佳設定自動還原 Banner */}
                        {restoredFromSaved && (
                            <div className="bg-emerald-900/30 border border-emerald-500/50 rounded-xl p-4 mb-4 flex flex-wrap items-start gap-3">
                                <Icon name="check-circle" className="w-5 h-5 text-emerald-400 mt-0.5 flex-shrink-0" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-bold text-emerald-300">✅ 已自動還原歷史最佳權重設定</p>
                                    <div className="mt-1 flex flex-wrap gap-2 text-xs text-emerald-200/80">
                                        <span className="px-2 py-0.5 bg-emerald-900/50 rounded">演算法：{params.algorithm}</span>
                                        <span className="px-2 py-0.5 bg-emerald-900/50 rounded">Lookback：{params.lookback}</span>
                                        <span className="px-2 py-0.5 bg-emerald-900/50 rounded">Layers：{params.layers}</span>
                                        <span className="px-2 py-0.5 bg-emerald-900/50 rounded">特徵數：{numFeatures}</span>
                                        <span className="px-2 py-0.5 bg-emerald-900/50 rounded">DirectionalHuber α：{params.directionalLambda}</span>
                                        <span className="px-2 py-0.5 bg-emerald-900/50 rounded font-bold">模式：僅預測 (歷史最佳)</span>
                                    </div>
                                    <p className="text-xs text-emerald-400/60 mt-1">所有參數與特徵選擇已同步為訓練時配置，可直接點擊「執行預測 T+N 日」。</p>
                                </div>
                            </div>
                        )}

                        <AgentMemoryPanel agentModels={agentModels} agentRuns={agentRuns} agentPerf={agentPerf} setAgentTick={setAgentTick} logsUpdater={logsUpdater} />

                        {/* 🤖 AI 自動搜尋代理面板 */}
                        <div className="glass-panel mb-4">
                            {/* Header bar — 永遠顯示 */}
                            <div
                                className="flex items-center justify-between px-5 py-3 cursor-pointer select-none"
                                onClick={() => setAgentPanelOpen(p => !p)}
                            >
                                <div className="flex items-center gap-2">
                                    <Icon name="cpu" className="w-5 h-5" style={{color:'var(--metal-dim)'}} />
                                    <span className="font-semibold text-base" style={{color:'var(--metal-bright)'}}>超參數搜尋</span>
                                    {agentState.running && (
                                        <span className="ml-2 text-xs px-2 py-0.5 rounded-full animate-pulse-slow" style={{background:'rgba(217,164,65,0.15)', color:'var(--warn)'}}>
                                            搜尋中 {agentState.currentTrial}/{agentState.totalTrials}
                                        </span>
                                    )}
                                    {agentState.bestTrial && !agentState.running && (
                                        <span className="ml-2 text-xs px-2 py-0.5 bg-emerald-700/60 text-emerald-200 rounded-full">
                                            ⭐ 已找到最佳: {agentState.metric==='da'?'DA='+agentState.bestTrial.da.toFixed(1)+'%':'Loss='+agentState.bestTrial.loss.toFixed(4)}
                                        </span>
                                    )}
                                </div>
                                <Icon name={agentPanelOpen ? 'chevron-up' : 'chevron-down'} className="w-4 h-4" style={{color:'var(--metal-dim)'}} />
                            </div>

                            {/* Expandable body */}
                            {agentPanelOpen && (
                                <div className="border-t px-5 py-4 space-y-4" style={{borderColor:'var(--edge)'}}>

                                    {/* ── 搜尋設定 ── */}
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                                        <div>
                                            <label className="text-xs text-zinc-400 block mb-1">搜尋策略</label>
                                            <select
                                                value={agentState.strategy}
                                                onChange={e => setAgentState(p => ({...p, strategy: e.target.value}))}
                                                disabled={agentState.running}
                                                className="w-full px-2 py-1.5 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100 text-xs"
                                            >
                                                <option value="random">隨機採樣 (Random)</option>
                                                <option value="grid">固定網格 (Grid)</option>
                                                <option value="bayesian">貝葉斯引導 (Bayesian)</option>
                                            </select>
                                        </div>
                                        <div>
                                            <label className="text-xs text-zinc-400 block mb-1">試驗次數</label>
                                            <select
                                                value={agentState.maxTrials}
                                                onChange={e => setAgentState(p => ({...p, maxTrials: parseInt(e.target.value)}))}
                                                disabled={agentState.running}
                                                className="w-full px-2 py-1.5 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100 text-xs"
                                            >
                                                {[5,10,15,20,30,50,75,100].map(n => (
                                                    <option key={n} value={n}>{n} 次</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div>
                                            <label className="text-xs text-zinc-400 block mb-1">每次快速 Epochs</label>
                                            <select
                                                value={agentState.searchEpochs}
                                                onChange={e => setAgentState(p => ({...p, searchEpochs: parseInt(e.target.value)}))}
                                                disabled={agentState.running}
                                                className="w-full px-2 py-1.5 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100 text-xs"
                                            >
                                                {[20,30,40,50,60,80,100,150,200].map(n => (
                                                    <option key={n} value={n}>{n} epochs</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div>
                                            <label className="text-xs text-zinc-400 block mb-1">最佳化目標</label>
                                            <select
                                                value={agentState.metric}
                                                onChange={e => setAgentState(p => ({...p, metric: e.target.value}))}
                                                disabled={agentState.running}
                                                className="w-full px-2 py-1.5 rounded-lg bg-zinc-900/60 border border-zinc-700 text-zinc-100 text-xs"
                                            >
                                                <option value="loss">最小化 Loss</option>
                                                <option value="da">最大化方向準確率 (DA)</option>
                                            </select>
                                        </div>
                                    </div>

                                    {/* ── 控制按鈕 ── */}
                                    <div className="flex flex-wrap gap-2">
                                        <button
                                            onClick={runAgentSearch}
                                            disabled={agentState.running || modelState.isTraining || !marketData}
                                            className={`px-4 py-2 rounded-xl font-bold text-sm transition-all flex items-center gap-2
                                                ${agentState.running || modelState.isTraining || !marketData
                                                    ? 'bg-zinc-700 text-zinc-500 cursor-not-allowed'
                                                    : 'bg-violet-600 hover:bg-violet-500 text-white'}`}
                                        >
                                            <Icon name="play" className="w-4 h-4" />
                                            {agentState.running ? '搜尋中...' : '🚀 啟動自動搜尋'}
                                        </button>
                                        {agentState.running && (
                                            <button
                                                onClick={() => { agentStopRef.current = true; }}
                                                className="px-4 py-2 rounded-xl font-bold text-sm bg-rose-700 hover:bg-rose-600 text-white transition-all flex items-center gap-2"
                                            >
                                                <Icon name="square" className="w-4 h-4" /> 中止搜尋
                                            </button>
                                        )}
                                        {agentState.bestTrial && !agentState.running && (
                                            <button
                                                onClick={applyAgentBest}
                                                className="px-4 py-2 rounded-xl font-bold text-sm bg-emerald-600 hover:bg-emerald-500 text-white transition-all flex items-center gap-2"
                                            >
                                                <Icon name="check" className="w-4 h-4" /> 套用最佳參數至 UI
                                            </button>
                                        )}
                                        {agentState.trials.length > 0 && (
                                            <button
                                                onClick={() => setAgentState(p => ({...p, trials:[], bestTrial:null, log:[], currentTrial:0}))}
                                                disabled={agentState.running}
                                                className="px-3 py-2 rounded-xl text-sm bg-zinc-700 hover:bg-zinc-600 text-zinc-300 transition-all"
                                            >
                                                清除紀錄
                                            </button>
                                        )}
                                    </div>

                                    {/* ── 進度條 ── */}
                                    {agentState.running && (
                                        <div>
                                            <div className="flex justify-between text-xs text-zinc-400 mb-1">
                                                <span>進度：試驗 {agentState.currentTrial} / {agentState.totalTrials}</span>
                                                <span>{((agentState.currentTrial / agentState.totalTrials) * 100).toFixed(0)}%</span>
                                            </div>
                                            <div className="w-full bg-zinc-700 h-2 rounded-full">
                                                <div
                                                    className="h-full rounded-full transition-all duration-500"
                                                    style={{width: `${(agentState.currentTrial / agentState.totalTrials) * 100}%`, background:'var(--metal-bright)'}}
                                                ></div>
                                            </div>
                                        </div>
                                    )}

                                    {/* ── 試驗結果排行榜 ── */}
                                    {agentState.trials.length > 0 && (
                                        <div>
                                            <div className="text-xs font-semibold text-zinc-400 mb-2">
                                                試驗排行榜（依 {agentState.metric === 'da' ? 'DA%' : 'Loss'} 排序，共 {agentState.trials.length} 筆）
                                            </div>
                                            <div className="overflow-x-auto rounded-lg border border-zinc-700">
                                                <table className="w-full text-xs">
                                                    <thead>
                                                        <tr className="bg-zinc-800 text-zinc-400">
                                                            <th className="px-2 py-1.5 text-left">#</th>
                                                            <th className="px-2 py-1.5 text-left">演算法</th>
                                                            <th className="px-2 py-1.5 text-right">LB</th>
                                                            <th className="px-2 py-1.5 text-right">Units</th>
                                                            <th className="px-2 py-1.5 text-right">α</th>
                                                            <th className="px-2 py-1.5 text-right">LR</th>
                                                            <th className="px-2 py-1.5 text-right">因子</th>
                                                            <th className="px-2 py-1.5 text-right">Loss</th>
                                                            <th className="px-2 py-1.5 text-right">DA%</th>
                                                            <th className="px-2 py-1.5 text-center">操作</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {[...agentState.trials]
                                                            .sort((a,b) => agentState.metric==='da'?(b.da||0)-(a.da||0):(a.loss||999)-(b.loss||999))
                                                            .slice(0, 15)
                                                            .map((tr, ri) => {
                                                            const isBest = agentState.bestTrial?.trialIdx === tr.trialIdx;
                                                            const factorCount = 1 + Object.values(tr.factors).filter(Boolean).length;
                                                            return (
                                                                <tr key={tr.trialIdx} className={`border-t border-zinc-700/50 ${isBest ? 'bg-emerald-950/40' : ri % 2 === 0 ? 'bg-zinc-900/20' : ''}`}>
                                                                    <td className="px-2 py-1.5">
                                                                        {isBest ? <span className="text-amber-400 font-bold">⭐</span> : <span className="text-zinc-500">{ri+1}</span>}
                                                                    </td>
                                                                    <td className="px-2 py-1.5 font-mono text-violet-300">{tr.params.algorithm}</td>
                                                                    <td className="px-2 py-1.5 text-right font-mono">{tr.params.lookback}</td>
                                                                    <td className="px-2 py-1.5 text-right font-mono">{tr.params.layers}</td>
                                                                    <td className="px-2 py-1.5 text-right font-mono">{tr.params.directionalLambda}</td>
                                                                    <td className="px-2 py-1.5 text-right font-mono text-xs">{tr.params.lr}</td>
                                                                    <td className="px-2 py-1.5 text-right">{factorCount}</td>
                                                                    <td className="px-2 py-1.5 text-right font-mono text-yellow-300">{tr.loss.toFixed(4)}</td>
                                                                    <td className={`px-2 py-1.5 text-right font-mono ${tr.da >= 55 ? 'text-emerald-400' : 'text-rose-400'}`}>{tr.da.toFixed(1)}</td>
                                                                    <td className="px-2 py-1.5 text-center">
                                                                        <button
                                                                            onClick={() => {
                                                                                setParams(prev => ({...prev, ...tr.params, epochs: prev.epochs}));
                                                                                setSelectedFactors(prev => ({...prev, ...tr.factors}));
                                                                                setRunMode(RUN_MODE.TRAIN_CONTINUE);
                                                                            }}
                                                                            className="px-2 py-0.5 rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-200 text-xs"
                                                                        >套用</button>
                                                                    </td>
                                                                </tr>
                                                            );
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}

                                    {/* ── 即時日誌 ── */}
                                    {agentState.log.length > 0 && (
                                        <div>
                                            <div className="text-xs font-semibold text-zinc-400 mb-1">代理日誌</div>
                                            <div className="bg-zinc-950/60 rounded-lg p-3 max-h-36 overflow-y-auto font-mono text-xs space-y-0.5">
                                                {agentState.log.map((line, i) => (
                                                    <div key={i} className={`${line.startsWith('⭐')||line.startsWith('🏆') ? 'text-amber-300' : line.startsWith('❌')||line.startsWith('⛔') ? 'text-rose-400' : line.startsWith('✅') ? 'text-emerald-400' : 'text-zinc-400'}`}>{line}</div>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {/* ── 搜尋空間說明 ── */}
                                    <details className="text-xs text-zinc-500">
                                        <summary className="cursor-pointer hover:text-zinc-300">📋 搜尋空間 & 策略說明</summary>
                                        <div className="mt-2 space-y-1 pl-2 border-l border-zinc-700">
                                            <div><span className="text-zinc-300">演算法：</span>BiGRU / LSTM / Transformer / TimesFM</div>
                                            <div><span className="text-zinc-300">Lookback：</span>5 / 10 / 15 / 20 / 30 / 40 / 60</div>
                                            <div><span className="text-zinc-300">Units：</span>16 / 32 / 48 / 64 / 96 / 128</div>
                                            <div><span className="text-zinc-300">α (方向權重)：</span>0.0 ~ 0.7</div>
                                            <div><span className="text-zinc-300">LR：</span>0.0005 / 0.001 / 0.002 / 0.003</div>
                                            <div><span className="text-zinc-300">因子：</span>23 個因子各以機率採樣（僅 Random/Bayesian）</div>
                                            <div className="pt-1 border-t border-zinc-800"><span className="text-violet-300">Random：</span>完全隨機採樣，探索性最強</div>
                                            <div><span className="text-violet-300">Grid：</span>掃描 9 種固定組合，可重現</div>
                                            <div><span className="text-violet-300">Bayesian：</span>前 4 次隨機後，以最佳結果為基礎引導採樣</div>
                                        </div>
                                    </details>
                                </div>
                            )}
                        </div>

                        {/* 🏆 歷史最佳表現儀表板 */}
                        <div className="bg-zinc-800 p-4 rounded-xl shadow-2xl mb-6 border border-amber-500/50">
                            <h3 className="text-xl font-bold mb-4 text-amber-300 flex items-center">
                                <Icon name="trophy" className="w-6 h-6 mr-2" />
                                <span>歷史最佳表現 (Historical Best)</span>
                            </h3>
                            <div className="flex justify-around space-x-4">
                                <div className="text-center p-3 bg-zinc-700 rounded-lg flex-1">
                                    <p className="text-sm text-zinc-400">當前訓練最低 Loss</p>
                                    <p className="text-3xl font-mono font-extrabold text-white mt-1">
                                        {bestLossValue !== null ? bestLossValue.toFixed(4) : 'N/A'}
                                    </p>
                                </div>
                                <div className="text-center p-3 bg-zinc-700 rounded-lg flex-1">
                                    <p className="text-sm text-zinc-400">歷史最佳模型儲存 Loss</p>
                                    <p className="text-3xl font-mono font-extrabold text-white mt-1">
                                        {currentBestLoss < 100 ? currentBestLoss.toFixed(4) : 'N/A'}
                                    </p>
                                </div>
                            </div>
                        </div>


                        {/* 控制面板 (Control Panel) */}
                        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                            
                            
                            <CotStatusPanel cotStatus={cotStatus} />

                            <ProxySettingsPanel logsUpdater={logsUpdater} />

                            <StockTwitsPanel stockTwitsStatus={stockTwitsStatus} loadData={loadData} isLoadingData={isLoadingData} />

                            <FearGreedPanel fearGreedStatus={fearGreedStatus} loadData={loadData} isLoadingData={isLoadingData} />

                            {/* 模式切換與執行 */}
                            
                            <BacktestPanel backtestConfig={backtestConfig} setBacktestConfig={setBacktestConfig} backtestResult={backtestResult} runBacktest={runBacktest} />

<div className="glass-panel rounded-xl shadow-accent-900/20 flex flex-col overflow-hidden">

                                {/* ══ 一鍵智慧區 ══ */}
                                <div className="p-4 border-b" style={{borderColor:'var(--edge)', background:'var(--surface-2)'}}>
                                    <div className="flex items-baseline gap-2 mb-3 flex-wrap">
                                        <span className="font-semibold text-base" style={{color:'var(--metal-bright)'}}>一鍵智慧流程</span>
                                        <span className="text-xs" style={{color:'var(--metal-faint)'}}>搜尋最佳參數、訓練模型，然後產出預測</span>
                                    </div>

                                    {/* Phase 指示器 */}
                                    {(() => {
                                        const phases = [
                                            { id:'searching', icon:'search',        label:'搜尋參數' },
                                            { id:'applying',  icon:'check-circle',  label:'套用最佳' },
                                            { id:'training',  icon:'activity',      label:'訓練模型' },
                                            { id:'done',      icon:'zap',           label:'完成預測' },
                                        ];
                                        const activeIdx = phases.findIndex(p => p.id === autoFlowPhase);
                                        return (
                                            <div className="flex items-center gap-0 mb-3">
                                                {phases.map((ph, i) => {
                                                    const isDone    = activeIdx > i;
                                                    const isActive  = activeIdx === i;
                                                    const isPending = activeIdx < i;
                                                    return (
                                                        <React.Fragment key={ph.id}>
                                                            <div className={`flex flex-col items-center gap-0.5 flex-1 transition-all ${isPending ? 'opacity-30' : ''}`}>
                                                                <div className="w-7 h-7 rounded-full flex items-center justify-center transition-all"
                                                                     style={{
                                                                        background: isDone ? 'var(--up)' : isActive ? 'var(--metal-bright)' : 'var(--surface-3)',
                                                                        border: '1px solid ' + (isDone ? 'var(--up)' : isActive ? 'var(--metal-bright)' : 'var(--edge)'),
                                                                        color: (isDone || isActive) ? '#0B1014' : 'var(--metal-faint)'
                                                                     }}>
                                                                    <Icon name={isDone ? 'check' : ph.icon} className="w-3.5 h-3.5" />
                                                                </div>
                                                                <span className="text-xs font-medium"
                                                                      style={{color: isActive ? 'var(--metal-bright)' : isDone ? 'var(--up)' : 'var(--metal-faint)'}}>{ph.label}</span>
                                                            </div>
                                                            {i < phases.length - 1 && (
                                                                <div className="h-px flex-1 mb-4 transition-all" style={{background: isDone ? 'var(--up)' : 'var(--edge)'}}></div>
                                                            )}
                                                        </React.Fragment>
                                                    );
                                                })}
                                            </div>
                                        );
                                    })()}

                                    {/* 快速設定 */}
                                    <div className="grid grid-cols-2 gap-2 mb-3">
                                        <div>
                                            <label className="text-xs text-zinc-400 block mb-1">搜尋試驗次數</label>
                                            <select value={autoFlowTrials} onChange={e => setAutoFlowTrials(+e.target.value)} disabled={autoFlowPhase !== 'idle'} className="w-full px-2 py-1.5 rounded-lg bg-zinc-900/70 border border-zinc-600 text-zinc-100 text-xs">
                                                {[5,10,15,20,30].map(n => <option key={n} value={n}>{n} 次</option>)}
                                            </select>
                                        </div>
                                        <div>
                                            <label className="text-xs text-zinc-400 block mb-1">每次快速 Epochs</label>
                                            <select value={autoFlowEpochs} onChange={e => setAutoFlowEpochs(+e.target.value)} disabled={autoFlowPhase !== 'idle'} className="w-full px-2 py-1.5 rounded-lg bg-zinc-900/70 border border-zinc-600 text-zinc-100 text-xs">
                                                {[20,30,40,50,60].map(n => <option key={n} value={n}>{n} epochs</option>)}
                                            </select>
                                        </div>
                                    </div>

                                    {/* 主按鈕 */}
                                    {autoFlowPhase === 'idle' ? (
                                        <button
                                            onClick={runAutoFullPipeline}
                                            disabled={!marketData || modelState.isTraining || isLoadingData}
                                            className="w-full py-3.5 rounded-xl font-bold text-base transition-all duration-300 flex items-center justify-center gap-2 bg-gradient-to-r from-violet-600 to-teal-600 hover:from-violet-500 hover:to-teal-500 text-white shadow-lg disabled:opacity-40 disabled:cursor-not-allowed"
                                        >
                                            <Icon name="sparkles" className="w-5 h-5" />
                                            一鍵智慧最佳化並預測
                                        </button>
                                    ) : (
                                        <div className="space-y-2">
                                            {/* 階段標籤 */}
                                            <div className="flex items-center justify-between text-xs">
                                                <span className={`font-semibold ${autoFlowPhase === 'searching' ? 'text-violet-300' : autoFlowPhase === 'training' ? 'text-teal-300' : autoFlowPhase === 'done' ? 'text-emerald-400' : 'text-zinc-400'}`}>
                                                    {autoFlowPhase === 'searching' && '🔍 搜尋最佳超參數中…'}
                                                    {autoFlowPhase === 'applying'  && '✅ 套用最佳參數…'}
                                                    {autoFlowPhase === 'training'  && '🏋️ 完整訓練中…'}
                                                    {autoFlowPhase === 'done'      && '🎉 完成！'}
                                                </span>
                                                <span className="font-mono text-zinc-300 font-bold">{Math.min(100, modelState.progress).toFixed(1)}%</span>
                                            </div>

                                            {/* 進度條（訓練時較粗，方便看） */}
                                            <div className="relative w-full bg-zinc-800 rounded-lg overflow-hidden"
                                                 style={{height: (autoFlowPhase === 'training' || autoFlowPhase === 'done') ? '28px' : '10px'}}>
                                                <div className="h-full rounded-lg bg-gradient-to-r from-violet-500 to-teal-500 transition-all duration-300"
                                                     style={{width: `${Math.min(100, modelState.progress)}%`}}></div>
                                                {/* Epoch 文字直接疊在條上 */}
                                                {(autoFlowPhase === 'training' || autoFlowPhase === 'done') && (
                                                    <div className="absolute inset-0 flex items-center justify-center gap-3 text-xs font-mono font-bold text-white drop-shadow">
                                                        <span>
                                                            Epoch&nbsp;
                                                            <span className="text-yellow-300">{modelState.currentEpoch}</span>
                                                            <span className="text-zinc-300 font-normal"> / {modelState.totalEpochs || 500}</span>
                                                        </span>
                                                        {modelState.loss !== 'N/A' && (
                                                            <span className="text-zinc-200">
                                                                Loss&nbsp;<span className="text-yellow-300">{parseFloat(modelState.loss).toFixed(4)}</span>
                                                            </span>
                                                        )}
                                                        {modelState.loss !== 'N/A' && currentBestLoss < 100 && (
                                                            <span className="text-zinc-200">
                                                                Best&nbsp;<span className="text-emerald-300">{currentBestLoss.toFixed(4)}</span>
                                                            </span>
                                                        )}
                                                    </div>
                                                )}
                                            </div>

                                            {/* 停止 / 完成 按鈕 */}
                                            {autoFlowPhase === 'done' ? (
                                                <button onClick={() => { setAutoFlowPhase('idle'); setModelState(prev=>({...prev, progress:0})); }}
                                                    className="w-full py-2.5 rounded-xl font-bold text-sm flex items-center justify-center gap-2 bg-emerald-700 hover:bg-emerald-600 text-white transition-colors">
                                                    <Icon name="check" className="w-4 h-4" /> 完成，關閉進度
                                                </button>
                                            ) : (
                                                <button onClick={stopAutoFlow} className="w-full py-2.5 rounded-xl font-bold text-sm flex items-center justify-center gap-2 bg-rose-700 hover:bg-rose-600 text-white transition-colors">
                                                    <Icon name="square" className="w-4 h-4" /> 停止流程
                                                </button>
                                            )}
                                        </div>
                                    )}
                                </div>

                                {/* ══ 背景執行控制列 ══ */}
                                <div className="px-4 py-2 border-t border-zinc-800 flex items-center gap-3 bg-zinc-900/30">
                                    {/* 背景執行開關 */}
                                    <button
                                        onClick={async () => {
                                            if (!bgEnabled) {
                                                // Request notification permission when enabling
                                                const ok = await window.SilverNotify.init();
                                                setNotifyEnabled(ok);
                                                setBgEnabled(true);
                                                setBgStatus('idle');
                                            } else {
                                                setBgEnabled(false);
                                                window.SilverWakeLock.release();
                                            }
                                        }}
                                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all border ${bgEnabled ? 'border-teal-500 bg-teal-900/30 text-teal-300' : 'border-zinc-700 bg-zinc-800/40 text-zinc-400 hover:border-zinc-600'}`}
                                    >
                                        <Icon name={bgEnabled ? 'wifi' : 'wifi-off'} className="w-3.5 h-3.5" />
                                        {bgEnabled ? '背景執行中' : '啟用背景執行'}
                                    </button>

                                    {/* 通知狀態 */}
                                    {bgEnabled && (
                                        <span className={`text-xs flex items-center gap-1 ${notifyEnabled ? 'text-emerald-400' : 'text-yellow-400'}`}>
                                            <Icon name={notifyEnabled ? 'bell' : 'bell-off'} className="w-3 h-3" />
                                            {notifyEnabled ? '通知已開啟' : '通知被拒絕'}
                                        </span>
                                    )}

                                    {/* 執行狀態指示 */}
                                    {bgEnabled && bgStatus === 'active' && (
                                        <span className="ml-auto text-xs text-teal-300 animate-pulse flex items-center gap-1">
                                            <Icon name="loader-2" className="w-3 h-3 animate-spin" />
                                            訓練中（請保持螢幕開啟）
                                        </span>
                                    )}
                                    {bgEnabled && bgStatus === 'done' && (
                                        <span className="ml-auto text-xs text-emerald-400 flex items-center gap-1">
                                            <Icon name="check-circle" className="w-3 h-3" />
                                            已完成
                                        </span>
                                    )}
                                </div>

                                {/* ══ 手動模式 ══ */}
                                <div className="p-4 space-y-3">
                                    <div className="text-xs text-zinc-500 font-semibold uppercase tracking-wider border-b border-zinc-800 pb-2">── 手動分步操作 ──</div>

                                    {/* 三個快速模式按鈕 */}
                                    <div className="grid grid-cols-3 gap-2">
                                        <button
                                            onClick={() => { setRunMode(RUN_MODE.TRAIN_CONTINUE); }}
                                            className={`py-2 px-1 rounded-lg text-xs font-semibold text-center transition-all border ${runMode === RUN_MODE.TRAIN_CONTINUE ? 'border-violet-500 bg-violet-900/40 text-violet-200' : 'border-zinc-700 bg-zinc-800/40 text-zinc-400 hover:border-zinc-600'}`}
                                        >
                                            <Icon name="refresh-cw" className="w-3.5 h-3.5 mx-auto mb-0.5" />
                                            重新訓練
                                        </button>
                                        <button
                                            onClick={() => { setRunMode(RUN_MODE.TRAIN_RESUME); }}
                                            className={`py-2 px-1 rounded-lg text-xs font-semibold text-center transition-all border ${runMode === RUN_MODE.TRAIN_RESUME ? 'border-violet-500 bg-violet-900/40 text-violet-200' : 'border-zinc-700 bg-zinc-800/40 text-zinc-400 hover:border-zinc-600'}`}
                                        >
                                            <Icon name="fast-forward" className="w-3.5 h-3.5 mx-auto mb-0.5" />
                                            接續訓練
                                        </button>
                                        <button
                                            onClick={() => { setRunMode(RUN_MODE.PREDICT_BEST); }}
                                            className={`py-2 px-1 rounded-lg text-xs font-semibold text-center transition-all border ${runMode === RUN_MODE.PREDICT_BEST || runMode === RUN_MODE.PREDICT_UPLOADED ? 'border-emerald-500 bg-emerald-900/30 text-emerald-200' : 'border-zinc-700 bg-zinc-800/40 text-zinc-400 hover:border-zinc-600'}`}
                                        >
                                            <Icon name="zap" className="w-3.5 h-3.5 mx-auto mb-0.5" />
                                            執行預測
                                        </button>
                                    </div>

                                    {/* 手動上傳模型 (collapsible hint) */}
                                    {(runMode === RUN_MODE.PREDICT_BEST || runMode === RUN_MODE.PREDICT_UPLOADED) && (
                                        <div className="p-3 rounded-lg bg-zinc-900/50 border border-zinc-700 space-y-2">
                                            <p className="text-xs text-zinc-400">
                                                {restoredFromSaved ? '✅ 已自動載入歷史最佳模型設定' : '尚無歷史最佳模型，請先訓練'}
                                            </p>
                                            <label className="text-xs text-zinc-500 block">或上傳外部模型 (.json + .bin)</label>
                                            <input type="file" multiple onChange={handleUploadModel} className="text-zinc-400 w-full text-xs" accept=".json,.bin"/>
                                            <p className={`text-xs font-mono ${uploadStatus.type === 'error' ? 'text-red-400' : uploadStatus.type === 'success' ? 'text-emerald-400' : 'text-zinc-500'}`}>{uploadStatus.message}</p>
                                        </div>
                                    )}

                                    {/* 執行按鈕 */}
                                    <button
                                        onClick={trainModel}
                                        disabled={modelState.isTraining || isLoadingData || (!marketData && !fetchError) || autoFlowPhase !== 'idle'}
                                        className={`w-full px-4 py-3 rounded-xl font-bold transition-all duration-300 flex items-center justify-center gap-2
                                            ${modelState.isTraining
                                                ? 'bg-zinc-800 text-zinc-500 cursor-wait'
                                                : runMode === RUN_MODE.TRAIN_CONTINUE || runMode === RUN_MODE.TRAIN_RESUME
                                                    ? 'bg-violet-700 hover:bg-violet-600 text-white'
                                                    : 'bg-emerald-700 hover:bg-emerald-600 text-white'}`}
                                    >
                                        {modelState.isTraining
                                            ? <><Icon name="loader-2" className="animate-spin w-4 h-4" /> {getButtonText()}</>
                                            : <><Icon name={runMode.includes('TRAIN') ? 'play' : 'zap'} className="w-4 h-4" /> {getButtonText()}</>
                                        }
                                    </button>

                                    {/* 訓練日誌 */}
                                    <div className="h-20 overflow-y-auto font-mono text-xs text-zinc-500 bg-zinc-900 p-2 rounded-lg border border-zinc-700">
                                        {modelState.logs.map((log, i) => <div key={i} className="whitespace-pre-wrap">{log}</div>)}
                                    </div>

                                    {/* 工具按鈕列 */}
                                    <div className="flex gap-2">
                                        <button onClick={() => exportModel(logsUpdater)} className="flex-1 px-3 py-1.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-xs font-medium text-white transition-colors flex items-center justify-center gap-1">
                                            <Icon name="download" className="w-3 h-3" /> 下載模型
                                        </button>
                                        <button onClick={handleClearBest} className="flex-1 px-3 py-1.5 rounded-lg bg-rose-800 hover:bg-rose-700 text-xs font-medium text-white transition-colors flex items-center justify-center gap-1">
                                            <Icon name="trash-2" className="w-3 h-3" /> 清除最佳
                                        </button>
                                    </div>
                                </div>
                            </div>
                            
                            <ModelParamsPanel params={params} handleParamChange={handleParamChange} />

                            {/* 因子選擇 */}
                            <div className="glass-panel p-4 rounded-xl flex flex-col space-y-4">
                                <h2 className="text-lg font-bold text-accent border-b border-zinc-700 pb-2 flex items-center">
                                    <Icon name="grip" className="w-5 h-5 mr-2" />
                                    特徵因子選擇 ({numFeatures} / 24) 
                                </h2>
                                <div className="flex-1 overflow-y-auto max-h-[500px] pr-2">
                                    <FactorSelector selectedFactors={selectedFactors} onChange={handleFactorChange} />
                                </div>
                            </div>
                        </div>

                        {/* 圖表與結果展示 (第一排) */}
                        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
                            
                            <div className="glass-panel p-4 rounded-xl lg:col-span-2">
                                <h2 className="text-lg font-bold text-white mb-2">價格走勢圖</h2>
                                {marketData && (
                                    <ChartComponent 
                                        data={marketData} 
                                        prediction={prediction} 
                                        lookback={params.lookback} 
                                        futureDays={params.futureDays}
                                    />
                                )}
                            </div>

                            <div className="glass-panel p-4 rounded-xl">
                                <h2 className="text-lg font-bold text-white mb-2">Loss 曲線</h2>
                                {trainingResults && trainingResults.lossHistory && (
                                    <LossChart lossHistory={trainingResults.lossHistory} bestLoss={currentBestLoss} />
                                )}
                                <div className="mt-4 space-y-2">
                                    <div className="p-3 bg-zinc-700 rounded-lg">
                                        <p className="text-sm text-zinc-400">當前 Loss</p>
                                        <p className="text-xl font-mono font-bold text-white">{modelState.loss}</p>
                                    </div>
                                    <div className="p-3 bg-zinc-700 rounded-lg">
                                        <p className="text-sm text-zinc-400">訓練進度</p>
                                        <div className="w-full bg-zinc-600 h-2 rounded-full mt-1">
                                            <div className="h-full rounded-full bg-accent-600 transition-all duration-300" style={{width: `${modelState.progress}%`}}></div>
                                        </div>
                                        <p className="text-xs text-zinc-400 mt-1">{modelState.progress.toFixed(1)}%</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        {/* 圖表與結果展示 (第二排) */}
                        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
                            
                            {/* 即時因子狀態面板 */}
                            {latestFactorData && (
                                <FactorStatusPanel latestData={latestFactorData} activeFactors={selectedFactors} />
                            )}
                            {/* K-Fold 驗證結果 */}
                            {(modelState.kFoldMape !== 'N/A' || modelState.kFoldDa !== 'N/A') && (
                                <div className="glass-panel p-4 rounded-xl">
                                    <h2 className="text-lg font-bold text-indigo-300 mb-3 flex items-center gap-2">
                                        <Icon name="layers" className="w-5 h-5" />交叉驗證
                                    </h2>
                                    <div className="space-y-3">
                                        <div className="p-3 bg-zinc-700 rounded-lg flex justify-between items-center">
                                            <span className="text-sm text-zinc-400">平均 MAPE</span>
                                            <span className="font-mono text-xl font-bold text-yellow-300">{modelState.kFoldMape}%</span>
                                        </div>
                                        <div className="p-3 bg-zinc-700 rounded-lg flex justify-between items-center">
                                            <span className="text-sm text-zinc-400">平均 DA</span>
                                            <span className={parseFloat(modelState.kFoldDa)>=55?"font-mono text-xl font-bold text-emerald-300":"font-mono text-xl font-bold text-rose-300"}>{modelState.kFoldDa}%</span>
                                        </div>
                                    </div>
                                </div>
                            )}
                            
                            {prediction && prediction.influenceScores && (
                                <FactorInfluence influenceScores={prediction.influenceScores} numFeatures={numFeatures} />
                            )}
                        </div>
                    </div>
                </div>
            );
        };

        // 先完成模型儲存位置的搬移再渲染：App 掛載時就可能讀取已存模型
        migrateModelStorage().finally(() => {
            const root = ReactDOM.createRoot(document.getElementById('root'));
            root.render(<App />);
        });

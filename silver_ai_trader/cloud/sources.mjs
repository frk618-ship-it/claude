// 雲端（Node）版的輔助資料抓取：COT、TIPS、Fear & Greed、StockTwits、Google Trends
//
// 網址與解析都來自 ../src/core.js，和瀏覽器版一致；這裡只負責「怎麼抓」：
// 直接連線（不經 CORS 代理）、帶瀏覽器標頭（CNN 會擋沒有標頭的請求）、逾時與重試。
// 每個函式成功時回傳資料，失敗時丟出說明原因的錯誤，由呼叫端決定是否排除該因子。

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36';
// FRED（Akamai）會把「像瀏覽器但缺少其他瀏覽器標頭」的請求掛到逾時；用工具型 UA 反而正常
const TOOL_UA = 'silver-ai-trader/1.0 (+https://github.com)';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const fetchWithRetry = async (url, { headers = {}, timeoutMs = 20000, retries = 2, as = 'json' } = {}) => {
    let lastErr;
    for (let i = 0; i <= retries; i++) {
        const ctrl = new AbortController();
        const tid = setTimeout(() => ctrl.abort(), timeoutMs);
        try {
            const r = await fetch(url, { headers: { 'User-Agent': UA, ...headers }, signal: ctrl.signal });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return as === 'json' ? await r.json() : await r.text();
        } catch (e) {
            lastErr = e.name === 'AbortError' ? new Error('逾時') : (e.cause?.code ? new Error(e.cause.code) : e);
            if (i < retries) await sleep(1500 * 2 ** i);
        } finally {
            clearTimeout(tid);
        }
    }
    throw lastErr;
};

// CFTC COT：先試 Disaggregated（Managed Money），再試 Legacy（Non-Commercial）
export const fetchCOT = async (core) => {
    for (const [kind, src, parse] of [['Disagg', core.COT_API.disagg, core.parseCotDisagg], ['Legacy', core.COT_API.legacy, core.parseCotLegacy]]) {
        for (const where of core.COT_API.silverWheres) {
            try {
                const map = parse(await fetchWithRetry(core.cotQueryUrl(src, where), { retries: 1 }));
                if (map.size > 0) return { map, source: kind, firstDate: [...map.keys()].sort()[0] };
            } catch (_) { /* 換下一個條件 */ }
        }
    }
    throw new Error('CFTC API 沒有回傳白銀資料');
};

// 美國財政部 5 年期 TIPS 實質殖利率：每年一份 XML，平行抓取
export const fetchTIPS = async (core, dates) => {
    const years = [...new Set(dates.map(d => d.slice(0, 4)))].sort();
    const maps = await Promise.all(years.map(y =>
        fetchWithRetry(core.treasuryRealYieldUrl(y), { as: 'text' }).then(core.parseTreasuryRealYieldXML).catch(() => null)));
    const all = new Map();
    maps.forEach(m => m && m.forEach((v, k) => all.set(k, v)));
    if (all.size === 0) throw new Error('財政部 XML 沒有資料');
    const covered = years.filter((_, i) => maps[i]?.size > 0).length;
    return { values: core.alignMapToDates(all, dates, 0.5), points: all.size, coverage: `${covered}/${years.length} 年`, firstDate: [...all.keys()].sort()[0] };
};

// 美國財政部名目 5 年期殖利率（與 TIPS 相減得到損益兩平通膨率）
export const fetchTreasuryNominal5y = async (core, dates) => {
    const years = [...new Set(dates.map(d => d.slice(0, 4)))].sort();
    const maps = await Promise.all(years.map(y =>
        fetchWithRetry(core.treasuryNominalYieldUrl(y), { as: 'text' }).then(core.parseTreasuryRealYieldXML).catch(() => null)));
    const all = new Map();
    maps.forEach(m => m && m.forEach((v, k) => all.set(k, v)));
    if (all.size === 0) throw new Error('財政部 XML 沒有資料');
    const covered = years.filter((_, i) => maps[i]?.size > 0).length;
    return { values: core.alignMapToDates(all, dates, NaN), points: all.size, coverage: `${covered}/${years.length} 年`, firstDate: [...all.keys()].sort()[0] };
};

// 經濟政策不確定性 EPU（每日，新聞計算）
export const fetchEPU = async (core) => {
    const map = core.parseEpuDailyCSV(await fetchWithRetry('https://policyuncertainty.com/media/All_Daily_Policy_Data.csv', { as: 'text', timeoutMs: 60000, headers: { 'User-Agent': TOOL_UA } }));
    if (map.size === 0) throw new Error('EPU 檔案沒有資料');
    return { map, firstDate: [...map.keys()].sort()[0], lastDate: [...map.keys()].sort().pop() };
};

// FRED 單一序列（CSV）
export const fetchFred = async (core, id, start) => {
    const map = core.parseFredCSV(await fetchWithRetry(core.fredCsvUrl(id, start), { as: 'text', timeoutMs: 60000, retries: 3, headers: { 'User-Agent': TOOL_UA } }));
    if (map.size === 0) throw new Error(`FRED ${id} 沒有資料`);
    return { map, firstDate: [...map.keys()].sort()[0], lastDate: [...map.keys()].sort().pop() };
};

// 地緣政治風險 GPR（每日，Excel 檔）。網址會轉到 www.matteoiacoviello.com
export const fetchGPR = async (core) => {
    const XLSX = (await import('xlsx')).default;
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 60000);
    let buf;
    try {
        const r = await fetch('https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls', { headers: { 'User-Agent': UA }, signal: ctrl.signal });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        buf = Buffer.from(await r.arrayBuffer());
    } catch (e) {
        throw new Error(e.cause?.code || e.message);
    } finally { clearTimeout(tid); }
    const wb = XLSX.read(buf, { type: 'buffer', cellDates: true });
    const rows = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]]);
    const { gpr, threat } = core.parseGprRows(rows);
    if (gpr.size === 0) throw new Error('GPR 檔案沒有可辨識的 GPRD 欄位');
    return { gpr, threat, firstDate: [...gpr.keys()].sort()[0], lastDate: [...gpr.keys()].sort().pop() };
};

// CNN Fear & Greed：網址加上起始日可取得較長的歷史
// CNN 的歷史大約從 2021 年開始；要求更早的起始日會回 HTTP 500，此時改用最早可用的日期
export const fetchFearGreed = async (core, dates) => {
    const headers = { Accept: 'application/json, text/plain, */*', Origin: 'https://edition.cnn.com', Referer: 'https://edition.cnn.com/' };
    const start = dates[0] < '2021-01-04' ? '2021-01-04' : dates[0];
    const json = await fetchWithRetry(`${core.FEAR_GREED_URL}/${start}`, { headers })
        .catch(() => fetchWithRetry(core.FEAR_GREED_URL, { headers }));
    const { map, currentScore, currentRating } = core.parseFearGreed(json);
    if (map.size === 0) throw new Error('CNN 沒有回傳歷史資料');
    // 歷史開始之前填中性值 50，不能填今天的分數
    return { values: core.alignMapToDates(map, dates, 50), source: 'real', currentScore, currentRating, points: map.size, firstDate: [...map.keys()].sort()[0] };
};

// StockTwits：只有最新約 30 則訊息的當下情緒
export const fetchStockTwits = async (core) => {
    const counts = await Promise.all(core.STOCKTWITS_SYMBOLS.map(sym =>
        fetchWithRetry(core.stockTwitsUrl(sym), { retries: 1 }).then(core.countStockTwits).catch(() => null)));
    const result = core.summarizeStockTwits(counts);
    if (result.source !== 'real') throw new Error('StockTwits 沒有帶情緒標記的訊息');
    return result;
};

// Google Trends：explore 取 token → widgetdata 取週資料。雲端機房的 IP 常被 Google 以 429 拒絕。
export const fetchGoogleTrends = async (core, dates) => {
    let cookie = '';
    let explore;
    for (let i = 0; i < 3; i++) {
        const r = await fetch(core.googleTrendsExploreUrl(dates), { headers: { 'User-Agent': UA, ...(cookie ? { Cookie: cookie } : {}) } });
        const sc = r.headers.get('set-cookie');
        if (sc) cookie = sc.split(';')[0];
        const text = await r.text();
        if (r.ok) { explore = core.parseGoogleTrendsJSON(text); break; }
        if (r.status !== 429) throw new Error(`HTTP ${r.status}`);
        await sleep(3000 * (i + 1));
    }
    if (!explore) throw new Error('Google 拒絕此雲端 IP 的請求（HTTP 429）');
    const widget = (explore.widgets || []).find(w => w.id === 'TIMESERIES');
    if (!widget?.token) throw new Error('找不到 TIMESERIES widget');
    const text = await fetchWithRetry(core.googleTrendsWidgetUrl(dates, widget.token), { as: 'text', headers: cookie ? { Cookie: cookie } : {} });
    const timeline = core.parseGoogleTrendsJSON(text)?.default?.timelineData || [];
    if (timeline.length === 0) throw new Error('沒有週資料');
    return { values: core.alignWeeklyTrends(timeline, dates), points: timeline.length };
};

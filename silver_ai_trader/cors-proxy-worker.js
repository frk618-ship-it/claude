// Silver AI Trader 專用的 CORS 代理（Cloudflare Workers）
//
// 為什麼要自架：公共 CORS 代理（corsproxy.io、allorigins…）常限流、停止服務，
// 而且你的每個請求都會經過第三方。自架後只有你自己用，免費額度（每天 10 萬次）綽綽有餘。
//
// 部署步驟：
//   1. 登入 https://dash.cloudflare.com → Workers & Pages → Create → Create Worker
//   2. 名稱填 silver-proxy → Deploy → Edit code，把本檔全部內容貼上 → Deploy
//   3. 得到網址 https://silver-proxy.<你的帳號>.workers.dev
//   4. 在 Silver AI Trader 的「資料代理」欄位填入：
//        https://silver-proxy.<你的帳號>.workers.dev/?url=
//
// 安全性：只轉發到下列資料來源，避免被別人拿去當萬用代理。
// 程式新增資料來源時，要把它的主機名稱加進 ALLOWED_HOSTS。
const ALLOWED_HOSTS = new Set([
    'stooq.com',
    'query1.finance.yahoo.com',
    'query2.finance.yahoo.com',
    'publicreporting.cftc.gov',
    'www.cftc.gov',
    'production.dataviz.cnn.io',
    'home.treasury.gov',
    'api.stocktwits.com',
    'trends.google.com',
]);

const CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Accept, Content-Type',
    'Access-Control-Max-Age': '86400',
};

export default {
    async fetch(request) {
        if (request.method === 'OPTIONS') return new Response(null, { headers: CORS_HEADERS });
        if (request.method !== 'GET') return new Response('Method not allowed', { status: 405, headers: CORS_HEADERS });

        const target = new URL(request.url).searchParams.get('url');
        let url;
        try { url = new URL(target); } catch (_) {
            return new Response('Missing or invalid ?url=', { status: 400, headers: CORS_HEADERS });
        }
        if (url.protocol !== 'https:' || !ALLOWED_HOSTS.has(url.hostname)) {
            return new Response('Host not allowed', { status: 403, headers: CORS_HEADERS });
        }

        const upstream = await fetch(url.toString(), {
            headers: {
                'Accept': request.headers.get('Accept') || '*/*',
                // 部分來源（Yahoo、CNN）會擋沒有 User-Agent 的請求
                'User-Agent': 'Mozilla/5.0 (compatible; silver-ai-trader-proxy)',
            },
            // 市場資料一天才更新一次，邊緣快取 10 分鐘可大幅減少對來源的請求
            cf: { cacheTtl: 600, cacheEverything: true },
        });

        const headers = new Headers(upstream.headers);
        for (const [k, v] of Object.entries(CORS_HEADERS)) headers.set(k, v);
        headers.delete('set-cookie');
        return new Response(upstream.body, { status: upstream.status, headers });
    },
};

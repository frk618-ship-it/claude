# Silver AI Trader

瀏覽器端的白銀價格預測工具（React + TensorFlow.js）。

## 使用

直接用瀏覽器開啟 `silver-ai-trader.html` 即可，不需要伺服器。

## 修改程式

`silver-ai-trader.html` 是建置產物，**不要直接編輯**。請改 `src/` 底下的檔案再重新建置：

| 檔案 | 內容 |
| --- | --- |
| `src/app.jsx` | 主程式（React 元件、資料抓取、模型訓練） |
| `src/template.html` | 頁面骨架、CSS 變數、背景執行與通知等啟動腳本 |
| `build.mjs` | 建置腳本 |
| `src/core.js` | 與雲端訓練共用的核心：特徵、資料集、模型、評估、預測 |
| `cors-proxy-worker.js` | 可自架的 CORS 代理（Cloudflare Workers） |
| `cloud/` | 雲端訓練（原生 TensorFlow，見 `cloud/README.md`） |

```bash
npm install      # 第一次
npm run build    # 產生 silver-ai-trader.html
npm run check    # 檢查 silver-ai-trader.html 是否與 src/ 同步
```

建置會做這些事：

- JSX 預先編譯成一般 JavaScript：不再於每次開頁面時下載 Babel、現場編譯。
- React、Tailwind CSS、用到的 Lucide 圖示全部內嵌到 HTML。
- TF.js 與 Plotly 檔案大，仍從 jsDelivr 載入；版本鎖定在 `package.json`，並附上 SRI 雜湊，CDN 內容不符時瀏覽器會拒絕執行。
- 要升級 TF.js 或 Plotly，改 `package.json` 的版本、`npm install`，再 `npm run build`，SRI 會自動重算。
- 版本號只在 `package.json` 的 `version` 維護，建置時寫入頁面標題與右上角標記。

## 波動目標化部位

「波動目標化部位」面板不需要訓練模型，載入資料後就會計算：

- 每天收盤後用 EWMA（λ = 0.94）估計年化波動，建議下個交易日的部位 = min(目標波動 ÷ 預測波動, 槓桿上限)。
- 可調整目標波動（10–40%）、槓桿上限、調整門檻（變化小於此值不換倉）與換倉成本；設定會記在瀏覽器裡。
- 顯示今天的建議部位，以及在「歷史資料年數」期間和買進持有的回測比較。

計算在 `src/core.js` 的 `simulateVolTarget`，和雲端 `cloud/voltarget.mjs` 的研究版本規則相同。
雲端 10 年回測顯示它能大幅降低回撤與極端單日損失，但夏普值和買進持有差不多（見 `cloud/output/voltarget.html`）。

## 自架資料代理

Yahoo、CFTC、美國財政部等資料源不允許瀏覽器跨網域讀取，需經過 CORS 代理。程式內建幾個公共代理當備援，但它們常限流。建議自架：

1. 依 `cors-proxy-worker.js` 檔頭的步驟部署到 Cloudflare Workers（免費方案即可）。
2. 在頁面的「資料代理（CORS Proxy）」欄位填入 `https://silver-proxy.<你的帳號>.workers.dev/?url=` 後按儲存。
3. 下次載入資料時會優先使用自架代理。

## 模型儲存

訓練好的模型存在瀏覽器的 IndexedDB（`indexeddb://silver-model-v19`、`indexeddb://silver-champion-v19`）。舊版存在 localStorage 的模型，第一次開啟新版時會自動搬過去。

## 雲端訓練

瀏覽器訓練太慢時，可以在雲端（例如 Claude 雲端工作階段）用原生 TensorFlow 訓練，產生網頁報告與可在瀏覽器載入的模型。
做法見 [`cloud/README.md`](cloud/README.md)。

# 靶材產業網站 — 企業內網版

這個資料夾是可以直接放進公司內網的版本:新聞摘要、技術文獻庫、商情預測、靶材產業島小遊戲與財務指標儀表板。

與公開的 GitHub Pages 版相比:

| 項目 | GitHub Pages 版 | 內網版 |
|---|---|---|
| 存取控制 | 秘密網址 + 網頁密碼(頁面加密) | 公司 SSO(Entra ID / AD),不加密頁面 |
| 3D 小遊戲的 three.js | 從 jsdelivr 載入 | 隨網站一起提供(`site/news/vendor/`,MIT 授權) |
| 字型 | Google Fonts | 系統字型(微軟正黑體、蘋方等) |
| 網址 | 絕對網址指向 github.io | 全部改為相對網址,任何主機名稱都可用 |
| 瀏覽時的對外連線 | 字型、CDN | **無**(只有點「閱讀原文」才會前往新聞網站) |

## 內容

```
site/                         網站根目錄(放到網頁主機,例如 /srv/target-news/site)
  index.html                  轉到 news/
  news/                       新聞摘要、各日報告、技術文獻庫、商情預測、小遊戲
  news/vendor/                three.js 與授權
  financials/                 財務指標儀表板
  robots.txt
nginx/target-news.conf        網頁主機的 Nginx 設定範例(HTTPS、SSO、安全標頭)
collector/daily-update.sh     蒐集主機的每日排程腳本
collector/collector.env.example  蒐集主機的設定範本
source/                       (打包時附上)產生這個網站的程式碼與歷次報告
```

## 架構

- **網頁主機**:Nginx(或 IIS)只提供 `site/` 的靜態檔案,只開 443,前面掛 SSO;不需要連外。
- **蒐集主機**:每天排程執行 `collector/daily-update.sh`,抓新聞與技術文獻、產生網站、打包內網版,再用 rsync 單向推送到網頁主機。不開放任何連入。
- 兩台之間只開「蒐集主機 → 網頁主機」的 SSH。

## 部署步驟

### 1. 網頁主機(資訊部門)

1. 安裝 Nginx 與 [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/),在 Entra ID 建立應用程式並設定可存取的群組。
2. 把 `site/` 複製到 `/srv/target-news/site`(目錄對 Nginx 唯讀)。
3. 參考 `nginx/target-news.conf` 設定網站,換上內部 CA 憑證與主機名稱,重新載入 Nginx。
4. 用公司帳號登入確認各頁面正常;未登入時應被導向登入頁。

只想先看內容、還沒有 SSO 時,可以暫時在內部測試機上直接開 `site/index.html`,或用 `python3 -m http.server` 在 `site/` 目錄提供(小遊戲的 3D 地圖需要經由 http 開啟)。

### 2. 蒐集主機(網站維護者)

1. 需要 Python 3.12 與 rsync。取得程式碼(`source/` 或公司內部 Git),執行 `pip install -r requirements.txt`。
2. 複製 `collector/collector.env.example` 為 `/etc/target-news/collector.env`,填入網頁主機與(選填的)API 金鑰,權限設為 600。
3. 設定推送用 SSH 金鑰,限定只能寫入網頁主機的網站目錄。
4. 先手動執行一次 `collector/daily-update.sh`,確認網站更新,再加入 crontab(範例在腳本開頭)。

### 對外白名單(蒐集主機,經公司 Proxy)

`news.google.com`、`query1.finance.yahoo.com`、`stooq.com`、`api.finmindtrade.com`、`api.openalex.org`、`export.arxiv.org`、`rss.sciencedirect.com`、`doi.org`;使用 Claude 摘要時加上 `api.anthropic.com`。更新公司 logo(`python -m fin_analyzer logos`,手動)時才需要連到各公司官網。

## 資安注意事項

- 存取控制交給 SSO;建議以 AD 群組限制部門,並保留 Nginx 存取紀錄。
- API 金鑰放在公司密碼保管系統,執行時注入,不要寫進設定檔或版本控制。
- Claude API 只會收到公開新聞的標題與片段;是否啟用請依公司 AI 使用規範。未設定金鑰時,網站會用規則自動整理摘要。
- 商情預測頁目前只放富士キメラ総研報告的重點摘要;若要在內網放更完整的內容,請先確認報告授權範圍。
- 頁面內嵌 script / style,CSP 暫時需要 `'unsafe-inline'`(設定範例已包含其他安全標頭)。
- 內網上線後,建議停用公開的 GitHub Pages 網站,並把公開 repo 改為私人或封存。

## 重新打包

在程式碼目錄執行:

```bash
python -m target_news_agent site -i reports -o build/site/news --root-redirect build/site
mkdir -p build/site/financials && cp reports/dashboard/{index.html,dashboard.json,comparison.csv} fin_analyzer/assets/og.png build/site/financials/
python -m target_news_agent intranet --src build/site --out build/intranet --zip
```

或在 GitHub Actions 手動執行「打包內網版」,完成後從該次執行的 Artifacts 下載。

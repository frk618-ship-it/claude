# 靶材產業新聞蒐集 Agent

自動蒐集**濺鍍靶材(sputtering target)生產商與銷售商**,以及**濺鍍設備商、使用 PVD 製程的半導體廠與封裝廠**的新聞與產業動態,過濾、去重、分類後產出每日 Markdown / JSON 報告;若設定了 Anthropic API 金鑰,會再用 Claude 撰寫中文摘要、重要度評分與本期總覽。

## 運作流程

```
config/watchlist.yaml ──► 產生查詢 ──► Google News RSS(中/英/日/韓版本)
                                      Bing News RSS(選用)、自訂 RSS
                                         │
                                         ▼
     日期過濾 → 公司比對(寬業務公司須含「靶材」相關字詞)→ 繁簡去重 → 關鍵字分類 → 評分排序
                                         │
                       data/seen.sqlite3 過濾已報告過的新聞
                                         │
                    (選用)Claude:逐則摘要、重要度 1–5、類別校正、本期總覽/趨勢
                                         │
                                         ▼
                      reports/YYYY-MM-DD.md   reports/YYYY-MM-DD.json
```

報告內容:本期總覽與趨勢(LLM)、重點新聞 Top 5、依類別(擴產與投資、營收與財報、併購與合作、原物料與價格、技術與新產品、客戶與供應鏈、政策與貿易)、各公司新聞量、熱門關鍵字。

## 追蹤對象(預設)

| 類型 | 公司 |
|---|---|
| 生產商-日本 | JX金屬、東曹、三井金屬、三菱綜合材料、住友金屬礦山、愛發科、古屋金屬、田中貴金屬、Proterial |
| 生產商-歐美 | Solstice(原 Honeywell 電子材料)、Materion、Plansee、Heraeus、Umicore |
| 生產商-中國大陸 | 江豐電子、有研新材、阿石創、隆華科技、先導稀材、歐萊新材 |
| 生產商-台灣 | 光洋科、鑫科材料 |
| 銷售商 | Kurt J. Lesker、Stanford Advanced Materials、American Elements、Angstrom Sciences、Testbourne |
| 濺鍍設備商 | 應用材料(AMAT)、北方華創、佳能安內華、芝浦機電、Evatec、Bühler Leybold Optics、VON ARDENNE、Singulus、Veeco、Intevac、天虹科技、아바코(AVACO) |
| 製程相關(PVD 半導體廠) | 台積電、聯電、力積電、世界先進、華邦電、南亞科、三星電子、SK海力士、美光、英特爾、格羅方德、中芯國際、華虹、長江存儲、長鑫存儲、鎧俠、Rapidus;只收錄提到 PVD / 濺鍍 / 靶材、資本支出 / 設備投資,或擴產 / 擴建 / 新廠的新聞 |
| 封裝廠(PVD) | 日月光投控、矽品、Amkor、長電科技、通富微電、華天科技、力成、頎邦、南茂、精材;只收錄提到 PVD / 濺鍍 / 靶材、先進封裝製程(凸塊、RDL、扇出、面板級、CoWoS 等),或資本支出 / 擴產 / 新廠的新聞 |
| 產業主題 | 濺鍍靶材 / 溅射靶材 / sputtering target / スパッタリングターゲット / 스퍼터링 타겟;濺鍍設備 / 溅射设备 / sputtering equipment / スパッタ装置 / 스퍼터 장비 |
| 靶材回收循環 | 廢靶 / 殘靶回收、ITO 銦回收、鉭與貴金屬精煉再生、循環經濟(繁中、簡中、英文、日文主題搜尋;新聞必須同時提到回收再生字詞「和」靶材、濺鍍、ITO、銦、鉭等字詞才收錄,避免一般回收新聞混入)。網站首頁有「靶材回收循環」專區,另新增「回收與循環」新聞分類,追蹤公司的回收相關新聞也會列入 |
| 貴金屬行情 | 金、銀、鉑、鈀、釕、銥、銠價格新聞(繁中、英文);新聞量大,每次各保留最新 4 則(`max_items`) |

清單全在 `config/watchlist.yaml`,可自行增刪公司(例如你們的客戶、台灣代理商)、別名、搜尋語系與分類關鍵字。業務範圍很廣的公司(如 Solstice/Honeywell、三菱綜合材料)請設 `require_context: true`,只收錄同時提到靶材/濺鍍的新聞。設備商可另設 `context_terms`(例如濺鍍、PVD、sputtering),只收錄濺鍍相關新聞,避開蝕刻、CVD 等其他設備的消息。

## 使用方式

```bash
pip install -r requirements.txt

# 蒐集最近 7 天的新聞,報告輸出到 reports/
python -m target_news_agent -v run

# 常用選項
python -m target_news_agent run --days 7                 # 改成最近 7 天
python -m target_news_agent run --only 江豐 Solstice     # 只跑部分公司
python -m target_news_agent run --include-seen           # 包含已報告過的新聞
python -m target_news_agent run --no-llm                 # 不呼叫 Claude
python -m target_news_agent queries                      # 列出會抓取的 RSS 網址(不連網)
```

每日報告保存最近一年:每次 `run` 結束時刪除 `reports/` 中超過 `settings.keep_days`(預設 365 天)的 `YYYY-MM-DD.json` / `.md`,網站的歷次報告也隨之只列這段期間;設為 0 則永久保存。

### 技術文獻:靶材製造技術(期刊與研究)

每次執行也會蒐集靶材製造技術相關的期刊論文與預印本,寫在報告與網站的「技術文獻」區塊:

- **來源**:[OpenAlex](https://openalex.org/)(免費學術文獻索引)、arXiv、15 種期刊的 RSS(MDPI 的 Coatings、Metals、Materials 由 OpenAlex 涵蓋):薄膜與鍍膜(Thin Solid Films、Surface and Coatings Technology、Vacuum、Materials Science in Semiconductor Processing)、金屬靶的合金與加工(Journal of Alloys and Compounds、Int. J. Refractory Metals and Hard Materials、Materials Science and Engineering A、Materials Characterization、J. Materials Processing Technology、J. Materials Science & Technology、J. Materials Research and Technology、Trans. Nonferrous Metals Society of China)、陶瓷與氧化物靶(Ceramics International、J. European Ceramic Society)、回收精煉(Hydrometallurgy)。檢查各來源是否正常:`python -m target_news_agent check-papers`(或在 GitHub Actions 手動執行「檢查文獻來源」),會列出每個來源抓到幾篇、幾篇相關、哪些尚未收錄,不寫入報告。
- **篩選**:標題或摘要必須明確談到濺鍍靶材("target" 單字太廣,醫學、雷射等論文會被排除);同一篇論文來自多個來源只留一筆。
- **分類**:粉末冶金與燒結、熔煉與鑄造、塑性加工與晶粒織構、高純化與雜質控制、綁定與背板、陶瓷與氧化物靶、合金與複合靶、旋轉靶、濺鍍行為與靶材利用率、回收與再利用。
- **靶材循環回收**:另以「spent sputtering target recovery」「spent ITO target indium recovery」「ITO / 鉭靶 scrap recycling」「貴金屬 / 釕靶回收」「废靶材 回收」等字詞搜尋;標題或摘要提到廢靶、殘靶、scrap / spent target 的回收、浸出、精煉再生研究都會收錄,歸入「回收與再利用」。手動執行時可填「papers_days」(例如 365)一次回溯較長期間的文獻。
- **範圍**:預設最近 30 天,已報告過的論文不會重複出現。設定 `ANTHROPIC_API_KEY` 後,Claude 會為每篇寫中文摘要並評 1–5 分(對靶材製造的參考價值)。
- 搜尋字、期刊 RSS、分類關鍵字都在 `config/watchlist.yaml` 的 `research:` 區段。
- **網站**:新聞首頁有「技術文獻」區塊(本期新收錄;沒有新文獻時顯示最近收錄的 10 篇),另有累積全部文獻的「技術文獻庫」頁(`/news/papers.html`),可依製程主題與來源篩選。
- **商情預測**(`/news/forecast.html`):市場調查報告的重點摘要,來源在 `config/forecasts.yaml`(關鍵數字、重點摘要、出處)。每份報告的 `context` 也會作為每日 AI 摘要的產業背景。付費報告只放改寫後的重點與少量關鍵數字並註明出處,完整數據表與原始檔不要放進 repo(repo 與網站都是公開的)。
- **靶材產業島**(`/news/quiz.html`,知識小遊戲):六座以靶材造型設計的浮島圍著中央的「靶材廣場」(每座島都是貼在銅背板上的靶材:圓形平面靶、長方形平面靶、金靶、銅靶、拼接式 ITO 陶瓷靶,刻有侵蝕跑道,擺飾有元素方塊、磁控濺鍍腔體、旋轉靶燈塔、長條圖、工廠與四色旗等)。地圖以 three.js(從 jsdelivr 載入 `three@0.160.0`)繪製成 3D 場景:島嶼底下是岩錐、浮在有波浪的海面上,會上下漂浮、微幅自轉,點選島嶼時鏡頭飛近,拖曳可轉動視角(放開後慢慢回正);島名、關聯說明與對話框是疊在畫面上的 HTML 文字。three.js 載入失敗或瀏覽器不支援 WebGL 時,仍可用側欄清單選島作答;島上每顆小柱是一道題:材料島、製程島(含顯示封裝與回收)、新聞港、市場島、廠商島、配對遊樂島。答對點亮小柱、全部點亮就征服該島並插旗。嚮導角色「靶靶」(頭是一片靶材)會走到你點選的島、用對話框介紹,答對時跳起來、答錯時冒汗,點它可以聽小提示;某座島上藏著每天換位置的金色靶材。虛線連接有關聯的島。可用 `?island=<id>` 直接開啟某座島。題目來源:固定題庫 `config/quiz.yaml`(`island` 欄位:materials / process / market / makers)、`config/target_makers.yaml` 的公司、元素符號、最近 3 天的重點新聞(公司名遮蔽成「○○」);每座島最多 12 題,固定題優先。島嶼、關聯線與嚮導提示定義在 `target_news_agent/quiz.py` 的 `ISLANDS` / `LINKS` / `GUIDE_TIPS`。進度只存在玩家自己的瀏覽器。
- 選項:`--no-papers` 不蒐集文獻、`--papers-days 60` 改天數、`--papers-only` 只蒐集文獻並併入今天既有的報告(不重抓新聞;Actions 手動執行時勾選 papers_only)。選填 secrets:`OPENALEX_MAILTO`(聯絡 email,使用 OpenAlex 較穩定的服務)、`OPENALEX_API_KEY`。
- 同一天再執行一次時,新抓到的新聞會併入當天既有的報告,不會覆蓋早上的內容。
- 補摘要:設定 `ANTHROPIC_API_KEY` 之前產生的報告沒有 AI 摘要,可執行 `python -m target_news_agent resummarize` 補上(只處理還缺摘要的報告;`--force` 全部重做)。Actions 手動執行時勾選 resummarize 即可。
- API 無法使用時(未設定金鑰、額度用完、服務中斷):報告仍會產生**自動整理**的本期總結(依類別統計與評分最高的新聞,網頁會標示「AI 摘要尚未產生」),新聞卡片顯示原文導言。
- 不經 API 補摘要(每日排程任務的 Claude 使用):
  1. `python -m target_news_agent pending -o pending.json`:列出最新報告中缺摘要 / 翻譯的新聞與文獻,並附上撰寫規則(與 API 摘要相同)。
  2. 依規則寫成 JSON:`{"news": [{"id", "summary", "importance", "category", "title_zh"?, "snippet_zh"?}], "papers": [{"id", "summary", "importance", "category", "title_zh"?, "abstract_zh"?}], "overview", "key_trends": [], "watch_points": []}`。
  3. `python -m target_news_agent apply-digest written.json`:併入報告(自動整理的總結會被取代),commit 並推送到 main 後,GitHub Pages 會自動重新發布。

### Claude 摘要(選用)

設定 `ANTHROPIC_API_KEY` 後自動啟用,預設模型 `claude-opus-5-5`(可用 `--model` 或環境變數 `TARGET_NEWS_MODEL` 更換)。已開啟伺服器端 fallback(`fallbacks: "default"`):若請求被模型拒絕,API 會自動改用備援模型重跑;仍失敗時該批次略過,報告照常產生。新聞內容以資料形式傳入並於提示中標明不可執行其中指示。

### 每日自動執行(GitHub Actions)

`.github/workflows/target-news.yml` 每天台北時間 05:41 排程(GitHub 排程常延遲約 2 小時,實際多在 08:00 前後完成),並把報告與已讀紀錄 commit 回 repo。也可在 Actions 頁面手動觸發並指定天數。若要啟用 Claude 摘要,請在 repo 的 *Settings → Secrets and variables → Actions* 新增 `ANTHROPIC_API_KEY`。(排程只會在預設分支上執行。)

### 繁體中文顯示(自動翻譯)

蒐集後、寫入報告前,所有非繁體中文的標題與摘要都會轉成繁體中文,網站、報告與 Notion 都顯示譯文(滑鼠移到標題上可看原文):

- **簡體中文**:用 OpenCC 轉成繁體字(離線、免費、不需金鑰)。只轉字形、不改用詞,避免「申請文件」被改成「申請檔案」這類誤譯。
- **英文 / 日文 / 韓文**:用 Claude 翻譯(需設定 `ANTHROPIC_API_KEY`);公司名、期刊名、化學式、數字單位保留原文,技術用語用台灣慣用譯法(如 sputtering target → 濺鍍靶材)。未設定金鑰時這些文字維持原文,其他功能照常。
- 原文保留在報告 JSON(`title`、`abstract` 等欄位),譯文另存於 `title_zh`、`snippet_zh`、`abstract_zh`;公司比對、分類與去重仍用原文。已翻譯過的不會重複翻譯。
- 選項:`--no-translate` 關閉翻譯。

### 貴金屬報價

每次執行會抓黃金、白銀、鉑金、鈀金的日收盤價與日漲跌(Stooq 每日歷史 CSV,失敗時改用 Yahoo Finance;免金鑰),寫入報告 JSON 的 `metals`、Markdown 報告與網站首頁的「貴金屬行情」區塊(漲紅跌綠)。品項在 `config/watchlist.yaml` 的 `metals.symbols` 增刪;來源暫時失敗時沿用當天稍早的報價,不影響新聞蒐集。

### 新聞網站(GitHub Pages)

每次「靶材產業新聞」workflow 執行後,會把 `reports/` 的所有報告產生成網站並發布到 GitHub Pages 的 `/news/`:首頁合併最近 3 天的報告(重點新聞、技術文獻、可依類型與公司篩選的全部新聞;本期總結為最新一期的 AI 整理),各日完整內容保留在「歷次報告」的日期頁。網站不提供原始 MD / JSON 下載(報告檔仍保存在 repo 的 `reports/`)。

- **同一事件合併**:多家媒體報導同一件事(例如同一份公告被十幾個入口網站轉載)時,網頁只顯示評分最高的一則,其餘收在卡片下方「另有 N 家媒體報導」。判斷方式見 `target_news_agent/events.py`:提到同一家追蹤公司、發布日相差 3 天內,且標題(去掉公司名與通用詞後)有一半以上相同。只影響網頁顯示,報告、Markdown 與 Notion 仍保留每一則。
- **低參考價值收合**:行情與股價頁、資金流向、論壇與自媒體、市場報告、資料頁,以及 Claude 評為 1 分的報導,收在「全部新聞」最後的「其他 N 則參考價值較低」,預設收合;篩選時若有符合的項目會自動展開。
- **公司追蹤**(`/news/companies.html`、`/news/co-<公司>.html`):每家追蹤公司近 30 天的新聞時間軸(同一事件合併、低參考價值收合)、主題分布與重要事件數;首頁「追蹤清單」的公司名稱可直接點進去。
- **全站搜尋**(`/news/search.html`):搜尋 `reports/` 保存中的所有報告(預設一年)的標題(含原文)、摘要、公司與來源,多個關鍵字須全部符合,可依類型篩選;網址 `search.html#q=關鍵字` 可直接帶入搜尋。索引是另一個檔案 `search-index.json`;受保護的網站上改為以相同金鑰加密的 `search-index.bin`,頁面用解鎖時記住的金鑰解密,未解鎖時無法讀取。
- **重點新聞置頂與理由**:評為 4-5 分的新聞在「全部新聞」中置頂,卡片標出「為何重要」(例如「靶材需求:日月光擴產先進封裝」)。理由由 Claude 摘要時一併寫入(`reason` 欄位);沒有時依新聞主題與公司角色(客戶 / 靶材廠 / 設備商)自動推得。
- **週報**(`/news/weekly.html`、`/news/week-<週一>.html`):每週一至週日的重點事件(4-5 分,同一事件合併,附理由)、貴金屬週行情(週漲跌與區間)、主題分布、報導最多的公司與每日焦點;首頁有「上週週報」按鈕。`.github/workflows/weekly-report.yml` 每週一台北時間約 09:47 把上週週報寄給貴金屬預警的 email 群組(同一組 SMTP_* 與 ALERT_EMAIL_TO secrets),也可手動執行並指定週一日期。本機預覽:`python -m target_news_agent weekly-email --dry-run weekly.html`。

- 網址:`https://<帳號>.github.io/<repo>/news/`(根目錄會自動轉到這裡)。
- 只重新發布網站、不蒐集新聞:Actions 頁面執行「發布網站」workflow。
- 每則新聞會自動標上類型(新聞、公司公告、行情數據、論壇與自媒體、市場報告、資料頁),方便略過雜訊。
- 本機預覽:`python -m target_news_agent site -o site/news`,再開 `site/news/index.html`。
- 啟用方式:*Settings → Pages → Source* 選 **GitHub Actions**。網站是公開的。

### 同步到 Notion(選用)

在一個 Notion 頁面下維護三個資料庫;新聞與文獻已存在的(依 ID 判斷)不會重複新增,追蹤公司每次更新本期則數。摘要常在第一次同步之後才補寫:「發布網站」workflow 每次執行會再同步最近兩份報告,替還沒有重要度的資料列補上標題譯文、摘要與重要度;已有重要度的資料列(包含你在 Notion 手動改過的)不會被覆蓋。可以在 Notion 依公司、公司類型、類別、主題、日期篩選、排序或做看板。

| 資料庫 | 欄位 |
|---|---|
| 靶材產業新聞 | 標題、日期、公司、**公司類型**(靶材生產商 / 銷售商 / 濺鍍設備商 / PVD 半導體廠 / 封裝廠 / 產業主題)、類別、來源、語系、重要度、分數、摘要、連結、ID |
| 靶材技術文獻 | 標題、日期、主題、期刊、作者、來源、重要度、摘要(Claude 中文摘要)、原文摘要、DOI、連結、ID |
| 靶材追蹤公司 | 名稱、類型、地區、股票代號、搜尋別名、本期則數、最近報導 |

**設定(只要兩個 secrets,資料庫會自動建立):**

1. 到 <https://www.notion.so/my-integrations> 建立 Internal integration,複製 token(`ntn_…` 或 `secret_…`)。
2. 在 Notion 開一個頁面(例如「靶材情報」),右上角 `…` → *Connections* → 加入剛建立的 integration。頁面網址最後 32 碼就是頁面 ID。
3. 在 GitHub repo 的 *Settings → Secrets and variables → Actions* 新增 `NOTION_TOKEN` 與 `NOTION_PARENT_PAGE_ID`(頁面 ID)。
4. 下次 workflow 執行(或手動執行「靶材產業新聞」)時,會在該頁面下找到或建立「靶材產業新聞」「靶材技術文獻」「靶材追蹤公司」三個資料庫並寫入資料。舊版建立的新聞資料庫會自動補上「公司類型」欄位。同步失敗不影響報告與網站,可之後手動重跑。

本機執行:

```bash
export NOTION_TOKEN=ntn_xxx NOTION_PARENT_PAGE_ID=<頁面ID>
python -m target_news_agent notion-sync                      # reports/ 中最新一份
python -m target_news_agent notion-sync reports/2026-10-13.json
```

也可以指定既有資料庫:設定 `NOTION_DATABASE_ID`、`NOTION_PAPERS_DATABASE_ID`、`NOTION_COMPANIES_DATABASE_ID`(有設定的優先於自動尋找),或用 `notion-setup --parent <頁面ID>` 一次建立三個並印出 ID。自建資料庫的欄位名稱與型別需相同:標題 / 名稱=Title、日期 / 最近報導=Date、公司 / 公司類型 / 類別 / 主題=Multi-select、來源 / 語系 / 期刊 / 類型 / 地區=Select、重要度 / 分數 / 本期則數=Number、連結=URL,其餘=Text。

## 開發

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

測試使用 `tests/fixtures` 中的 RSS 樣本與假的 Claude client,不需要網路或金鑰。

## 注意事項

- Google News RSS 連結為 Google 轉址連結,點擊後會導向原始新聞網站。
- 預設約 50 個 RSS 查詢,每次間隔 0.6 秒;查詢過多或過於頻繁可能被來源暫時限流。
- 預設公司清單僅為起點,請依實際關注的競爭者、客戶與代理商調整。

---

# 靶材製造商財務指標儀表板(`fin_analyzer`)

比較全球主要**上市櫃濺鍍靶材製造商**的財務指標,產生可互動的 HTML 儀表板;也能針對單一公司產生深度財報報告。

## 比較公司(預設,`config/target_makers.yaml`)

| 市場 | 公司(代號) | 靶材比重 |
|---|---|---|
| 台灣 | 光洋科(1785)、鑫科(3663) | 主要事業 / 專業廠 |
| 日本 | JX金屬(5016.T)、東曹(4042.T)、三井金屬(5706.T)、住友化學(4005.T)、ULVAC 愛發科(6728.T) | 主要事業 / 多角化 / 多角化 / 多角化 / 多角化 |
| 美國 | Solstice Advanced Materials(SOLS,原 Honeywell 電子材料)、Materion(MTRN) | 多角化 |
| 中國大陸 | 江豐電子(300666.SZ)、有研新材(600206.SS)、阿石創(300706.SZ)、歐萊新材(688530.SS) | 專業廠 / 主要事業 / 專業廠 / 專業廠 |

公司可在設定檔自由增刪;`target_focus` 標示靶材占營收的比重。多角化公司(化工、特用化學品集團)的指標反映整家公司,儀表板會以 ＊ 標示。

## 儀表板內容

- **焦點公司指標卡**:近四季營收、營收年增、毛利率、營益率、ROE、負債比,以及在篩選範圍內的排名。
- **指標排名長條圖**:可選 25 項指標(規模與成長、獲利、效率、財務結構、現金流、評價、F-score / Z''-score)。
- **獲利 × 成長散佈圖**:近四季營收年增 vs 營業利益率;泡泡大小代表靶材營收比重(專業廠最大、多角化最小),避免把化工集團非靶材部門的獲利誤當成靶材獲利。
- **季度趨勢折線**:最多 4 家公司比較毛利率、營益率、ROE、存貨天數、負債比等。
- **台股月營收年增率**:光洋科、鑫科的月營收。
- **完整比較表**:可排序,標示最佳值與各公司規則訊號數(🔴 風險 / 🟡 注意 / 🟢 優勢);公司名旁的彩色小標籤標示靶材比重(專業廠 / 主要事業 / 多角化)。
- **存取限制**(秘密網址+密碼):GitHub Pages 沒有權限控管,所以發布前會執行 `python -m target_news_agent protect`:整個網站放到一串隨機路徑下(`<網址>/claude/<SITE_PATH>/news/`、`.../financials/`),根目錄不連過去、robots 禁止收錄;每一頁都以 AES-256-GCM 加密(密碼經 PBKDF2-SHA256 產生金鑰),開啟時輸入密碼在瀏覽器解密,同一個瀏覽器工作階段換頁不必重輸(可勾選「在這個裝置上記住」)。分享時可用 `<網址>#k=<密碼>` 的連結直接開啟(`#` 後面不會送到伺服器,開啟後會自動從網址列移除)。報告 JSON / CSV 不再發布。`SITE_PATH`(16–64 個英數、`-`、`_`)與 `SITE_PASSWORD` 設在 repo 的 Actions Secrets;沒設定時只發布一個佔位頁。注意:repo 本身若仍是公開的,`reports/` 裡的報告原始資料仍可在 GitHub 上看到。
- **分享預覽**:頁面內含靜態標題、`description` 與 Open Graph / Twitter 標籤(圖片 `fin_analyzer/assets/og.png`,絕對網址由 `settings.site_url` 組成),貼到 LINE、Slack、Teams 會顯示預覽卡片;新聞網站同樣設定(`config/watchlist.yaml` 的 `settings.site_url`)。預覽圖以 `python scripts/make_og_images.py` 產生(扁平風格,需要 Playwright 與中文字型)。
- 圖表載入前顯示骨架屏(skeleton),版面不會跳動。
- 篩選列:市場(台/日/美/中)、靶材比重、焦點公司;固定明亮配色,支援手機版面;標題列連到靶材產業新聞摘要網站(設定檔 `settings.news_url`)。
- 金額依匯率換算為**億台幣**(匯率由 `fetch` 從 Yahoo Finance 取得,取不到則用設定檔 `fx_rates`);比率不受匯率影響。

## 使用方式

```bash
pip install -r requirements.txt

python -m fin_analyzer fetch        # 下載所有公司財報、股價與匯率 → data/financials/
python -m fin_analyzer dashboard    # 產生 reports/dashboard/index.html、dashboard.json、comparison.csv
python -m fin_analyzer analyze      # 焦點公司(預設鑫科)的深度報告 → reports/financials/

python -m fin_analyzer dashboard --focus 300666.SZ            # 改以江豐電子為焦點
python -m fin_analyzer fetch --tickers 5016.T SOLS            # 只更新部分公司
python -m fin_analyzer dashboard -c examples/demo_config.yaml # 離線示範(虛構資料)
```

- **資料來源**:台股走 [FinMind](https://finmindtrade.com)(公開資訊觀測站財報,含月營收;`FINMIND_TOKEN` 選填);日本、美國、中國大陸走 Yahoo Finance(`yfinance`,季報通常只有最近 4–6 季,因此部分近四季年增等指標可能缺值)。
- **手動資料**:也可依 `data/financials/<代號>/quarterly.csv`、`monthly_revenue.csv`、`prices.csv`、`meta.json`(含 `currency`)格式自行填入。
- **跨國比較注意**:各國會計準則不同,日本公司多為 3 月制財年;季度一律依季末日期對齊日曆季度。
- **Yahoo Finance 資料缺口的處理**(`fetch` 會在 `meta.json` 的 `note` 記錄,儀表板以提示列顯示):
  - 某一季缺損益/現金流量(常見於 A 股的 Q3、美股的較早季度),而同一財年其餘三季齊全時,以「年度合計 − 其餘三季」補回。
  - 日本公司在 Yahoo 多半沒有季度損益與現金流量,資產負債表也只有半年度:此時改用 `annual.csv` 的年度財報計算近四季指標,最新季為財年結束季(3 月制 → Q1),單季年增等單季指標留空。
  - 股票分割後,Yahoo 股價已還原,但財報股數未調整;`fetch` 依分割紀錄把股數與 EPS 換算為分割後基準。
  - 營業利益一律採「毛利 − 營業費用」(Yahoo `Operating Income`),與台股的營業利益口徑一致;IFRS 日本公司(如 JX金屬)申報的營業利益、中國公司的「營業利潤」包含其他營業收支或投資收益,會比本工具的數字高,差額歸入業外。
- **FinMind 限流**:未設定 `FINMIND_TOKEN` 時,短時間內連續請求會被回 402,程式會等待後自動重試;FinMind 的現金流量表不含發放現金股利,台股的 `dividends_paid` 會是空白。
- **深度報告**(`analyze`):單一公司的損益、杜邦分析、營運效率、財務結構、現金流、月營收、F-score / Z''-score、評價與同業比較;設定 `ANTHROPIC_API_KEY` 後加上 Claude 評論(預設模型 `claude-opus-5-5`,可用 `--model` 或 `FIN_ANALYZER_MODEL` 更換)。
- **網站**:儀表板與新聞網站一起發布到 GitHub Pages:`https://<帳號>.github.io/<repo>/financials/`(另有 `dashboard.json`、`comparison.csv`)。`main` 上的 `reports/dashboard/` 更新時,「發布網站」workflow 會自動重新發布。
- **自動執行**:每月 12 日(月營收公布後)由 Claude 排程任務下載資料、產生儀表板與報告、commit 並更新儀表板 Artifact;`.github/workflows/fin-report.yml` 只保留手動觸發(可指定焦點公司)。
- `examples/demo_financials/`、`examples/demo_dashboard/` 為**虛構**公司資料與其產出的示範,不代表任何真實公司。
- 本工具僅為財務資料的量化整理,不構成投資建議。

---

# 特定物料比價系統(`price_compare`)

輸入物料**品名規格**(例如「鈦靶 4N5 Φ300x6.35mm」),系統會解析材質、純度、型態與尺寸,從各來源找出市場流通報價,換算成新台幣與統一計價單位後比較,產出比價摘要與明細。

## 運作流程

```
品名規格 ──► 規格解析(材質 / 純度 4N5=99.995% / 型態 / 尺寸 Φ、x、吋→mm)
                 │
   ┌─────────────┼──────────────────────────────┐
   ▼             ▼                              ▼
報價紀錄 CSV   Claude 網路搜尋(web_search +      PChome 24h(選用,
(供應商報價、   web_fetch:供應商產品頁、目錄價、    一般消費性商品)
 歷史採購價)    B2B 平台、行情 → 結構化報價)
   └─────────────┼──────────────────────────────┘
                 ▼
  規格相符度評分(符合 / 近似 / 差異大,材質不同直接排除)
  → 匯率換算(臺灣銀行即期中價,離線時用設定檔)→ 單位換算(每片 / 每公斤)
  → 去重、過舊與異常價標記 → 最低 / 中位數 / 最高、建議優先詢價對象
                 ▼
     reports/prices/<品名>-<時間>.md / .json / .csv(CSV 可直接用 Excel 開)
```

## 使用方式

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # 網路搜尋需要;未設定時只查報價紀錄

# 命令列比價
python -m price_compare search "鈦靶 4N5 Φ300x6.35mm"
python -m price_compare search "ITO 靶材 99.99% 127x406x6mm" -q "4 片"
python -m price_compare search                         # 不帶參數會互動詢問品名規格
python -m price_compare search "金蒸鍍料 5N 顆粒" --no-web   # 只查報價紀錄
python -m price_compare search "鉭靶 4N5 Φ300x6.35" --save-web  # 符合規格的網路報價存入報價紀錄

# 網頁介面(本機瀏覽器開 http://127.0.0.1:8765/)
python -m price_compare serve

# 新增供應商報價單 / 歷史採購價到報價紀錄
python -m price_compare add --vendor "XX材料" --title 鈦靶 --spec "4N5 Φ300x6.35mm 含銅背板" \
    --price 56000 --currency TWD --unit pcs --moq "1 片" --lead-time "4 週"

# 離線示範(虛構資料)
python -m price_compare -c examples/price_compare_demo.yaml search --no-web 鈦靶 4N5 Φ300x6.35mm
```

也可以在 GitHub Actions 頁面手動執行「物料比價」workflow,輸入品名規格,結果會顯示在執行摘要並 commit 到 `reports/prices/`(需在 repo Secrets 設定 `ANTHROPIC_API_KEY`)。

### 比價紀錄網站(已下線)

比價網站已不再發布到 GitHub Pages(GitHub Pages 網站是公開的,報價與供應商資料不宜公開)。比價報告仍會 commit 到 `reports/prices/`;要用網頁瀏覽請在本機或內網執行 `serve`,或本機產生靜態頁:`python -m price_compare site -i reports/prices -o site`,再開 `site/index.html`。

## 規格寫法

| 項目 | 可辨識的寫法 |
|---|---|
| 材質 | 中文(繁/簡)、英文或元素符號:鈦 / 钛 / Titanium / Ti;化合物與合金:ITO、IGZO、AZO、SiO2、Al2O3、WTi、NiCr、AlCu、GeSbTe… |
| 純度 | 4N、4N5、5N、99.99%、99.995 |
| 型態 | 靶材、粉、顆粒、蒸鍍料、線、箔、板、棒、錠、管(旋轉靶) |
| 尺寸 | Φ300x6.35mm、D76.2*3、直徑300 厚度6、127x406x6mm、3" dia x 0.125"(吋自動換算 mm) |

背板(「含銅背板」、"bonded to copper backing plate")不會被當成材質。

## 設定與資料

- `config/price_compare.yaml`:備援匯率、相符度門檻、尺寸容許誤差(預設 ±3%)、異常價倍數、報價有效天數、網路搜尋次數、地區與封鎖網站、搜尋提示。
- `data/quotes/quotes.csv`:自有報價紀錄,欄位 `quote_date, vendor, title, spec_text, price, currency, unit(pcs/kg/g/lb/oz/ozt/set), pack_qty, moq, lead_time, region, url, price_type(quote/list/reference), note`。可用 Excel 編輯(存成 UTF-8 CSV)。
- **Claude 網路搜尋**:預設模型 `claude-opus-5-5`(`--model` 或 `PRICE_COMPARE_MODEL` 可換),使用伺服器端 web_search / web_fetch 工具,再以結構化輸出整理成報價;要求只記錄網頁上實際出現的數字,網址若不在搜尋紀錄中會標註「請人工確認」。已開啟伺服器端 fallback(`fallbacks: "default"`)。
- `examples/demo_quotes.csv` 為**虛構**示範資料,僅供離線示範與測試。

## 注意事項

- 網路公開標價通常未含稅、運費、綁靶 / 背板與加工費,且多為小量零售價;正式採購請以供應商報價單為準。
- 「參考行情」(例如金屬現貨價)、超過有效天數或偏離中位數 3 倍以上的報價會列出但不納入統計。
- 每次網路比價約 1–3 分鐘,費用依搜尋次數與內容長度而定,可用 `max_searches` 控制。
## 企業內網版

`python -m target_news_agent intranet --src <建好的網站> --out build/intranet [--zip] [--with-source]` 會把網站打包成可放在公司內網的版本:three.js 隨網站提供、改用系統字型(瀏覽時沒有任何對外連線)、不加密頁面(改由 SSO 控管)、絕對網址改為相對網址,並附上 Nginx 設定範例、蒐集主機的每日更新腳本與部署說明(`deploy/intranet/`)。也可以在 GitHub Actions 手動執行「打包內網版」,從 Artifacts 下載。


---

# 貴金屬價格預警(`metal_alert`)

每小時抓取**黃金、白銀、鉑金、鈀金**的 1 小時 K 線(Yahoo Finance,COMEX / NYMEX 期貨連續月 `GC=F`、`SI=F`、`PL=F`、`PA=F`,免金鑰),用多項股票技術分析指標算出綜合分數,提示**相對低點(進場)**與**相對高點(出場)**,分數進入進場 / 出場區時發出預警,並以網頁模組呈現。

## 運作流程

```
Yahoo Finance chart API ─► 1h K 線(60 天)+ 日 K 線(1 年)
          │
          ▼
  指標投票(−1 ~ +1,正 = 超賣偏進場):RSI、KD、MACD、布林 %B、威廉 %R、CCI、乖離率 Z 分數、近 20 日區間位置
          │  加權平均 ×100
          ▼
  趨勢濾網(EMA200 小時線 + 日線 MA50):多頭中的超賣訊號加強、空頭中的打折(出場訊號反之)
          │
          ▼
  綜合分數 −100 ~ +100 → 強烈進場 / 留意進場 / 中性 / 留意出場 / 強烈出場
          │
  與上一次狀態比較(冷卻時間、升級為「強烈」) + 自訂價位線
          │
          ▼
  預警紀錄 data/metal_alert/alerts.jsonl · 通知(Webhook / Telegram)· 網頁 /metals/
```

## 網頁內容

- **總覽卡片**:各品項價格、日 / 24 小時漲跌(漲紅跌綠)、綜合分數量尺與訊號等級。
- **技術面圖表**(最近 240 根小時 K):收盤價、布林通道、EMA200 與歷次進場 ▲ / 出場 ▼ 訊號;綜合分數走勢與門檻;RSI 與 K 值。滑鼠移到圖上可看各時點數值。
- **指標明細**:每個指標的數值、投票方向與判讀。
- **參考價位**:支撐 / 壓力(布林軌道、20 日區間)、ATR、進出場的參考停損與目標價(收盤 ± 倍數 × ATR)。
- **歷史訊號回測**:過去 60 天分數剛進入進場 / 出場區後 24 小時的勝率與平均報酬。
- **預警紀錄**:最近的預警與觸發原因。支援深色模式與手機版面。

## 使用方式

```bash
pip install -r requirements.txt

python -m metal_alert run                 # 執行一次檢查(排程用),有預警時發通知
python -m metal_alert run --site site/metals   # 同時輸出靜態網頁
python -m metal_alert serve               # 網頁模組 http://127.0.0.1:8770/ ,背景每小時自動檢查,可按「立即更新」
python -m metal_alert site --fetch -o site/metals   # 抓最新資料產生靜態網頁(唯讀:不發通知、不改狀態)
```

- `serve` 另提供 JSON API:`GET /api/latest`(最新評分)、`GET /api/alerts`(預警紀錄)、`POST /api/refresh`(立即檢查)、`GET /healthz`。
- 狀態、預警紀錄與最新資料存在 `data/metal_alert/`(`-d` 可改)。

## 預警規則

- 分數由中性 / 反方向進入進場(≥ +30)或出場(≤ −30)區時發出預警;由「留意」升級為「強烈」(±60)時也會發出。
- 同一品項、同一方向的預警至少間隔 `cooldown_hours`(預設 6 小時),避免價格在門檻附近來回時重複通知。
- 第一次執行時,已經在進場 / 出場區的品項會各發出一次預警。
- 自訂價位線:在 `config/metal_alert.yaml` 的 `price_alerts` 加入例如 `{code: Au, below: 3800}`,價格跌破 / 突破時預警。

## 通知(選填)

| 環境變數 / Actions Secret | 說明 |
|---|---|
| `METAL_ALERT_WEBHOOK_URL` | Slack / Discord / Microsoft Teams / Google Chat 的 incoming webhook 網址 |
| `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` | Telegram 機器人 |
| `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`ALERT_EMAIL_TO`、`ALERT_EMAIL_FROM` | Email(SMTP)。`SMTP_PORT` 填 465 用 SSL、587 用 STARTTLS(預設 465);`ALERT_EMAIL_TO` 可填多個收件人,以逗號分隔;`ALERT_EMAIL_FROM` 不填時用 `SMTP_USER` |

Email 設定範例:

| 信箱 | `SMTP_HOST` | `SMTP_PORT` | `SMTP_PASSWORD` |
|---|---|---|---|
| Gmail | `smtp.gmail.com` | `465` | 應用程式密碼(Google 帳戶 → 安全性 → 開啟兩步驟驗證 → 應用程式密碼,16 碼),不是登入密碼 |
| Outlook / Microsoft 365 | `smtp.office365.com` | `587` | 帳號密碼(公司帳號需管理員開啟 SMTP AUTH) |
| 公司郵件伺服器 | 依 IT 提供 | 465 或 587 | 依 IT 提供 |

同一次檢查的多則預警會合併成一封信。

每則通知(Email、Webhook、Telegram)最後都會附上預警網頁網址,方便收件者查看圖表與指標明細。GitHub Actions 會用 `SITE_PATH` 自動組成 `https://<帳號>.github.io/<repo>/<SITE_PATH>/metals/`;網站放在其他位置(例如內網)時,可設定 `METAL_ALERT_PAGE_URL` 覆寫。網站有密碼保護時,收件者開啟網頁需輸入 `SITE_PASSWORD`,請另外告知,不要寫進通知內容。

都沒設定時,預警仍會寫入紀錄、顯示在網頁與 Actions 執行摘要。

## 每小時自動執行(GitHub Actions)

`.github/workflows/metal-alert.yml` 每小時第 17 分執行:檢查並發送預警 → 有新預警時把 `data/metal_alert/alerts.jsonl` commit 回 repo → 重新建置整個網站並發布,頁面位於 `<網站>/metals/`(和新聞、財務儀表板一樣受 `SITE_PATH` / `SITE_PASSWORD` 保護,頁面右下角的切換選單可互相切換)。上一次的指標狀態存在 Actions cache,不會每小時產生 commit。「靶材產業新聞」與「發布網站」workflow 發布時也會一併產生最新的貴金屬頁面。GitHub 排程可能延遲數分鐘到數十分鐘;需要準時的每小時檢查,可在自己的主機執行 `serve` 或用 cron 執行 `run`。

## 設定(`config/metal_alert.yaml`)

- `settings`:檢查頻率 `interval_minutes`(預設 60)、K 線期間與週期、圖表根數、冷卻時間、回測觀察根數、時區。
- `levels`:分級門檻。
- `indicators`:各指標參數與權重(權重設 0 即停用)、趨勢濾網、ATR 停損 / 目標倍數。
- `metals`:品項與 Yahoo 代號,可加入其他商品(例如銅 `HG=F`)。

## 注意事項

- 期貨連續月在換月時可能出現價格跳動;期貨價格與現貨、實體金屬報價有價差。
- Yahoo Finance 對完整瀏覽器 User-Agent 但沒有 cookie 的請求會回 429,程式使用精簡 UA 並在 query1 / query2 兩個主機間重試。
- 技術指標在強趨勢中可能持續鈍化(例如下跌中長期超賣),趨勢濾網只能降低、不能消除逆勢訊號;回測統計僅供參考。
- 本工具僅為技術指標的量化整理,不構成投資建議。

---

# 商機雷達:首次業務開發(`lead_radar`)

找出**正在尋找**濺鍍靶材、純鈦 / 鈦合金、鎳基合金、銅材 / 銅合金、蒸鍍材料的潛在客戶。商機雷達以**客群**(segment)為單位:每個客群有自己的目標公司、次產業與搜尋字詞,把公開資訊當成「需求訊號」落到「次產業 × 產品線」網格上,用 Getis-Ord Gi* 熱點分析找出需求集中的區塊,再依「產品適配 × 近期訊號」排出開發順序。

目前的客群:

| 客群 | key | 次產業 | 公司數 | 網頁 |
|---|---|---|---:|---|
| **台灣半導體設備廠**(主要客群) | `tw_semi_equipment` | 前段製程設備、先進封裝設備、關鍵零組件與模組、廠務與氣化系統、載具與自動化 | 23 | `<網站>/leads/` |
| 半導體生產供應鏈 | `tw_semi_production` | 晶圓代工、記憶體、晶圓減薄與再生、封裝、測試 | 16 | `<網站>/leads/tw_semi_production/` |
| 台灣 PCB 設備廠 | `tw_pcb_equipment` | 濕製程與電鍍設備、曝光 / 塗佈 / 壓合、鑽孔與成型、檢測、自動化 | 8 | `<網站>/leads/tw_pcb_equipment/` |
| 台灣面板設備廠 | `tw_display_equipment` | 鍍膜與電漿設備、濕製程、雷射與 Micro LED、檢測、自動化 | 7 | `<網站>/leads/tw_display_equipment/` |
| 台灣 AI 伺服器設備廠 | `tw_ai_server` | 伺服器代工組裝、散熱與液冷、電源與配電、機殼與滑軌、連接器與線材 | 17 | `<網站>/leads/tw_ai_server/` |
| 台灣重工業製造廠 | `tw_heavy_industry` | 鋼鐵冶煉、不銹鋼與特殊鋼、煉油與石化上游、化學品與塑化、工程統包 | 20 | `<網站>/leads/tw_heavy_industry/` |
| 台灣 CPO 與光通訊供應鏈 | `tw_cpo` | 雷射與磊晶、光引擎與封裝、光收發模組、光纖與被動元件、光學鍍膜、測試介面與設備、交換器系統 | 18 | `<網站>/leads/tw_cpo/` |

主要客群由設定檔 `settings.primary_segment` 指定,是網站 `/leads/` 的首頁;頁首的客群頁籤可切換。原本「產品線 × 應用產業 × 地區」的全市場商機總覽已移除(未屬於任何客群的公司不再搜尋)。

## 決策樹:擴廠規模 → 預估材料需求

報導標題有投資 / 採購金額時(「斥資 110 億」「資本支出 731 億元擴建」「拿下 2.4 億美元訂單」),依決策樹估算每年材料需求金額:

```
報導金額(換算新台幣)× 意圖係數(擴產 1、招標 1、訂單 0.5、歲修 0.3)
  × 客群 → 次產業 → 產品線的材料占比(settings.estimate.tree)× 公司適配度 / 3
```

取每家公司最大的一筆,以對數刻度換成規模分數(100 萬元 = 0、10 億元 = 100),× 0.15 加入綜合分數(上限 100)。沒有金額新聞的公司分數不變。
在手訂單、產業總額、客戶預付款、營收與獲利不算專案金額。**材料占比是粗估的規劃假設**,請依實際接單修正 `config/lead_radar.yaml` 的 `settings.estimate`。

## 流程

```
① 訊號蒐集(每個客群)                   ② 定位                                 ③ 熱度
客群公司名稱 × 客群字詞(擴產、訂單…) ─┐  產品線:靶材 / 鈦 / 鎳(關鍵字;         意圖權重(招標 5 > 缺料 / 擴產 4
產品字詞 × 客群產業字詞             ─┼► 沒提到材料時以公司適配度 ≥ 2 代入,   > 訂單 / 認證 3 > 新品 2.5)
CSV 匯入(種子報導、展會、詢價)      ─┘  且必須帶有需求意圖)                   × 相關度 × 股市雜訊降權
                                         公司:標題主詞 1.0、標題提及 0.5、     × 同一事件轉載合計最多 +0.5
                                         僅內文 0.3                              × 0.5^(天數 / 半衰期 45 天)
                                                │
                                                ▼
④ 熱點:「次產業 × 產品線」網格的 Gi* z(自己 1 + 同列 / 同欄 0.3);z ≥ 1.96 且 ≥ 2 件事 = 熱點,z ≥ 1.0 = 溫點
⑤ 開發順序:綜合分數 = 0.4 × 產品適配(0–3)+ 0.6 × 訊號強度 + 0.15 × 規模分數(決策樹) → ≥ 70 優先開發、≥ 45 積極經營、其餘持續觀察
                                                │
                                                ▼
   reports/leads/segments/<key>/YYYY-MM-DD.json / .md / -accounts.csv(開發名單)/ -leads.csv · 網頁 · data/lead_radar/signals.sqlite3
```

## 使用方式

```bash
pip install -r requirements.txt

python -m lead_radar run                                  # 全部客群:蒐集 → 判讀 → 熱點 → 開發名單(主要客群優先)
python -m lead_radar run --segment tw_semi_production     # 只跑一個客群
python -m lead_radar run --site site/leads                # 同時產生網頁
python -m lead_radar analyze                              # 不連網,用資料庫重新分析(--asof 2026-10-01 可回看)
python -m lead_radar import data/lead_radar/seeds/tw_semi_equipment.csv data/lead_radar/seeds/tw_semi_production.csv
python -m lead_radar queries                              # 列出所有搜尋網址
python -m lead_radar site                                 # 主要客群 → site/leads/index.html,其他 → site/leads/<key>/
python -m lead_radar demo                                 # 離線示範(虛構資料)→ examples/lead_radar_demo/
```

- **開發順序**:客群內每一家都會列出,沒有新聞也會依產品適配度排名。每家附主推產品、建議行動、推估理由(`note`)與佐證報導;`-accounts.csv` 可直接匯入 CRM。
- **一個分數**:網頁、CSV 與報告都只用「綜合分數」與同一套分級(優先開發 / 積極經營 / 持續觀察),並列出組成:產品適配分(0.4 × 適配度換算)與訊號分(0.6 × 訊號強度)。訊號強度只看新聞證據;新聞中提到、但不在客群名單內的公司,列在頁面下方「其他被提及公司(未評估適配)」,只顯示訊號強度。
- **公司歸屬**:一則新聞常同時提到供應商與客戶(「漢唐、帆宣卡位台積電赴美大單」),所以標題中第一個出現的公司(主詞)拿全部熱度,標題中其他公司拿一半,只出現在內文的拿 0.3。
- **股市雜訊**:上市櫃公司的新聞多半是股市評論;沒有提到材料的公司新聞必須帶有需求意圖(擴產、訂單、缺料、認證…)才收錄,看盤、目標價、個股概覽等字詞降權且不計入熱點事件數。
- **種子訊號**:`data/lead_radar/seeds/` 是整理好的公開報導(有日期與連結),新客群可先匯入,之後由每週排程補充。
- **CSV 匯入欄位**(UTF-8,第一列為欄名,`#` 開頭為註解):`date, org, title, product, industry, region, intent, note, link, source, weight`。指定了 `org` 的列不再猜公司名,也不當成股市雜訊。
- **Claude 判讀**(選用,設定 `ANTHROPIC_API_KEY`):逐則判斷買方與供應商、產品線、意圖、急迫度 1–5,並寫一句需求研判;預設 `claude-opus-5-5`(`--model` 或 `LEAD_RADAR_MODEL`)。沒有金鑰時全部用關鍵字規則。
- **網頁**(與其他頁面一樣受 `SITE_PATH` / `SITE_PASSWORD` 保護):客群頁籤、本期概況、次產業 × 產品線熱點矩陣(點格子篩選名單與訊號)、**目標客戶開發順序**(唯一的公司清單:搜尋、分級、產品線與「本週新出現」篩選;點列展開分數組成、需求研判、推估理由、切入重點與規格依據、佐證報導)、其他被提及公司(未評估適配)、需求訊號列表;「下載開發名單 CSV」依目前篩選在瀏覽器產生 Excel 可開的檔案。
- **業務報告**:頁首「業務報告」按鈕連到 `<網站>/leads/report/`,只需網站密碼、不需登入 Claude 即可閱讀。內容來自 `reports/leads/business_report.md`(由 claude.ai 上的業務報告文件匯出;報告改版時重新匯出並 commit),`python -m lead_radar site` 會一併產生這一頁。頁首連結在設定檔 `settings.links` 設定:`page` = 網站內頁、`url` = 外部網址。
- **每週自動執行**:`.github/workflows/lead-radar.yml` 每週一台北時間 06:23 跑全部客群,commit 報告與訊號資料庫,再呼叫「發布網站」;也可在 Actions 頁面手動執行。其他會重建網站的 workflow 都以 `python -m lead_radar site -o site/leads` 帶上全部客群頁。
- **新增客群**:在 `segments` 加一段(名稱、產業、地區、時間窗、搜尋字詞、次產業),在 `accounts` 為每家公司填 `segment`、`subsegment`、`fit`(0–3)與 `note`。`fit` 與 `note` 是業務假設,請依拜訪結果修正。
- 設定檔中含半形逗號的值(例如 `note`、`action`)必須加引號,否則 YAML 會把逗號後的文字切成另一個欄位;讀取時會檢查並報錯。

## 設定(`config/lead_radar.yaml`)

| 區段 | 內容 |
|---|---|
| `segments` | 客群:名稱、產業、地區、時間窗、搜尋字詞、次產業;`settings.primary_segment` 指定主要客群 |
| `products` | 三條產品線的比對字詞、搜尋字詞、排除字詞、目標應用產業、切入話術 `pitch` 與其規格依據 `pitch_source`(網頁、CSV、報告都會標示;目前為一般產業常見規格,尚未對照公司型錄) |
| `industries` | 16 個應用產業(半導體、面板、太陽能、光學鍍膜、儲存、工具鍍膜、航太、醫材、化工、油氣、發電、氫能、船舶、消費電子、運動器材、積層製造) |
| `intents` | 8 種需求訊號的字詞、權重與建議行動 |
| `regions` | 地區字詞(都沒命中時用新聞版本推定:繁中 → 台灣、日文 → 日本…) |
| `accounts` | 目標客戶清單:別名、客群、次產業、產品適配 `fit`、推估理由 `note`;沒有 `segment` 的公司目前不搜尋 |
| `settings` | 主要客群、熱點門檻、鄰格權重 `neighbor_weight`、股市雜訊與捨棄字詞、`exclude_orgs`(自家與同業,不列入名單);時間窗與半衰期由各客群設定 |

## 注意事項

- **資料隱私**:repo 是公開的,`data/lead_radar/signals.sqlite3` 會被 commit。客戶詢價、展會名單等非公開資料請用本機資料庫匯入與分析,例如 `python -m lead_radar --db data/lead_radar/local.sqlite3 import 詢價.csv`(`local*.sqlite3` 已列入 `.gitignore`),不要 commit。
- 第一次執行時沒有歷史資料,熱點都會標為「新興」;資料庫只採用涵蓋完整的週次比較(避免早期週次看起來偏冷),約一個月後趨勢標籤才有意義。
- 公司名稱的規則擷取只是近似(中文新聞常省略「公司」字尾);目標客戶清單的別名最準,其次是 Claude 判讀。
- 名單是開發順序的建議,依公開新聞與自行匯入的資料量化而來;接觸前請人工確認公司、窗口與實際需求。`examples/lead_radar_demo*` 為**虛構**公司與事件。

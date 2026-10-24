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

### 技術文獻:靶材製造技術(期刊與研究)

每次執行也會蒐集靶材製造技術相關的期刊論文與預印本,寫在報告與網站的「技術文獻」區塊:

- **來源**:[OpenAlex](https://openalex.org/)(免費學術文獻索引)、arXiv、期刊 RSS(Thin Solid Films、Surface and Coatings Technology、Vacuum、Journal of Alloys and Compounds、Ceramics International、Int. J. Refractory Metals and Hard Materials)。
- **篩選**:標題或摘要必須明確談到濺鍍靶材("target" 單字太廣,醫學、雷射等論文會被排除);同一篇論文來自多個來源只留一筆。
- **分類**:粉末冶金與燒結、熔煉與鑄造、塑性加工與晶粒織構、高純化與雜質控制、綁定與背板、陶瓷與氧化物靶、合金與複合靶、旋轉靶、濺鍍行為與靶材利用率、回收與再利用。
- **靶材循環回收**:另以「spent sputtering target recovery」「spent ITO target indium recovery」「ITO / 鉭靶 scrap recycling」「貴金屬 / 釕靶回收」「废靶材 回收」等字詞搜尋;標題或摘要提到廢靶、殘靶、scrap / spent target 的回收、浸出、精煉再生研究都會收錄,歸入「回收與再利用」。手動執行時可填「papers_days」(例如 365)一次回溯較長期間的文獻。
- **範圍**:預設最近 30 天,已報告過的論文不會重複出現。設定 `ANTHROPIC_API_KEY` 後,Claude 會為每篇寫中文摘要並評 1–5 分(對靶材製造的參考價值)。
- 搜尋字、期刊 RSS、分類關鍵字都在 `config/watchlist.yaml` 的 `research:` 區段。
- **網站**:新聞首頁有「技術文獻」區塊(本期新收錄;沒有新文獻時顯示最近收錄的 10 篇),另有累積全部文獻的「技術文獻庫」頁(`/news/papers.html`),可依製程主題與來源篩選。
- **商情預測**(`/news/forecast.html`):市場調查報告的重點摘要,來源在 `config/forecasts.yaml`(關鍵數字、重點摘要、出處)。每份報告的 `context` 也會作為每日 AI 摘要的產業背景。付費報告只放改寫後的重點與少量關鍵數字並註明出處,完整數據表與原始檔不要放進 repo(repo 與網站都是公開的)。
- **靶材產業島**(`/news/quiz.html`,知識小遊戲):一張可探索的六角形島嶼地圖,每座島是一個主題(導線材料、製程工藝、元素、顯示與封裝、回收、市場預測、新聞港、台日中美廠商、配對遊樂島),島上每棟建築是一道題,答對點亮建築、全部點亮就征服該島並插旗。虛線連接有關聯的島(例如導線材料 ↔ 日本廠商:JX金屬 Cu 靶第一),點選島嶼時顯示關聯說明;配對遊樂島是元素符號翻牌遊戲;地圖上每天藏著一隻靶材小精靈。可用 `?island=<id>` 直接開啟某座島。題目來源:固定題庫 `config/quiz.yaml`(`island` 欄位決定所在島嶼)、`config/target_makers.yaml` 的公司、元素符號、最近 3 天的重點新聞(公司名遮蔽成「○○」)。島嶼與關聯線定義在 `target_news_agent/quiz.py` 的 `ISLANDS` / `LINKS`。進度只存在玩家自己的瀏覽器。
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

- 網址:`https://<帳號>.github.io/<repo>/news/`(根目錄會自動轉到這裡)。
- 只重新發布網站、不蒐集新聞:Actions 頁面執行「發布網站」workflow。
- 每則新聞會自動標上類型(新聞、公司公告、行情數據、論壇與自媒體、市場報告、資料頁),方便略過雜訊。
- 本機預覽:`python -m target_news_agent site -o site/news`,再開 `site/news/index.html`。
- 啟用方式:*Settings → Pages → Source* 選 **GitHub Actions**。網站是公開的。

### 同步到 Notion(選用)

在一個 Notion 頁面下維護三個資料庫;新聞與文獻已存在的(依 ID 判斷)不會重複新增,追蹤公司每次更新本期則數。可以在 Notion 依公司、公司類型、類別、主題、日期篩選、排序或做看板。

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
- **分享預覽**:頁面內含靜態標題、`description` 與 Open Graph / Twitter 標籤(圖片 `fin_analyzer/assets/og.png`,絕對網址由 `settings.site_url` 組成),貼到 LINE、Slack、Teams 會顯示預覽卡片;新聞網站同樣設定(`config/watchlist.yaml` 的 `settings.site_url`)。
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

# 靶材產業新聞蒐集 Agent

自動蒐集**濺鍍靶材(sputtering target)生產商與銷售商**的新聞與產業動態,過濾、去重、分類後產出每日 Markdown / JSON 報告;若設定了 Anthropic API 金鑰,會再用 Claude 撰寫中文摘要、重要度評分與本期總覽。

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
| 生產商-歐美 | Honeywell 電子材料、Materion、Plansee、Heraeus、Umicore |
| 生產商-中國大陸 | 江豐電子、有研新材、阿石創、隆華科技、先導稀材、歐萊新材 |
| 生產商-台灣 | 光洋科、鑫科材料 |
| 銷售商 | Kurt J. Lesker、Stanford Advanced Materials、American Elements、Angstrom Sciences、Testbourne |
| 產業主題 | 濺鍍靶材 / 溅射靶材 / sputtering target / スパッタリングターゲット |

清單全在 `config/watchlist.yaml`,可自行增刪公司(例如你們的客戶、台灣代理商)、別名、搜尋語系與分類關鍵字。業務範圍很廣的公司(如 Honeywell、三菱綜合材料)請設 `require_context: true`,只收錄同時提到靶材/濺鍍的新聞。

## 使用方式

```bash
pip install -r requirements.txt

# 蒐集最近 3 天的新聞,報告輸出到 reports/
python -m target_news_agent -v run

# 常用選項
python -m target_news_agent run --days 7                 # 改成最近 7 天
python -m target_news_agent run --only 江豐 Honeywell    # 只跑部分公司
python -m target_news_agent run --include-seen           # 包含已報告過的新聞
python -m target_news_agent run --no-llm                 # 不呼叫 Claude
python -m target_news_agent queries                      # 列出會抓取的 RSS 網址(不連網)
```

### Claude 摘要(選用)

設定 `ANTHROPIC_API_KEY` 後自動啟用,預設模型 `claude-opus-5-5`(可用 `--model` 或環境變數 `TARGET_NEWS_MODEL` 更換)。已開啟伺服器端 fallback(`fallbacks: "default"`):若請求被模型拒絕,API 會自動改用備援模型重跑;仍失敗時該批次略過,報告照常產生。新聞內容以資料形式傳入並於提示中標明不可執行其中指示。

### 每日自動執行(GitHub Actions)

`.github/workflows/target-news.yml` 每天台北時間 08:23 執行,並把報告與已讀紀錄 commit 回 repo。也可在 Actions 頁面手動觸發並指定天數。若要啟用 Claude 摘要,請在 repo 的 *Settings → Secrets and variables → Actions* 新增 `ANTHROPIC_API_KEY`。(排程只會在預設分支上執行。)

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
| 日本 | JX金屬(5016.T)、東曹(4042.T)、三井金屬(5706.T) | 主要事業 / 多角化 / 多角化 |
| 美國 | Solstice Advanced Materials(SOLS,原 Honeywell 電子材料)、Materion(MTRN) | 多角化 |
| 中國大陸 | 江豐電子(300666.SZ)、有研新材(600206.SS)、阿石創(300706.SZ)、歐萊新材(688530.SS) | 專業廠 / 主要事業 / 專業廠 / 專業廠 |

公司可在設定檔自由增刪;`target_focus` 標示靶材占營收的比重。多角化公司(化工、特用化學品集團)的指標反映整家公司,儀表板會以 ＊ 標示。

## 儀表板內容

- **焦點公司指標卡**:近四季營收、營收年增、毛利率、營益率、ROE、負債比,以及在篩選範圍內的排名。
- **指標排名長條圖**:可選 25 項指標(規模與成長、獲利、效率、財務結構、現金流、評價、F-score / Z''-score)。
- **獲利 × 成長散佈圖**:近四季營收年增 vs 營業利益率。
- **季度趨勢折線**:最多 4 家公司比較毛利率、營益率、ROE、存貨天數、負債比等。
- **台股月營收年增率**:光洋科、鑫科的月營收。
- **完整比較表**:可排序,標示最佳值與各公司規則訊號數(🔴 風險 / 🟡 注意 / 🟢 優勢)。
- 篩選列:市場(台/日/美/中)、靶材比重、焦點公司;支援深色模式與手機版面。
- 金額依匯率換算為**億台幣**(匯率由 `fetch` 從 Yahoo Finance 取得,取不到則用設定檔 `fx_rates`);比率不受匯率影響。

## 使用方式

```bash
pip install -r requirements.txt

python -m fin_analyzer fetch        # 下載所有公司財報、股價與匯率 → data/financials/
python -m fin_analyzer dashboard    # 產生 reports/dashboard/index.html、dashboard.json、comparison.csv
python -m fin_analyzer analyze      # 焦點公司(預設光洋科)的深度報告 → reports/financials/

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
- **自動執行**:每月 12 日(月營收公布後)由 Claude 排程任務下載資料、產生儀表板與報告、commit 並更新儀表板 Artifact;`.github/workflows/fin-report.yml` 只保留手動觸發(可指定焦點公司)。
- `examples/demo_financials/`、`examples/demo_dashboard/` 為**虛構**公司資料與其產出的示範,不代表任何真實公司。
- 本工具僅為財務資料的量化整理,不構成投資建議。

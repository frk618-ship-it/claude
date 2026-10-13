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

# 標的公司財報分析工具(`fin_analyzer`)

針對台股上市櫃公司做財報量化分析,預設以**半導體材料製造商「光洋科(1785,濺鍍靶材/貴金屬材料)」**為標的,並與鑫科、中砂、台特化、昇陽半導體、環球晶等半導體材料同業比較。標的與同業都可在 `config/fin_targets.yaml` 更換。

## 分析內容

| 面向 | 指標 |
|---|---|
| 成長 | 單季營收年增 / 季增、EPS 年增、近四季營收年增 |
| 獲利能力 | 毛利率、營益率、淨利率、費用率、業外占稅前比重、有效稅率 |
| 報酬率 | 近四季 ROE / ROA、杜邦分析(淨利率 × 資產週轉率 × 權益乘數) |
| 營運效率 | 應收帳款天數、存貨天數、應付帳款天數、現金轉換循環 |
| 財務結構 | 流動 / 速動比率、負債比率、淨負債/權益、利息保障倍數 |
| 現金流量 | 近四季營業現金流、自由現金流、營業現金流/淨利、資本支出/營收、資本支出/折舊 |
| 月營收 | 月增、年增、累計年增、近 3 月/近 12 月均值比、創新高 |
| 綜合評分 | Piotroski F-score(9 項)、Altman Z''-score(新興市場版) |
| 評價 | 本益比、股價淨值比、股價營收比、EV/EBITDA、自由現金流殖利率 |
| 同業比較 | 關鍵指標並列與排名 |
| 規則訊號 | 🟢優勢 / 🟡注意 / 🔴風險(毛利率連續下滑、存貨天數攀升、盈餘含金量低、負債比過高…,門檻可調) |
| Claude 評論(選用) | 依計算結果撰寫摘要、優勢、風險、同業位置與追蹤重點 |

`config/fin_targets.yaml` 的 `industry.notes` 列出半導體材料(靶材 / 貴金屬)的判讀重點,例如貴金屬營收隨金屬價格波動、存貨跌價風險、晶圓廠認證週期等,會附在報告中並提供給 Claude 作為背景。

## 使用方式

```bash
pip install -r requirements.txt

# 1) 從 FinMind 下載財報、月營收、股價(target + peers)到 data/financials/<代號>/
python -m fin_analyzer fetch
python -m fin_analyzer fetch --tickers 1785 --start 2020-01-01

# 2) 分析並產生報告 reports/financials/<代號>-<日期>.md / .json / -quarterly.csv
python -m fin_analyzer analyze
python -m fin_analyzer analyze --target 3663 --peers 1785 1560   # 換標的 / 同業
python -m fin_analyzer analyze --no-llm                          # 不呼叫 Claude

# 離線示範(虛構資料,不需網路)
python -m fin_analyzer analyze -c examples/demo_config.yaml --no-llm
```

- **資料來源**:[FinMind](https://finmindtrade.com) 開放資料(轉載公開資訊觀測站財報)。免 token 可用;設定環境變數 `FINMIND_TOKEN` 可提高額度。台灣現金流量表為年初累計數,程式會自動轉為單季。若某公司的會計科目名稱不同,在設定檔 `settings.finmind.field_map` 補上對應即可。
- **手動資料**:也可以不用 FinMind,直接依 `data/financials/<代號>/quarterly.csv`(欄位見 `fin_analyzer/models.py`,損益與現金流為單季金額、資產負債為季末餘額,單位元)、`monthly_revenue.csv`、`prices.csv` 的格式自行填入,例如從公開資訊觀測站複製。
- **Claude 評論**:設定 `ANTHROPIC_API_KEY` 後自動啟用,模型預設 `claude-opus-5-5`(`--model` 或 `FIN_ANALYZER_MODEL` 可更換);只傳入計算後的數字,並要求不得捏造數據。
- **自動執行**:`.github/workflows/fin-report.yml` 於每月 12 日(月營收公布後)下載資料、產生報告並 commit;也可手動觸發並指定標的代號。可在 repo Secrets 設定 `FINMIND_TOKEN`、`ANTHROPIC_API_KEY`(皆選填)。
- `examples/demo_financials/` 為**虛構**公司資料,只用於離線示範與測試,不代表任何真實公司。
- 報告為財務資料的量化整理,不構成投資建議。

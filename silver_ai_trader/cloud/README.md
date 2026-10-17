# 雲端訓練（cloud/）

在有 Node.js 的機器上用**原生 TensorFlow** 訓練 Silver AI Trader 的模型，例如 Claude 雲端工作階段。
訓練結果會輸出成網頁報告和瀏覽器可直接載入的模型。

和瀏覽器版共用 `../src/core.js`：特徵工程、正規化、時序切分、模型結構、評分方式完全相同。

## 為什麼比瀏覽器快

- **原生 TensorFlow**：使用 oneDNN 與 AVX-512 的 C++ 實作，不是瀏覽器裡的 JavaScript 或 WebGL。
  在 4 核心的雲端容器實測，LSTM 每個 epoch 0.28 秒，瀏覽器的 CPU 模式是 5.2 秒。
- **多行程平行**：參數搜尋的每組試驗、交叉驗證的每一折，分給多個行程同時跑（預設一個 CPU 核心一個）。
  RNN 在小 batch 下單一行程只用得到約一個核心，所以平行化幾乎是線性加速。

## 使用

```bash
cd cloud
npm install                          # 第一次；會下載 TensorFlow 原生函式庫（約 100 MB）
node train.mjs                       # 從 Yahoo Finance 抓資料後訓練
node report.mjs                      # 產生 output/report.html
```

常用選項：

| 選項 | 說明 |
| --- | --- |
| `--data 檔案.json` | 使用瀏覽器「匯出訓練資料」的檔案（含 COT、TIPS、Google Trends、Fear & Greed） |
| `--config 檔案.json` | 覆寫參數，格式見 `config.example.json`（只需寫要改的欄位） |
| `--trials 20` | 先做 20 組超參數搜尋，再用最佳組合訓練 |
| `--epochs 200` | epoch 上限（實際由早停決定） |
| `--workers 4` | 平行行程數（預設 = CPU 核心數） |
| `--no-refit` | 不用全部資料重訓，直接輸出評估用的模型 |
| `--out 目錄` | 輸出目錄（預設 `output/`） |
| `--compare-loss` | 比較三種訓練目標（誤差／方向／混合），輸出 `loss-compare.json`；`node compare-report.mjs` 產生網頁 |
| `--compare-features` | 比較三組輸入變數（原有／原有 + 文獻新增／只用文獻新增），輸出 `feature-compare.json`；`node feature-report.mjs` 產生網頁 |
| `--repeats 3` | 比較實驗中每個設定在每個期間重訓幾次 |

## 資料來源

- **不帶 `--data`**：只從 Yahoo Finance 抓 8 個市場品種（白銀、SLV、黃金、原油、美元指數、VIX、10 年期利率、銅）。
  COT、TIPS、Google Trends、Fear & Greed、StockTwits 這幾個因子會自動排除，報告會列出原因。
- **帶 `--data`**：在瀏覽器版載入資料後按「匯出訓練資料（交給雲端訓練）」，把下載的 JSON 上傳到雲端工作階段再訓練。
  所有因子都可以用，而且和瀏覽器看到的資料完全一樣。

在 Claude 雲端工作階段裡，Stooq、CFTC、美國財政部、CNN、Google Trends、StockTwits 預設被網路政策擋住。
要讓雲端直接抓這些資料，請在環境設定的 Network access 加入這些網域；不然就用 `--data`。

### 文獻建議的額外變數

不帶 `--data` 時，另外從 Yahoo 抓 8 個品種、從財政部抓名目 5 年期殖利率，產生 13 個文獻建議的變數
（定義與出處見 `src/core.js` 的 `LITERATURE_FEATURES`）：

| 類別 | 變數 | 依據 |
| --- | --- | --- |
| 股市 | 標普 500 日報酬 | Baur & Lucey (2010) |
| 貴金屬 | 鉑金、鈀金日報酬 | Sari, Hammoudeh & Soytas (2010) |
| 利率 | 期限利差 10Y−3M | Pierdzioch, Risse & Rohloff (2016) |
| 通膨預期 | 5 年期損益兩平通膨率（名目 − TIPS） | Batten, Ciner & Lucey (2010) |
| 波動率 | 黃金隱含波動率 GVZ；白銀 5 日、22 日已實現波動率 | GVZ/VXSLV 文獻；Corsi (2009) |
| 匯率 | 歐元、人民幣、印度盧比對美元日報酬 | Sari 等 (2010)；Pierdzioch & Risse (2020) |
| 動能 | 白銀 3 個月、12 個月累積報酬 | Moskowitz, Ooi & Pedersen (2012) |

第二批是不確定性與信用風險變數（需要網路允許 `policyuncertainty.com`、`www.matteoiacoviello.com`、`fred.stlouisfed.org`）：

| 變數 | 來源 | 使用時點 | 依據 |
| --- | --- | --- | --- |
| 經濟政策不確定性 EPU | policyuncertainty.com 每日 | 7 日對數平均，延後 1 天 | Baker, Bloom & Davis (2016) |
| 地緣政治風險 GPR、GPR Threats | matteoiacoviello.com 每日（Excel） | 同上 | Caldara & Iacoviello (2022)；Baur & Smales (2020) |
| 信用利差 Baa − 10Y | FRED `BAA10Y` 每日 | 延後 1 天 | Goyal & Welch (2008) |
| 金融壓力指數 | FRED `STLFSI4` 每週 | 延後 7 天（週五基準、下週四公布） | Kliesen, Owyang & Vermann (2012) |

`--compare-features` 比較三組：原有變數、原有 + 不確定性、原有 + 全部文獻變數。

用瀏覽器匯出的資料（`--data`）訓練時沒有這些變數，會自動略過。用了這些變數的模型無法載入瀏覽器版。

## 評估方式

資料依時間切成**訓練 | 驗證 | 測試**三段：

- **驗證段**：只用來決定早停與挑選超參數。
- **測試段**：完全不參與任何選擇，對應瀏覽器版的「保留集」。報告上的準確率、回測都來自這一段。
- **比較基準**：報告會並列「永遠猜漲」「跟昨天同方向」「明天 = 今天」三種簡單規則，並附方向準確率的 95% 信賴區間。
- **重訓**：評估完成後，預設用相同參數與相同 epoch 數，以全部資料重訓一次作為輸出模型，讓預測用上最新資料。

## 輸出（`output/`）

| 檔案 | 用途 |
| --- | --- |
| `report.html` | 網頁報告：未來預測、價格與預測對照、測試表現、回測、訓練過程、因子影響 |
| `model.json`、`weights.bin` | TF.js 模型 |
| `silver-config.json` | 訓練時的參數、因子與正規化範圍 |
| `results.json` | 報告用的全部數據 |

**在瀏覽器版使用雲端模型**：切到「執行預測」，在「上傳外部模型」一次選取 `model.json`、`weights.bin`、`silver-config.json`，再按執行。
參數與因子會自動套用。如果目前資料的期間和訓練時不同，畫面會提示正規化範圍有差異。

> 注意：開啟重訓時，輸出模型已經看過測試段；在瀏覽器上對它做保留集評分或回測，分數會偏高。請以雲端報告的測試段數字為準。

# 選股雷達 eggRolls

台股篩選儀表板。每天自動篩選符合條件的標的，打開網頁就能看。

## 篩選條件

| # | 條件 | 邏輯 |
|---|------|------|
| 1 | MA 突破 | 收盤價 > 5 日均線 |
| 2 | RSI 超賣回升 | RSI(14) 介於 30–50 |
| 3 | 低估 | 現價 < 預估 EPS × 20 |
| 4 | 營收成長 | YoY ≥ 10%（雙位數） |
| 5 | 放量 | 當日量 > 20 日均量 × 1.5 |

通過 ≥ 3 個條件即列入，5/5 為「多重交叉」訊號。

---

## 專案結構

```
stock-screener/
├── backend/
│   ├── screener.py          # Python 篩選腳本（每天跑一次）
│   ├── requirements.txt
│   └── latest_result.json   # 最新結果備份
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── vercel.json          # Vercel 部署設定
│   ├── index.html
│   ├── public/
│   │   └── data/
│   │       └── stocks.json  # ← Python 輸出，前端讀取
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── index.css
│       └── components/
│           ├── MetricCards.jsx
│           └── StockTable.jsx
└── README.md
```

---

## 快速開始

### 1. 安裝 Python 後端

```bash
cd backend
pip install -r requirements.txt
```

> 如果是 Ubuntu/Debian 系統，加上 `--break-system-packages`

### 2. 跑篩選腳本

```bash
python screener.py
```

腳本會：
- 掃描 40 檔台股（可在 `screener.py` 裡自訂清單）
- 計算 MA、RSI、EPS 估值、YoY、量能
- 輸出結果到 `frontend/public/data/stocks.json`

### 3. 啟動前端開發模式

```bash
cd frontend
npm install
npm run dev
```

打開 `http://localhost:5173` 就能看到儀表板。

---

## 部署到 Vercel

### 方法一：透過 GitHub（推薦）

1. 把整個專案推到 GitHub
2. 到 [vercel.com](https://vercel.com) 用 GitHub 登入
3. Import 你的 repo
4. 設定：
   - **Root Directory**: `frontend`
   - **Framework Preset**: Vite
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
5. 點 Deploy，完成！

之後每次 `git push` 都會自動重新部署。

### Vercel 環境變數（Web UI 可編輯設定必填）

在 Vercel 專案的 **Environment Variables** 新增：

- `KV_REST_API_URL`
- `KV_REST_API_TOKEN`
- `CONFIG_KV_KEY`（可選，預設 `eggrolls:config:current`）

選填（若要「按儲存就立即觸發更新」）：

- `GITHUB_TOKEN`
- `GITHUB_REPO`（例如 `owner/repo`）
- `GITHUB_BRANCH`（預設 `main`）
- `GITHUB_WORKFLOW_FILE`（預設 `update-data.yml`）

### 方法二：Vercel CLI

```bash
# 安裝 CLI
npm i -g vercel

# 在 frontend 資料夾裡
cd frontend
vercel

# 正式部署
vercel --prod
```

---

## 自動化排程

每天收盤後自動跑 Python 腳本，更新資料。

### GitHub Actions（推薦，搭配 Vercel 部署）

專案已內建 `.github/workflows/update-data.yml`，推到 GitHub 後即自動生效：

- **自動執行**：每週一到五 14:35（台灣時間），收盤後 5 分鐘
- **手動觸發**：GitHub repo → Actions → `Update Stock Data` → `Run workflow`
- JSON 有變動才會 commit，commit 後 Vercel 自動重新部署
- commit message 帶 `[skip ci]` 避免其他 CI 重複觸發
- workflow 會先嘗試從 Vercel KV 同步最新 config（抓不到才 fallback repo 內 `backend/config.json`）

GitHub repo 的 **Actions secrets** 請新增：

- `KV_REST_API_URL`
- `KV_REST_API_TOKEN`
- `CONFIG_KV_KEY`（可選，未設定時預設 `eggrolls:config:current`）

流程：

```
GitHub Actions 排程觸發
  → 安裝 Python + 依賴
  → 執行 screener.py（產出 stocks.json + portfolio.json）
  → git commit + push（僅在資料有變動時）
  → Vercel 偵測 push → 自動部署
```

### macOS / Linux (crontab)

```bash
# 每天下午 2:00 自動執行
crontab -e

# 加入這行（路徑改成你自己的）
0 14 * * 1-5 cd /path/to/stock-screener/backend && python screener.py && cd ../frontend && git add -A && git commit -m "update $(date +\%Y\%m\%d)" && git push
```

### Windows (Task Scheduler)

建立一個排程任務，每天 14:00 執行 `run_screener.bat`：

```bat
@echo off
cd C:\path\to\stock-screener\backend
python screener.py
cd ..\frontend
git add -A
git commit -m "update %date%"
git push
```

---

## 自訂設定

設定來源說明：

- **生產環境**：以 Vercel KV 的 `CONFIG_KV_KEY` 設定為主
- **本地開發**：仍可直接編輯 `backend/config.json`，修改後重跑 `python screener.py` 即可生效

### 修改掃描股票清單

編輯 `backend/config.json`：

```jsonc
{
  "portfolio_tickers": [     // 手上持有的標的
    "0050.TW",
    "2330.TW",
    // 加入你持有的股票...
  ],
  "screener_tickers": [      // 大盤分析掃描清單
    "2330.TW",
    "2454.TW",
    // 加入你想掃描的股票...
  ]
}
```

格式：代號`.TW`（上市）或 代號`.TWO`（上櫃）

### 修改篩選參數

同樣在 `backend/config.json`：

```jsonc
{
  "screener_params": {       // 買進條件
    "ma_period": 5,          // 均線天數
    "rsi_low": 30,           // RSI 買進區間下限
    "rsi_high": 50,          // RSI 買進區間上限
    "pe_multiple": 20,       // 合理本益比倍數
    "yoy_min": 10,           // 最低 YoY 成長率 (%)
    "vol_ratio_min": 1.5     // 量能倍數門檻
  },
  "sell_params": {           // 賣出條件
    "rsi_sell_low": 60,      // RSI 過熱區間下限
    "rsi_sell_high": 90      // RSI 過熱區間上限
  }
}
```

---

## 資料來源

- **股價 / 技術指標**：Yahoo Finance（透過 yfinance）
- **營收 YoY**：Yahoo Finance info API
- **EPS 預估**：Yahoo Finance forwardEps

### 替代資料源

如果 Yahoo Finance 不穩定，可以考慮：

| 資料源 | 優點 | 缺點 |
|--------|------|------|
| [FinMind](https://finmindtrade.com/) | 台股專用、資料完整 | 免費版有流量限制 |
| [證交所 OpenData](https://openapi.twse.com.tw/) | 官方資料 | 需要自己處理格式 |
| [twstock](https://github.com/mlouielu/twstock) | Python 套件、簡單 | 只有基本資料 |

---

## 注意事項

- 這是工具，不是投資建議。篩選結果僅供參考。
- Yahoo Finance 的台股資料偶爾會有延遲或缺漏。
- 建議搭配其他資訊源交叉驗證再做決策。

---

## License

MIT — 自用隨意改。

# 選股雷達 Stock Screener

給姊姊看的台股篩選儀表板。每天自動篩選符合條件的標的，打開網頁就能看。

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

每天收盤後自動跑 Python 腳本，更新資料：

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

### 修改掃描股票清單

編輯 `backend/screener.py` 裡的 `DEFAULT_TICKERS`：

```python
DEFAULT_TICKERS = [
    "2330.TW",   # 台積電
    "2454.TW",   # 聯發科
    # 加入你想掃描的股票...
]
```

### 修改篩選參數

```python
CONFIG = {
    "ma_period": 5,        # 改成 10 就是用 MA10
    "rsi_low": 30,         # RSI 下限
    "rsi_high": 50,        # RSI 上限
    "pe_multiple": 20,     # 本益比倍數
    "yoy_min": 10,         # YoY 最低成長率
    "vol_ratio_min": 1.5,  # 量能倍數門檻
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

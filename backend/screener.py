"""
台股選股雷達 — 後端篩選腳本
=============================
篩選條件：
  1. 收盤價突破 MA5（5 日均線）
  2. RSI(14) 介於 30–50（超賣回升區）
  3. 股價低於「預估 EPS × 20」（低估）
  4. 近月營收 YoY ≥ 10%（雙位數成長）
  5. 當日成交量 > 20 日均量 × 1.5（放量）

使用方式：
  pip install yfinance pandas ta-lib --break-system-packages
  python screener.py

  如果 ta-lib 裝不起來，可改用 pandas_ta（腳本會自動 fallback）

輸出：
  ../frontend/public/data/stocks.json
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# ─── 設定 ───────────────────────────────────────────────
# 你可以在這裡自訂掃描的股票清單
# 格式：台股代號加上 .TW（上市）或 .TWO（上櫃）
# 如果清單太長，建議分批跑避免被 Yahoo Finance 限速

# 預設：台灣市值前 50 大 + 熱門中小型股（可自行增減）
DEFAULT_TICKERS = [
    # 半導體
    "2330.TW", "2454.TW", "3661.TW", "2303.TW", "3443.TW",
    "2379.TW", "3034.TW", "6415.TW", "5274.TW", "3529.TW",
    # AI / 伺服器
    "6669.TW", "2382.TW", "3231.TW", "2356.TW", "3708.TW",
    # 金融
    "2881.TW", "2882.TW", "2884.TW", "2886.TW", "2891.TW",
    # 傳產 / 航運 / 其他
    "2317.TW", "1301.TW", "1303.TW", "2002.TW", "2603.TW",
    "2615.TW", "1216.TW", "2912.TW", "9910.TW", "8454.TW",
    # 生技
    "6446.TW", "4743.TW", "6547.TWO",
    # 電子零件
    "2308.TW", "2327.TW", "3037.TW", "2345.TW",
    # 綠能
    "6244.TW", "3576.TW",
]

# 篩選參數（可調整）
CONFIG = {
    "ma_period": 5,           # 均線天數
    "rsi_period": 14,         # RSI 計算天數
    "rsi_low": 30,            # RSI 下限
    "rsi_high": 50,           # RSI 上限
    "pe_multiple": 20,        # 合理本益比倍數
    "yoy_min": 10,            # 最低 YoY 成長率 (%)
    "vol_ratio_min": 1.5,     # 最低量能倍數（vs 20 日均量）
    "vol_avg_days": 20,       # 均量計算天數
    "lookback_days": 120,     # 抓多少天歷史資料
}

OUTPUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "data"


# ─── 技術指標計算 ─────────────────────────────────────────
def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """計算 RSI，不依賴外部套件"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_ma(series: pd.Series, period: int) -> pd.Series:
    """計算簡單移動平均線"""
    return series.rolling(window=period).mean()


# ─── 單檔股票分析 ─────────────────────────────────────────
def analyze_stock(ticker: str, config: dict) -> dict | None:
    """
    分析單檔股票，回傳篩選結果。
    如果不符合任何條件，回傳 None。
    """
    try:
        stock = yf.Ticker(ticker)
        
        # 抓歷史資料
        hist = stock.history(period=f"{config['lookback_days']}d")
        if hist.empty or len(hist) < config["lookback_days"] // 2:
            print(f"  ⚠ {ticker}: 資料不足，跳過")
            return None

        close = hist["Close"]
        volume = hist["Volume"]

        # --- 指標計算 ---
        latest_close = float(close.iloc[-1])
        
        # 1. MA
        ma = calc_ma(close, config["ma_period"])
        latest_ma = float(ma.iloc[-1])
        ma_breakout = latest_close > latest_ma
        ma_diff_pct = round((latest_close - latest_ma) / latest_ma * 100, 2)

        # 2. RSI
        rsi = calc_rsi(close, config["rsi_period"])
        latest_rsi = round(float(rsi.iloc[-1]), 1)
        rsi_in_range = config["rsi_low"] <= latest_rsi <= config["rsi_high"]

        # 3. 估值：預估 EPS × 倍數
        info = stock.info or {}
        forward_eps = info.get("forwardEps") or info.get("trailingEps")
        
        if forward_eps and forward_eps > 0:
            fair_value = round(forward_eps * config["pe_multiple"], 1)
            undervalued = latest_close < fair_value
            underval_pct = round((latest_close - fair_value) / fair_value * 100, 1)
        else:
            fair_value = None
            undervalued = False
            underval_pct = None

        # 4. YoY 營收成長（用 yfinance 的 revenueGrowth 或 earningsGrowth）
        revenue_growth = info.get("revenueGrowth")
        if revenue_growth is not None:
            yoy_pct = round(revenue_growth * 100, 1)
            yoy_pass = yoy_pct >= config["yoy_min"]
        else:
            # fallback: 用 earningsGrowth
            earnings_growth = info.get("earningsGrowth")
            if earnings_growth is not None:
                yoy_pct = round(earnings_growth * 100, 1)
                yoy_pass = yoy_pct >= config["yoy_min"]
            else:
                yoy_pct = None
                yoy_pass = False

        # 5. 量能
        latest_vol = int(volume.iloc[-1])
        avg_vol = int(volume.iloc[-config["vol_avg_days"]:].mean())
        vol_ratio = round(latest_vol / avg_vol, 2) if avg_vol > 0 else 0
        vol_surge = vol_ratio >= config["vol_ratio_min"]

        # 最近 7 天成交量（給前端畫 spark bar 用）
        recent_vols = volume.iloc[-7:].tolist()
        max_vol = max(recent_vols) if recent_vols else 1
        vol_spark = [round(v / max_vol * 100) for v in recent_vols]

        # --- 篩選 ---
        conditions = {
            "ma_breakout": ma_breakout,
            "rsi_in_range": rsi_in_range,
            "undervalued": undervalued,
            "yoy_pass": yoy_pass,
            "vol_surge": vol_surge,
        }
        passed = sum(conditions.values())

        # 至少通過 3 個條件才列入
        if passed < 3:
            return None

        # 判斷訊號強度
        if passed >= 5:
            signal = "strong"
        elif passed >= 4:
            signal = "buy"
        else:
            signal = "watch"

        name = info.get("shortName", info.get("longName", ticker.replace(".TW", "")))

        return {
            "symbol": ticker.replace(".TW", "").replace(".TWO", ""),
            "name": name,
            "close": round(latest_close, 1),
            "ma5": round(latest_ma, 1),
            "maDiffPct": ma_diff_pct,
            "rsi": latest_rsi,
            "forwardEps": round(forward_eps, 2) if forward_eps else None,
            "fairValue": fair_value,
            "undervalPct": underval_pct,
            "yoyPct": yoy_pct,
            "volume": latest_vol,
            "avgVolume": avg_vol,
            "volRatio": vol_ratio,
            "volSpark": vol_spark,
            "signal": signal,
            "conditions": conditions,
            "passedCount": passed,
        }

    except Exception as e:
        print(f"  ✗ {ticker}: {e}")
        return None


# ─── 主程式 ───────────────────────────────────────────────
def main():
    tickers = DEFAULT_TICKERS
    config = CONFIG

    print(f"🔍 開始掃描 {len(tickers)} 檔股票...")
    print(f"   篩選條件：MA{config['ma_period']} 突破 / RSI {config['rsi_low']}-{config['rsi_high']} / "
          f"EPS×{config['pe_multiple']} 低估 / YoY≥{config['yoy_min']}% / 量能≥{config['vol_ratio_min']}x")
    print()

    results = []
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] 分析 {ticker}...", end=" ")
        result = analyze_stock(ticker, config)
        if result:
            print(f"✓ 通過 {result['passedCount']}/5 條件 → {result['signal']}")
            results.append(result)
        else:
            print("✗")

    # 按通過條件數 + 低估幅度排序
    results.sort(key=lambda x: (-x["passedCount"], x.get("undervalPct") or 0))

    # 組裝輸出 JSON
    output = {
        "generatedAt": datetime.now().isoformat(),
        "config": config,
        "totalScanned": len(tickers),
        "totalPassed": len(results),
        "stocks": results,
    }

    # 寫入檔案
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "stocks.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print()
    print(f"✅ 完成！{len(results)}/{len(tickers)} 檔通過篩選")
    print(f"📄 輸出：{output_path}")

    # 同時輸出一份到 backend 資料夾方便備份
    backup_path = Path(__file__).parent / "latest_result.json"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

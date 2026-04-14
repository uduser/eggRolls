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
# 所有標的清單與篩選參數統一放在 config.json
# 修改 config.json 即可，不需動程式碼

CONFIG_PATH = Path(__file__).parent / "config.json"
OUTPUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "data"


def load_config() -> dict:
    """讀取 config.json，回傳完整設定"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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


def calc_yoy_trend_down(stock) -> tuple[bool, list | None]:
    """
    用季報營收判斷 YoY 趨勢是否向下。
    比較最近兩季各自的 YoY（本季 vs 去年同季），
    若最新一季 YoY < 前一季 YoY 即為下降趨勢。
    回傳 (is_down, [前季YoY%, 最新季YoY%] or None)
    """
    try:
        qis = stock.quarterly_income_stmt
        if qis is None or qis.empty:
            return False, None

        # 找 Total Revenue 行
        rev_key = None
        for k in qis.index:
            if "Total Revenue" in str(k):
                rev_key = k
                break
        if rev_key is None:
            return False, None

        rev = qis.loc[rev_key].dropna().sort_index()  # 由舊到新
        if len(rev) < 6:
            return False, None

        # 最新季 YoY：rev[-1] vs rev[-5]（4 季前同期）
        # 前一季 YoY：rev[-2] vs rev[-6]
        latest_yoy = (rev.iloc[-1] - rev.iloc[-5]) / abs(rev.iloc[-5]) * 100
        prev_yoy = (rev.iloc[-2] - rev.iloc[-6]) / abs(rev.iloc[-6]) * 100

        return latest_yoy < prev_yoy, [round(prev_yoy, 1), round(latest_yoy, 1)]
    except Exception:
        return False, None


# ─── 單檔股票分析 ─────────────────────────────────────────
def analyze_stock(ticker: str, config: dict, sell_params: dict | None = None,
                  min_conditions: int = 3, name_map: dict | None = None) -> dict | None:
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

        # 至少通過指定條件數才列入
        if passed < min_conditions:
            return None

        # 判斷訊號強度
        if passed >= 5:
            signal = "strong"
        elif passed >= 4:
            signal = "buy"
        elif passed >= 3:
            signal = "watch"
        else:
            signal = "hold"

        name = (name_map or {}).get(ticker) or info.get("shortName", info.get("longName", ticker.replace(".TW", "").replace(".TWO", "")))

        # --- 賣出訊號 ---
        sell_conditions = None
        sell_passed_count = 0
        sell_signal = None
        yoy_trend = None

        if sell_params:
            # 1. 跌破均線：收盤 < MA
            sell_ma_below = latest_close < latest_ma
            # 2. RSI 過熱區
            sell_rsi = sell_params["rsi_sell_low"] <= latest_rsi <= sell_params["rsi_sell_high"]
            # 3. YoY 趨勢向下（季報營收）
            sell_yoy_down, yoy_trend = calc_yoy_trend_down(stock)

            sell_conditions = {
                "ma_below": sell_ma_below,
                "rsi_overbought": sell_rsi,
                "yoy_trend_down": sell_yoy_down,
            }
            sell_passed_count = sum(sell_conditions.values())

            if sell_passed_count >= 3:
                sell_signal = "sell"
            elif sell_passed_count >= 2:
                sell_signal = "caution"

        return {
            "symbol": ticker.replace(".TW", "").replace(".TWO", ""),
            "exchange": "TPEX" if ".TWO" in ticker else "TWSE",
            "name": name,
            "close": round(latest_close, 1),
            "ma5": round(latest_ma, 1),
            "maDiffPct": ma_diff_pct,
            "rsi": latest_rsi,
            "forwardEps": round(forward_eps, 2) if forward_eps else None,
            "fairValue": fair_value,
            "undervalPct": underval_pct,
            "yoyPct": yoy_pct,
            "yoyTrend": yoy_trend,
            "volume": latest_vol,
            "avgVolume": avg_vol,
            "volRatio": vol_ratio,
            "volSpark": vol_spark,
            "signal": signal,
            "conditions": conditions,
            "passedCount": passed,
            "sellConditions": sell_conditions,
            "sellPassedCount": sell_passed_count,
            "sellSignal": sell_signal,
        }

    except Exception as e:
        print(f"  ✗ {ticker}: {e}")
        return None


# ─── 主程式 ───────────────────────────────────────────────
def main():
    cfg = load_config()
    tickers = cfg["screener_tickers"]
    config = cfg["screener_params"]
    sell_params = cfg.get("sell_params")
    name_map = cfg.get("name_map", {})

    print(f"🔍 開始掃描 {len(tickers)} 檔股票...")
    print(f"   篩選條件：MA{config['ma_period']} 突破 / RSI {config['rsi_low']}-{config['rsi_high']} / "
          f"EPS×{config['pe_multiple']} 低估 / YoY≥{config['yoy_min']}% / 量能≥{config['vol_ratio_min']}x")
    if sell_params:
        print(f"   賣出條件：跌破 MA / RSI {sell_params['rsi_sell_low']}-{sell_params['rsi_sell_high']} / YoY 趨勢向下")
    print()

    results = []
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] 分析 {ticker}...", end=" ")
        result = analyze_stock(ticker, config, sell_params=sell_params, name_map=name_map)
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

    # ─── 手上標的掃描 ─────────────────────────────────────
    portfolio_tickers = cfg["portfolio_tickers"]
    print(f"\n📊 掃描手上標的 {len(portfolio_tickers)} 檔...")

    portfolio_results = []
    for i, ticker in enumerate(portfolio_tickers, 1):
        print(f"  [{i}/{len(portfolio_tickers)}] 分析 {ticker}...", end=" ")
        result = analyze_stock(ticker, config, sell_params=sell_params, min_conditions=0, name_map=name_map)
        if result:
            print(f"✓ {result['passedCount']}/5 條件 → {result['signal']}")
            portfolio_results.append(result)
        else:
            print("✗ 資料取得失敗")

    portfolio_results.sort(key=lambda x: (-x["passedCount"], x.get("undervalPct") or 0))

    portfolio_output = {
        "generatedAt": datetime.now().isoformat(),
        "config": config,
        "totalHoldings": len(portfolio_tickers),
        "totalFetched": len(portfolio_results),
        "stocks": portfolio_results,
    }

    portfolio_path = OUTPUT_DIR / "portfolio.json"
    with open(portfolio_path, "w", encoding="utf-8") as f:
        json.dump(portfolio_output, f, ensure_ascii=False, indent=2)

    print(f"✅ 手上標的完成！{len(portfolio_results)}/{len(portfolio_tickers)} 檔取得資料")
    print(f"📄 輸出：{portfolio_path}")


if __name__ == "__main__":
    main()

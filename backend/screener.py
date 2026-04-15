"""
台股選股雷達 — 後端篩選腳本
=============================
篩選條件：
  1. 收盤價突破 MA5（前日 < MA，今日 > MA）
  2. RSI(14) 介於 30–50（超賣回升區）
  3. 股價低於「預估 EPS × 20」（低估）
  4. 近月營收 YoY ≥ 10%（雙位數成長）
  5. 當日成交量 > 20 日均量 × 1.5（放量）

使用方式：
  pip install yfinance pandas requests --break-system-packages
  python screener.py

輸出：
  ../frontend/public/data/stocks.json
  ../frontend/public/data/portfolio.json
"""

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

TW_TZ = timezone(timedelta(hours=8))
MAX_WORKERS = 10

# ─── 設定 ───────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.json"
OUTPUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "data"
LOGOS_DIR = Path(__file__).parent.parent / "frontend" / "public" / "logos"


def load_config() -> dict:
    """讀取 config.json，回傳完整設定"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 全市場股票清單 ──────────────────────────────────────
def fetch_all_tw_tickers() -> tuple[list[str], dict[str, str]]:
    """從 TWSE / TPEX 官方 API 抓取全市場股票代號與中文名稱。
    回傳 (tickers, auto_name_map)，僅一般股票（排除 ETF / 債券 / 權證）。
    """
    tickers = []
    names = {}  # {"2330.TW": "台積電", ...}

    # ── TWSE 上市 ──
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            timeout=30,
        )
        r.raise_for_status()
        for item in r.json():
            code = item.get("Code", "").strip()
            # 4 碼數字 ≥ 1100 → 一般上市股票（排除 ETF 0050 等）
            if code.isdigit() and len(code) == 4 and int(code) >= 1100:
                ticker = f"{code}.TW"
                tickers.append(ticker)
                name = item.get("Name", "").strip()
                if name:
                    names[ticker] = name
    except Exception as e:
        print(f"  ⚠ TWSE API 失敗: {e}")

    # ── TPEX 上櫃 ──
    try:
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
            timeout=30,
        )
        r.raise_for_status()
        for item in r.json():
            code = item.get("SecuritiesCompanyCode", "").strip()
            if code.isdigit() and len(code) == 4 and int(code) >= 1100:
                ticker = f"{code}.TWO"
                tickers.append(ticker)
                name = item.get("CompanyName", "").strip()
                if name:
                    names[ticker] = name
    except Exception as e:
        print(f"  ⚠ TPEX API 失敗: {e}")

    return tickers, names


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

        rev_key = None
        for k in qis.index:
            if "Total Revenue" in str(k):
                rev_key = k
                break
        if rev_key is None:
            return False, None

        rev = qis.loc[rev_key].dropna().sort_index()
        if len(rev) < 6:
            return False, None

        latest_yoy = (rev.iloc[-1] - rev.iloc[-5]) / abs(rev.iloc[-5]) * 100
        prev_yoy = (rev.iloc[-2] - rev.iloc[-6]) / abs(rev.iloc[-6]) * 100

        return latest_yoy < prev_yoy, [round(prev_yoy, 1), round(latest_yoy, 1)]
    except Exception:
        return False, None


# ─── 單檔股票分析 ─────────────────────────────────────────
def analyze_stock(ticker: str, config: dict, sell_params: dict | None = None,
                  min_conditions: int = 3, name_map: dict | None = None,
                  quiet: bool = False) -> dict | None:
    """
    分析單檔股票，回傳篩選結果。
    如果不符合條件或資料不足，回傳 None。
    """
    try:
        stock = yf.Ticker(ticker)

        hist = stock.history(period=f"{config['lookback_days']}d")
        if hist.empty or len(hist) < config["lookback_days"] // 2:
            if not quiet:
                print(f"  ⚠ {ticker}: 資料不足，跳過")
            return None

        close = hist["Close"]
        volume = hist["Volume"]

        # --- 指標計算 ---
        latest_close = float(close.iloc[-1])

        # 1. MA：前一天收盤 < MA，當天收盤 > MA → 突破
        ma = calc_ma(close, config["ma_period"])
        latest_ma = float(ma.iloc[-1])
        prev_close = float(close.iloc[-2])
        prev_ma = float(ma.iloc[-2])
        ma_breakout = prev_close < prev_ma and latest_close > latest_ma
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

        # 4. YoY 營收成長
        revenue_growth = info.get("revenueGrowth")
        if revenue_growth is not None:
            yoy_pct = round(revenue_growth * 100, 1)
            yoy_pass = yoy_pct >= config["yoy_min"]
        else:
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

        # 最近 7 天成交量（前端 spark bar 用）
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

        if passed < min_conditions:
            return None

        if passed >= 5:
            signal = "strong"
        elif passed >= 4:
            signal = "buy"
        elif passed >= 3:
            signal = "watch"
        else:
            signal = "hold"

        name = (name_map or {}).get(ticker) or info.get(
            "shortName", info.get("longName", ticker.replace(".TW", "").replace(".TWO", ""))
        )

        # --- 賣出訊號 ---
        sell_conditions = None
        sell_passed_count = 0
        sell_signal = None
        yoy_trend = None

        if sell_params:
            sell_ma_below = prev_close > prev_ma and latest_close < latest_ma
            sell_rsi = sell_params["rsi_sell_low"] <= latest_rsi <= sell_params["rsi_sell_high"]
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
        if not quiet:
            print(f"  ✗ {ticker}: {e}")
        return None


# ─── Logo 自動產生 ────────────────────────────────────────
def generate_logo_svg(symbol: str, name: str, output_dir: Path):
    """為缺少 logo 的標的自動產生簡易 SVG 圖示"""
    logo_path = output_dir / f"{symbol}.svg"
    if logo_path.exists():
        return False

    colors = [
        "#E40001", "#0066CC", "#00A651", "#F5A623",
        "#9B59B6", "#1ABC9C", "#E74C3C", "#3498DB",
    ]
    h = sum(ord(c) for c in symbol) % len(colors)
    bg = colors[h]

    display = name if name and len(name) <= 4 else symbol
    size = 22 if len(display) <= 2 else 16 if len(display) <= 4 else 13

    svg = (
        f'<svg width="56" height="56" xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="56" height="56" rx="4" fill="{bg}"/>'
        f'<text x="28" y="30" text-anchor="middle" dominant-baseline="central" '
        f'fill="#fff" font-family="Arial,sans-serif" font-size="{size}" '
        f'font-weight="700">{display}</text></svg>'
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    logo_path.write_text(svg, encoding="utf-8")
    return True


def ensure_logos(tickers: list[str], name_map: dict):
    """檢查所有標的是否都有 logo，缺少的自動產生"""
    generated = []
    for ticker in tickers:
        symbol = ticker.replace(".TW", "").replace(".TWO", "")
        name = name_map.get(ticker, "")
        if generate_logo_svg(symbol, name, LOGOS_DIR):
            generated.append(symbol)
    if generated:
        print(f"🖼️  自動產生 {len(generated)} 個 logo")


# ─── 多執行緒掃描 ────────────────────────────────────────
def scan_stocks_parallel(tickers: list[str], config: dict, sell_params, name_map: dict) -> list[dict]:
    """用多執行緒掃描大量股票，回傳通過篩選的結果"""
    results = []
    total = len(tickers)
    progress = {"done": 0, "passed": 0}
    lock = threading.Lock()

    def worker(ticker):
        result = analyze_stock(
            ticker, config, sell_params=sell_params, name_map=name_map, quiet=True,
        )
        with lock:
            progress["done"] += 1
            if result:
                progress["passed"] += 1
            done = progress["done"]
            if done % 100 == 0 or done == total:
                print(f"  進度 {done}/{total} ({done * 100 // total}%) — 通過 {progress['passed']} 檔")
        return result

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(worker, t) for t in tickers]
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception:
                pass

    return results


# ─── 主程式 ───────────────────────────────────────────────
def main():
    cfg = load_config()
    config = cfg["screener_params"]
    sell_params = cfg.get("sell_params")
    name_map = cfg.get("name_map", {})

    # ─── 全市場掃描 ─────────────────────────────────────
    print("📡 從 TWSE / TPEX 抓取全市場股票清單...")
    tickers, auto_names = fetch_all_tw_tickers()

    # 合併中文名稱：API 抓到的 + config.json 手動覆寫（手動優先）
    merged_names = {**auto_names, **name_map}
    name_map = merged_names

    if not tickers:
        # API 失敗時 fallback 到 config.json
        tickers = cfg.get("screener_tickers", [])
        print(f"  ⚠ API 無回應，使用 config.json 的 {len(tickers)} 檔標的")
    else:
        print(f"  ✓ 共取得 {len(tickers)} 檔股票（上市 + 上櫃）")

    print(f"\n🔍 開始掃描 {len(tickers)} 檔股票（{MAX_WORKERS} 執行緒）...")
    print(f"   篩選條件：MA{config['ma_period']} 突破 / RSI {config['rsi_low']}-{config['rsi_high']} / "
          f"EPS×{config['pe_multiple']} 低估 / YoY≥{config['yoy_min']}% / 量能≥{config['vol_ratio_min']}x")
    if sell_params:
        print(f"   賣出條件：跌破 MA / RSI {sell_params['rsi_sell_low']}-{sell_params['rsi_sell_high']} / YoY 趨勢向下")
    print()

    results = scan_stocks_parallel(tickers, config, sell_params, name_map)
    results.sort(key=lambda x: (-x["passedCount"], x.get("undervalPct") or 0))

    # 組裝輸出 JSON
    output = {
        "generatedAt": datetime.now(TW_TZ).isoformat(),
        "config": config,
        "totalScanned": len(tickers),
        "totalPassed": len(results),
        "stocks": results,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "stocks.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print()
    print(f"✅ 大盤掃描完成！{len(results)}/{len(tickers)} 檔通過篩選")
    print(f"📄 輸出：{output_path}")

    # 備份
    backup_path = Path(__file__).parent / "latest_result.json"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ─── 手上標的掃描（單執行緒，數量少） ─────────────────
    portfolio_tickers = cfg.get("portfolio_tickers", [])
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
        "generatedAt": datetime.now(TW_TZ).isoformat(),
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

    # ─── Logo 自動產生（只為通過的標的 + 持有標的） ────────
    passed_tickers = [
        f"{r['symbol']}.{'TWO' if r['exchange'] == 'TPEX' else 'TW'}"
        for r in results + portfolio_results
    ]
    ensure_logos(passed_tickers, name_map)

    # 複製 config.json 到 frontend/public/data/ 供 production 讀取
    config_copy_path = OUTPUT_DIR / "config.json"
    with open(config_copy_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"📄 設定檔複製：{config_copy_path}")


if __name__ == "__main__":
    main()

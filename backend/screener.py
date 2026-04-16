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
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

TW_TZ = timezone(timedelta(hours=8))
MAX_WORKERS = 4
MAX_RETRIES = 3
RETRY_BASE_SECONDS = 1.5
REQUEST_JITTER_RANGE = (0.05, 0.2)
BATCH_PAUSE_EVERY = 120
BATCH_PAUSE_SECONDS = 2.0
HISTORY_TIMEOUT_SECONDS = 12
FINMIND_TIMEOUT_SECONDS = 12
FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_MIN_LOOKBACK_DAYS = 240
RETRYABLE_ERROR_KEYWORDS = (
    "Too Many Requests",
    "Rate limited",
    "429",
    "Invalid Crumb",
    "Unauthorized",
    "temporarily unavailable",
    "Service Unavailable",
    "Bad Gateway",
    "timed out",
    "Connection",
)

# ─── 設定 ───────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.json"
OUTPUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "data"
LOGOS_DIR = Path(__file__).parent.parent / "frontend" / "public" / "logos"


def load_config() -> dict:
    """讀取 config.json，回傳完整設定"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 全市場股票清單 ──────────────────────────────────────
def fetch_all_tw_tickers() -> tuple[list[str], dict[str, str], set[str]]:
    """從 TWSE / TPEX 官方 API 抓取全市場股票代號與中文名稱。
    回傳 (tickers, auto_name_map, zero_volume_tickers)，僅一般股票（排除 ETF / 債券 / 權證）。
    zero_volume_tickers: 當日成交量為 0 的標的（停牌/冷門），可用來預過濾。
    """
    tickers = []
    names = {}  # {"2330.TW": "台積電", ...}
    zero_vol = set()

    def _parse_vol(raw) -> int:
        """把逗號分隔的成交量字串轉 int，失敗回 0"""
        try:
            return int(str(raw).replace(",", "").strip())
        except (ValueError, TypeError):
            return 0

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
                vol = _parse_vol(item.get("TradeVolume"))
                if vol == 0:
                    zero_vol.add(ticker)
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
                vol = _parse_vol(item.get("TradingVolume") or item.get("TradeVolume"))
                if vol == 0:
                    zero_vol.add(ticker)
    except Exception as e:
        print(f"  ⚠ TPEX API 失敗: {e}")

    return tickers, names, zero_vol


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


def fetch_finmind_history(ticker: str, lookback_days: int) -> pd.DataFrame:
    """
    從 FinMind 抓日線價量，回傳欄位對齊 yfinance 的 DataFrame。
    失敗時回傳空 DataFrame。
    """
    stock_id = ticker.replace(".TW", "").replace(".TWO", "").upper()
    start_date = (datetime.now(TW_TZ) - timedelta(days=max(lookback_days * 2, FINMIND_MIN_LOOKBACK_DAYS))).date().isoformat()
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date,
    }
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if token:
        params["token"] = token

    try:
        resp = requests.get(FINMIND_API_URL, params=params, timeout=FINMIND_TIMEOUT_SECONDS)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data") or []
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        if "date" not in df.columns or "close" not in df.columns:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
        if df.empty:
            return pd.DataFrame()

        hist = pd.DataFrame(index=df["date"])
        hist["Close"] = pd.to_numeric(df["close"], errors="coerce")
        hist["Volume"] = pd.to_numeric(df.get("Trading_Volume"), errors="coerce").fillna(0)
        if "open" in df.columns:
            hist["Open"] = pd.to_numeric(df["open"], errors="coerce")
        if "max" in df.columns:
            hist["High"] = pd.to_numeric(df["max"], errors="coerce")
        if "min" in df.columns:
            hist["Low"] = pd.to_numeric(df["min"], errors="coerce")
        hist = hist.dropna(subset=["Close"])
        return hist
    except Exception:
        return pd.DataFrame()


def _finmind_get(dataset: str, params: dict | None = None, timeout: int = 30) -> list[dict]:
    """FinMind 共用 GET，自動帶 token。回傳 data list，失敗回空 list。"""
    base = {
        "dataset": dataset,
        **(params or {}),
    }
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if token:
        base["token"] = token
    try:
        resp = requests.get(FINMIND_API_URL, params=base, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("data") or []
    except Exception as e:
        print(f"  ⚠ FinMind {dataset} 失敗: {e}")
        return []


def fetch_bulk_fundamentals() -> dict[str, dict]:
    """批次從 FinMind 抓 PER 和月營收，回傳 {ticker: {pe, eps, yoy_pct}, ...}。

    ticker 格式為 "2330.TW" / "6547.TWO"（與 config 一致）。
    需要 FINMIND_TOKEN 才能執行批次查詢（不帶 data_id）。
    """
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if not token:
        print("  ⚠ FINMIND_TOKEN 未設定，跳過批次基本面抓取（將 fallback 到 Yahoo stock.info）")
        return {}

    result: dict[str, dict] = {}

    # ── 1. PER（本益比）→ 反推 EPS ──
    # 取最近 10 個交易日，避免剛好遇到假日空值
    per_start = (datetime.now(TW_TZ) - timedelta(days=14)).date().isoformat()
    print("  📥 從 FinMind 批次抓取 PER...")
    per_rows = _finmind_get("TaiwanStockPER", {"start_date": per_start})
    if per_rows:
        df = pd.DataFrame(per_rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date")
        # 取每檔最新一筆
        latest = df.groupby("stock_id").last().reset_index()
        for _, row in latest.iterrows():
            sid = str(row["stock_id"]).strip()
            per = pd.to_numeric(row.get("PER"), errors="coerce")
            close = pd.to_numeric(row.get("close"), errors="coerce")
            entry: dict = {}
            if pd.notna(per) and per > 0:
                entry["pe"] = round(float(per), 2)
                if pd.notna(close) and close > 0:
                    entry["eps"] = round(float(close) / float(per), 2)
            # 先用純代號當 key，後面再對應 .TW / .TWO
            result[sid] = entry
        print(f"  ✓ PER 取得 {len(latest)} 檔")
    else:
        print("  ⚠ PER 無資料")

    # ── 2. 月營收 → 算 YoY ──
    # 取最近 14 個月，確保能拿到去年同月
    rev_start = (datetime.now(TW_TZ) - timedelta(days=430)).date().isoformat()
    print("  📥 從 FinMind 批次抓取月營收...")
    rev_rows = _finmind_get("TaiwanStockMonthRevenue", {"start_date": rev_start}, timeout=60)
    if rev_rows:
        df = pd.DataFrame(rev_rows)
        df["revenue"] = pd.to_numeric(df.get("revenue"), errors="coerce")
        df["revenue_month"] = pd.to_numeric(df.get("revenue_month"), errors="coerce")
        df["revenue_year"] = pd.to_numeric(df.get("revenue_year"), errors="coerce")
        df = df.dropna(subset=["revenue", "revenue_month", "revenue_year"])

        # 每檔取最新月份 + 去年同月
        for sid, grp in df.groupby("stock_id"):
            sid = str(sid).strip()
            grp = grp.sort_values(["revenue_year", "revenue_month"])
            if grp.empty:
                continue
            latest_row = grp.iloc[-1]
            latest_month = int(latest_row["revenue_month"])
            latest_year = int(latest_row["revenue_year"])
            # 找去年同月
            prev_year_rows = grp[
                (grp["revenue_year"] == latest_year - 1) &
                (grp["revenue_month"] == latest_month)
            ]
            if prev_year_rows.empty:
                continue
            prev_rev = float(prev_year_rows.iloc[-1]["revenue"])
            curr_rev = float(latest_row["revenue"])
            if prev_rev > 0:
                yoy = round((curr_rev - prev_rev) / prev_rev * 100, 1)
                entry = result.get(sid, {})
                entry["yoy_pct"] = yoy
                result[sid] = entry
        yoy_count = sum(1 for v in result.values() if "yoy_pct" in v)
        print(f"  ✓ 月營收 YoY 計算完成，{yoy_count} 檔有資料")
    else:
        print("  ⚠ 月營收無資料")

    return result


def fetch_bulk_history(lookback_days: int) -> dict[str, pd.DataFrame]:
    """從 FinMind 一次抓全市場日線價量，回傳 {stock_id: DataFrame, ...}。

    DataFrame 欄位對齊 yfinance: Close, Volume, Open, High, Low。
    需要 FINMIND_TOKEN。
    """
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if not token:
        print("  ⚠ FINMIND_TOKEN 未設定，跳過批次價量抓取")
        return {}

    # 多抓一些天數，確保扣除假日後仍有足夠交易日
    start = (datetime.now(TW_TZ) - timedelta(days=lookback_days * 2)).date().isoformat()
    print(f"  📥 從 FinMind 批次抓取全市場價量（{start} 起）...")
    rows = _finmind_get("TaiwanStockPrice", {"start_date": start}, timeout=120)
    if not rows:
        print("  ⚠ 全市場價量無資料")
        return {}

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")

    result: dict[str, pd.DataFrame] = {}
    for sid, grp in df.groupby("stock_id"):
        sid = str(sid).strip()
        grp = grp.sort_values("date")
        hist = pd.DataFrame(index=grp["date"].values)
        hist["Close"] = pd.to_numeric(grp["close"].values, errors="coerce")
        hist["Volume"] = pd.to_numeric(grp["Trading_Volume"].values, errors="coerce").fillna(0)
        if "open" in grp.columns:
            hist["Open"] = pd.to_numeric(grp["open"].values, errors="coerce")
        if "max" in grp.columns:
            hist["High"] = pd.to_numeric(grp["max"].values, errors="coerce")
        if "min" in grp.columns:
            hist["Low"] = pd.to_numeric(grp["min"].values, errors="coerce")
        hist = hist.dropna(subset=["Close"])
        if not hist.empty:
            result[sid] = hist

    print(f"  ✓ 全市場價量取得 {len(result)} 檔")
    return result


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
class AnalysisError(Exception):
    """analyze_stock 發生錯誤時拋出，用來和「條件不符」區分"""
    pass


def is_retryable_error(msg: str) -> bool:
    text = (msg or "").lower()
    return any(keyword.lower() in text for keyword in RETRYABLE_ERROR_KEYWORDS)


def analyze_with_retry(
    ticker: str,
    config: dict,
    sell_params: dict | None = None,
    min_conditions: int = 3,
    name_map: dict | None = None,
    allow_finmind_fallback: bool = False,
    raise_on_error: bool = False,
    prefetched: dict | None = None,
    prefetched_hist: pd.DataFrame | None = None,
) -> dict | None:
    """包裝 analyze_stock：遇到限流/暫時性錯誤時自動重試。"""
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            return analyze_stock(
                ticker,
                config,
                sell_params=sell_params,
                min_conditions=min_conditions,
                name_map=name_map,
                allow_finmind_fallback=allow_finmind_fallback,
                quiet=True,
                prefetched=prefetched,
                prefetched_hist=prefetched_hist,
            )
        except AnalysisError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1 and is_retryable_error(str(e)):
                sleep_seconds = RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.4)
                time.sleep(sleep_seconds)
                continue
            break

    if raise_on_error and last_error:
        raise last_error
    return None


def analyze_stock(ticker: str, config: dict, sell_params: dict | None = None,
                  min_conditions: int = 3, name_map: dict | None = None,
                  allow_finmind_fallback: bool = False,
                  quiet: bool = False,
                  prefetched: dict | None = None,
                  prefetched_hist: pd.DataFrame | None = None) -> dict | None:
    """
    分析單檔股票，回傳篩選結果。

    prefetched: 從 fetch_bulk_fundamentals() 預抓的基本面資料 {pe, eps, yoy_pct}。
    prefetched_hist: 從 fetch_bulk_history() 預抓的價量 DataFrame。
    有值時跳過 Yahoo API 呼叫，大幅降低限流失敗率。

    條件不符回傳 None；資料錯誤時 quiet=True 拋 AnalysisError，quiet=False 印訊息回傳 None。
    """
    try:
        # ── 價量資料：優先用批次預抓，沒有才逐檔打 Yahoo / FinMind ──
        hist = pd.DataFrame()
        hist_source = "finmind-bulk"
        history_error = None
        stock = None

        if prefetched_hist is not None and not prefetched_hist.empty:
            hist = prefetched_hist
        else:
            stock = yf.Ticker(ticker)
            hist_source = "yahoo"
            try:
                hist = stock.history(
                    period=f"{config['lookback_days']}d",
                    timeout=HISTORY_TIMEOUT_SECONDS,
                )
            except Exception as e:
                history_error = str(e)
                hist = pd.DataFrame()

            if hist.empty and allow_finmind_fallback:
                hist = fetch_finmind_history(ticker, config["lookback_days"])
                if not hist.empty:
                    hist_source = "finmind"

        # 完全沒資料 → 失敗
        if hist.empty:
            msg = "無任何資料" if not history_error else f"無資料 ({history_error})"
            if quiet:
                raise AnalysisError(msg)
            print(f"  ⚠ {ticker}: {msg}，跳過")
            return None

        # 資料太少：大盤篩選跳過，手上標的（min_conditions=0）仍嘗試分析
        if len(hist) < config["lookback_days"] // 2 and min_conditions > 0:
            if quiet:
                raise AnalysisError("資料不足")
            print(f"  ⚠ {ticker}: 資料不足，跳過")
            return None

        close = hist["Close"]
        volume = hist["Volume"]

        # --- 指標計算 ---
        latest_close = float(close.iloc[-1])

        # 1. MA：前一天收盤 < MA，當天收盤 > MA → 突破
        ma = calc_ma(close, config["ma_period"])
        latest_ma = float(ma.iloc[-1]) if not pd.isna(ma.iloc[-1]) else None
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
        prev_ma = float(ma.iloc[-2]) if len(ma) >= 2 and not pd.isna(ma.iloc[-2]) else None

        if latest_ma is not None and prev_close is not None and prev_ma is not None:
            ma_breakout = prev_close < prev_ma and latest_close > latest_ma
            ma_diff_pct = round((latest_close - latest_ma) / latest_ma * 100, 2)
        else:
            ma_breakout = False
            ma_diff_pct = None

        # 2. RSI
        rsi = calc_rsi(close, config["rsi_period"])
        latest_rsi = round(float(rsi.iloc[-1]), 1) if not pd.isna(rsi.iloc[-1]) else None
        rsi_in_range = (config["rsi_low"] <= latest_rsi <= config["rsi_high"]) if latest_rsi is not None else False

        # 3. 估值：預估 EPS × 倍數
        # 優先使用批次預抓的 FinMind 資料，沒有才 fallback 到 stock.info
        forward_eps = None
        info = {}
        if prefetched and prefetched.get("eps"):
            forward_eps = prefetched["eps"]
        else:
            if hist_source == "yahoo" and stock is not None:
                try:
                    info = stock.info or {}
                except Exception:
                    info = {}
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
        # 優先使用批次預抓的 FinMind 月營收 YoY
        yoy_pct = None
        yoy_pass = False
        if prefetched and prefetched.get("yoy_pct") is not None:
            yoy_pct = prefetched["yoy_pct"]
            yoy_pass = yoy_pct >= config["yoy_min"]
        else:
            revenue_growth = info.get("revenueGrowth")
            if revenue_growth is not None:
                yoy_pct = round(revenue_growth * 100, 1)
                yoy_pass = yoy_pct >= config["yoy_min"]
            else:
                earnings_growth = info.get("earningsGrowth")
                if earnings_growth is not None:
                    yoy_pct = round(earnings_growth * 100, 1)
                    yoy_pass = yoy_pct >= config["yoy_min"]

        # 5. 量能
        latest_vol = int(volume.iloc[-1]) if len(volume) > 0 else 0
        vol_window = volume.iloc[-config["vol_avg_days"]:]
        avg_vol = int(vol_window.mean()) if len(vol_window) > 0 else 0
        vol_ratio = round(latest_vol / avg_vol, 2) if avg_vol > 0 else 0
        vol_surge = vol_ratio >= config["vol_ratio_min"]

        # 最近 7 天成交量（前端 spark bar 用）
        recent_vols = volume.iloc[-7:].tolist()
        max_vol = max(recent_vols) if recent_vols else 1
        vol_spark = [round(v / max_vol * 100) for v in recent_vols] if max_vol > 0 else []

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
            sell_ma_below = (prev_close is not None and prev_ma is not None and latest_ma is not None
                            and prev_close > prev_ma and latest_close < latest_ma)
            sell_rsi = (latest_rsi is not None
                        and sell_params["rsi_sell_low"] <= latest_rsi <= sell_params["rsi_sell_high"])
            if hist_source == "yahoo" and stock is not None:
                sell_yoy_down, yoy_trend = calc_yoy_trend_down(stock)
            else:
                sell_yoy_down, yoy_trend = False, None

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
            "ma5": round(latest_ma, 1) if latest_ma is not None else None,
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
            "dataSource": hist_source,
            "signal": signal,
            "conditions": conditions,
            "passedCount": passed,
            "sellConditions": sell_conditions,
            "sellPassedCount": sell_passed_count,
            "sellSignal": sell_signal,
        }

    except AnalysisError:
        raise  # 讓 caller 處理
    except Exception as e:
        if quiet:
            raise AnalysisError(str(e))
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
def scan_stocks_parallel(tickers: list[str], config: dict, sell_params, name_map: dict,
                         bulk_fundamentals: dict | None = None,
                         bulk_history: dict[str, pd.DataFrame] | None = None) -> list[dict]:
    """用多執行緒掃描大量股票，回傳通過篩選的結果，並統計錯誤數"""
    results = []
    total = len(tickers)
    progress = {"done": 0, "passed": 0, "errors": 0}
    error_samples = []  # 記錄前幾筆錯誤原因
    lock = threading.Lock()

    def _ticker_to_id(ticker: str) -> str:
        return ticker.replace(".TW", "").replace(".TWO", "").upper()

    def _get_prefetched(ticker: str) -> dict | None:
        if not bulk_fundamentals:
            return None
        return bulk_fundamentals.get(_ticker_to_id(ticker))

    def _get_hist(ticker: str) -> pd.DataFrame | None:
        if not bulk_history:
            return None
        return bulk_history.get(_ticker_to_id(ticker))

    def worker(ticker):
        pause_now = False
        hist = _get_hist(ticker)
        try:
            # 有預抓價量就不用打 Yahoo，不需要 jitter
            if hist is None:
                time.sleep(random.uniform(*REQUEST_JITTER_RANGE))
            result = analyze_with_retry(
                ticker,
                config,
                sell_params=sell_params,
                name_map=name_map,
                allow_finmind_fallback=True,
                raise_on_error=True,
                prefetched=_get_prefetched(ticker),
                prefetched_hist=hist,
            )
        except AnalysisError as e:
            result = None
            with lock:
                progress["errors"] += 1
                if len(error_samples) < 5:
                    error_samples.append(f"{ticker}: {e}")
        with lock:
            progress["done"] += 1
            if result:
                progress["passed"] += 1
            done = progress["done"]
            if done % 100 == 0 or done == total:
                print(f"  進度 {done}/{total} ({done * 100 // total}%) "
                      f"— 通過 {progress['passed']} 檔 / 失敗 {progress['errors']} 檔")
            if done % BATCH_PAUSE_EVERY == 0 and done < total:
                pause_now = True
        if pause_now:
            print(f"  ⏸️ 批次冷卻 {BATCH_PAUSE_SECONDS:.1f}s，降低限流機率...")
            time.sleep(BATCH_PAUSE_SECONDS)
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

    # 掃描摘要
    errors = progress["errors"]
    if errors > 0:
        pct = errors * 100 // total
        print(f"\n  ⚠ 共 {errors}/{total} 檔失敗 ({pct}%)")
        if pct > 30:
            print(f"  🚨 失敗率偏高，可能被 Yahoo Finance 限流！")
        if error_samples:
            print(f"  錯誤範例：")
            for s in error_samples:
                print(f"    - {s}")

    return results


# ─── 主程式 ───────────────────────────────────────────────
def main():
    cfg = load_config()
    config = cfg["screener_params"]
    sell_params = cfg.get("sell_params")
    name_map = cfg.get("name_map", {})
    skip_tickers = {t.upper() for t in cfg.get("skip_tickers", [])}

    # ─── 抓取全市場股票清單 + 中文名稱 ─────────────────
    print("📡 從 TWSE / TPEX 抓取全市場股票清單...")
    tickers, auto_names, zero_vol = fetch_all_tw_tickers()

    # 合併中文名稱：API 抓到的 + config.json 手動覆寫（手動優先）
    merged_names = {**auto_names, **name_map}
    name_map = merged_names

    if not tickers:
        tickers = cfg.get("screener_tickers", [])
        print(f"  ⚠ API 無回應，使用 config.json 的 {len(tickers)} 檔標的")
    else:
        print(f"  ✓ 共取得 {len(tickers)} 檔股票（上市 + 上櫃）")

    if skip_tickers:
        original = len(tickers)
        tickers = [t for t in tickers if t.upper() not in skip_tickers]
        removed = original - len(tickers)
        if removed > 0:
            print(f"  ⏭️ 已排除 {removed} 檔 skip_tickers 標的")

    # 預過濾：排除當日零成交量（停牌/冷門）
    if zero_vol:
        before = len(tickers)
        tickers = [t for t in tickers if t not in zero_vol]
        skipped = before - len(tickers)
        if skipped > 0:
            print(f"  ⏭️ 預過濾排除 {skipped} 檔零成交量標的（停牌/冷門）")

    # ─── 批次抓取基本面 + 價量資料 ────────────────────
    print("\n📥 批次抓取基本面資料...")
    bulk_fundamentals = fetch_bulk_fundamentals()
    print("\n📥 批次抓取全市場價量...")
    bulk_history = fetch_bulk_history(config["lookback_days"])

    # ─── 手上標的先掃（數量少，避免全市場掃完後被限流） ───
    portfolio_tickers = cfg.get("portfolio_tickers", [])
    if skip_tickers:
        original_portfolio = len(portfolio_tickers)
        portfolio_tickers = [t for t in portfolio_tickers if t.upper() not in skip_tickers]
        removed_portfolio = original_portfolio - len(portfolio_tickers)
        if removed_portfolio > 0:
            print(f"  ⏭️ 手上標的排除 {removed_portfolio} 檔 skip_tickers")
    print(f"\n📊 掃描手上標的 {len(portfolio_tickers)} 檔...")

    def _ticker_to_id(ticker: str) -> str:
        return ticker.replace(".TW", "").replace(".TWO", "").upper()

    def _get_prefetched(ticker: str) -> dict | None:
        return bulk_fundamentals.get(_ticker_to_id(ticker))

    def _get_hist(ticker: str) -> pd.DataFrame | None:
        return bulk_history.get(_ticker_to_id(ticker)) if bulk_history else None

    portfolio_results = []
    for i, ticker in enumerate(portfolio_tickers, 1):
        print(f"  [{i}/{len(portfolio_tickers)}] 分析 {ticker}...", end=" ")
        result = analyze_with_retry(
            ticker,
            config,
            sell_params=sell_params,
            min_conditions=0,
            name_map=name_map,
            allow_finmind_fallback=True,
            raise_on_error=False,
            prefetched=_get_prefetched(ticker),
            prefetched_hist=_get_hist(ticker),
        )
        if result:
            print(f"✓ {result['passedCount']}/5 條件 → {result['signal']}")
            portfolio_results.append(result)
        else:
            print("✗ 資料取得失敗")

    portfolio_results.sort(key=lambda x: (-x["passedCount"], x.get("undervalPct") or 0))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

    # ─── 全市場掃描（多執行緒） ───────────────────────────
    print(f"\n🔍 開始掃描 {len(tickers)} 檔股票（{MAX_WORKERS} 執行緒）...")
    print(f"   篩選條件：MA{config['ma_period']} 突破 / RSI {config['rsi_low']}-{config['rsi_high']} / "
          f"EPS×{config['pe_multiple']} 低估 / YoY≥{config['yoy_min']}% / 量能≥{config['vol_ratio_min']}x")
    if sell_params:
        print(f"   賣出條件：跌破 MA / RSI {sell_params['rsi_sell_low']}-{sell_params['rsi_sell_high']} / YoY 趨勢向下")
    print()

    results = scan_stocks_parallel(tickers, config, sell_params, name_map, bulk_fundamentals, bulk_history)
    results.sort(key=lambda x: (-x["passedCount"], x.get("undervalPct") or 0))

    output = {
        "generatedAt": datetime.now(TW_TZ).isoformat(),
        "config": config,
        "totalScanned": len(tickers),
        "totalPassed": len(results),
        "stocks": results,
    }

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

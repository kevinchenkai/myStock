"""yfinance 日线行情抓取。

  - 设 auto_adjust=False 以同时保留 Close 与 Adj Close。
  - 单标的失败不应中断整体流程（调用方记录到 sync_log 后继续）。
  - 批量抓取时加适当 sleep / 重试以缓解限频。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

from ..code_map import futu_to_yf

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

# 退市 / 无数据的标的，yfinance 会通过 logging 打印
# "possibly delisted; no price data found" 等警告，污染输出。
# 这里把 yfinance 自身的日志级别提高，由我们的跳过名单机制接管这类情况。
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


class YFError(RuntimeError):
    pass


def _require_yf() -> None:
    if yf is None:
        raise YFError("未安装 yfinance。请先 `conda activate mk` 并 pip install yfinance")


# yfinance 列名 -> 数据库列名
_COL_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividends",
    "Stock Splits": "stock_splits",
}


def fetch_daily(
    futu_code: str,
    start: str,
    end: Optional[str] = None,
    now: str = "",
    max_retries: int = 3,
    sleep_sec: float = 1.0,
) -> list[dict]:
    """抓取单个标的的日线，返回 daily_quotes 入库 dict 列表。

    Args:
        futu_code: 富途代码（如 HK.00700 / US.AAPL），内部转 yfinance 代码。
        start: 'YYYY-MM-DD'
        end:   'YYYY-MM-DD' 或 None（到今天）
    """
    _require_yf()
    yf_symbol = futu_to_yf(futu_code)

    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            df = yf.Ticker(yf_symbol).history(
                start=start, end=end, auto_adjust=False
            )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < max_retries:
                time.sleep(sleep_sec * attempt)
            else:
                raise YFError(f"抓取 {yf_symbol} 失败（{max_retries} 次重试）: {e}") from e

    if df is None or df.empty:
        return []

    df = df.reset_index()
    # 日期列名可能是 'Date' 或 'Datetime'
    date_col = "Date" if "Date" in df.columns else df.columns[0]

    rows = []
    for _, r in df.iterrows():
        date_val = r[date_col]
        date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
        row = {
            "yf_symbol": yf_symbol,
            "futu_code": futu_code,
            "date": date_str,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "adj_close": None,
            "volume": None,
            "dividends": None,
            "stock_splits": None,
            "synced_at": now,
        }
        for yf_col, db_col in _COL_MAP.items():
            if yf_col in df.columns and pd.notna(r[yf_col]):
                row[db_col] = float(r[yf_col])
        rows.append(row)
    return rows


def _profile_from_info(info: dict) -> dict:
    """从 yfinance Ticker.info 提取常用公司/估值信息，键为 stock_profiles 列名。"""
    market_cap = info.get("marketCap")
    shares = info.get("sharesOutstanding")
    # yfinance 新版 dividendYield 已是百分比数值（如 0.36 表示 0.36%）
    dividend_yield = info.get("dividendYield")
    return {
        "long_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "exchange": info.get("exchange"),
        "market_cap_mm": market_cap / 1_000_000 if market_cap else None,
        "shares_mm": shares / 1_000_000 if shares else None,
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "trailing_eps": info.get("trailingEps"),
        "dividend_yield": dividend_yield,
        "beta": info.get("beta"),
        "target_mean_price": info.get("targetMeanPrice"),
        "recommendation": info.get("recommendationKey"),
        "currency": info.get("currency"),
        "website": info.get("website"),
    }

# 这些列才是“真正的资料”——全为空视为无有效资料（如退市），不入库。
_PROFILE_VALUE_COLS = (
    "long_name", "sector", "industry", "exchange", "market_cap_mm",
    "shares_mm", "trailing_pe", "forward_pe", "price_to_book",
    "trailing_eps", "dividend_yield", "beta", "target_mean_price",
    "recommendation", "currency", "website",
)


def fetch_profile(futu_code: str, now: str = "") -> Optional[dict]:
    """抓取单个标的的通用信息（公司/估值），返回 stock_profiles 入库 dict。

    实时调用 yfinance Ticker.info。失败或无有效资料返回 None。
    """
    _require_yf()
    yf_symbol = futu_to_yf(futu_code)
    try:
        info = yf.Ticker(yf_symbol).info or {}
    except Exception:  # noqa: BLE001 — 资料缺失不应中断整体流程
        return None
    if not info:
        return None
    profile = _profile_from_info(info)
    # 全为空说明无有效资料（如退市），返回 None
    if all(profile.get(c) is None for c in _PROFILE_VALUE_COLS):
        return None
    profile["futu_code"] = futu_code
    profile["yf_symbol"] = yf_symbol
    profile["synced_at"] = now
    return profile

"""富途 OpenD 连接与查询：持仓 / 历史订单 / 历史成交。

注意：
  - futu-api 通过本地 OpenD 网关通信（默认 127.0.0.1:11111），程序本身不直连富途。
  - 历史成交 history_deal_list_query 仅支持实盘 TrdEnv.REAL。
  - 长区间历史接口需显式传 start/end；不传单次默认仅 90 天，故按时间窗口分段抓取后合并。
  - 部分查询接口可能需要先 unlock_trade。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..config import CONFIG
from ..code_map import futu_market_of

try:
    from futu import (
        OpenSecTradeContext,
        TrdMarket,
        TrdEnv,
        SecurityFirm,
        RET_OK,
    )
except ImportError:  # pragma: no cover - 仅在未安装 futu-api 时触发
    OpenSecTradeContext = None
    TrdMarket = None
    TrdEnv = None
    SecurityFirm = None
    RET_OK = "RET_OK"


class FutuError(RuntimeError):
    """富途相关错误（连接 / 查询 / 解锁失败）。"""


def _require_futu() -> None:
    if OpenSecTradeContext is None:
        raise FutuError(
            "未安装 futu-api。请先 `conda activate mk` 并安装依赖："
            "pip install futu-api"
        )


# 时间窗口分段大小（天）。富途单次默认 90 天，留余量取 80。
WINDOW_DAYS = 80

# ---- 限频控制 ----
# 富途历史成交 / 订单接口限频：每 30 秒最多 10 次请求。
# 每次窗口查询之间主动间隔，保证速率低于上限（30/10=3s，取 3.2s 留余量）。
RATE_LIMIT_INTERVAL = 3.2
# 命中限频时的退避重试
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BACKOFF = 30.0  # 命中限频后等待秒数（窗口期为 30s）
# 富途返回信息中表示限频的关键字
_RATE_LIMIT_HINTS = ("频率太高", "频率太快", "max", "请求失败，每")


def _is_rate_limit_msg(msg: object) -> bool:
    s = str(msg)
    return any(h in s for h in _RATE_LIMIT_HINTS)


def _query_with_retry(fn: Callable[[], tuple], label: str) -> object:
    """执行一次富途查询并处理限频。

    fn: 无参可调用，返回 (ret, data)。
    命中限频（ret != RET_OK 且信息含限频关键字）时退避重试；
    其它错误直接抛出 FutuError。成功返回 data。
    """
    last_msg: object = ""
    for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
        ret, data = fn()
        if ret == RET_OK:
            return data
        last_msg = data
        if _is_rate_limit_msg(data) and attempt < RATE_LIMIT_MAX_RETRIES:
            wait = RATE_LIMIT_BACKOFF
            print(
                f"  ⏳ {label} 命中限频，{wait:.0f}s 后重试（{attempt}/{RATE_LIMIT_MAX_RETRIES - 1}）…"
            )
            time.sleep(wait)
            continue
        # 非限频错误，或重试已用尽
        raise FutuError(f"{label}: {data}")
    raise FutuError(f"{label}: {last_msg}")


def _market_enum(market: str):
    return TrdMarket.HK if market.upper() == "HK" else TrdMarket.US


def _trd_env_enum(name: str):
    return TrdEnv.SIMULATE if str(name).upper() == "SIMULATE" else TrdEnv.REAL


def _date_windows(start: str, end: str):
    """把 [start, end] 切成不超过 WINDOW_DAYS 的窗口。

    start: 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS'
    end:   同上
    生成 (win_start_str, win_end_str)，格式 'YYYY-MM-DD HH:MM:SS'。
    """
    def parse(s: str) -> datetime:
        s = s.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"无法解析日期: {s}")

    cur = parse(start)
    end_dt = parse(end)
    if end_dt < cur:
        return
    while cur <= end_dt:
        win_end = min(cur + timedelta(days=WINDOW_DAYS - 1), end_dt)
        # 窗口起始用当天 00:00:00，结束用当天 23:59:59（除非是总 end）
        ws = cur.strftime("%Y-%m-%d 00:00:00")
        if win_end.date() == end_dt.date():
            we = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            if we.endswith("00:00:00"):
                we = end_dt.strftime("%Y-%m-%d 23:59:59")
        else:
            we = win_end.strftime("%Y-%m-%d 23:59:59")
        yield ws, we
        cur = win_end + timedelta(days=1)


class FutuClient:
    """对一个市场的交易上下文做封装。with 语句自动关闭。"""

    def __init__(self, market: str):
        _require_futu()
        self.market = market.upper()
        self.trd_env = _trd_env_enum(CONFIG.futu_trd_env)
        self._ctx: Optional["OpenSecTradeContext"] = None

    def __enter__(self) -> "FutuClient":
        try:
            self._ctx = OpenSecTradeContext(
                filter_trdmarket=_market_enum(self.market),
                host=CONFIG.futu_host,
                port=CONFIG.futu_port,
                security_firm=SecurityFirm.FUTUSECURITIES,
            )
        except Exception as e:  # noqa: BLE001
            raise FutuError(
                f"无法连接富途 OpenD（{CONFIG.futu_host}:{CONFIG.futu_port}）。"
                f"请确认 OpenD 已启动并登录。原始错误: {e}"
            ) from e
        self._maybe_unlock()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None

    def _maybe_unlock(self) -> None:
        pwd = CONFIG.futu_trade_pwd
        if not pwd:
            return
        ret, data = self._ctx.unlock_trade(password=pwd)
        if ret != RET_OK:
            raise FutuError(f"解锁交易失败: {data}")

    # ---------------- 查询 ----------------
    def query_positions(self) -> pd.DataFrame:
        """当前持仓快照（该市场）。"""
        ret, data = self._ctx.position_list_query(
            position_market=_market_enum(self.market),
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            raise FutuError(f"查询持仓失败({self.market}): {data}")
        if data is None or len(data) == 0:
            return pd.DataFrame()
        return data

    def query_orders(self, start: str, end: str) -> pd.DataFrame:
        """历史订单（全部状态），按时间窗口分段合并。带限频间隔与重试。"""
        frames = []
        for ws, we in _date_windows(start, end):
            # 每个窗口前都垫间隔，跨市场连续调用也被隔开，保证速率低于上限
            time.sleep(RATE_LIMIT_INTERVAL)
            data = _query_with_retry(
                lambda ws=ws, we=we: self._ctx.history_order_list_query(
                    status_filter_list=[],  # 空 = 全部状态
                    code="",
                    start=ws,
                    end=we,
                    trd_env=self.trd_env,
                ),
                f"查询历史订单失败({self.market} {ws}~{we})",
            )
            if data is not None and len(data) > 0:
                frames.append(data)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def query_deals(self, start: str, end: str) -> pd.DataFrame:
        """历史成交（仅实盘），按时间窗口分段合并。带限频间隔与重试。"""
        if self.trd_env != TrdEnv.REAL:
            raise FutuError("历史成交 history_deal_list_query 仅支持实盘 TrdEnv.REAL")
        frames = []
        for ws, we in _date_windows(start, end):
            # 每个窗口前都垫间隔，跨市场连续调用也被隔开，保证速率低于上限
            time.sleep(RATE_LIMIT_INTERVAL)
            data = _query_with_retry(
                lambda ws=ws, we=we: self._ctx.history_deal_list_query(
                    code="",
                    start=ws,
                    end=we,
                    trd_env=self.trd_env,
                ),
                f"查询历史成交失败({self.market} {ws}~{we})",
            )
            if data is not None and len(data) > 0:
                frames.append(data)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)


# ---------------- DataFrame -> 入库 dict 的规整 ----------------

def _is_missing(v) -> bool:
    """标量安全的空值判断。

    对 list / ndarray 等非标量，pd.isna 会返回数组导致真值歧义，
    这里只把「空容器」视为缺失，非空容器视为有值。
    """
    if v is None:
        return True
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) == 0
    if isinstance(v, np.ndarray):
        return v.size == 0
    try:
        return bool(pd.isna(v))
    except (ValueError, TypeError):
        # 仍是数组型 / 不可判定 → 视为有值
        return False


def _g(row, *names, default=None):
    """从 row（pd.Series 或 dict）按候选列名取值，返回第一个存在且非空的。"""
    for n in names:
        if n in row and not _is_missing(row[n]):
            return row[n]
    return default


def _market_from_code(code: str, fallback: str = "") -> str:
    m = futu_market_of(code or "")
    return m or fallback


def _row_to_jsonable(r) -> str:
    """把一行（pd.Series）转成 JSON 字符串，缺失值置 None，数组型转字符串。"""
    out = {}
    for k, v in r.to_dict().items():
        out[k] = None if _is_missing(v) else v
    return json.dumps(out, ensure_ascii=False, default=str)


def position_rows(df: pd.DataFrame, market: str, snapshot_date: str, now: str) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        code = _g(r, "code", default="")
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "market": _market_from_code(code, market),
                "code": code,
                "name": _g(r, "stock_name", "name"),
                "qty": _g(r, "qty"),
                "can_sell_qty": _g(r, "can_sell_qty"),
                "cost_price": _g(r, "cost_price", "diluted_cost", "average_cost"),
                "nominal_price": _g(r, "nominal_price"),
                "market_val": _g(r, "market_val"),
                "pl_val": _g(r, "pl_val"),
                "pl_ratio": _g(r, "pl_ratio"),
                "currency": _g(r, "currency"),
                "updated_at": now,
            }
        )
    return rows


def order_rows(df: pd.DataFrame, market: str, now: str) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        code = _g(r, "code", default="")
        rows.append(
            {
                "order_id": str(_g(r, "order_id", default="")),
                "market": _market_from_code(code, market),
                "code": code,
                "name": _g(r, "stock_name", "name"),
                "trd_side": _g(r, "trd_side"),
                "order_type": _g(r, "order_type"),
                "order_status": _g(r, "order_status"),
                "price": _g(r, "price"),
                "qty": _g(r, "qty"),
                "dealt_qty": _g(r, "dealt_qty"),
                "dealt_avg_price": _g(r, "dealt_avg_price"),
                "create_time": _g(r, "create_time"),
                "updated_time": _g(r, "updated_time"),
                "currency": _g(r, "currency"),
                "raw_json": _row_to_jsonable(r),
                "synced_at": now,
            }
        )
    return rows


def deal_rows(df: pd.DataFrame, market: str, now: str) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        code = _g(r, "code", default="")
        rows.append(
            {
                "deal_id": str(_g(r, "deal_id", default="")),
                "order_id": str(_g(r, "order_id", default="")),
                "market": _market_from_code(code, market),
                "code": code,
                "name": _g(r, "stock_name", "name"),
                "trd_side": _g(r, "trd_side"),
                "price": _g(r, "price"),
                "qty": _g(r, "qty"),
                "create_time": _g(r, "create_time"),
                "counter_broker_id": _g(r, "counter_broker_id"),
                "raw_json": _row_to_jsonable(r),
                "synced_at": now,
            }
        )
    return rows

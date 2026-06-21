"""富途代码 <-> yfinance 代码映射（纯函数，可单测）。

| 市场 | 富途格式   | yfinance 格式 | 规则                                              |
| ---- | --------- | ------------- | ------------------------------------------------- |
| 港股 | HK.00700  | 0700.HK       | 去 HK. 前缀 → 数字规整为 4 位 → 加 .HK 后缀        |
| 美股 | US.AAPL   | AAPL          | 去 US. 前缀，直接用 ticker                          |

港股代码位数说明：富途常见 5 位（如 00700），yfinance 习惯 4 位（0700.HK）。
本实现把港股数字部分去掉前导 0 后，左侧补 0 至 4 位；若本身超过 4 位（极少数），
则保留去前导 0 后的位数（不强行截断，避免改变标的）。
"""
from __future__ import annotations


def futu_market_of(futu_code: str) -> str:
    """从富途代码取市场前缀（HK / US）。无法识别返回空串。"""
    if not futu_code or "." not in futu_code:
        return ""
    prefix = futu_code.split(".", 1)[0].upper()
    return prefix if prefix in ("HK", "US") else ""


def normalize_hk_number(num: str) -> str:
    """规整港股数字部分为 yfinance 习惯：去前导 0 后补足至 4 位。

    例：00700 -> 0700, 0700 -> 0700, 700 -> 0700, 09988 -> 9988
    超过 4 位（去前导 0 后）保持原样，例如 100000 -> 100000。
    """
    stripped = num.lstrip("0") or "0"
    if len(stripped) < 4:
        return stripped.zfill(4)
    return stripped


def futu_to_yf(futu_code: str) -> str:
    """富途代码 -> yfinance 代码。

    HK.00700 -> 0700.HK
    US.AAPL  -> AAPL
    无法识别时原样返回。
    """
    if not futu_code or "." not in futu_code:
        return futu_code

    market, symbol = futu_code.split(".", 1)
    market = market.upper()

    if market == "HK":
        return f"{normalize_hk_number(symbol)}.HK"
    if market == "US":
        return symbol.upper()
    return futu_code


def yf_to_futu(yf_symbol: str) -> str:
    """yfinance 代码 -> 富途代码（尽力还原）。

    0700.HK -> HK.00700   （港股富途用 5 位，左补 0 到 5 位）
    AAPL    -> US.AAPL
    """
    if not yf_symbol:
        return yf_symbol

    if yf_symbol.upper().endswith(".HK"):
        num = yf_symbol[:-3]
        stripped = num.lstrip("0") or "0"
        return f"HK.{stripped.zfill(5)}"
    # 默认按美股处理
    return f"US.{yf_symbol.upper()}"

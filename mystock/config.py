"""配置读取。

优先从仓库根目录的 config.yaml 读取；敏感字段（交易密码）允许用环境变量覆盖：
  - MYSTOCK_FUTU_TRADE_PWD

不存在 config.yaml 时回退到 config.example.yaml 的默认值，并给出提示。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# 仓库根目录（mystock/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config.example.yaml"


class Config:
    """简单的点访问配置包装。"""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    # ---- futu ----
    @property
    def futu_host(self) -> str:
        return self._data.get("futu", {}).get("host", "127.0.0.1")

    @property
    def futu_port(self) -> int:
        return int(self._data.get("futu", {}).get("port", 11111))

    @property
    def futu_trade_pwd(self) -> str:
        # 环境变量优先
        env = os.environ.get("MYSTOCK_FUTU_TRADE_PWD")
        if env:
            return env
        return self._data.get("futu", {}).get("trade_pwd", "") or ""

    @property
    def futu_trd_env(self) -> str:
        return self._data.get("futu", {}).get("trd_env", "REAL")

    # ---- collect ----
    @property
    def start_date(self) -> str:
        return self._data.get("collect", {}).get("start_date", "2025-01-01")

    @property
    def markets(self) -> list[str]:
        markets = self._data.get("collect", {}).get("markets", ["HK", "US"])
        # 只允许 HK / US
        return [m for m in markets if m in ("HK", "US")]

    # ---- db ----
    @property
    def db_path(self) -> str:
        rel = self._data.get("db", {}).get("path", "data/mystock.db")
        p = Path(rel)
        if not p.is_absolute():
            p = ROOT_DIR / p
        return str(p)

    # ---- web ----
    @property
    def web_host(self) -> str:
        return self._data.get("web", {}).get("host", "127.0.0.1")

    @property
    def web_port(self) -> int:
        return int(self._data.get("web", {}).get("port", 8888))


def load_config() -> Config:
    """加载配置。优先 config.yaml，否则回退 example。"""
    path = CONFIG_PATH
    if not path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            print(
                f"[config] 未找到 {CONFIG_PATH.name}，回退使用 {EXAMPLE_CONFIG_PATH.name} 的默认值。\n"
                f"          建议: cp config.example.yaml config.yaml 后按需修改。"
            )
            path = EXAMPLE_CONFIG_PATH
        else:
            raise FileNotFoundError(
                "未找到 config.yaml 或 config.example.yaml，无法加载配置。"
            )

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(data)


# 模块级单例，方便直接 import
CONFIG = load_config()

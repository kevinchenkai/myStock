#!/usr/bin/env bash
# ML 数据采集（独立于 update.sh）：
#   抓 NVDA/TSLA/PDD 的 5 年日线 + 2 年 1h → data/ml/mystock_ml.db
#   并只读快照生产库 deals/orders/positions。
# 本机用 conda 环境 mk（已装 yfinance）；不碰生产库写入、不碰 web。
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_NAME="${MYSTOCK_ML_ENV:-mk}"

echo "== myStock ML fetch =="

if ! command -v conda >/dev/null 2>&1; then
  echo "✗ 未检测到 conda。" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if ! conda env list | grep -qE "^\s*${ENV_NAME}\s"; then
  echo "✗ conda 环境 '${ENV_NAME}' 不存在。本机请用 mk，H20 请用 mystock-ml。" >&2
  exit 1
fi
conda activate "${ENV_NAME}"

python -m mystock.ml.fetch

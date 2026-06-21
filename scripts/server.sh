#!/usr/bin/env bash
# 启动 Web 服务（默认 127.0.0.1:8888）。仅读数据库，不触发抓取。
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_NAME="mk"

echo "== myStock server =="

if ! command -v conda >/dev/null 2>&1; then
  echo "✗ 未检测到 conda。" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if ! conda env list | grep -qE "^\s*${ENV_NAME}\s"; then
  echo "✗ conda 环境 '${ENV_NAME}' 不存在。请先运行: bash scripts/init.sh" >&2
  exit 1
fi
conda activate "${ENV_NAME}"

if [ ! -f data/mystock.db ]; then
  echo "⚠ 数据库 data/mystock.db 不存在，页面将无数据。建议先运行: bash scripts/init.sh"
fi

python -m mystock.web.app

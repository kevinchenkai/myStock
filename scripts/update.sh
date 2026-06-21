#!/usr/bin/env bash
# 增量更新：读取上次同步点 → 抓取至今的新数据（当天覆盖）→ 写 sync_log。
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_NAME="mk"

echo "== myStock update =="

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
  echo "✗ 数据库 data/mystock.db 不存在。请先运行: bash scripts/init.sh" >&2
  exit 1
fi

echo "提示：请确保富途 OpenD 已启动并登录。"

python -m mystock.pipelines.update_load

echo "✓ update 完成。"

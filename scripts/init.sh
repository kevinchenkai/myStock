#!/usr/bin/env bash
# 首次初始化：建环境/装依赖 → 建库建表 → 全量抓取 → 写 sync_log。可重复执行（幂等）。
set -euo pipefail

cd "$(dirname "$0")/.."   # 切到仓库根目录
ENV_NAME="mk"

echo "== myStock init =="

# 1) conda 环境
if ! command -v conda >/dev/null 2>&1; then
  echo "✗ 未检测到 conda。请先安装 Miniconda/Anaconda 后重试。" >&2
  exit 1
fi

# 让 conda activate 在脚本中可用
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if conda env list | grep -qE "^\s*${ENV_NAME}\s"; then
  echo "✓ conda 环境 '${ENV_NAME}' 已存在，更新依赖…"
  conda env update -n "${ENV_NAME}" -f environment.yml --prune
else
  echo "→ 创建 conda 环境 '${ENV_NAME}'…"
  conda env create -f environment.yml
fi

conda activate "${ENV_NAME}"

# 2) 配置检查
if [ ! -f config.yaml ]; then
  echo "→ 未找到 config.yaml，从模板复制（请按需修改端口/交易密码）。"
  cp config.example.yaml config.yaml
fi

# 3) 提示 OpenD
echo "提示：请确保富途 OpenD 已在本机启动并登录（默认 127.0.0.1:11111），否则富途数据抓取会失败。"

# 4) 建库建表 + 全量抓取（pipeline 内部会先 init_db）
python -m mystock.pipelines.init_load

echo "✓ init 完成。接下来可运行: bash scripts/server.sh"

#!/usr/bin/env bash
# 把 Mac 本机的 ML **代码** 同步到 H20 私人机器的 workspace。
#
# 用 ssh + tar（H20 远端无 rsync，只有 tar）。
# ⚠️ 数据库**不在本脚本同步**：把真实交易数据推到外部主机会被安全策略硬阻断，
#    需用户**手动** scp（脚本末尾打印命令）。本脚本只同步代码/脚本/文档（非敏感）。
set -euo pipefail

cd "$(dirname "$0")/.."

# H20 连接与目标目录（可用环境变量覆盖）
H20_HOST="${H20_HOST:-vscode-h20-hh-970624@hanhai-prod.ai.kingsoft.com}"
H20_PORT="${H20_PORT:-2222}"
H20_DIR="${H20_DIR:-/home/share/user/chenkai/mystock-ml}"
ENV_PREFIX="${H20_DIR}/env"

SSH="ssh -p ${H20_PORT} -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=20"

echo "== sync code → ${H20_HOST}:${H20_DIR} =="

# 远端目录骨架 + 让 mystock 可 import
${SSH} "${H20_HOST}" "mkdir -p ${H20_DIR}/mystock/ml ${H20_DIR}/scripts ${H20_DIR}/docs ${H20_DIR}/tests ${H20_DIR}/data/ml && touch ${H20_DIR}/mystock/__init__.py"

# 打包代码（排除缓存与数据库），经 stdin 解到远端；解包后清理 macOS sidecar(._*)
tar czf - \
  mystock/ml/__init__.py mystock/ml/config.py mystock/ml/schema.sql \
  mystock/ml/db.py mystock/ml/fetch.py mystock/ml/data.py \
  mystock/ml/simulator.py mystock/ml/calibrate.py \
  mystock/ml/features.py mystock/ml/predictor.py \
  mystock/ml/policy.py mystock/ml/backtest.py \
  mystock/ml/report.py mystock/ml/offline_rl.py \
  mystock/code_map.py \
  scripts/ml.sh scripts/ml_setup_h20.sh scripts/ml_sync_h20.sh scripts/ml_vllm.sh \
  docs/ML_OVERVIEW.md docs/ML_PLAN.md \
  tests/test_ml_simulator.py tests/test_ml_policy.py tests/test_ml_offline_rl.py \
  2>/dev/null \
  | ${SSH} "${H20_HOST}" "tar xzf - -C ${H20_DIR} 2>/dev/null && find ${H20_DIR} -name '._*' -delete && echo '✓ 代码已解包到远端'"

echo
echo "⚠ 数据库需**手动** scp（安全策略禁止脚本/自动化推送真实数据）："
echo "    scp -P ${H20_PORT} data/ml/mystock_ml.db \\"
echo "      ${H20_HOST}:${H20_DIR}/data/ml/"
echo
echo "H20 上运行： conda activate ${ENV_PREFIX} && cd ${H20_DIR}"

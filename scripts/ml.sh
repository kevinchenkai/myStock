#!/usr/bin/env bash
# myStock ML 统一入口。三件事，一个脚本，用子命令组织：
#
#   bash scripts/ml.sh data       # 例行更新数据（采集 5 年日线 + 2 年 1h + 生产库只读快照）
#   bash scripts/ml.sh train      # 本地 Mac 训练/评估（P1 校准 → P2 预测 → P3 回测 → 生成报告）
#   bash scripts/ml.sh publish    # 发布 HTML 报告到展示服务器（www，公网）
#   bash scripts/ml.sh all        # data → train → publish 一条龙（供 cron）
#
# 默认子命令 = all。S0/P1/P2/P3.x/报告全是 CPU 算法，本机 mk 环境即可（P4 RL 才需 GPU，见 ml_setup_h20.sh）。
#
# cron 示例（工作日早 8 点北京时间，约对应前一晚美股收盘后）：
#   0 8 * * 1-5  cd /Users/kk/Work/Workpace/GitHub/Seattle/myStock && bash scripts/ml.sh all >> data/ml/cron.log 2>&1
set -uo pipefail   # 不用 -e：单步失败要记录但尽量继续

cd "$(dirname "$0")/.."
ENV_NAME="${MYSTOCK_ML_ENV:-mk}"

# 展示服务器（可用环境变量覆盖）
PUB_HOST="${PUB_HOST:-ubuntu@211.159.177.55}"
PUB_DIR="${PUB_DIR:-/www/wwwroot/g.ismayday.mobi/mystock}"
REPORTS_DIR="data/ml/reports"
TODAY="$(date +%Y-%m-%d)"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=20"

CMD="${1:-all}"
PREFIX="[ml $(date '+%Y-%m-%d %H:%M:%S')]"

activate_env() {
  if ! command -v conda >/dev/null 2>&1; then
    echo "✗ 未检测到 conda。" >&2; exit 1
  fi
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  if ! conda env list | grep -qE "^\s*${ENV_NAME}\s"; then
    echo "✗ conda 环境 '${ENV_NAME}' 不存在。" >&2; exit 1
  fi
  set +u; conda activate "${ENV_NAME}"; set -u   # activate 钩子在 set -u 下会报错
}

run_step() {
  local name="$1"; shift
  echo "${PREFIX} ──> ${name}"
  if "$@"; then
    echo "${PREFIX}     ✓ ${name}"
  else
    echo "${PREFIX}     ✗ ${name} 失败（rc=$?），继续后续步骤" >&2
    return 1
  fi
}

# ---- 子命令实现 ----
do_data() {
  run_step "采集数据（fetch）" python -m mystock.ml.fetch
}

do_train() {
  run_step "P1 撮合校准（calibrate）" python -m mystock.ml.calibrate
  run_step "P2 预测器（predictor）"   python -m mystock.ml.predictor
  run_step "P3/P3.1 回测（backtest）" python -m mystock.ml.backtest
  run_step "生成 HTML 报告（report）" python -m mystock.ml.report
}

do_publish() {
  if [ ! -f "${REPORTS_DIR}/latest.html" ]; then
    echo "✗ 未找到 ${REPORTS_DIR}/latest.html，请先 train。" >&2; return 1
  fi
  # latest → 首页 index.html
  scp ${SSH_OPTS} "${REPORTS_DIR}/latest.html" "${PUB_HOST}:${PUB_DIR}/index.html"
  # 当日归档（便于回溯）
  if [ -d "${REPORTS_DIR}/${TODAY}" ]; then
    ssh ${SSH_OPTS} "${PUB_HOST}" "mkdir -p ${PUB_DIR}/${TODAY}"
    scp ${SSH_OPTS} -r "${REPORTS_DIR}/${TODAY}/." "${PUB_HOST}:${PUB_DIR}/${TODAY}/"
  fi
  echo "已发布： https://g.ismayday.mobi/mystock/  （当日： .../${TODAY}/）"
}

echo "${PREFIX} 开始 [${CMD}]"
case "${CMD}" in
  data)    activate_env; do_data ;;
  train)   activate_env; do_train ;;
  publish) run_step "发布到服务器（publish）" do_publish ;;
  all)
    activate_env
    do_data
    do_train
    run_step "发布到服务器（publish）" do_publish
    ;;
  *)
    echo "用法: bash scripts/ml.sh {data|train|publish|all}" >&2
    echo "  data    例行更新数据"
    echo "  train   本地训练/评估 + 生成报告"
    echo "  publish 发布 HTML 到 www"
    echo "  all     三步一条龙（默认）"
    exit 1
    ;;
esac
echo "${PREFIX} 结束 [${CMD}]"

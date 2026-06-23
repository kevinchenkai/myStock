#!/usr/bin/env bash
# 在 H20 上创建隔离的 ML conda 环境（与 base/vllm 等互不影响）。
# ⚠️ 关键：该机 /opt/conda/envs 是**临时层**，容器重置后会丢失（曾实测命名环境被清空）。
#    故环境建成 **prefix 环境，放进持久 workspace（JuiceFS）** 下，随重置存活。
# ⚠️ prefix env **不继承 base 的 torch/libgomp**：lightgbm 需的 libgomp 用 conda 装；
#    P4 的 torch 单独装 cu129（匹配 H20 驱动 CUDA 12.9；装 cu130 会导致 GPU 不可用）。
# 在 H20 远端执行：bash scripts/ml_setup_h20.sh
set -euo pipefail

# workspace 根（持久，JuiceFS）；环境建在其下的 env/ 子目录
WORKSPACE="${MYSTOCK_ML_WORKSPACE:-/home/share/user/chenkai/mystock-ml}"
ENV_PREFIX="${WORKSPACE}/env"

echo "== setup ML env (prefix) at ${ENV_PREFIX} =="

if ! command -v conda >/dev/null 2>&1; then
  echo "✗ 未检测到 conda。" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if [ -d "${ENV_PREFIX}" ]; then
  echo "环境 ${ENV_PREFIX} 已存在，跳过创建。"
else
  conda create -p "${ENV_PREFIX}" python=3.11 -y
fi
# conda 的 activate/deactivate 钩子在 set -u 下会因 CONDA_BACKUP_* 未定义报错，
# 故 activate 期间临时关掉 -u。
set +u
conda activate "${ENV_PREFIX}"
set -u

# 国内网络用阿里云 PyPI 源（d3rlpy 等重依赖否则极慢/超时）。
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ || true
pip config set global.trusted-host mirrors.aliyun.com || true

# 依赖分两批，避免 d3rlpy 的重依赖树拖死整批安装：
#   批1（P1/P2/P3 必需，轻量）：数据/特征/预测/报告/模拟器/bandit
#   批2（P4 RL，研究性，含 torch 重依赖）：单独装，失败不影响前面
pip install --upgrade pip

# OpenMP 运行时：lightgbm 依赖 libgomp.so.1，prefix env 不自带 → 用 conda 装。
echo "== libgomp（lightgbm 依赖）=="
conda install -p "${ENV_PREFIX}" -y -c conda-forge libgomp _openmp_mutex || true

echo "== 批1：核心依赖（P1/P2/P3）=="
pip install \
  yfinance pandas numpy scikit-learn pyarrow \
  lightgbm gymnasium jinja2 matplotlib pytest

echo "== 批2：RL 依赖（P4，可选，失败不阻塞）=="
# 先装 cu129 的 torch（匹配 H20 驱动 CUDA 12.9；否则 d3rlpy 会拉 cu130 → GPU 不可用）。
pip install 'torch==2.10.*' --index-url https://download.pytorch.org/whl/cu129 \
  || echo "⚠ cu129 torch 安装失败；P4 GPU 不可用，先不阻塞"
pip install stable-baselines3 d3rlpy || echo "⚠ RL 依赖安装失败/跳过；P4 再处理，不影响 P1/P2/P3"

echo "== verify =="
python - <<'PY'
import importlib
# 核心（P1/P2/P3 必需）
for m in ["yfinance","pandas","numpy","sklearn","lightgbm","gymnasium","jinja2","pytest"]:
    try:
        importlib.import_module(m); print("ok", m)
    except Exception as e:
        print("FAIL", m, repr(e)[:60])
# RL（P4，可选）
for m in ["stable_baselines3","d3rlpy"]:
    try:
        importlib.import_module(m); print("ok", m, "(P4)")
    except Exception as e:
        print("skip", m, "(P4 未就绪)")
try:
    import torch
    print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
          "gpus", torch.cuda.device_count())
except Exception as e:
    print("torch 未就绪：", e)
PY

echo "完成。后续："
echo "  conda activate ${ENV_PREFIX}"
echo "  cd ${WORKSPACE} && python -m mystock.ml.fetch   # 或 calibrate / predictor / backtest"

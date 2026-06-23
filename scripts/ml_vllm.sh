#!/usr/bin/env bash
# 在 H20 上安全地 暂停 / 恢复 vllm 服务，给 ML 训练腾出 2 张 H20。
# vllm 跑在 tmux 会话 'vllm' 中，由 /home/share/game/seasun/vllm/start_vllm.sh 启动，TP=2 占满双卡。
#
# 用法（在 H20 远端执行）：
#   bash scripts/ml_vllm.sh stop      # 训练前：优雅停 vllm，等显存释放
#   bash scripts/ml_vllm.sh status    # 查看 vllm 进程与 GPU 占用
#   bash scripts/ml_vllm.sh restore   # 训练后：在原 tmux 会话重启 vllm
set -euo pipefail

VLLM_TMUX="${VLLM_TMUX:-vllm}"
VLLM_START="${VLLM_START:-/home/share/game/seasun/vllm/start_vllm.sh}"
ACTION="${1:-status}"

gpu_busy() {
  nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -q .
}

case "${ACTION}" in
  status)
    echo "=== vllm processes ==="; pgrep -af vllm || echo "no vllm process"
    echo "=== GPU compute apps ==="
    nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader || echo none
    ;;

  stop)
    if ! tmux has-session -t "${VLLM_TMUX}" 2>/dev/null; then
      echo "tmux 会话 '${VLLM_TMUX}' 不存在；若 vllm 以其他方式启动，请手动停。"
    else
      echo "向 tmux '${VLLM_TMUX}' 发送 Ctrl-C 优雅停 vllm…"
      tmux send-keys -t "${VLLM_TMUX}" C-c
    fi
    # 等显存释放（最多 ~60s）
    for i in $(seq 1 30); do
      if ! gpu_busy; then echo "✓ GPU 已释放"; exit 0; fi
      sleep 2
    done
    echo "⚠ 等待超时，仍有进程占用 GPU。请检查： bash scripts/ml_vllm.sh status"
    exit 1
    ;;

  restore)
    if [ ! -f "${VLLM_START}" ]; then
      echo "✗ 找不到启动脚本 ${VLLM_START}，无法自动恢复。" >&2; exit 1
    fi
    if ! tmux has-session -t "${VLLM_TMUX}" 2>/dev/null; then
      tmux new-session -d -s "${VLLM_TMUX}"
    fi
    echo "在 tmux '${VLLM_TMUX}' 重启 vllm…"
    tmux send-keys -t "${VLLM_TMUX}" "bash ${VLLM_START} 2>&1 | tee /tmp/vllm.log" Enter
    echo "已发起恢复。稍候用 status 确认服务与端口 8000 就绪。"
    ;;

  *)
    echo "用法: bash scripts/ml_vllm.sh {stop|status|restore}" >&2; exit 1
    ;;
esac

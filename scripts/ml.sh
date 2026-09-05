#!/usr/bin/env bash
# Manual only. No scheduler installed. No argument prints usage.
# train performs guarded report training; standalone diagnostics are offline tools.
set -euo pipefail
cd "$(dirname "$0")/.."
CMD="${1:-help}"
PYTHON="${MYSTOCK_ML_PYTHON:-python}"
if [ -z "${MYSTOCK_ML_PYTHON:-}" ] && [ "$CMD" != help ]; then
  if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    set +u; conda activate "${MYSTOCK_ML_ENV:-mk}"; set -u
  fi
fi
export MYSTOCK_ML_RUN_ID="${MYSTOCK_ML_RUN_ID:-$(date -u +%Y%m%dT%H%M%S)-$$}"
export MYSTOCK_ML_RECEIPT="${MYSTOCK_ML_RECEIPT:-data/ml/receipts/${MYSTOCK_ML_RUN_ID}.json}"
PUB_HOST="${PUB_HOST:-ubuntu@211.159.177.55}"
PUB_DIR="${PUB_DIR:-/www/wwwroot/g.ismayday.mobi/mystock}"
do_data() { "$PYTHON" -m mystock.ml.fetch; }
do_train() { "$PYTHON" -m mystock.ml.report; }
do_publish() {
  local artifact
  artifact="$("$PYTHON" -m mystock.ml.pipeline)" || return $?
  scp -o ConnectTimeout=20 "$artifact" "${PUB_HOST}:${PUB_DIR}/index.html" || return $?
  "$PYTHON" -m mystock.ml.pipeline --record-published
}
case "$CMD" in
 data) do_data ;;
 train) do_train ;;
 publish) do_publish ;;
 all)
   do_data
   do_train
   # All skipped: no artifact, no stale report publication.
   if "$PYTHON" -c 'import json,os,sys; r=json.load(open(os.environ["MYSTOCK_ML_RECEIPT"])); sys.exit(0 if r["artifact"] else 1)'; then do_publish; fi
   ;;
 help) echo 'Manual usage: ml.sh {data|train|publish|all}; publish requires MYSTOCK_ML_RUN_ID and matching receipt from train.' ;;
 *) exit 2 ;;
esac

#!/usr/bin/env bash
# Manual only. No scheduler installed. No argument prints usage.
# train performs guarded report training; standalone diagnostics are offline tools.
set -euo pipefail
cd "$(dirname "$0")/.."
CMD="${1:-help}"
if [ "$CMD" = publish ] && [ -n "${2:-}" ]; then
  export MYSTOCK_ML_RECEIPT="$2"
fi
# Standalone publish consumes an explicit receipt; never invent a fresh run or
# silently choose an older report. The Python validator still checks hash/time.
if [ "$CMD" = publish ] && [ -z "${MYSTOCK_ML_RECEIPT:-}" ]; then
  echo 'Usage: ml.sh publish <receipt.json> (use the receipt printed by train)' >&2
  exit 2
fi
PYTHON="${MYSTOCK_ML_PYTHON:-python}"
if [ -z "${MYSTOCK_ML_PYTHON:-}" ] && [ "$CMD" != help ]; then
  if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    set +u; conda activate "${MYSTOCK_ML_ENV:-mk}"; set -u
  fi
fi
if [ "$CMD" != publish ]; then
  export MYSTOCK_ML_RUN_ID="${MYSTOCK_ML_RUN_ID:-$(date -u +%Y%m%dT%H%M%S)-$$}"
  export MYSTOCK_ML_RECEIPT="${MYSTOCK_ML_RECEIPT:-data/ml/receipts/${MYSTOCK_ML_RUN_ID}.json}"
fi
PUB_HOST="${PUB_HOST:-ubuntu@211.159.177.55}"
PUB_DIR="${PUB_DIR:-/www/wwwroot/g.ismayday.mobi/mystock}"
do_data() { "$PYTHON" -m mystock.ml.fetch; }
do_train() {
  "$PYTHON" -m mystock.ml.report
  printf 'Train receipt: %s\nPublish before target deadline: bash scripts/ml.sh publish %q\n' "$MYSTOCK_ML_RECEIPT" "$MYSTOCK_ML_RECEIPT"
}
do_publish() {
  local artifact
  artifact="$("$PYTHON" -m mystock.ml.pipeline)" || return $?
  scp -o ConnectTimeout=20 "$artifact" "${PUB_HOST}:${PUB_DIR}/index.html" || return $?
  "$PYTHON" -m mystock.ml.pipeline --record-published
}
case "$CMD" in
 data) do_data ;;
 train) do_train ;;
 shadow)
   # D5 forward shadow (pre-open V2 next to V1, status=shadow, never published). Run inside the
   # market's pre-open window: HK about 08:30 HKT, US about 09:00 ET. Production train/publish unchanged.
   [ -n "${2:-}" ] || { echo 'Usage: ml.sh shadow HK|US' >&2; exit 2; }
   "$PYTHON" -m mystock.ml.shadow --market "$2" ;;
 publish) do_publish ;;
 all)
   do_data
   do_train
   # All skipped: no artifact, no stale report publication.
   if "$PYTHON" -c 'import json,os,sys; r=json.load(open(os.environ["MYSTOCK_ML_RECEIPT"])); sys.exit(0 if r["artifact"] else 1)'; then do_publish; fi
   ;;
 help) echo 'Manual usage: ml.sh {data|train|all}; ml.sh publish <receipt.json>; ml.sh shadow HK|US (pre-open D5 shadow, not published). Train prints the receipt; publish checks its artifact hash and target deadline. No automatic receipt selection.' ;;
 *) exit 2 ;;
esac

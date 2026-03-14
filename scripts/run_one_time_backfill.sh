#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/debug.log}"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "[INFO] One-time backfill started at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

export PIPELINE_ROOT="${PIPELINE_ROOT:-$ROOT_DIR}"
export DATA_FOLDER="${DATA_FOLDER:-$ROOT_DIR/rice_blast_prediction/data}"
export RECENT_OUTPUT_FOLDER="${RECENT_OUTPUT_FOLDER:-$ROOT_DIR/rice_blast_prediction/recent_daily_by_station}"
export OUTPUT_CSV="${OUTPUT_CSV:-$ROOT_DIR/rice_blast_prediction/recent_summary.csv}"
export PLAN_FOLDER="${PLAN_FOLDER:-}"

export ERA5_INPUT_DIR="${ERA5_INPUT_DIR:-$ROOT_DIR/ERA5_archive}"
export BACKFILL_START_DATE="${BACKFILL_START_DATE:-2018-04-05}"
export BACKFILL_END_DATE="${BACKFILL_END_DATE:-2025-12-31}"
export ERA5_BACKFILL_CHUNK_DAYS="${ERA5_BACKFILL_CHUNK_DAYS:-180}"

# BlastDT2 backfill window (same as one-time backfill window)
export BLASTDT2_BACKFILL_START_DATE="${BLASTDT2_BACKFILL_START_DATE:-$BACKFILL_START_DATE}"
export BLASTDT2_BACKFILL_END_DATE="${BLASTDT2_BACKFILL_END_DATE:-$BACKFILL_END_DATE}"

# BLASTAM import from legacy repo
export BLASTAM_LEGACY_START_DATE="${BLASTAM_LEGACY_START_DATE:-$BACKFILL_START_DATE}"
export BLASTAM_LEGACY_END_DATE="${BLASTAM_LEGACY_END_DATE:-$BACKFILL_END_DATE}"

mkdir -p "$DATA_FOLDER" "$RECENT_OUTPUT_FOLDER"

run_py() {
  local workdir="$1"
  local script="$2"
  echo "[INFO] Running ${script} (cwd=${workdir})"
  (
    cd "$workdir"
    python "$script"
  )
}

run_era5_models_for_window() {
  local chunk_start="$1"
  local chunk_end="$2"
  echo "[INFO] ERA5 model chunk: ${chunk_start} ~ ${chunk_end}"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/BlastLSTLS" "cron_predict.py"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/230127_GRU" "predictor.py"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/BLBTSLS" "predict.py"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/230128_Transformer" "predictor_250628.py"
}

if [[ "$ERA5_BACKFILL_CHUNK_DAYS" -le 0 ]]; then
  run_era5_models_for_window "$BACKFILL_START_DATE" "$BACKFILL_END_DATE"
else
  while IFS=',' read -r chunk_start chunk_end; do
    run_era5_models_for_window "$chunk_start" "$chunk_end"
  done < <(python - <<'PY'
import os
from datetime import datetime, timedelta
start = datetime.strptime(os.environ['BACKFILL_START_DATE'], '%Y-%m-%d').date()
end = datetime.strptime(os.environ['BACKFILL_END_DATE'], '%Y-%m-%d').date()
chunk = int(os.environ.get('ERA5_BACKFILL_CHUNK_DAYS', '180'))
cur = start
while cur <= end:
    chunk_end = min(cur + timedelta(days=chunk-1), end)
    print(f"{cur.isoformat()},{chunk_end.isoformat()}")
    cur = chunk_end + timedelta(days=1)
PY
)
fi

# BlastDT2: fetch whatever exists in upstream repo within selected date range
run_py "$ROOT_DIR/models/BlastDT2" "fetch_and_convert.py"

# BLASTAM: import historical outputs from legacy repo raw csvs
run_py "$ROOT_DIR/models/BLASTAM" "import_legacy_blastam.py"

# Organize downstream products
run_py "$ROOT_DIR/models" "recent_forecast_organizer.py"
run_py "$ROOT_DIR/models" "crop_season_avg.py"

echo "[INFO] One-time backfill completed at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

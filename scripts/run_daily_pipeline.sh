#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/debug.log}"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "[INFO] Daily pipeline started at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

export PIPELINE_ROOT="${PIPELINE_ROOT:-$ROOT_DIR}"
export DATA_FOLDER="${DATA_FOLDER:-$ROOT_DIR/rice_blast_prediction/data}"
export RECENT_OUTPUT_FOLDER="${RECENT_OUTPUT_FOLDER:-$ROOT_DIR/rice_blast_prediction/recent_daily_by_station}"
export OUTPUT_CSV="${OUTPUT_CSV:-$ROOT_DIR/rice_blast_prediction/recent_summary.csv}"
export PLAN_FOLDER="${PLAN_FOLDER:-}"
export ERA5_OUTPUT_DIR="${ERA5_OUTPUT_DIR:-$ROOT_DIR/ERA5}"

# GitHub Actions scheduled runs may pass optional workflow inputs as empty strings.
# Use the broad default window when backfill dates are omitted.
export BACKFILL_START_DATE="${BACKFILL_START_DATE:-1900-01-01}"
export BACKFILL_END_DATE="${BACKFILL_END_DATE:-2100-12-31}"

# Compatibility directories for legacy relative outputs (../../rice_blast_prediction/...)
mkdir -p "$ERA5_OUTPUT_DIR" "$ROOT_DIR/rice_blast_prediction/data" "$ROOT_DIR/rice_blast_prediction/recent_daily_by_station"
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

run_py "$ROOT_DIR/models" "ERA5_current_download_cron.py"
run_py "$ROOT_DIR/models/BlastLSTLS" "cron_predict.py"
run_py "$ROOT_DIR/models/230127_GRU" "predictor.py"
run_py "$ROOT_DIR/models/BLBTSLS" "predict.py"
run_py "$ROOT_DIR/models/230128_Transformer" "predictor_250628.py"
run_py "$ROOT_DIR/models/BlastGAT" "predict.py"
run_py "$ROOT_DIR/models/BlastDT2" "fetch_and_convert.py"
run_py "$ROOT_DIR/models" "recent_forecast_organizer.py"
run_py "$ROOT_DIR/models" "crop_season_avg.py"

echo "[INFO] Daily pipeline completed at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

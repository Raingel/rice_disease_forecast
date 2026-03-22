#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/debug.log}"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "[INFO] BlastDT2 backfill pipeline started at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

export PIPELINE_ROOT="${PIPELINE_ROOT:-$ROOT_DIR}"
export DATA_FOLDER="${DATA_FOLDER:-$ROOT_DIR/rice_blast_prediction/data}"
export RECENT_OUTPUT_FOLDER="${RECENT_OUTPUT_FOLDER:-$ROOT_DIR/rice_blast_prediction/recent_daily_by_station}"
export OUTPUT_CSV="${OUTPUT_CSV:-$ROOT_DIR/rice_blast_prediction/recent_summary.csv}"
export PLAN_FOLDER="${PLAN_FOLDER:-}"
export BLASTDT2_BACKFILL_START_DATE="${BLASTDT2_BACKFILL_START_DATE:-2025-01-01}"
export BLASTDT2_BACKFILL_END_DATE="${BLASTDT2_BACKFILL_END_DATE:-$(date -u '+%Y-%m-%d')}"

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

run_py "$ROOT_DIR/models/BlastDT2" "fetch_and_convert.py"
run_py "$ROOT_DIR/models" "recent_forecast_organizer.py"
run_py "$ROOT_DIR/models" "crop_season_avg.py"

echo "[INFO] BlastDT2 backfill pipeline completed at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

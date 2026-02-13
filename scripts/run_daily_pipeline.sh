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

mkdir -p "$DATA_FOLDER" "$RECENT_OUTPUT_FOLDER"

echo "[INFO] Running ERA5 download"
python "$ROOT_DIR/models/ERA5_current_download_cron.py"

echo "[INFO] Running BlastLSTLS"
python "$ROOT_DIR/models/BlastLSTLS/cron_predict.py"

echo "[INFO] Running BlastGRU-TW"
python "$ROOT_DIR/models/230127_GRU/predictor.py"

echo "[INFO] Running BLBTSLS"
python "$ROOT_DIR/models/BLBTSLS/predict.py"

echo "[INFO] Running BlastTF (Transformer)"
python "$ROOT_DIR/models/230128_Transformer/predictor_250628.py"

echo "[INFO] Running BlastDT2 fetch and conversion"
python "$ROOT_DIR/models/BlastDT2/fetch_and_convert.py"

echo "[INFO] Organizing recent forecast outputs"
python "$ROOT_DIR/models/recent_forecast_organizer.py"

echo "[INFO] Building crop-season summary"
python "$ROOT_DIR/models/crop_season_avg.py"

echo "[INFO] Daily pipeline completed at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

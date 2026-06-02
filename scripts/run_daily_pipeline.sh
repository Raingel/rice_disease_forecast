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

# Daily scheduled runs rebuild only the latest rolling forecast window.
# The furthest Open-Meteo-based model output is the latest downloaded weather day + 4 days.
BACKFILL_WINDOW_DAYS="${BACKFILL_WINDOW_DAYS:-14}"
FORECAST_MAX_SHIFT_DAYS="${FORECAST_MAX_SHIFT_DAYS:-4}"

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

configure_backfill_window() {
  local supplied_start="${BACKFILL_START_DATE:-}"
  local supplied_end="${BACKFILL_END_DATE:-}"
  local manual_backfill_window=0

  if [[ -n "$supplied_start" || -n "$supplied_end" ]]; then
    if [[ -z "$supplied_start" || -z "$supplied_end" ]]; then
      echo "[ERROR] BACKFILL_START_DATE and BACKFILL_END_DATE must be provided together."
      exit 1
    fi
    manual_backfill_window=1
    export BACKFILL_START_DATE="$supplied_start"
    export BACKFILL_END_DATE="$supplied_end"
    echo "[INFO] Using requested backfill window: ${BACKFILL_START_DATE} ~ ${BACKFILL_END_DATE}"
  else
    local detected_window
    detected_window="$(python - "$ERA5_OUTPUT_DIR" "$BACKFILL_WINDOW_DAYS" "$FORECAST_MAX_SHIFT_DAYS" <<'PY'
from pathlib import Path
import sys
import pandas as pd

era5_dir = Path(sys.argv[1])
window_days = int(sys.argv[2])
max_shift_days = int(sys.argv[3])

if window_days < 1:
    raise SystemExit("BACKFILL_WINDOW_DAYS must be at least 1")

latest_weather_day = None
for csv_path in era5_dir.glob("*.csv"):
    try:
        times = pd.read_csv(csv_path, usecols=["time"])["time"]
    except Exception:
        continue
    parsed = pd.to_datetime(times, errors="coerce").dropna()
    if parsed.empty:
        continue
    station_latest = parsed.max().normalize()
    if latest_weather_day is None or station_latest > latest_weather_day:
        latest_weather_day = station_latest

if latest_weather_day is None:
    raise SystemExit(f"No valid time values found in {era5_dir}")

latest_output_day = latest_weather_day + pd.Timedelta(days=max_shift_days)
start_output_day = latest_output_day - pd.Timedelta(days=window_days - 1)
print(
    start_output_day.strftime("%Y-%m-%d"),
    latest_output_day.strftime("%Y-%m-%d"),
    latest_weather_day.strftime("%Y-%m-%d"),
)
PY
)"
    read -r BACKFILL_START_DATE BACKFILL_END_DATE LATEST_WEATHER_DATE <<< "$detected_window"
    export BACKFILL_START_DATE BACKFILL_END_DATE
    echo "[INFO] Auto backfill window: ${BACKFILL_START_DATE} ~ ${BACKFILL_END_DATE}"
    echo "[INFO] Latest downloaded weather day: ${LATEST_WEATHER_DATE}; max model shift: +${FORECAST_MAX_SHIFT_DAYS} days"
  fi

  # BlastDT2 is based on a separate upstream repository and has a shorter date horizon.
  # Scheduled runs let its importer auto-detect its own latest valid 14-day window.
  # Explicit manual backfills still use the requested common window unless BlastDT2-specific values are supplied.
  if [[ -n "${BLASTDT2_BACKFILL_START_DATE:-}" || -n "${BLASTDT2_BACKFILL_END_DATE:-}" ]]; then
    echo "[INFO] BlastDT2 will use its requested own backfill window."
  elif [[ "$manual_backfill_window" -eq 1 ]]; then
    export BLASTDT2_BACKFILL_START_DATE="$BACKFILL_START_DATE"
    export BLASTDT2_BACKFILL_END_DATE="$BACKFILL_END_DATE"
    echo "[INFO] BlastDT2 will use the requested common backfill window."
  else
    echo "[INFO] BlastDT2 will auto-detect its own rolling window."
  fi
}

run_py "$ROOT_DIR/models" "ERA5_current_download_cron.py"
configure_backfill_window
run_py "$ROOT_DIR/models/BlastLSTLS" "cron_predict.py"
run_py "$ROOT_DIR/models/230127_GRU" "predictor.py"
run_py "$ROOT_DIR/models/BLBTSLS" "predict.py"
run_py "$ROOT_DIR/models/230128_Transformer" "predictor_250628.py"
run_py "$ROOT_DIR/models/BlastGAT" "predict.py"
run_py "$ROOT_DIR/models/BlastDT2" "fetch_and_convert.py"
run_py "$ROOT_DIR/models" "recent_forecast_organizer.py"
run_py "$ROOT_DIR/models" "crop_season_avg.py"

echo "[INFO] Daily pipeline completed at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

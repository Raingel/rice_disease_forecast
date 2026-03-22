#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/debug.log}"

exec > >(tee -a "$LOG_FILE") 2>&1

RUN_START_EPOCH="$(date +%s)"

export PIPELINE_ROOT="${PIPELINE_ROOT:-$ROOT_DIR}"
export DATA_FOLDER="${DATA_FOLDER:-$ROOT_DIR/rice_blast_prediction/data}"
export RECENT_OUTPUT_FOLDER="${RECENT_OUTPUT_FOLDER:-$ROOT_DIR/rice_blast_prediction/recent_daily_by_station}"
export OUTPUT_CSV="${OUTPUT_CSV:-$ROOT_DIR/rice_blast_prediction/recent_summary.csv}"
export PLAN_FOLDER="${PLAN_FOLDER:-}"

export ERA5_INPUT_DIR="${ERA5_INPUT_DIR:-$ROOT_DIR/ERA5_archive}"
export BACKFILL_START_DATE="${BACKFILL_START_DATE:-2018-04-05}"
export BACKFILL_END_DATE="${BACKFILL_END_DATE:-2025-12-31}"
export ERA5_BACKFILL_CHUNK_DAYS="${ERA5_BACKFILL_CHUNK_DAYS:-180}"

# Self-stop before GitHub hard timeout; default target ~5.5h with 5min safety buffer.
export MAX_RUNTIME_SECONDS="${MAX_RUNTIME_SECONDS:-19800}"
export SAFE_STOP_BUFFER_SECONDS="${SAFE_STOP_BUFFER_SECONDS:-300}"
DEADLINE_EPOCH=$((RUN_START_EPOCH + MAX_RUNTIME_SECONDS - SAFE_STOP_BUFFER_SECONDS))

# Progress persistence for resumable runs.
export BACKFILL_PROGRESS_FILE="${BACKFILL_PROGRESS_FILE:-$ROOT_DIR/rice_blast_prediction/data/.one_time_backfill_progress.json}"

# BlastDT2 backfill window (same as one-time backfill window)
export BLASTDT2_BACKFILL_START_DATE="${BLASTDT2_BACKFILL_START_DATE:-$BACKFILL_START_DATE}"
export BLASTDT2_BACKFILL_END_DATE="${BLASTDT2_BACKFILL_END_DATE:-$BACKFILL_END_DATE}"

# BLASTAM import from legacy repo
export BLASTAM_LEGACY_START_DATE="${BLASTAM_LEGACY_START_DATE:-$BACKFILL_START_DATE}"
export BLASTAM_LEGACY_END_DATE="${BLASTAM_LEGACY_END_DATE:-$BACKFILL_END_DATE}"

mkdir -p "$DATA_FOLDER" "$RECENT_OUTPUT_FOLDER"

echo "[INFO] One-time backfill started at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "[INFO] Runtime budget: MAX_RUNTIME_SECONDS=${MAX_RUNTIME_SECONDS}, SAFE_STOP_BUFFER_SECONDS=${SAFE_STOP_BUFFER_SECONDS}"
echo "[INFO] Progress file: ${BACKFILL_PROGRESS_FILE}"

run_py() {
  local workdir="$1"
  local script="$2"
  echo "[INFO] Running ${script} (cwd=${workdir})"
  (
    cd "$workdir"
    python "$script"
  )
}

load_progress_state() {
  eval "$(python - <<'PY'
import json, os
from datetime import datetime

p = os.environ['BACKFILL_PROGRESS_FILE']
cfg = {
    'start': os.environ['BACKFILL_START_DATE'],
    'end': os.environ['BACKFILL_END_DATE'],
    'chunk': os.environ['ERA5_BACKFILL_CHUNK_DAYS'],
    'era5_input_dir': os.environ['ERA5_INPUT_DIR'],
}
next_start = cfg['start']
era5_complete = '0'
if os.path.exists(p):
    try:
        with open(p, 'r', encoding='utf-8') as f:
            state = json.load(f)
        if state.get('config') == cfg:
            next_start = state.get('next_start', next_start)
            era5_complete = '1' if state.get('era5_complete') else '0'
    except Exception:
        pass
print(f"NEXT_START={next_start}")
print(f"ERA5_COMPLETE={era5_complete}")
PY
)"
}

save_progress_state() {
  local next_start="$1"
  local era5_complete="$2" # 0/1
  NEXT_START_ARG="$next_start" ERA5_COMPLETE_ARG="$era5_complete" python - <<'PY'
import json, os
from datetime import datetime

p = os.environ['BACKFILL_PROGRESS_FILE']
os.makedirs(os.path.dirname(p), exist_ok=True)

state = {
    'config': {
        'start': os.environ['BACKFILL_START_DATE'],
        'end': os.environ['BACKFILL_END_DATE'],
        'chunk': os.environ['ERA5_BACKFILL_CHUNK_DAYS'],
        'era5_input_dir': os.environ['ERA5_INPUT_DIR'],
    },
    'next_start': os.environ['NEXT_START_ARG'],
    'era5_complete': os.environ['ERA5_COMPLETE_ARG'] == '1',
    'updated_at_utc': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
}
with open(p, 'w', encoding='utf-8') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
PY
}

should_stop_now() {
  local now
  now="$(date +%s)"
  if [[ "$now" -ge "$DEADLINE_EPOCH" ]]; then
    return 0
  fi
  return 1
}

run_era5_models_for_window() {
  local chunk_start="$1"
  local chunk_end="$2"
  echo "[INFO] ERA5 model chunk: ${chunk_start} ~ ${chunk_end}"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/BlastLSTLS" "cron_predict.py"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/230127_GRU" "predictor.py"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/BLBTSLS" "predict.py"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/230128_Transformer" "predictor_250628.py"
  BACKFILL_START_DATE="$chunk_start" BACKFILL_END_DATE="$chunk_end" run_py "$ROOT_DIR/models/BlastGAT" "predict.py"
}

load_progress_state

if [[ "$ERA5_COMPLETE" == "1" ]]; then
  echo "[INFO] ERA5 chunked backfill already completed for current config; skipping ERA5 stage."
else
  if [[ "$ERA5_BACKFILL_CHUNK_DAYS" -le 0 ]]; then
    if should_stop_now; then
      echo "[WARN] Reached runtime budget before ERA5 stage. Saving progress and exiting gracefully."
      save_progress_state "$NEXT_START" "0"
      exit 0
    fi
    run_era5_models_for_window "$BACKFILL_START_DATE" "$BACKFILL_END_DATE"
    save_progress_state "$BACKFILL_END_DATE" "1"
  else
    ERA5_TIMED_OUT=0
    while IFS=',' read -r chunk_start chunk_end; do
      if should_stop_now; then
        echo "[WARN] Reached runtime budget before chunk ${chunk_start}~${chunk_end}."
        save_progress_state "$chunk_start" "0"
        ERA5_TIMED_OUT=1
        break
      fi

      run_era5_models_for_window "$chunk_start" "$chunk_end"

      NEXT_CHUNK_START="$(CHUNK_END="$chunk_end" python - <<'PY'
import os
from datetime import datetime, timedelta
end = datetime.strptime(os.environ['CHUNK_END'], '%Y-%m-%d').date()
print((end + timedelta(days=1)).isoformat())
PY
)"

      if [[ "$chunk_end" == "$BACKFILL_END_DATE" ]]; then
        save_progress_state "$NEXT_CHUNK_START" "1"
      else
        save_progress_state "$NEXT_CHUNK_START" "0"
      fi
    done < <(NEXT_START="$NEXT_START" python - <<'PY'
import os
from datetime import datetime, timedelta
start = datetime.strptime(os.environ['NEXT_START'], '%Y-%m-%d').date()
end = datetime.strptime(os.environ['BACKFILL_END_DATE'], '%Y-%m-%d').date()
chunk = int(os.environ.get('ERA5_BACKFILL_CHUNK_DAYS', '180'))
cur = start
while cur <= end:
    chunk_end = min(cur + timedelta(days=chunk-1), end)
    print(f"{cur.isoformat()},{chunk_end.isoformat()}")
    cur = chunk_end + timedelta(days=1)
PY
)

    load_progress_state
    if [[ "$ERA5_TIMED_OUT" -eq 1 || "$ERA5_COMPLETE" != "1" ]]; then
      echo "[WARN] ERA5 stage not completed in this run. Progress saved; exit gracefully for resumable rerun."
      exit 0
    fi
  fi
fi

# BlastDT2: fetch whatever exists in upstream repo within selected date range
run_py "$ROOT_DIR/models/BlastDT2" "fetch_and_convert.py"

# BLASTAM: import historical outputs from legacy repo raw csvs
run_py "$ROOT_DIR/models/BLASTAM" "import_legacy_blastam.py"

# Organize downstream products
run_py "$ROOT_DIR/models" "recent_forecast_organizer.py"
run_py "$ROOT_DIR/models" "crop_season_avg.py"

echo "[INFO] One-time backfill completed at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"


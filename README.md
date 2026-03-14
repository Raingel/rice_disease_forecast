# rice_disease_forecast
run rice disease forecast on cloud

## GitHub Actions automation

This repository now includes a full daily automation workflow at:

- `.github/workflows/daily-forecast.yml`

### What it does

1. Installs Python dependencies from `requirements-github-actions.txt`.
2. Runs the full pipeline via `scripts/run_daily_pipeline.sh`:
   - ERA5/Open-Meteo download
   - BlastLSTLS / BlastGRU-TW / BLBTSLS / BlastTF predictions
   - BlastDT2 repository fetch + conversion
   - recent forecast organizer
   - crop season summary generation
3. Commits and pushes generated/updated files automatically when changes exist.

### Trigger

- Scheduled daily at **00:00 Asia/Taipei** (`0 16 * * *` UTC)
- Manual trigger through **workflow_dispatch**

### Notes

- `PLAN_FOLDER` is optional in GitHub Actions and defaults to empty.
- Path settings are now environment-variable driven for portability:
  - `PIPELINE_ROOT`
  - `DATA_FOLDER`
  - `RECENT_OUTPUT_FOLDER`
  - `OUTPUT_CSV`
  - `PLAN_FOLDER`


## One-time ERA5 archive run (GitHub Actions)

Use workflow `.github/workflows/era5-archive-once.yml` (manual trigger) to run `models/ERA5_archive_download.py` once.

- Default max runtime is 5 hours (`18000` seconds).
- If archive API repeatedly fails (e.g., rate limit), the script stops early and keeps already-downloaded results.
- Workflow still attempts to commit/push partial outputs (`ERA5_archive/`) even when the run step reports an error.


## BLASTAM workflow (independent)

A separate workflow was added at:

- `.github/workflows/blastam-forecast.yml`

### Why separate?

BLASTAM needs sunshine information in addition to temperature/wind/rain. To avoid coupling this requirement into the existing ERA5 pipeline, BLASTAM runs in its own pipeline (`scripts/run_blastam_pipeline.sh`) and model runner (`models/BLASTAM/run_blastam.py`).

### Sunshine design notes

- BLASTAM requires hourly sunshine duration (0–1 hour fraction).
- The implementation now **prefers `sunshine_duration`** from Open-Meteo and converts it by `sunshine_duration / 3600`.
- If `sunshine_duration` is unavailable, it falls back to the legacy approximation `direct_radiation / 120` (clipped to 0–1).

This is generally more faithful to the model hypothesis than using radiation-only scaling.


## BLASTAM one-time backfill (2024-2025)

Use workflow `.github/workflows/blastam-backfill-2024-2025.yml` (manual trigger) to backfill BLASTAM outputs for historical dates.

- Default window: `2024-01-01` to `2025-12-31`
- Inputs can be adjusted in `workflow_dispatch` (`start_date`, `end_date`).
- Archive download is chunked by date range to improve reliability for long windows; chunk size can be tuned via `BLASTAM_ARCHIVE_CHUNK_DAYS` (default `60`).
- Runs `scripts/run_blastam_backfill_2024_2025.sh`, which executes:
  - `models/BLASTAM/backfill_2024_2025.py`
  - `models/recent_forecast_organizer.py`
  - `models/crop_season_avg.py`




## One-time all-model backfill

Use workflow `.github/workflows/one-time-backfill-all-models.yml` (manual trigger) to run a full historical backfill once.

- ERA5-based models (BlastLSTLS / BlastGRU-TW / BLBTSLS / BlastTF) read from `ERA5_archive` by default (`era5_input_dir` input can be changed).
- BlastDT2 uses upstream `BlastDT` repo data and imports whatever dates exist in the selected window.
- BLASTAM imports legacy daily outputs from `Raingel/rice_blast_prediction` raw CSV files for the selected date window.
- After model outputs are generated/imported, workflow also runs:
  - `models/recent_forecast_organizer.py`
  - `models/crop_season_avg.py`

## BlastDT2 one-time backfill

Use workflow `.github/workflows/blastdt2-backfill.yml` (manual trigger) to backfill BlastDT2 outputs for historical dates.

- Default window: `2025-01-01` to `2026-12-31`
- Inputs can be adjusted in `workflow_dispatch` (`start_date`, `end_date`).
- Runs `scripts/run_blastdt2_backfill.sh`, which executes:
  - `models/BlastDT2/fetch_and_convert.py` (with date-range env vars)
  - `models/recent_forecast_organizer.py`
  - `models/crop_season_avg.py`
- Daily pipeline BlastDT2 conversion now defaults to process **previous + current year** to avoid missing early-year dates after incubation shift.

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

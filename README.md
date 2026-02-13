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

# Staged historical validation from 2020

The `Staged EPIRICE backfill from 2020` workflow runs 2020, 2021, 2022, 2023,
2024, 2025 and 2026 as dependent stages. The first five years are reconstructed;
2025-2026 reuse the verified archive through 2026-10-11. Existing recent EPIRICE
files are preserved when reusing years. No historical risk thresholds or
`recent_summary.csv` changes are introduced.

One shared station/calendar/config snapshot is frozen at the start. Each new
year uses up to 50 current rice stations per batch, at most two concurrent batch
jobs, a 40-minute batch limit, bounded HTTP retries and saved annual weather
checkpoints. Year assembly/integration has a separate 45-minute limit. Each
verified year is committed immediately; a later failure does not discard it.
Use **Re-run failed jobs** on the same workflow run to reuse successful artifacts
and continue remaining stages. Checkpoints remain for 14 days.
For a new workflow run after artifact expiry, `resume_from` can reuse earlier
published annual archives and begin new computations at the selected year.

Historical weather is Open-Meteo hourly reanalysis at the current station
coordinates; current representative crop dates and model parameters are applied
to historical years. These are retrospective scenarios, not forecasts issued at
that time or reconstructions of each field's actual planting dates. Historical
model station IDs absent from the current EPIRICE station master are retained
with their existing model scores and blank EPIRICE values. Historical/current
coordinate differences are counted and EPIRICE input lat/lon are preserved.
If an existing model has different values for multiple coordinates under the
same station ID/date, the annual table selects a unique match to the EPIRICE
input coordinates. Unresolved cases remain blank with a model-specific status;
all alternatives are retained in `source_coordinate_variants.json`. Original
source model files are unchanged. Empty renamed aliases cannot hide valid values.

Outputs:

- `rice_blast_prediction/epirice_blb_history/YYYY/`: full new-year states,
  daily model outputs, exact daily weather inputs, snapshots and batch audits.
- `rice_blast_prediction/data/YYYYMMDD_EPIRICE_BLB.csv`: new historical model
  files next to the existing models, using the same v3 raw-infection-flow and
  non-crop -99 contract. All state columns remain present.
- `rice_blast_prediction/history_daily_by_station/YYYY/<stationID>.csv`:
  annual station-wide validation tables with all available local model scores
  and every EPIRICE state column. Other models' source CSVs are never modified.
  Missing models remain blank; no planthopper history is fabricated or fetched.
- `history_daily_by_station/YYYY/integration_audit.json`: source-file hashes,
  coverage, model row counts, coordinate differences and schema definition.
- `rice_blast_prediction/epirice_backfill_2020_manifest.json`: published only
  after all seven integration stages finish successfully.

Model gaps, missing calendars, and dates beyond the 120-day model duration are
blank with a status. Confirmed non-crop periods are -99, and actual zero flow
stays zero. No future daily forecasting or website settings are changed.

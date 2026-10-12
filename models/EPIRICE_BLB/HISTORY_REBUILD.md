# Rebuild 2025-2026 EPIRICE history

Run the **Rebuild EPIRICE 2025-2026 history** Actions workflow manually. The
initial deployment commit with the exact message
`data: start EPIRICE history rebuild 2025-2026` also starts it once. Other pushes
do not start a rebuild. The default historical cutoff is 2026-10-11; a supplied
cutoff must be in 2026 and at least two Taiwan-calendar days before execution.

This rebuild covers the currently active station list, using the current
coordinates, model configuration and published representative county crop dates
for both years. It is a retrospective model calculation with Open-Meteo hourly
reanalysis, not archived forecasts issued in 2025 or field disease observations.
It does not restore decommissioned station metadata or historical management
changes. Counties without a rice calendar are recorded as exclusions.

The workflow snapshots the calendar, stations and configuration once, then
creates batches of up to 25 rice stations with at most two concurrent workers.
Each worker has a 45-minute limit and bounded HTTP retries. Successful annual
weather requests are uploaded as 90-day checkpoint artifacts even if calculation
fails. Use **Re-run failed jobs** on the same run to reuse those checkpoints and
successful result artifacts. A new workflow run starts a separate snapshot and
does not reuse the old run's artifacts.

Only when all batches succeed does the publish job verify expected scenarios,
complete contiguous dates, no forecast flags, no missing core states, unique
station/date pairs, and matching daily/state row counts. It preserves a verified
artifact before pushing. Its push retries use rebase to accommodate a concurrent
scheduled daily forecast commit. Sparse checkout and minimal dependencies avoid
the large weather repository and TensorFlow installation.

Published output: `rice_blast_prediction/epirice_blb_history/2025_2026/`

- `data/YYYYMMDD_EPIRICE_BLB.csv`: daily station outputs, same column schema as
  the daily EPIRICE runner. Empty dates outside crop periods still have headers.
- `states/2025_1`, `2025_2`, `2026_1`, `2026_2`: complete per-station crop states,
  ending at harvest, the model's 120-day limit, or the historical cutoff.
- `weather_daily`: hourly weather aggregated to the exact daily model inputs.
- `inputs`: shared source snapshots, hashes and expected-scenario manifest.
- `batch_*_audit.json`, `batch_*_weather_provenance.json`, `rebuild_audit.json`:
  per-batch and overall coverage, requests, returned grid coordinates and hashes.

Original hourly weather is retained in the 90-day checkpoint artifacts; daily
inputs and full model states are committed permanently. The archive is separate
from rolling forecast outputs and their forecast flags. No historical results
are fabricated for dates after the cutoff or outside a crop period.

For local use:

```sh
python scripts/rebuild_epirice_history.py prepare --end-date 2026-10-11
python scripts/rebuild_epirice_history.py batch --batch-index 0
# Collect each batch's result/ under rebuild-parts/epirice-result-<index>/.
python scripts/rebuild_epirice_history.py merge
```

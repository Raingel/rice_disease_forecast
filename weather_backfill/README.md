# Resumable hourly weather archive

This workflow supplements weather only. It does not run disease models, alter
forecast tables, or modify `ERA5_archive/` on main.

The frozen plan covers 892 current station coordinates from 2013-01-01 through
2026-10-22. These are historical gridded weather series at current coordinates,
not a reconstruction of the historical station roster. The cutoff is deliberately
fixed so this one-time job can finish. A later extension requires a new plan.

## Execution and storage

`.github/workflows/weather-archive-backfill.yml` runs at minute 37 each hour,
subject to GitHub scheduling delays. It also supports manual dispatch. One writer
is allowed; each normal batch has a 35-minute worker budget and a 45-minute job
limit to leave time for publishing. A batch may finish sooner on request limits
or API errors. Smoke mode uses one station and at most two supplement requests.

Only code, tests and frozen inputs are checked out. Existing coordinate-matched
archive CSVs are fetched individually from a pinned source commit. Each station
is rebuilt from durable checkpoints, not from a disposable Actions cache.

Compressed annual files and their provenance JSON are bundled per station into
immutable GitHub Release assets (16 hash buckets, tags `weather-archive-2013-0`
through `weather-archive-2013-f`). Old assets are retained. The index branch
`weather-archive-2013` has root
`progress.json` records input fingerprints, per-year checksums, pending work,
last attempts, errors and total coverage. The five locally completed stations
are imported as the initial checkpoint without rerunning their downloads. Only
those five seed stations have compressed files directly on the index branch;
all newly computed weather bundles use release assets to avoid expanding normal
forecast checkouts. Use each task's `archive_asset` descriptor to download its
current bundle and verify SHA256. `csv_blob`/`metadata_blob` identify content by
Git SHA1; they are downloadable Git objects only for the initial seed files.

Each station is committed before the next one starts. Partial years are saved
as well. Hard cancellation can lose only the current uncommitted station's work.
Data are never silently replaced with zero; existing valid cells are retained.
Temperature, RH, rain, wind speed and direction must be finite; RH, rain and wind
range checks are applied, timestamps must be unique hourly values. Wind vectors
are derived in km/h using the existing project convention.

The original product selection was Open-Meteo archive default. Supplements keep
that selection and save request URLs, returned grid coordinates, units, response
SHA256 and retrieval times. This does not assert all legacy data came from one
specified reanalysis product. Unmatched coordinates are downloaded independently.

## Resume, retry and stop

An incomplete station rotates behind unattempted/older tasks. HTTP 429 stops the
batch and records a six-hour cooldown; hourly scheduled runs during that period
exit without weather requests. Other errors are recorded for later attempts.
Persistent missing data remain incomplete rather than being declared successful.

Completion requires every planned station-year to have the full requested hourly
axis and zero missing values in all five fields. Checksum-verified files and their
metadata are published before the summary is updated. Once all stations are
complete, the worker disables **only** `weather-archive-backfill.yml` through the
GitHub API. Existing daily forecasts remain enabled. The workflow requests
`contents: write` for its data branch and `actions: write` for self-disable.

To stop early, disable this workflow in Actions. To resume the same frozen plan,
enable it and use Run workflow. Do not change the frozen plan in place: the worker
rejects mismatching inputs instead of overwriting the old coverage record.

## Verification

Run `python -m unittest discover -s tests -p 'test_weather_archive*.py'`.
Tests cover invalid data, duplicate hours, valid-cell preservation, local resume,
fresh-runner checkpoint restoration, corrupted files, frozen inputs, quota cooldown
and disabling only after full completion. Progress counts are not field measurements.

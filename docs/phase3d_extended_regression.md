# Phase 3D — Extended Forecast Compatibility Regression

**Scope:** GitHub source-side read-only regression and website **snapshot
audit**. This is not a production deployment and it does not modify the
website frontend, PHP API v1, live forecast CSV files or model mathematics.

## Why Phase 3D exists

Phase 3A pinned a single published day and five stations. Phase 3B
introduced optional normalized `ForecastRecord` values. Phase 3C kept
the complete legacy CSV bytes while exposing those normalized records.
Phase 3D checks whether those contracts remain valid over **multiple
real production dates**, **different stations**, **different crop status
conditions**, and **real duplicated relocated station records**.

**Do not conflate passing an offline fixture test with a completed
end-to-end validation of the PHP website**.

## Pinned upstream model evidence

Read from public `Raingel/rice_disease_forecast` commit
`9addfbb00deeacae501b2bfda2426fe23e90e3d2`, not dynamic `main`.
The file `tests/fixtures/public_contract_v1/phase3d_epirice_multiday_2026.json`
contains **full original rows with every daily source column** for
48 selected stations, in six model/day files:

| Date | Model | Upstream rows (observed) | Sampled rows | Observed whole-file statuses |
| --- | --- | ---: | ---: | --- |
| 2026-06-04 | EPIRICE_BLB | 892 | 48 | ok 521; non-crop 223; missing-calendar 19; outside-duration 129 |
| 2026-08-30 | EPIRICE_BLB | 892 | 48 | ok 743; non-crop 130; missing-calendar 19 |
| 2026-10-19 | EPIRICE_BLB | 892 | 48 | ok 599; non-crop 274; missing-calendar 19 |
| 2026-10-19 | EPIRICE_LB | 892 | 48 | ok 599; non-crop 274; missing-calendar 19 |
| 2026-11-01 | EPIRICE_BLB | 892 | 48 | ok 599; non-crop 274; missing-calendar 19 |
| 2026-11-01 | EPIRICE_LB | 892 | 48 | ok 599; non-crop 274; missing-calendar 19 |

Across all six source files there are **5,352 published source rows**,
of which **288 sampled rows** are included in the immutable offline
fixtures and individually subjected to conversion and round-trip tests.
The full-file status counts above were observed during the read-only
source audit; the offline tests do *not* re-download or validate every
one of the original 5,352 values.

The accompanying relocation fixture pins real duplicated `C0AC60`
2026-10-19 `BlastGRU-TW` rows (historical and current coordinates) and
the expected current-station value `0.20114526`, which must not depend
on the input row order.

## Offline tests

`tests/test_epirice_phase3d_multiday.py` verifies:

1. A consistent set of 48 station IDs and canonical dates across all
   six published model/day samples.
2. Unique (station, date, model) keys: 288 unique sample records.
3. Exact original v1 CSV byte round-trip through the v2 envelope,
   including all 44 BLB or 53 LB published daily columns.
4. Original raw infection flows equal the normalized source-native
   `value` for `ok`; no conversion to risk percentages.
5. Valid zero versus `non_crop_period` (-99 in v1, null in v2),
   `missing_crop_calendar`, and actual
   `outside_model_duration` entries (129 across the June full file;
   six present in the frozen sample).
6. Re-serialization of the complete source DataFrame uses unchanged
   pandas UTF-8-BOM settings and preserves all detailed state fields.
7. Deliberately malformed source values are rejected.
8. Synthetic leap-day and cross-year **date/key** continuity, without
   claiming to rerun the crop model.
9. Real relocated `C0AC60` duplicate selection by current coordinates,
   independent of source row order; ambiguous coordinates fail closed.

Tests use no network and never write production CSVs.

## Separate website warning-map snapshot audit

The 2026-11-01 **server-side, read-only** development ZIP
`forecast-v2-development-complete.zip` included seven golden
`warning_map_MODEL.json` response snapshots. Independently inspected:

| Model | Observed dates | Observed stations | Response matrix |
| --- | ---: | ---: | --- |
| BLASTAM | 47 | 470 | 47 × 470 |
| BLBTSLS | 47 | 470 | 47 × 470 |
| BlastGAT | 47 | 470 | 47 × 470 |
| BlastGRU-TW | 47 | 470 | 47 × 470 |
| BlastLSTLS | 47 | 470 | 47 × 470 |
| BlastTF | 47 | 470 | 47 × 470 |
| **BlastDT2** | **34** | **389** | **34 × 389 (439 nulls)** |

All saved matrices are rectangular with unique dates and station
IDs. **Not all seven models provide 47 dates × 470 stations.** In
particular, BlastDT2's observed smaller coverage and missing values
are legitimate facts to preserve as coverage differences rather than
silently filling or inventing data.

The snapshot is historical evidence only; there is **no new website
request or site-side v1-v2 warning-map comparison in this PR**.

## What Phase 3D does *not* establish

- No claim that all **5,352** upstream rows have been tested end-to-end.
  Only the fixed 288-row sampled daily CSV set is executed by the new
  offline tests.
- No claim that every past or future day is valid. LB is not present in
  the upstream archive on the selected June/August dates.
- No claim that every model's website warning map is 47 × 470.
- No claim that production `forecast_station`, `season_summary` or
  live `warning_map` JSON were compared against v2 on the server
  in this PR.
- No assertion that an observed `outside_model_duration` is a model
  failure or zero risk.
- No changes to risk threshold, BlastGAT transformations, or historical
  summary denominators.
- No website UI, schedules, jobs or public data changed.

## Remaining gate before website deployment

A **separate**, read-only website staging acceptance should compare
live/current v1 and v2 outputs over common dates/stations with:
per-model date coverage, station set, null mask, numeric parity where
semantically comparable, BlastGAT transformation, season aggregation,
cache freshness and frontend screenshots. Model-specific differences
must be reported rather than coerced to a common risk scale.

The normal upstream `main` branch continues to receive forecast
updates; all regression evidence in this PR is pinned to immutable
source commit(s) and must never be regenerated automatically from
code under test.

## CI command

```bash
python -m unittest discover -s tests -p 'test_epirice_phase3d_multiday.py' -v
python -m unittest discover -s tests -p 'test_epirice_*.py'
python -m unittest discover -s tests -p 'test_public_contract_v1.py'
python -m unittest discover -s tests -p 'test_recent_station_coordinates.py'
```

# Phase 4B — Opt-in Registry catalog shadow for existing v1 pipelines

**Scope:** technical proof of Registry metadata consumption, not a scheduled
migration or website deployment.

Phase 4A introduced `registry/models.json` as upstream metadata. This PR
makes the two existing aggregators able to opt in to that catalog **only when
it reproduces their current public v1 model sets, filenames, fields and order
exactly**.

## Architecture

`models/registry_legacy_bridge.py` reads and validates the local
`registry/models.json` using the existing strict loader. It has two
interfaces:

- `resolve_daily_model_files(MODEL_FILES, mode="legacy"|"registry")`
- `resolve_seasonal_model_columns(MODEL_COLS, mode="legacy"|"registry")`

Legacy mode is the default, needs no registry read and returns a copy of
the existing hard-coded mapping. Registry mode selects matching values
from the validated document but **reorders** them to the pre-existing v1
mapping to protect the organizer's first-model precedence. It raises
`ModelRegistryError` if any existing model is missing, an unexpected
model was added, the source filename/field changes, a private mechanistic
model enters seasonal publication, or an unapproved seasonal risk
profile is registered.

The published legacy constants remain present as an **approval guard**.
This step intentionally does not auto-publish newly registered models:
those need separate science, website, output-schema, coverage and risk
review. A model may be registered without changing the daily pipeline.

For daily per-station organizer:
```python
from models.recent_forecast_organizer import merge_daily_predictions
baseline = merge_daily_predictions("20261101")  # existing default
shadow = merge_daily_predictions("20261101", catalog_mode="registry")
```

For seasonal summary:
```python
from datetime import date
from models.crop_season_avg import accumulate_for_period
legacy = accumulate_for_period(2026, date(2026, 11, 1), date(2026, 11, 2), need_plan=False)
shadow = accumulate_for_period(2026, date(2026, 11, 1), date(2026, 11, 2),
                               need_plan=False, catalog_mode="registry")
```

When explicitly set, `FORECAST_MODEL_CATALOG_MODE=registry` applies to
the `main()` methods of both modules. **Neither scheduled workflow nor
either shell pipeline sets this variable.** In the absence of the
variable, both main paths continue to use `legacy`. The station
coverage switch `RECENT_STATION_COVERAGE_MODE` is *independent* and
continues to default to `legacy`; Phase 4B does not activate the
experimental `union` station coverage change.

## Public contract and scientific constraints

- Daily `MODEL_FILES` remains in the same order. Ordinary classic
  models retain their existing left joins; mechanistic EPIRICE retains
  dedicated, validated outer merges and complete source states.
- `EPIRICE_BLB` and `EPIRICE_LB` remain private in Registry metadata
  even though their current model CSVs are kept in the organizer's
  historical internal columns. No website publication is enabled.
- Seasonal `MODEL_COLS` and the public 20-column summary header stay
  unchanged. The snapshot records 0.5 as the standard threshold, 0.23
  for BlastGAT and `>0` for planthopper. Risk profile IDs are **not**
  mistaken for numerical calibration coefficients.
- No reinterpretation of EPIRICE infection flow as probability or risk.
  Public `-99` non-crop values, valid zeros and missing values remain
  distinct.
- The Planthopper source remains a separate spatial feed; registry
  metadata does not replace the existing nearest-cell calculation.
- No new CSV/JSON format or website API version, and no production file
  backfills or cache invalidation.

## Shadow regression

`tests/test_phase4b_registry_shadow.py` checks:

1. Both registry-derived model dictionaries equal the frozen v1 model
   dictionaries including **insertion order**.
2. Legacy mode does not load Registry; unknown modes fail closed.
3. Missing/invalid JSON, added unreviewed models, changed suffixes,
   unauthorized risk profile or private model exposure cannot silently
   mutate the current public outputs.
4. Bit-identical legacy/registry UTF-8-BOM serialization from the
   immutable 2026-11-01 five-station published sample for both legacy
   and `union` station coverage modes.
5. Equivalent organizer output on the pinned 2026-11-20–22 source
   model sample including the earlier 92-vs-892-station scenario.
6. Seasonal counts, denominators and native GAT 0.23 / baseline 0.5
   threshold behavior on isolated synthetic dates.
7. Existing GitHub Actions and runner shell scripts have not opted in.

Dedicated CI runs the new tests and the pre-existing public v1,
EPIRICE, relocation, Registry and station-coverage regression gates.

## Next approval gate

A future separately reviewed PR may make the Registry the *default*
catalog after full daily/station/seasonal parity, deployment
coordination, provenance validation, and an agreed rollback strategy.
The current PR has **no effect on already published website data or
scheduled models** and must be merged/reviewed independently.

# Phase 3C — Lossless Legacy CSV Compatibility Adapters

**Status:** Opt-in, isolated Python library. Does **not** change production
outputs or any code called by the scheduled model runners.

Phase 3B introduced a strict `ForecastRecord` normalized view of EPIRICE
BLB/LB results. That view deliberately omits full disease-state columns,
weather traces and legacy CSV column ordering. Therefore **a list of v2
records is not sufficient to reconstruct a legacy EPIRICE daily CSV**.

This stage introduces an explicit, lossless sidecar rather than silently
fabricating missing SEIR states or republishing partial records.

## Supported interfaces

`models.epirice.legacy_csv_adapter` exports:

| Interface | Input | Output | Guarantee |
|---|---|---|---|
| `read_legacy_daily_csv(blob, model_id)` | Complete existing CSV bytes | `LegacyDailyEnvelope` | Strict CSV structure + normalized v2 record validation, original bytes preserved |
| `envelope.records` | Immutable envelope | Tuple of immutable `ForecastRecord` | A v2 view with nulls and native `infection_flow` |
| `envelope.to_v1_bytes()` | Unchanged envelope | **Exact original bytes** | Preserves BOM, CRLF, quoting, headers, state columns and metadata |
| `envelope.to_v1_bytes(records)` | Candidate records | Same bytes, or rejection | Reject edits to v2 values/status/metadata that cannot be reflected safely in full v1 state |
| `serialize_complete_legacy_daily(frame, model_id)` | **Full** BLB or LB daily DataFrame | Validated UTF-8-BOM CSV bytes | Same pandas serialization settings as daily runner, preserving input field order |

These APIs **do not write files**. A caller must explicitly choose an output
location after reviewing the v1 contract. No existing production call site
is changed by this stage.

## Example

```python
from pathlib import Path

from models.epirice.legacy_csv_adapter import read_legacy_daily_csv

blob = Path("rice_blast_prediction/data/20261101_EPIRICE_BLB.csv").read_bytes()
bundle = read_legacy_daily_csv(blob, "EPIRICE_BLB")
assert bundle.records[0].schema_version == "forecast_record_v2"
assert bundle.to_v1_bytes() == blob
```

The legacy source is the preservation boundary. In particular,
`EPIRICE_BLB_latent`, `EPIRICE_BLB_healthy_sites`, `generated_at`,
weather provenance and unknown future legacy fields remain in the original
CSV even though `ForecastRecord` does not expose them all.

For `non_crop_period`, the legacy `-99` survives the v1 bytes while
the v2 view holds `value: null`, `status: non_crop_period`. For an
`ok` record, raw and public primary values are checked for equality and
valid zero is preserved. An edited record cannot be exported via the
unchanged sidecar; raising `UnsafeLegacyConversionError` is by design.

## Contract boundaries

This is **not** a generic JSON->CSV exporter, full v2 source writer, or
license to change existing publication format.

To generate new forecasts natively from v2 while preserving full v1 daily
output, the upstream model must also provide its complete disease states
and provenance, or a new explicit, model-specific output producer must
be designed. A minimal v2 record alone is irreversibly incomplete.

The existing `recent_forecast_organizer.py` continues to build the
station-wide CSV using left joins for classic models and outer joins for
EPIRICE. `crop_season_avg.py` continues to compute the same seasonal
risk day counts. Both remain untouched.

## Regression and next work

```bash
python -m unittest discover -s tests -p 'test_epirice_legacy_adapter_v1.py' -v
python -m unittest discover -s tests -p 'test_epirice_*.py'
python -m unittest discover -s tests -p 'test_public_contract_v1.py'
```

The tests exercise real five-station published BLB/LB samples from Phase
3A; exact byte roundtrip including BOM and CRLF; native pandas full-data
writer; `0` vs `-99`; invalid CSV input, duplicate keys, and unsafe edits.

Before switching daily production writers: add complete full-data contract
fixtures from more than one date, one-to-one full-state equivalence tests,
and a **dry-run/shadow** writer comparison in CI or isolated staging.
Only then propose a separately reviewed runner integration PR. Real website
API/JS and forecast update schedules remain completely out of scope.

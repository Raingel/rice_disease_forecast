# Internal Forecast Output Contract v2 — Phase 3B

**Status:** Opt-in Python interface; not a website API, not a replacement for
published CSV v1. This document covers **EPIRICE_BLB and EPIRICE_LB only**.
Other models and registry-driven execution are Phase 4 work.

## Compatibility boundary

The website still consumes the three products recorded in
[`public_forecast_contract_v1.md`](public_forecast_contract_v1.md).
**Do not** change `rice_blast_prediction/data/YYYYMMDD_MODEL.csv`,
`rice_blast_prediction/recent_daily_by_station/STATION.csv`,
`rice_blast_prediction/recent_summary.csv`, `api/data.php`, or frontend
HTML/JS/CSS as part of this phase.

No production model runner calls the v2 conversion. A model's existing
`complete_daily`, CSV export and `recent_forecast_organizer` operate as
before. Consumers can *opt in* to v2 validation once they have an already
completed legacy model DataFrame.

## Schema: one immutable record per station, date, and model

```json
{
  "schema_version": "forecast_record_v2",
  "station_id": "C0G910",
  "date": "2026-11-01",
  "model_id": "EPIRICE_BLB",
  "value": 0.0,
  "status": "ok",
  "value_type": "infection_flow",
  "unit": null,
  "primary_field": "rateinf_new_infections",
  "model_version": null,
  "source_schema_version": "EPIRICE_BLB_daily_v3",
  "source_generated_at": null,
  "source_commit": null,
  "source_site_unit": null
}
```

**Illustrative data**, not a published observation. These stable field
names are JSON-safe and produced by `ForecastRecord.to_dict()`.
`source_generated_at` describes the **model's own** timestamp, never
the web-cache time. Nullable provenance fields remain null when missing.
The upstream model field `site_unit` is retained as `source_site_unit`;
this is **not** silently promoted to a comparable physical `unit`.
Scientific units have not been established across BLB/LB, so `unit=null`
is intentional. The `model_version` is independent of the
`source_schema_version`.

A station ID is a string (e.g. `"00123"`, not `123`) and a date is a
canonical Taiwan-local calendar day `YYYY-MM-DD`, without implicit UTC
conversion. The source model value is **not** a calibrated probability or
risk percentage.

### Explicit status interpretation

| Legacy status | v1 primary field | v2 value | v2 status |
| --- | ---: | ---: | --- |
| `ok` with raw infection flow zero | `0` | `0` | `ok` |
| `ok` with positive raw infection flow | native finite value | same native value | `ok` |
| `non_crop_period` | `-99` | `null` | `non_crop_period` |
| `missing_crop_calendar` | blank | `null` | `missing_crop_calendar` |
| `missing_model_output` | blank | `null` | `missing_model_output` |
| `outside_model_duration` | blank | `null` | `outside_model_duration` |

For `ok`, the public primary field and the raw
`MODEL_rateinf_new_infections` field must agree and be finite and
nonnegative. For `non_crop_period`, the raw flow must be blank and the
public primary must be exactly `-99`. For other non-`ok` statuses, both
must be blank. Unknown statuses, schema versions and model IDs raise a
`ContractValidationError`; nothing is converted to a plausible-looking
zero.

`EPIRICE_BLB_daily_v3` and `EPIRICE_LB_daily_v1` are the observed source
daily schemas. This internal projection does not replace either source.

### Validation rules

- Required legacy fields: `站號`, `日期`, `MODEL`,
  `MODEL_rateinf_new_infections`, `MODEL_status`.
- Station IDs must be read as strings; rejects implicit numeric IDs,
  invalid dates, duplicate header columns, duplicate station/day/model
  keys, non-finite numbers, negative model infection flows, and
  contradictory status/value combinations.
- A missing raw flow or status is **not** reconstructed from a
  flattened score column.
- Model state columns, county, daily CSV field order, weather traces,
  historical station/date coverage, and other v1 fields remain available
  in the original DataFrame and are not mutated.
- A normalized record is immutable. The validator does **not**
  add/replace/merge published station rows.

## Developer-facing Python API

```python
import pandas as pd

from models.EPIRICE_BLB.output_contract import normalized_v2_records

daily = pd.read_csv(
    "rice_blast_prediction/data/20261101_EPIRICE_BLB.csv",
    dtype={"站號": str},
)
records = normalized_v2_records(daily)  # validates; read-only
values = [r.to_dict() for r in records]
```

The corresponding LB module exports the **same** opt-in
`normalized_v2_records` entry point. Both use
`models.epirice.output_contract_v2.normalize_epirice_daily`.
`validate_record` and `validate_records` are available for downstream
consumers. Any future v1 writer or native v2 producer must be developed
and separately verified in Phase 3C; do not assume a reversible converter
can reconstruct full detailed source states from a minimal v2 record.

## Verification and planned expansion

Run offline:

```bash
python -m unittest discover -s tests -p 'test_epirice_internal_contract_v2.py' -v
python -m unittest discover -s tests -p 'test_public_contract_v1.py'
python -m unittest discover -s tests -p 'test_epirice_*.py'
```

Tests exercise five genuine published 2026-11-01 station records for
BLB and LB, including zero, non-crop sentinel and calendar-missing
examples; then adversarial invalid inputs and source metadata. No
network access or production output writes occur.

**Phase 3C:** optionally integrate the validation at a carefully chosen
point in the daily runners; add explicit v1 compatibility writers and
byte/semantic equivalence tests across multiple days.

**Phase 3D:** compare full forecast windows, relocated stations, model
incompleteness, web endpoint payloads and historical seasonal
denominators. The 3A five-station fixture is not exhaustive.

**Phase 4:** introduce authoritative upstream Model Registry and
additional models with distinct value types/units and explicit risk
profiles. The EPIRICE-only validator in this phase must *not* be used to
claim all models share the same `infection_flow` or risk threshold.

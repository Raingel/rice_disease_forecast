# Shared EPIRICE numerical engine

The numerical kernel in `models/epirice/engine.py` implements the original
source2023 EPIRICE SEIR/cohort loop. It is shared by **EPIRICE_BLB** and
**EPIRICE_LB**, which retain their existing disease-specific `config.json`
parameter files.

This first refactoring step changes **no** SEIR arithmetic, parameter values,
crop calendar, weather inputs, outputs, dates, threshold definitions, or
daily-pipeline invocation.

## Entry points

```python
from models.epirice.engine import simulate_epirice
states = simulate_epirice(weather, configuration)
```

The weather DataFrame contains `YYYYMMDD`, `TEMP`, `RHUM`, and `RAIN`.
The configuration supplies `duration_days`, `RcA_points`, `RcT_points`,
and the existing compartment and infection parameters. Input rows must equal
`duration_days`. The return value is the original full daily state table.

`models.EPIRICE_BLB.model.simulate_epirice_blb` is retained as a
backward-compatible alias. Its standalone `predict.py` workflow still works.

## Scientific boundary

The kernel preserves the legacy source2023 binary wetness rule
(RHUM >= `rhlim_percent` OR RAIN >= `rainlim_mm_day`) and legacy day
indexing. **This does not automatically validate its reuse for all diseases.**
BLB retains its source2023 parameters (`aggregation_a=4`, mean RH) rather
than silently switching to the Savary 2012 assumptions. LB retains its own
pinned epicrop v0.0.2 values and daily-weather validation.

Before adding sheath blight, independently verify the infection site
(tiller), age response, wetness and contact processes, and the precise
parameter/source version. Only identical mathematics belong in the common
kernel; disease-specific processes belong in adapters or explicit strategies.

Station metadata, crop calendar, output schemas, model registry, and
automatic result merging are deliberately outside this first-phase change.

## Regression tests

From the repository root, with NumPy and pandas installed:

```bash
python -m unittest discover -s tests -p 'test_epirice_shared_engine.py'
python -m unittest discover -s tests -p 'test_epirice_blb.py'
python -m unittest discover -s tests -p 'test_epirice_lb_runner.py'
python -m unittest discover -s tests -p 'test_epirice_lb_trial.py'
```

The pinned BLB reference compares 120 days of all numeric columns at
absolute tolerance 1e-6. This does not represent a fresh independent R run.

## Phase 2: reusable calendar, weather and station I/O

The following modules own the existing shared data behavior:

| Module | Responsibility |
| --- | --- |
| `crop_calendar.py` | Taiwan county crop calendar, representative planting/harvest dates, cross-year seasons, crop-period status |
| `stations.py` | active station list, county normalization, strict current-coordinate weather filename matching and renamed stations |
| `weather.py` | hourly temperature/humidity/precipitation CSV, Taiwan calendar days, 24-valid-hours requirement, daily TEMP/RHUM/RAIN |
| `sources.py` | local UTF-8-BOM CSV reading and HTTP retry helper |

BLB and LB prediction runners now import these modules directly; the historical
migration/rebuild tools use the shared helpers too. Old import paths under
`models.EPIRICE_BLB.crop_calendar`,
`models.EPIRICE_BLB.predict` and
`models.EPIRICE_BLB.output_contract.crop_context` remain supported.
The BLB `read_source` wrapper keeps its original `urlopen` mock hook.

No weather aggregation, crop date inference, station coordinate tolerance,
simulation arithmetic, output status, schema, model parameters, or daily
workflow commands are intentionally changed. The existing LB-specific choice
to stop a simulation at the first incomplete day is deliberately unchanged,
as is BLB's refusal to use any incomplete modeled crop weather.

```bash
python -m unittest discover -s tests -p 'test_epirice_*.py'
(cd models/EPIRICE_BLB && python predict.py --help >/dev/null)
(cd models/EPIRICE_LB && python predict.py --help >/dev/null)
```

The two model-specific output contracts remain separate. Consolidation of
state-field definitions, output schemas and model registration is a **later
phase** and should not be combined with these observational-I/O changes.


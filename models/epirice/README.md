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

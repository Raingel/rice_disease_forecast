# Candidate repair: station coverage dropped by the legacy first-model left join

**Status:** An opt-in **shadow implementation**, not a deployed public CSV
change. Public website, API v1/cache, PHP, scheduler, daily model outputs,
`crop_season_avg.py` and `registry/models.json` are not modified.

## Observed evidence

The public source files pinned at GitHub
`51fb7be702a28ae2133cbc466d409b47a8e5dee8` demonstrate the issue.

| Forecast date | First existing classic model | First-model stations | Other available full model | Published BLASTAM stations | C0G910 published station CSV |
| --- | --- | ---: | --- | ---: | --- |
| 2026-11-20 | BlastGRU-TW | 892 | BlastLSTLS (892) | 892 | present |
| **2026-11-21** | **BlastGRU-TW** | **92** | **BlastLSTLS / BLBTSLS (892)** | **892** | **absent** |
| **2026-11-22** | **BlastLSTLS** | **92** | BLBTSLS (92) | **892** | **absent** |

For **11/21**, the remaining full-coverage models have real values for
C0G910, including `BlastLSTLS=0.88`, `BLBTSLS=0.9660381`, and
`BLASTAM=0.0`. For **11/22**, the only published prediction
for this station among the audited model files is `BLASTAM=1.0`.
An absent model value on that day must stay **null**, never be
interpolated, zero-filled or inferred from a later date.

The daily GitHub BLASTAM files contain C0G910 on both dates. The public
`recent_daily_by_station/C0G910.csv` lacks those dates. This is an
organizer station-set-loss problem, not an HTTP cache problem.

## Root cause

`models/recent_forecast_organizer.py` initializes its merge from the
**first available model**, then left-joins later ordinary model columns.
As a result, valid station IDs appearing only in a later ordinary
model are excluded from the wide CSV. BLB/LB EPIRICE adapters already
perform validated outer joins, but those model files do not always
exist on far-forecast dates.

**The frozen 3A public contract explicitly documents this legacy
first-model/left-join behavior.** This candidate deliberately preserves
it as the default, rather than silently replacing published v1.

## Candidate repair

An explicitly opt-in `coverage_mode="union"` in
`merge_daily_predictions()`:

- Establish the union of station/day keys across every **available**
  ordinary model file, independent of model iteration order.
- Keep each published model's numerical value for keys that exist in
  that model. Missing values **remain null**; zero is a valid zero.
- For newly encountered stations, fill `站名`, `lat`, `lon`
  from the model where the station was first available.
  For existing station keys, retain existing/base metadata.
- Continue using the same relocated-station current-coordinate
  resolution and `validate="one_to_one"` to reject duplicate keys.
- Preserve EPIRICE_BLB/LB's existing outer joins,
  non-crop `-99` and explicit status semantics without modifying
  disease model mathematics.
- Preserve source-derived rows/columns and do not change season-summary
  denominators or thresholds.

```python
# Isolated/shadow call only. No website or daily CSV writes:
from models.recent_forecast_organizer import merge_daily_predictions

published = merge_daily_predictions("20261121")  # frozen v1, default
candidate = merge_daily_predictions("20261121", coverage_mode="union")
```

For a later separately approved GitHub scheduler adoption, the existing
`main()` can accept `RECENT_STATION_COVERAGE_MODE=union`. Both current
GitHub Actions pipelines **do not set** that environment variable, so
**this PR does not alter scheduled outputs**.

## Verification and immutable fixture

`tests/fixtures/coverage_shadow/20261120_22_published.json` records
all original station IDs and source Git blob SHAs for the 12 real model
files across 11/20, 11/21 and 11/22. It also freezes full original
model rows for 20 selected stations. It explicitly does **not** claim to
contain 892 real numerical rows per file.

`tests/test_station_coverage_repair.py` performs:

1. The source-key union calculation at the **full 892 station ID** level.
2. Legacy versus opt-in shadow equivalence on unchanged stations,
   preserving original scores, metadata and null masks.
3. Real 11/21 and 11/22 C0G910 scenarios: recovery of the absent station,
   conservation of LSTLS/BLBTSLS native values, and correct BLASTAM 0/1.
4. Unchanged 11/20 output, full 3A legacy five-station EPIRICE sample
   including non-crop `-99`, valid `0` and missing calendar status.
5. Synthetic leading-zero station IDs, missing-source metadata fallback
   and invalid-mode rejection.
6. The dedicated CI job additionally **sparse-checks out all 12 real
   full-sized CSV source files**, verifies each Git blob hash, and
   executes a full 892-station-day shadow comparison without network.

The new tests run offline and operate only in temporary directories.

## Deliberate limits / acceptance gate

**Do not merge or activate this change solely because the tests pass.**
The user should review the report and explicitly decide whether/when
to change the public station CSV station/day coverage. Adding missing
rows improves availability but is still an observable public-data
behavior change. Require explicit approval and a follow-up production
activation + post-run comparison, including API cache TTL policy.

The website's `forecast_station`/season-summary caches may still be
older than their TTL; that is a **separate** issue. No API calls or cache
refresh are made in this PR.

The 2026-11-21/22 forecast dates were in the forecast horizon when the
source snapshots were observed. This fix is not intended to synthesize
future dates missing from **all** source models.

## Test command

```bash
python -m unittest discover -s tests -p 'test_station_coverage_repair.py' -v
```

The 892-row end-to-end test requires
`REQUIRE_FULL_COVERAGE_SHADOW=1` and the 12 original source files,
which the dedicated CI workflow checks out explicitly. Ordinary
developer runs execute the 20-station pinned shadow and skip the
full-source-only test.

# BlastDT2 2026-01-05 archive repair

The historical daily file contained 1,327 rows for 664 stations. Of these,
663 stations had two rows for the same prediction date; 282 pairs disagreed.
For Puzi DARES (C2M920), the archived predictions were 1 and 0.

## Cause

`Raingel/BlastDT` annual files include the previous year's final date as a
warm-up row. `BlastDT2.py` clears the first row's `TxMaxAbs`, so that warm-up
row has a missing prediction even when the previous annual file has a valid
prediction for the same infection date. The old importer concatenated both
annual files, converted missing predictions to 0, and shifted both dates by
five days. The overlapping 2025-12-31 infection date therefore appeared twice
in the 2026-01-05 output.

The June 2 importer change preserved missing values and added `keep='last'`
deduplication, but only refreshed recent dates. It did not repair this older
file, and last-row precedence can discard a valid previous-year prediction
in favor of the next year's warm-up missing value.

## Repair and provenance

The repaired archive contains one row per station: 282 positive predictions,
370 negative predictions, and 12 missing predictions. All 664 station annual
sources were individually downloaded and checked. The 12 missing values are
missing in the actual-year source and were previously incorrectly stored as 0.
Puzi retains its valid prediction of 1.

Sources are pinned to `Raingel/BlastDT` commit
`7d74ea29edf0c1f7aca18babde8e2d7260a10bd4` (2026-05-22 12:21:33 UTC).
This is the latest upstream commit before the daily archive's final May 22
update, not a recomputation using today's revised weather observations.
Every non-missing actual-year source agrees with the corresponding older
non-warm-up archived value. The archived old daily file was also checked to
be byte-identical to the May 22 downstream commit.

- `station_audit.csv`: per-station old values, restored value, source row,
  source token, source checksum and pinned upstream commit.
- `repair_manifest.json`: counts, paths and before/after SHA256 hashes.
- `rice_blast_prediction/legacy_snapshots/20261007_blastdt2_year_boundary/20260105_BlastDT2.csv`:
  byte-identical preserved original (including incorrect duplicate values).

The current daily file is corrected. Historical results computed from the
old file are not silently rewritten and may require an explicit re-analysis.
This is a targeted January 5 repair; missing-to-zero errors in other old dates
have not been comprehensively reconstructed by this change.

## Preventing recurrence

The importer now keeps only infection dates belonging to each annual file
before applying the five-day shift. Actual missing values remain missing.
Exact repeated rows can be collapsed; genuine station/date value or location
conflicts stop the import instead of choosing an arbitrary last row.
This changes no model thresholds or weather observations.

Run the regression checks:

```sh
python -m unittest discover -s tests -p test_blastdt2_year_boundary.py -v
```

Reproduce the data repair into a new directory without changing the original:

```sh
python scripts/reproduce_blastdt2_20260105_repair.py --output-dir /new/repair-output
```

The reproduction checks all 664 pinned source checksums and the final CSV
checksum before writing. No other model's daily archives are changed. Gukeng
coordinate-version weather files require a separate manifest/retirement fix;
this patch does not remove or merge those files.

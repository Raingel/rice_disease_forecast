BlastGAT Integration Notes

- Canonical output field: BlastGAT
- Raw BlastGAT values should remain unchanged in CSV/API outputs.
- Model decision threshold: 0.23
- In this repo, threshold 0.23 is only used when counting BlastGAT high-risk days in recent_summary.csv.
- Frontend/UI should compute any separate display_score on its side if it wants a 0.5 visual cutoff.
- Do not overwrite the raw BlastGAT score with a display-mapped value.

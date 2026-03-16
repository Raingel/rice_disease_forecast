# -*- coding: utf-8 -*-
"""Build a reusable planthopper baseline snapshot from recent_summary.csv."""

import os
import pandas as pd

ROOT_DIR = os.getenv("PIPELINE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
INPUT_SUMMARY_CSV = os.getenv(
    "INPUT_SUMMARY_CSV",
    os.path.join(ROOT_DIR, "rice_blast_prediction", "recent_summary.csv"),
)
OUTPUT_SNAPSHOT_CSV = os.getenv(
    "PLAN_AVG_SNAPSHOT_CSV",
    os.path.join(ROOT_DIR, "rice_blast_prediction", "planthopper_avg_snapshot.csv"),
)


def main() -> None:
    if not os.path.exists(INPUT_SUMMARY_CSV):
        raise FileNotFoundError(f"summary not found: {INPUT_SUMMARY_CSV}")

    df = pd.read_csv(INPUT_SUMMARY_CSV)
    required = {"站號", "planthopper_avg"}
    if not required.issubset(df.columns):
        raise ValueError("recent_summary.csv missing required columns: 站號, planthopper_avg")

    out = df[["站號", "planthopper_avg"]].copy()
    out = out.dropna(subset=["planthopper_avg"]).drop_duplicates(subset=["站號"], keep="first")

    os.makedirs(os.path.dirname(OUTPUT_SNAPSHOT_CSV), exist_ok=True)
    out.to_csv(OUTPUT_SNAPSHOT_CSV, index=False)
    print(f"snapshot saved: {OUTPUT_SNAPSHOT_CSV}; rows={len(out)}")


if __name__ == "__main__":
    main()

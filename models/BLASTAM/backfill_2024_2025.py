import os
import time
from datetime import datetime, timedelta

import pandas as pd

from run_blastam import (
    WINDOW_HOURS,
    WINDOW_STEP,
    STA_LIST,
    compute_hourly_sunshine_fraction,
    extract_window,
    fetch_openmeteo_archive_batch,
    koshimizu_model,
)

ROOT_DIR = os.getenv("PIPELINE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
OUTPUT_DATA_DIR = os.getenv("DATA_FOLDER", os.path.join(ROOT_DIR, "rice_blast_prediction", "data"))
os.makedirs(OUTPUT_DATA_DIR, exist_ok=True)

BACKFILL_START_DATE = os.getenv("BLASTAM_BACKFILL_START_DATE", "2024-01-01")
BACKFILL_END_DATE = os.getenv("BLASTAM_BACKFILL_END_DATE", "2025-12-31")
INCUBATION_PERIOD = int(os.getenv("BLASTAM_INCUBATION_DAYS", "7"))
BATCH_SIZE = int(os.getenv("BLASTAM_BATCH_SIZE", "80"))
SLEEP_SECONDS = int(os.getenv("BLASTAM_BATCH_SLEEP_SECONDS", "60"))


def main():
    print(f"[INFO] BLASTAM backfill window: {BACKFILL_START_DATE} ~ {BACKFILL_END_DATE}")

    start_dt = datetime.strptime(BACKFILL_START_DATE, "%Y-%m-%d")
    end_dt = datetime.strptime(BACKFILL_END_DATE, "%Y-%m-%d")
    if end_dt < start_dt:
        raise ValueError("BLASTAM_BACKFILL_END_DATE must be >= BLASTAM_BACKFILL_START_DATE")

    df_sta = pd.read_csv(STA_LIST)
    df_sta = df_sta[df_sta["撤站日期"].isna()][["站號", "站名", "緯度", "經度"]].drop_duplicates()

    all_records = []

    for i in range(0, len(df_sta), BATCH_SIZE):
        group = df_sta.iloc[i : i + BATCH_SIZE]
        lat_list = group["緯度"].tolist()
        lon_list = group["經度"].tolist()

        archive_results = fetch_openmeteo_archive_batch(
            lat_list,
            lon_list,
            start=BACKFILL_START_DATE,
            end=BACKFILL_END_DATE,
        )

        for idx, (_, row) in enumerate(group.iterrows()):
            df_archive = archive_results[idx].sort_values("time")

            for window in extract_window(df_archive, WINDOW_HOURS, WINDOW_STEP):
                temp_5d = window["temperature_2m"].to_numpy(dtype=float)
                wind_5d = window["windspeed_10m"].to_numpy(dtype=float) / 3.6  # km/h -> m/s
                rainfall_5d = window["precipitation"].to_numpy(dtype=float)
                sun_shine_5d = compute_hourly_sunshine_fraction(window)

                blastam_pred = koshimizu_model(temp_5d, wind_5d, rainfall_5d, sun_shine_5d)
                pred_date = window["time"].max().strftime("%Y-%m-%d")

                all_records.append(
                    {
                        "站名": row["站名"],
                        "站號": row["站號"],
                        "日期": pred_date,
                        "lat": row["緯度"],
                        "lon": row["經度"],
                        "BLASTAM": blastam_pred[1]["blast_score"],
                    }
                )

        time.sleep(SLEEP_SECONDS)

    if not all_records:
        print("[WARN] No BLASTAM backfill records generated.")
        return

    total_df = pd.DataFrame(all_records)
    total_df["日期"] = pd.to_datetime(total_df["日期"]) + timedelta(days=INCUBATION_PERIOD)

    for date, group in total_df.groupby("日期"):
        out_path = os.path.join(OUTPUT_DATA_DIR, f"{date.strftime('%Y%m%d')}_BLASTAM.csv")
        group.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"[INFO] Saved {out_path} ({len(group)} rows)")


if __name__ == "__main__":
    main()

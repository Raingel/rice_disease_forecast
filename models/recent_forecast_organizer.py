import os
from datetime import datetime, timedelta

import pandas as pd

PLANTHOPPER_REMOTE_BASE_URL = os.getenv(
    "PLANTHOPPER_REMOTE_BASE_URL",
    "https://raw.githubusercontent.com/Raingel/HYSPLIT-Planthopper-Forecast/refs/heads/main/prediction",
)

ROOT_DIR = os.getenv("PIPELINE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
DATA_FOLDER = os.getenv("DATA_FOLDER", os.path.join(ROOT_DIR, "rice_blast_prediction", "data"))
OUTPUT_FOLDER = os.getenv("RECENT_OUTPUT_FOLDER", os.path.join(ROOT_DIR, "rice_blast_prediction", "recent_daily_by_station"))
PLANTHOPPER_FOLDER = os.getenv("PLAN_FOLDER", "")

STATION_ID_COL = "站號"
STATION_NAME_COL = "站名"
DATE_COL = "日期"
MODEL_FILES = {
    "BlastGRU-TW": "BlastGRU-TW",
    "BlastDT2": "BlastDT2",
    "BlastLSTLS": "BlastLSTLS",
    "BLBTSLS": "BLBTSLS",
    "BlastTF": "BlastTF",
    "BlastGAT": "BlastGAT",
    "BLASTAM": "BLASTAM",
}

os.makedirs(OUTPUT_FOLDER, exist_ok=True)
planthopper_daily_cache = {}


def load_planthopper_data(date_str: str):
    if date_str in planthopper_daily_cache:
        return planthopper_daily_cache[date_str]

    local_path = os.path.join(PLANTHOPPER_FOLDER, f"{date_str}_max_freq.csv") if PLANTHOPPER_FOLDER else ""
    remote_url = f"{PLANTHOPPER_REMOTE_BASE_URL}/{date_str}_max_freq.csv"

    for source in [local_path, remote_url]:
        if not source:
            continue
        try:
            if source == local_path and not os.path.exists(source):
                continue
            df = pd.read_csv(source)
            if {"y", "x", "value"}.issubset(df.columns):
                planthopper_daily_cache[date_str] = df
                return df
        except Exception:
            continue

    planthopper_daily_cache[date_str] = None
    return None


def find_nearest_planthopper_value(lat: float, lon: float, planthopper_data: pd.DataFrame):
    distances = ((planthopper_data["y"] - lat) ** 2 + (planthopper_data["x"] - lon) ** 2) ** 0.5
    nearest_row = planthopper_data.loc[distances.idxmin()]
    return nearest_row["value"]


def normalize_daily_frame(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    frame = df.copy()
    frame[DATE_COL] = pd.to_datetime(frame[DATE_COL], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
    cols = [c for c in [STATION_ID_COL, STATION_NAME_COL, DATE_COL, "lat", "lon", value_col] if c in frame.columns]
    return frame[cols]


def merge_daily_predictions(date_str: str):
    merged = None
    for model_name, suffix in MODEL_FILES.items():
        path = os.path.join(DATA_FOLDER, f"{date_str}_{suffix}.csv")
        if not os.path.exists(path):
            continue

        df = normalize_daily_frame(pd.read_csv(path), model_name)
        if merged is None:
            merged = df
        else:
            merged = pd.merge(
                merged,
                df[[STATION_ID_COL, DATE_COL, model_name]],
                on=[STATION_ID_COL, DATE_COL],
                how="left",
            )

    if merged is None:
        return None

    planthopper_data = load_planthopper_data(date_str)
    if planthopper_data is not None and {"lat", "lon"}.issubset(merged.columns):
        merged["planthopper"] = merged.apply(
            lambda row: find_nearest_planthopper_value(row["lat"], row["lon"], planthopper_data),
            axis=1,
        )

    return merged.drop_duplicates(subset=[STATION_ID_COL, DATE_COL], keep="first")


def main() -> None:
    all_data = []
    today = datetime.today() - timedelta(days=30)
    date_range = [today + timedelta(days=i) for i in range(60)]

    for target_date in date_range:
        date_str = target_date.strftime("%Y%m%d")
        merged = merge_daily_predictions(date_str)
        if merged is not None:
            all_data.append(merged)

    if not all_data:
        print("No data files found for the specified date range.")
        return

    final_df = pd.concat(all_data, ignore_index=True)

    for station_id, group in final_df.groupby(STATION_ID_COL):
        output_file = os.path.join(OUTPUT_FOLDER, f"{station_id}.csv")
        group.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"Data for station {station_id} has been saved to {output_file}")

    station_columns = [c for c in [STATION_ID_COL, STATION_NAME_COL, "lon", "lat"] if c in final_df.columns]
    if station_columns:
        station_list = final_df[station_columns].drop_duplicates(subset=[STATION_ID_COL], keep="first")
        station_list_file = os.path.join(OUTPUT_FOLDER, "station_list.csv")
        station_list.to_csv(station_list_file, index=False, encoding="utf-8-sig")
        print(f"Station list has been saved to {station_list_file}")


if __name__ == "__main__":
    main()

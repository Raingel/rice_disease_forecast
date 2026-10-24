import os
import sys
from pathlib import Path
from functools import lru_cache
from datetime import datetime, timedelta

import pandas as pd
# The pipeline executes this file directly from models/, outside package mode.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from models.EPIRICE_BLB.output_contract import merge_station_frames
    from models.EPIRICE_LB.output_contract import merge_compact, COMPACT_FIELDS
except ModuleNotFoundError:
    from EPIRICE_BLB.output_contract import merge_station_frames
    from EPIRICE_LB.output_contract import merge_compact, COMPACT_FIELDS

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
    "EPIRICE_BLB": "EPIRICE_BLB",
    "EPIRICE_LB": "EPIRICE_LB",
}

os.makedirs(OUTPUT_FOLDER, exist_ok=True)
planthopper_daily_cache = {}


@lru_cache(maxsize=1)
def current_station_coordinates():
    try:
        from models.EPIRICE_BLB.predict import load_stations, read_source
        from models.EPIRICE_BLB.crop_calendar import STATIONS_URL
    except ModuleNotFoundError:
        from EPIRICE_BLB.predict import load_stations, read_source
        from EPIRICE_BLB.crop_calendar import STATIONS_URL
    return load_stations(read_source(os.getenv("EPIRICE_STATIONS_SOURCE", STATIONS_URL))).set_index(STATION_ID_COL)


def resolve_station_days(frame, stations=None):
    """Resolve relocated-station variants; never select a conflicting first row."""
    keys = [STATION_ID_COL, DATE_COL]
    frame = frame.drop_duplicates().copy()
    duplicated = frame.duplicated(keys, keep=False)
    if not duplicated.any():
        return frame
    stations = current_station_coordinates() if stations is None else stations
    resolved = [frame.loc[~duplicated]]
    for (sid, day), group in frame.loc[duplicated].groupby(keys, dropna=False):
        if sid not in stations.index or not {"lat", "lon"}.issubset(group.columns):
            raise ValueError(f"Cannot resolve station/date variants: {sid} {day}; missing current coordinates")
        station = stations.loc[sid]
        match = (pd.to_numeric(group.lat, errors="coerce").sub(float(station["緯度"])).abs().le(1e-6)
                 & pd.to_numeric(group.lon, errors="coerce").sub(float(station["經度"])).abs().le(1e-6))
        selected = group.loc[match]
        if len(selected) != 1:
            raise ValueError(f"Ambiguous station/date variants: {sid} {day}; {len(selected)} current-coordinate rows")
        print(f"[INFO] {sid} {day}: selected current-coordinate row from {len(group)} variants")
        resolved.append(selected)
    return pd.concat(resolved, ignore_index=True)


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
    if value_col == "EPIRICE_BLB":
        cols.extend(c for c in frame.columns if c.startswith("EPIRICE_BLB_"))
    elif value_col == "EPIRICE_LB":
        cols.extend(c for c in COMPACT_FIELDS if c != value_col and c in frame.columns)
    return resolve_station_days(frame[cols])


def merge_daily_predictions(date_str: str):
    merged = None
    for model_name, suffix in MODEL_FILES.items():
        path = os.path.join(DATA_FOLDER, f"{date_str}_{suffix}.csv")
        if not os.path.exists(path):
            continue

        df = normalize_daily_frame(pd.read_csv(path, dtype={STATION_ID_COL: str}), model_name)
        if merged is None:
            merged = df
        elif model_name == "EPIRICE_BLB":
            merged = merge_station_frames(merged, df)
        elif model_name == "EPIRICE_LB":
            merged = merge_compact(merged, df)
        else:
            value_columns = [model_name]
            if model_name == "EPIRICE_BLB":
                value_columns.extend(c for c in df.columns if c.startswith("EPIRICE_BLB_"))
            merged = pd.merge(
                merged,
                df[[STATION_ID_COL, DATE_COL, *value_columns]],
                on=[STATION_ID_COL, DATE_COL],
                how="left",
                validate="one_to_one",
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

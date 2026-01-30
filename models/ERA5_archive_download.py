# %%
import pandas as pd
from datetime import datetime, timedelta
import math
import os
import time


ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_FIELDS = [
    "temperature_2m",
    "relativehumidity_2m",
    "dewpoint_2m",
    "precipitation",
    "cloudcover",
    "direct_radiation",
    "windspeed_10m",
    "winddirection_10m",
]
TIMEZONE = "Asia%2FSingapore"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER = os.path.join(BASE_DIR, "..", "ERA5_archive")
START_DATE = "2000-01-01"
END_DATE = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")
BATCH_SIZE = 80
MAX_DAYS_PER_REQUEST = 365
SLEEP_SECONDS = 360
CHECK_FULL_COVERAGE = True


def fetch_openmeteo_archive_batch(lat_list, lon_list, start="2013-01-01", end="2018-03-25"):
    """
    批次下載歷史資料，lat_list 與 lon_list 為數值列表。
    API 回傳為多站資料，每一筆資料會包含一個 'hourly' 鍵，將其轉成 DataFrame，
    並計算風的 u, v 分量。
    """
    lat_str = ",".join(map(str, lat_list))
    lon_str = ",".join(map(str, lon_list))
    url = (
        f"{ARCHIVE_API_URL}?"
        f"latitude={lat_str}&longitude={lon_str}&start_date={start}&end_date={end}"
        f"&hourly={','.join(HOURLY_FIELDS)}"
        f"&timezone={TIMEZONE}"
    )
    df = pd.read_json(url)
    results = []
    for _, row in df.iterrows():
        hourly = row["hourly"]
        df_hourly = pd.DataFrame(hourly)
        df_hourly["time"] = pd.to_datetime(df_hourly["time"])
        # 若有風速與風向資料，則計算 Wu, Wv (相容舊資料格式)
        if "windspeed_10m" in df_hourly.columns and "winddirection_10m" in df_hourly.columns:
            df_hourly["Wu"] = df_hourly["windspeed_10m"] * df_hourly["winddirection_10m"].apply(
                lambda x: math.cos(math.radians(270 - x))
            )
            df_hourly["Wv"] = df_hourly["windspeed_10m"] * df_hourly["winddirection_10m"].apply(
                lambda x: math.sin(math.radians(270 - x))
            )
        df_hourly = df_hourly.dropna(subset=["time"])
        results.append(df_hourly)
    return results


def normalize_wind_components(df):
    if "windspeed_10m" not in df.columns or "winddirection_10m" not in df.columns:
        return df
    if "Wu" not in df.columns:
        df["Wu"] = pd.NA
    if "Wv" not in df.columns:
        df["Wv"] = pd.NA
    if "u" not in df.columns:
        df["u"] = pd.NA
    if "v" not in df.columns:
        df["v"] = pd.NA

    missing_wu = df["Wu"].isna()
    missing_wv = df["Wv"].isna()
    missing_u = df["u"].isna()
    missing_v = df["v"].isna()

    if missing_wu.any():
        df.loc[missing_wu, "Wu"] = df.loc[missing_wu, "windspeed_10m"] * df.loc[
            missing_wu, "winddirection_10m"
        ].apply(lambda x: math.cos(math.radians(270 - x)))
    if missing_wv.any():
        df.loc[missing_wv, "Wv"] = df.loc[missing_wv, "windspeed_10m"] * df.loc[
            missing_wv, "winddirection_10m"
        ].apply(lambda x: math.sin(math.radians(270 - x)))
    if missing_u.any():
        df.loc[missing_u, "u"] = df.loc[missing_u, "windspeed_10m"] * df.loc[
            missing_u, "winddirection_10m"
        ].apply(lambda x: math.cos(math.radians(270 - x)))
    if missing_v.any():
        df.loc[missing_v, "v"] = df.loc[missing_v, "windspeed_10m"] * df.loc[
            missing_v, "winddirection_10m"
        ].apply(lambda x: math.sin(math.radians(270 - x)))
    return df


def chunk_date_range(start_date, end_date, max_days):
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=max_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def load_existing_info(path):
    if not os.path.exists(path):
        return pd.DatetimeIndex([]), 0
    try:
        df_existing = pd.read_csv(path, usecols=["time"])
    except ValueError:
        return pd.DatetimeIndex([]), 0
    time_index = pd.to_datetime(df_existing["time"]).dropna().unique()
    return time_index, len(df_existing)


def count_missing_hours(existing_index, chunk_start, chunk_end):
    chunk_range = pd.date_range(chunk_start, chunk_end, freq="H")
    if existing_index is None or len(existing_index) == 0:
        return len(chunk_range)
    missing = chunk_range.difference(pd.DatetimeIndex(existing_index))
    return len(missing)


def merge_and_save(existing_path, new_data):
    new_data = new_data.copy()
    new_data["time"] = pd.to_datetime(new_data["time"])
    new_data = normalize_wind_components(new_data)
    if os.path.exists(existing_path):
        df_existing = pd.read_csv(existing_path)
        if "time" not in df_existing.columns:
            df_existing = pd.DataFrame(columns=new_data.columns)
        else:
            df_existing["time"] = pd.to_datetime(df_existing["time"])
            df_existing = normalize_wind_components(df_existing)
        df_existing["__source"] = "existing"
    else:
        df_existing = pd.DataFrame(columns=new_data.columns)
        df_existing["__source"] = pd.Series(dtype="string")

    new_data["__source"] = "new"
    all_columns = sorted(set(df_existing.columns).union(new_data.columns))
    df_existing = df_existing.reindex(columns=all_columns)
    new_data = new_data.reindex(columns=all_columns)
    df_combined = pd.concat([df_existing, new_data], ignore_index=True)
    data_columns = [col for col in df_combined.columns if col not in ("time", "__source")]
    df_combined["_filled_count"] = df_combined[data_columns].notna().sum(axis=1)
    df_combined = df_combined.sort_values(["time", "_filled_count"], ascending=[True, False])
    df_combined = df_combined.drop_duplicates(subset=["time"], keep="first")

    existing_times = set(df_existing["time"].dropna())
    df_combined["__is_existing_time"] = df_combined["time"].isin(existing_times)
    added_count = int((~df_combined["__is_existing_time"] & (df_combined["__source"] == "new")).sum())
    updated_count = int((df_combined["__is_existing_time"] & (df_combined["__source"] == "new")).sum())

    df_combined = df_combined.drop(columns=["_filled_count", "__source", "__is_existing_time"]).sort_values("time")

    if os.path.exists(existing_path):
        existing_row_count = len(df_existing)
        combined_row_count = len(df_combined)
        if combined_row_count < existing_row_count:
            print(
                f"警告: {os.path.basename(existing_path)} 合併後列數變少 "
                f"({combined_row_count} < {existing_row_count})，將保留原檔案。"
            )
            return df_existing.drop(columns=["__source"]).sort_values("time"), 0, 0

    df_combined.to_csv(existing_path, index=False)
    return df_combined, added_count, updated_count


# %%
# 取得要下載的氣象站列表
STA_LIST = "https://raw.githubusercontent.com/Raingel/weather_station_list/refs/heads/main/data/weather_sta_list.csv"
df_sta = pd.read_csv(STA_LIST)
# 僅保留撤站日期為 nan 的資料
df_sta = df_sta[df_sta["撤站日期"].isna()]
# 只保留站號、站名、緯度、經度
df_sta = df_sta[["站號", "站名", "緯度", "經度"]]
# 移除重複的資料
df_sta = df_sta.drop_duplicates()
print(f"共有 {len(df_sta)} 個有效氣象站")

# %%
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

existing_index_map = {}
existing_row_count_map = {}
for _, row in df_sta.iterrows():
    filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
    output_path = os.path.join(OUTPUT_FOLDER, filename)
    existing_index, existing_rows = load_existing_info(output_path)
    existing_index_map[filename] = existing_index
    existing_row_count_map[filename] = existing_rows

for chunk_start, chunk_end in chunk_date_range(START_DATE, END_DATE, MAX_DAYS_PER_REQUEST):
    print(f"處理區間: {chunk_start.date()} ~ {chunk_end.date()}")
    for i in range(0, len(df_sta), BATCH_SIZE):
        group = df_sta.iloc[i : i + BATCH_SIZE]
        missing_rows = []
        missing_info = []
        for _, row in group.iterrows():
            filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
            output_path = os.path.join(OUTPUT_FOLDER, filename)
            existing_index = existing_index_map.get(filename, pd.DatetimeIndex([]))
            missing_count = count_missing_hours(existing_index, chunk_start, chunk_end)
            if CHECK_FULL_COVERAGE and missing_count == 0:
                continue
            missing_rows.append(row)
            missing_info.append((filename, output_path, missing_count))

        if not missing_rows:
            continue

        df_missing = pd.DataFrame(missing_rows)
        lat_list = df_missing["緯度"].tolist()
        lon_list = df_missing["經度"].tolist()
        print(
            f"批次下載: 站點 {i + 1} ~ {i + len(df_missing)} "
            f"(缺資料站點數: {len(df_missing)})"
        )

        archive_results = fetch_openmeteo_archive_batch(
            lat_list,
            lon_list,
            start=chunk_start.strftime("%Y-%m-%d"),
            end=chunk_end.strftime("%Y-%m-%d"),
        )

        for idx, info in enumerate(missing_info):
            filename, output_path, missing_count = info
            existing_rows = existing_row_count_map.get(filename, 0)
            df_archive = archive_results[idx]
            print(
                f"處理站點: {filename} | 既有 {existing_rows} 筆 | "
                f"本段缺 {missing_count} 筆 | 下載區間 {chunk_start.date()} ~ {chunk_end.date()}"
            )
            df_combined, added_count, updated_count = merge_and_save(output_path, df_archive)
            existing_index_map[filename] = pd.to_datetime(df_combined["time"]).dropna().unique()
            existing_row_count_map[filename] = len(df_combined)
            print(
                f"合併完成: {filename} | 新增 {added_count} 筆 | "
                f"更新 {updated_count} 筆 | 合併後 {len(df_combined)} 筆"
            )

        time.sleep(SLEEP_SECONDS)

# %%

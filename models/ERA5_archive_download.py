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
    "precipitation",
    "windspeed_10m",
    "winddirection_10m",
]
TIMEZONE = "Asia%2FSingapore"

OUTPUT_FOLDER = os.path.join("..", "ERA5_archive")
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
        # 若有風速與風向資料，則計算 u, v 分量
        if "windspeed_10m" in df_hourly.columns and "winddirection_10m" in df_hourly.columns:
            df_hourly["u"] = df_hourly["windspeed_10m"] * df_hourly["winddirection_10m"].apply(
                lambda x: math.cos(math.radians(270 - x))
            )
            df_hourly["v"] = df_hourly["windspeed_10m"] * df_hourly["winddirection_10m"].apply(
                lambda x: math.sin(math.radians(270 - x))
            )
        df_hourly = df_hourly.dropna()
        results.append(df_hourly)
    return results


def chunk_date_range(start_date, end_date, max_days):
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=max_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def load_existing_index(path):
    if not os.path.exists(path):
        return pd.DatetimeIndex([])
    df_existing = pd.read_csv(path)
    if "time" not in df_existing.columns:
        return pd.DatetimeIndex([])
    return pd.to_datetime(df_existing["time"]).dropna().unique()


def has_full_coverage(existing_index, chunk_start, chunk_end):
    if existing_index is None or len(existing_index) == 0:
        return False
    chunk_range = pd.date_range(chunk_start, chunk_end, freq="H")
    missing = chunk_range.difference(pd.DatetimeIndex(existing_index))
    return missing.empty


def merge_and_save(existing_path, new_data):
    if os.path.exists(existing_path):
        df_existing = pd.read_csv(existing_path)
        df_combined = pd.concat([df_existing, new_data], ignore_index=True)
    else:
        df_combined = new_data.copy()
    df_combined["time"] = pd.to_datetime(df_combined["time"])
    df_combined = df_combined.drop_duplicates(subset=["time"]).sort_values("time")
    df_combined.to_csv(existing_path, index=False)
    return df_combined


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
for _, row in df_sta.iterrows():
    filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
    output_path = os.path.join(OUTPUT_FOLDER, filename)
    existing_index_map[filename] = load_existing_index(output_path)

for chunk_start, chunk_end in chunk_date_range(START_DATE, END_DATE, MAX_DAYS_PER_REQUEST):
    print(f"處理區間: {chunk_start.date()} ~ {chunk_end.date()}")
    for i in range(0, len(df_sta), BATCH_SIZE):
        group = df_sta.iloc[i : i + BATCH_SIZE]
        missing_rows = []
        for _, row in group.iterrows():
            filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
            output_path = os.path.join(OUTPUT_FOLDER, filename)
            existing_index = existing_index_map.get(filename, pd.DatetimeIndex([]))
            if CHECK_FULL_COVERAGE and has_full_coverage(existing_index, chunk_start, chunk_end):
                continue
            missing_rows.append(row)

        if not missing_rows:
            continue

        df_missing = pd.DataFrame(missing_rows)
        lat_list = df_missing["緯度"].tolist()
        lon_list = df_missing["經度"].tolist()

        archive_results = fetch_openmeteo_archive_batch(
            lat_list,
            lon_list,
            start=chunk_start.strftime("%Y-%m-%d"),
            end=chunk_end.strftime("%Y-%m-%d"),
        )

        for idx, (_, row) in enumerate(df_missing.iterrows()):
            filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
            output_path = os.path.join(OUTPUT_FOLDER, filename)
            df_archive = archive_results[idx]
            df_combined = merge_and_save(output_path, df_archive)
            existing_index_map[filename] = pd.to_datetime(df_combined["time"]).dropna().unique()
            print(f"已更新 {filename} ({chunk_start.date()} ~ {chunk_end.date()})")

        time.sleep(SLEEP_SECONDS)

# %%

# %%
# -*- coding: utf-8 -*-
"""
ERA5 archive 清理與補齊 (Open-Meteo archive API)

保留原本「下載氣象站列表」的方法
STA_LIST 來源
https://raw.githubusercontent.com/Raingel/weather_station_list/refs/heads/main/data/weather_sta_list.csv

輸出資料夾
../ERA5_archive/

最終欄位固定
time,temperature_2m,relativehumidity_2m,precipitation,windspeed_10m,winddirection_10m,u,v

時間補齊到
2000-01-01 00:00:00 ~ 2025-12-31 23:00:00
"""

import os
import re
import time
import math
import requests
import numpy as np
import pandas as pd
from datetime import timedelta
from typing import Dict, List, Tuple, Optional

# =============================
# 0) 參數區
# =============================
ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"

# 站點列表來源，保留你原本的方式
STA_LIST = "https://raw.githubusercontent.com/Raingel/weather_station_list/refs/heads/main/data/weather_sta_list.csv"

# Open-Meteo archive API 的欄位命名
HOURLY_FIELDS_API = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
]

# 最終固定欄位 (你既有檔案格式)
FINAL_COLUMNS = [
    "time",
    "temperature_2m",
    "relativehumidity_2m",
    "precipitation",
    "windspeed_10m",
    "winddirection_10m",
    "u",
    "v",
]

TARGET_START_DATE = "2000-01-01"
TARGET_END_DATE = "2025-12-31"

TIMEZONE = "Asia/Singapore"

MAX_DAYS_PER_REQUEST = 365
API_BATCH_SIZE = 60

MAX_RETRIES = 5
RETRY_SLEEP_BASE = 3

SLEEP_SECONDS = 20  # 你原本是 360 秒，想快一點可以先用 5，遇到 rate limit 再調大

VERIFY_AT_END = True
FINAL_PATCH_MISSING = True
FINAL_PATCH_MAX_RANGES = 50

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER = os.path.normpath(os.path.join(BASE_DIR, "..", "ERA5_archive"))
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# =============================
# 1) 時間軸工具
# =============================
TARGET_START_DT = pd.Timestamp(TARGET_START_DATE)
TARGET_END_DT = pd.Timestamp(TARGET_END_DATE) + pd.Timedelta(hours=23)
EXPECTED_HOURS = int(((TARGET_END_DT - TARGET_START_DT) / pd.Timedelta(hours=1)) + 1)

def dt_to_hour_index(dt: pd.Timestamp) -> int:
    return int((dt - TARGET_START_DT) / pd.Timedelta(hours=1))

def build_full_has_array(times: pd.Series) -> np.ndarray:
    has = np.zeros(EXPECTED_HOURS, dtype=bool)
    if times is None or len(times) == 0:
        return has

    t = pd.to_datetime(times, errors="coerce").dropna()
    if len(t) == 0:
        return has

    t = t[(t >= TARGET_START_DT) & (t <= TARGET_END_DT)]
    if len(t) == 0:
        return has

    idx = ((t - TARGET_START_DT) / pd.Timedelta(hours=1)).astype("int64")
    idx = idx[(idx >= 0) & (idx < EXPECTED_HOURS)]
    if len(idx) == 0:
        return has

    has[np.unique(idx)] = True
    return has

def iter_date_chunks(start_date: str, end_date: str, max_days: int):
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days - 1), end)
        yield cur.date().isoformat(), chunk_end.date().isoformat()
        cur = chunk_end + timedelta(days=1)

# =============================
# 2) 站點列表下載與檔名
# =============================
def load_station_list() -> pd.DataFrame:
    df_sta = pd.read_csv(STA_LIST)
    # 僅保留撤站日期為 nan 的資料
    if "撤站日期" in df_sta.columns:
        df_sta = df_sta[df_sta["撤站日期"].isna()]
    # 只保留站號、站名、緯度、經度
    df_sta = df_sta[["站號", "站名", "緯度", "經度"]]
    df_sta = df_sta.drop_duplicates()
    df_sta = df_sta.reset_index(drop=True)
    print(f"共有 {len(df_sta)} 個有效氣象站")
    return df_sta

def make_filename(sta_id: str, sta_name: str, lat: float, lon: float) -> str:
    return f"{sta_id}_{sta_name}_{lat}_{lon}.csv"

# =============================
# 3) 風向量工具
#   保持你舊規則
#   u = speed * cos(radians(270 - dir))
#   v = speed * sin(radians(270 - dir))
# =============================
def uv_from_speed_dir(speed: pd.Series, direction: pd.Series) -> Tuple[pd.Series, pd.Series]:
    sp = pd.to_numeric(speed, errors="coerce")
    wd = pd.to_numeric(direction, errors="coerce")
    rad = np.deg2rad(270.0 - wd)
    u = sp * np.cos(rad)
    v = sp * np.sin(rad)
    return u, v

def speed_dir_from_uv(u: pd.Series, v: pd.Series) -> Tuple[pd.Series, pd.Series]:
    uu = pd.to_numeric(u, errors="coerce")
    vv = pd.to_numeric(v, errors="coerce")
    speed = np.sqrt(uu * uu + vv * vv)
    angle = np.degrees(np.arctan2(vv, uu))
    direction = (270.0 - angle) % 360.0
    return speed, direction

# =============================
# 4) DataFrame 清理
# =============================
# API 欄位轉成你要的欄位名
API_TO_FINAL_RENAME = {
    "relative_humidity_2m": "relativehumidity_2m",
    "wind_speed_10m": "windspeed_10m",
    "wind_direction_10m": "winddirection_10m",
}

# 也支援舊檔可能出現的欄位名
ALT_RENAME = {
    "wind_speed_10m": "windspeed_10m",
    "wind_direction_10m": "winddirection_10m",
    "relative_humidity_2m": "relativehumidity_2m",
}

EXTRA_WIND_ALIASES = [
    ("Wu", "Wv"),
    ("wu", "wv"),
    ("U", "V"),
]

def sanitize_df(df: pd.DataFrame, priority: int) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=FINAL_COLUMNS + ["__priority"])

    df = df.copy()

    # time
    if "time" not in df.columns:
        return pd.DataFrame(columns=FINAL_COLUMNS + ["__priority"])

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"])
    df = df[(df["time"] >= TARGET_START_DT) & (df["time"] <= TARGET_END_DT)]
    if len(df) == 0:
        return pd.DataFrame(columns=FINAL_COLUMNS + ["__priority"])

    # rename
    df = df.rename(columns=API_TO_FINAL_RENAME)
    df = df.rename(columns=ALT_RENAME)

    # ensure columns exist
    for col in FINAL_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    # 若存在 Wu/Wv 之類欄位，且 u/v 缺，先補進 u/v
    for a_u, a_v in EXTRA_WIND_ALIASES:
        if a_u in df.columns and a_v in df.columns:
            missing_u = df["u"].isna()
            missing_v = df["v"].isna()
            if missing_u.any():
                df.loc[missing_u, "u"] = df.loc[missing_u, a_u]
            if missing_v.any():
                df.loc[missing_v, "v"] = df.loc[missing_v, a_v]

    # 若 windspeed/winddirection 缺，但 u/v 有，先反推
    miss_sp = df["windspeed_10m"].isna()
    miss_wd = df["winddirection_10m"].isna()
    has_uv = df["u"].notna() & df["v"].notna()
    if (miss_sp | miss_wd).any() and has_uv.any():
        sp2, wd2 = speed_dir_from_uv(df.loc[has_uv, "u"], df.loc[has_uv, "v"])
        if miss_sp.any():
            df.loc[has_uv & miss_sp, "windspeed_10m"] = sp2.loc[has_uv & miss_sp]
        if miss_wd.any():
            df.loc[has_uv & miss_wd, "winddirection_10m"] = wd2.loc[has_uv & miss_wd]

    # 最終以 windspeed/winddirection 重算 u/v，確保一致
    has_spwd = df["windspeed_10m"].notna() & df["winddirection_10m"].notna()
    if has_spwd.any():
        u2, v2 = uv_from_speed_dir(df.loc[has_spwd, "windspeed_10m"], df.loc[has_spwd, "winddirection_10m"])
        df.loc[has_spwd, "u"] = u2
        df.loc[has_spwd, "v"] = v2

    # numeric
    for col in FINAL_COLUMNS:
        if col == "time":
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["__priority"] = int(priority)
    return df[FINAL_COLUMNS + ["__priority"]]

def dedupe_and_finalize(df_all: pd.DataFrame) -> pd.DataFrame:
    if df_all is None or len(df_all) == 0:
        return pd.DataFrame(columns=FINAL_COLUMNS)

    df = df_all.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"])

    data_cols = [c for c in FINAL_COLUMNS if c != "time"]
    df["_filled"] = df[data_cols].notna().sum(axis=1)

    df = df.sort_values(["time", "_filled", "__priority"], ascending=[True, False, False])
    df = df.drop_duplicates(subset=["time"], keep="first")
    df = df.drop(columns=["_filled", "__priority"]).sort_values("time")

    for col in FINAL_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[FINAL_COLUMNS]

# =============================
# 5) 下載
# =============================
def fetch_openmeteo_archive_batch(lat_list: List[float], lon_list: List[float], start_date: str, end_date: str) -> List[pd.DataFrame]:
    assert len(lat_list) == len(lon_list)

    params = {
        "latitude": ",".join(map(str, lat_list)),
        "longitude": ",".join(map(str, lon_list)),
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(HOURLY_FIELDS_API),
        "timezone": TIMEZONE,
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(ARCHIVE_API_URL, params=params, timeout=120)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()

            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise RuntimeError("Unexpected JSON structure")

            results = []
            for item in data:
                hourly = item.get("hourly", None)
                if hourly is None:
                    results.append(pd.DataFrame(columns=["time"]))
                else:
                    results.append(pd.DataFrame(hourly))

            while len(results) < len(lat_list):
                results.append(pd.DataFrame(columns=["time"]))

            return results

        except Exception as e:
            last_err = e
            sleep_s = RETRY_SLEEP_BASE * attempt
            print(f"API 失敗，重試 {attempt}/{MAX_RETRIES}，{sleep_s} 秒後再試，原因: {e}")
            time.sleep(sleep_s)

    raise RuntimeError(f"API 多次失敗，最後錯誤: {last_err}")

# =============================
# 6) 檔案狀態判斷與清理
# =============================
def load_existing_times(path: str) -> pd.Series:
    if not os.path.exists(path):
        return pd.Series(dtype="datetime64[ns]")
    try:
        df_t = pd.read_csv(path, usecols=["time"])
        t = pd.to_datetime(df_t["time"], errors="coerce").dropna()
        return t
    except Exception:
        return pd.Series(dtype="datetime64[ns]")

def needs_clean(path: str) -> bool:
    if not os.path.exists(path):
        return False

    try:
        cols = list(pd.read_csv(path, nrows=0).columns)
    except Exception:
        return True

    # 欄位只要不是剛好 FINAL_COLUMNS 就視為需要清理
    if cols != FINAL_COLUMNS:
        return True

    # 再做一個小抽樣檢查 u/v 是否一致
    try:
        df_s = pd.read_csv(path, nrows=200, usecols=FINAL_COLUMNS)
    except Exception:
        return True

    if len(df_s) == 0:
        return False

    # 若缺 windspeed 或 winddirection 或 u v，本來就不合格
    if df_s["windspeed_10m"].isna().all() or df_s["winddirection_10m"].isna().all():
        return True
    if df_s["u"].isna().all() or df_s["v"].isna().all():
        return True

    u2, v2 = uv_from_speed_dir(df_s["windspeed_10m"], df_s["winddirection_10m"])
    du = (pd.to_numeric(df_s["u"], errors="coerce") - u2).abs()
    dv = (pd.to_numeric(df_s["v"], errors="coerce") - v2).abs()
    # 容忍極小誤差
    if (du > 1e-6).any() or (dv > 1e-6).any():
        return True

    return False

def clean_file_in_place(path: str):
    if not os.path.exists(path):
        return
    try:
        df0 = pd.read_csv(path)
    except Exception:
        print(f"清理失敗，無法讀取: {path}")
        return

    df0 = sanitize_df(df0, priority=0)
    df_final = dedupe_and_finalize(df0)
    df_final["time"] = pd.to_datetime(df_final["time"], errors="coerce")
    df_final = df_final[(df_final["time"] >= TARGET_START_DT) & (df_final["time"] <= TARGET_END_DT)].sort_values("time")
    df_final.to_csv(path, index=False)

# =============================
# 7) 主處理流程
# =============================
def process_station_batch(batch_rows: pd.DataFrame):
    # state: 每個站點保留 has array 與是否需要清理
    state = {}

    for _, row in batch_rows.iterrows():
        sta_id = str(row["站號"])
        sta_name = str(row["站名"])
        lat = float(row["緯度"])
        lon = float(row["經度"])
        filename = make_filename(sta_id, sta_name, lat, lon)
        path = os.path.join(OUTPUT_FOLDER, filename)

        t_existing = load_existing_times(path)
        has = build_full_has_array(t_existing)
        state[filename] = {
            "path": path,
            "lat": lat,
            "lon": lon,
            "has": has,
            "need_clean": needs_clean(path),
            "touched": False,
        }

    # chunk 補齊
    for chunk_start_date, chunk_end_date in iter_date_chunks(TARGET_START_DATE, TARGET_END_DATE, MAX_DAYS_PER_REQUEST):
        cs = pd.Timestamp(chunk_start_date)               # 00:00
        ce = pd.Timestamp(chunk_end_date) + pd.Timedelta(hours=23)  # 23:00
        si = dt_to_hour_index(cs)
        ei = dt_to_hour_index(ce)

        need_fns = []
        for fn, st in state.items():
            missing = int((~st["has"][si:ei + 1]).sum())
            if missing > 0:
                need_fns.append(fn)

        if len(need_fns) == 0:
            continue

        print(f"區間 {chunk_start_date} 到 {chunk_end_date} 需要補資料站點數 {len(need_fns)}")

        for j in range(0, len(need_fns), API_BATCH_SIZE):
            sub_fns = need_fns[j:j + API_BATCH_SIZE]
            lat_list = [state[fn]["lat"] for fn in sub_fns]
            lon_list = [state[fn]["lon"] for fn in sub_fns]

            dfs = fetch_openmeteo_archive_batch(lat_list, lon_list, chunk_start_date, chunk_end_date)

            for fn, df_new_raw in zip(sub_fns, dfs):
                path = state[fn]["path"]

                # 讀既有完整資料，清理成標準格式
                if os.path.exists(path):
                    try:
                        df_old_raw = pd.read_csv(path)
                    except Exception:
                        df_old_raw = pd.DataFrame(columns=FINAL_COLUMNS)
                else:
                    df_old_raw = pd.DataFrame(columns=FINAL_COLUMNS)

                df_old = sanitize_df(df_old_raw, priority=0)
                df_new = sanitize_df(df_new_raw, priority=1)

                df_final = dedupe_and_finalize(pd.concat([df_old, df_new], ignore_index=True))
                df_final["time"] = pd.to_datetime(df_final["time"], errors="coerce")
                df_final = df_final[(df_final["time"] >= TARGET_START_DT) & (df_final["time"] <= TARGET_END_DT)].sort_values("time")
                df_final.to_csv(path, index=False)

                # 更新 has
                state[fn]["has"] = build_full_has_array(df_final["time"])
                state[fn]["touched"] = True
                # 被觸碰過就等於已清理
                state[fn]["need_clean"] = False

            time.sleep(SLEEP_SECONDS)

    # 最後補丁，處理零星缺口
    if FINAL_PATCH_MISSING:
        for fn, st in state.items():
            has = st["has"]
            missing_idx = np.where(~has)[0]
            if len(missing_idx) == 0:
                continue

            ranges = []
            start_i = int(missing_idx[0])
            prev_i = int(missing_idx[0])
            for k in missing_idx[1:]:
                k = int(k)
                if k == prev_i + 1:
                    prev_i = k
                else:
                    ranges.append((start_i, prev_i))
                    start_i = k
                    prev_i = k
            ranges.append((start_i, prev_i))
            ranges = ranges[:FINAL_PATCH_MAX_RANGES]

            print(f"{fn} 仍缺 {len(missing_idx)} 小時，缺口區間數 {len(ranges)}，進行補丁")

            for (a, b) in ranges:
                dt_a = TARGET_START_DT + pd.Timedelta(hours=int(a))
                dt_b = TARGET_START_DT + pd.Timedelta(hours=int(b))
                patch_start = dt_a.date().isoformat()
                patch_end = dt_b.date().isoformat()

                df_patch_raw = fetch_openmeteo_archive_batch([st["lat"]], [st["lon"]], patch_start, patch_end)[0]

                path = st["path"]
                if os.path.exists(path):
                    try:
                        df_old_raw = pd.read_csv(path)
                    except Exception:
                        df_old_raw = pd.DataFrame(columns=FINAL_COLUMNS)
                else:
                    df_old_raw = pd.DataFrame(columns=FINAL_COLUMNS)

                df_old = sanitize_df(df_old_raw, priority=0)
                df_new = sanitize_df(df_patch_raw, priority=1)
                df_final = dedupe_and_finalize(pd.concat([df_old, df_new], ignore_index=True))

                df_final["time"] = pd.to_datetime(df_final["time"], errors="coerce")
                df_final = df_final[(df_final["time"] >= TARGET_START_DT) & (df_final["time"] <= TARGET_END_DT)].sort_values("time")
                df_final.to_csv(path, index=False)

                st["has"] = build_full_has_array(df_final["time"])
                st["touched"] = True
                st["need_clean"] = False

                time.sleep(SLEEP_SECONDS)

    # 若某站點完全不缺資料但 need_clean=True，做一次純清理
    for fn, st in state.items():
        if st["need_clean"]:
            print(f"純清理: {fn}")
            clean_file_in_place(st["path"])
            st["need_clean"] = False

        # 最終檢查
        if VERIFY_AT_END:
            if os.path.exists(st["path"]):
                t = load_existing_times(st["path"])
                has_final = build_full_has_array(t)
                miss = int((~has_final).sum())
                try:
                    rows = sum(1 for _ in open(st["path"], "rb")) - 1
                except Exception:
                    rows = -1
                if miss == 0 and rows == EXPECTED_HOURS:
                    print(f"OK {fn} rows={rows}")
                else:
                    print(f"警告 {fn} rows={rows} missing_hours={miss}")

def main():
    df_sta = load_station_list()

    FILE_BATCH_SIZE = 25
    for i in range(0, len(df_sta), FILE_BATCH_SIZE):
        batch = df_sta.iloc[i:i + FILE_BATCH_SIZE].copy()
        print("")
        print(f"處理站點批次 {i + 1} 到 {i + len(batch)} 共 {len(df_sta)}")
        process_station_batch(batch)

if __name__ == "__main__":
    main()

# %%

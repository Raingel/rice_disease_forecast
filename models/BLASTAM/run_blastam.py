import math
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

ROOT_DIR = os.getenv("PIPELINE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
OUTPUT_DATA_DIR = os.getenv("DATA_FOLDER", os.path.join(ROOT_DIR, "rice_blast_prediction", "data"))
os.makedirs(OUTPUT_DATA_DIR, exist_ok=True)

STA_LIST = "https://raw.githubusercontent.com/Raingel/weather_station_list/refs/heads/main/data/weather_sta_list.csv"
INCUBATION_PERIOD = int(os.getenv("BLASTAM_INCUBATION_DAYS", "7"))
WINDOW_HOURS = 24 * 5
WINDOW_STEP = 24


def _openmeteo_get_json(url, retries=3, timeout=120):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("error"):
                reason = payload.get("reason", "unknown error")
                raise RuntimeError(f"Open-Meteo error: {reason}")
            return payload
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(2 * attempt)
            else:
                raise RuntimeError(f"Failed to fetch Open-Meteo after {retries} attempts: {e}") from e


def _openmeteo_payload_to_hourly_frames(payload):
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected Open-Meteo payload type: {type(payload)}")

    out = []
    for item in payload:
        if not isinstance(item, dict) or "hourly" not in item:
            raise RuntimeError(f"Unexpected Open-Meteo payload item: {str(item)[:200]}")

        hourly = pd.DataFrame(item["hourly"])
        if hourly.empty:
            out.append(hourly)
            continue

        hourly["time"] = pd.to_datetime(hourly["time"])
        if "windspeed_10m" in hourly.columns and "winddirection_10m" in hourly.columns:
            hourly["u"] = hourly["windspeed_10m"] * hourly["winddirection_10m"].apply(
                lambda x: math.cos(math.radians(270 - x))
            )
            hourly["v"] = hourly["windspeed_10m"] * hourly["winddirection_10m"].apply(
                lambda x: math.sin(math.radians(270 - x))
            )

        out.append(hourly.dropna(subset=["time", "temperature_2m", "precipitation", "windspeed_10m"]))

    return out


def fetch_openmeteo_archive_batch(lat_list, lon_list, start, end):
    lat_str = ",".join(map(str, lat_list))
    lon_str = ",".join(map(str, lon_list))
    url = (
        "https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat_str}&longitude={lon_str}&start_date={start}&end_date={end}"
        "&hourly=temperature_2m,precipitation,windspeed_10m,winddirection_10m,sunshine_duration,direct_radiation"
        "&timezone=Asia%2FSingapore"
    )
    payload = _openmeteo_get_json(url)
    return _openmeteo_payload_to_hourly_frames(payload)


def fetch_openmeteo_forecast_batch(lat_list, lon_list, past_days=7, forecast_days=16):
    lat_str = ",".join(map(str, lat_list))
    lon_str = ",".join(map(str, lon_list))
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat_str}&longitude={lon_str}"
        "&hourly=temperature_2m,precipitation,windspeed_10m,winddirection_10m,sunshine_duration,direct_radiation"
        f"&past_days={past_days}&forecast_days={forecast_days}&models=ecmwf_aifs025_single"
        "&timezone=Asia%2FSingapore"
    )
    payload = _openmeteo_get_json(url)
    return _openmeteo_payload_to_hourly_frames(payload)


def extract_window(df, window_size, step):
    i = 0
    while i + window_size <= len(df):
        yield df.iloc[i : i + window_size]
        i += step


def compute_hourly_sunshine_fraction(df_window: pd.DataFrame) -> np.ndarray:
    """
    BLASTAM 需要每小時日照時數（0~1）。
    這裡優先用 open-meteo `sunshine_duration`（秒）換算為小時比率；
    若缺值才回退到舊版 direct_radiation/120 近似法。
    """
    if "sunshine_duration" in df_window.columns:
        sunshine_fraction = df_window["sunshine_duration"].fillna(0).to_numpy(dtype=float) / 3600.0
        return np.clip(sunshine_fraction, 0, 1)

    # fallback: 舊作法（僅為相容）
    if "direct_radiation" in df_window.columns:
        return np.clip(df_window["direct_radiation"].fillna(0).to_numpy(dtype=float) / 120.0, 0, 1)

    return np.zeros(len(df_window), dtype=float)


def koshimizu_model(
    temp_5d,
    wind_5d,
    rainfall_5d,
    sun_shine_5d,
    accumulate_sunshine_threshold=0.2,
    invalid_hourly_rainfall=4,
    invalid_hourly_sunshine=0.1,
    invalid_hourly_wind=3,
    wet_period_hrs_compensation=0,
):
    assert len(temp_5d) == len(wind_5d) == len(rainfall_5d) == len(sun_shine_5d) == 24 * 5

    rainfall_1600_0700 = rainfall_5d[88:104]
    sun_shine_1600_0700 = sun_shine_5d[88:104]
    wind_1600_0700 = wind_5d[88:104]

    hour = 16
    leaf_wet = False
    leaf_wet_dict = {}
    accumulate_sunshine = 0
    key = 0

    for rainfall, sunshine, wind in zip(rainfall_1600_0700, sun_shine_1600_0700, wind_1600_0700):
        if key < 15 and rainfall_1600_0700[key + 1] > 0:
            leaf_wet = True

        if rainfall_1600_0700[key] > 0 and sun_shine_1600_0700[key] == 0.1:
            sun_shine_1600_0700[key] = 0

        accumulate_sunshine += sun_shine_1600_0700[key]

        if hour == 0:
            accumulate_sunshine = 0

        if accumulate_sunshine > accumulate_sunshine_threshold:
            leaf_wet = False

        if wind >= 4:
            leaf_wet = False

        if key > 1 and key < 15:
            if (
                wind_1600_0700[key - 1] >= 3
                and wind_1600_0700[key] >= 3
                and wind_1600_0700[key + 1] >= 3
                and (hour >= 16 or hour <= 4)
            ):
                leaf_wet = False

            if wind_1600_0700[key + 1] >= 4 and (hour >= 16 or hour <= 4):
                leaf_wet = False

        if (4 <= hour <= 7) and ((rainfall == 0 and wind >= 3) or (rainfall > 0 and wind >= 4)):
            leaf_wet = False

        leaf_wet_dict[hour] = leaf_wet
        hour = (hour + 1) % 24
        key += 1

    rainfall_0600_1600 = rainfall_5d[102:113]
    sun_shine_0600_1600 = sun_shine_5d[102:113]
    wind_0600_1600 = wind_5d[102:113]
    hour = 6
    key = 102

    for h in range(8, 16):
        leaf_wet_dict[h] = False

    for rainfall, sunshine, wind in zip(rainfall_0600_1600, sun_shine_0600_1600, wind_0600_1600):
        if 7 < hour < 16 and rainfall > 0:
            for offset in [-3, -2, -1, 0, 1, 2, 3]:
                check_hour = hour + offset
                if check_hour <= 7 or check_hour >= 16:
                    continue
                idx = key + offset
                if wind_5d[idx] < invalid_hourly_wind and sun_shine_5d[idx] <= invalid_hourly_sunshine:
                    leaf_wet_dict[check_hour] = True

        hour = (hour + 1) % 24
        key += 1

    hour = 6
    for sunshine, wind in zip(sun_shine_0600_1600, wind_0600_1600):
        if 7 < hour < 16 and leaf_wet_dict[hour] is False:
            if (
                leaf_wet_dict.get(hour - 1, False)
                and leaf_wet_dict.get(hour + 1, False)
                and sunshine <= invalid_hourly_sunshine
                and wind <= invalid_hourly_wind
            ):
                leaf_wet_dict[hour] = True

        hour = (hour + 1) % 24

    rainfall_1600_1500 = rainfall_5d[88:112]
    for hour in range(16, 40):
        if rainfall_1600_1500[hour - 16] > invalid_hourly_rainfall:
            for ineffective_hour in range(hour - 9, hour + 10):
                if 16 <= ineffective_hour <= 40:
                    hour_now = ineffective_hour % 24
                    leaf_wet_dict[hour_now] = -2

    start = None
    end = None
    wet_period_hrs = 0
    temp_avg = 0
    temp_1600_1500 = temp_5d[88:112]
    for hour in range(16, 40):
        hour_now = hour % 24
        if leaf_wet_dict[hour_now] is True:
            if start is None:
                start = hour_now
            end = hour_now
            wet_period_hrs += 1
            temp_avg += temp_1600_1500[hour - 16]
        elif start is not None:
            break

    if wet_period_hrs != 0:
        temp_avg = temp_avg / wet_period_hrs

    temp_towetness_hour_lower_limit = {15: 17, 16: 15, 17: 14, 18: 13, 19: 12, 20: 11, 21: 10, 22: 10, 23: 10, 24: 10, 25: 10}
    temp_5d_mean = temp_5d.mean()
    wet_period_hrs += wet_period_hrs_compensation

    blast_score = 0
    if wet_period_hrs < 10:
        blast_score = 0
    elif 15 <= temp_avg <= 25:
        if temp_5d_mean < 20:
            blast_score = 1
        elif temp_5d_mean > 25:
            blast_score = 2

        temp_bucket = int(round(temp_avg))
        temp_bucket = min(max(temp_bucket, 15), 25)
        if wet_period_hrs > temp_towetness_hour_lower_limit[temp_bucket]:
            blast_score = 10
        else:
            blast_score = max(blast_score, 4)
    else:
        blast_score = 3

    return leaf_wet_dict, {
        "start": start,
        "end": end,
        "wet_period_hrs": wet_period_hrs,
        "wet_avg_temp": temp_avg,
        "blast_score": blast_score / 10,
    }


def main():
    df_sta = pd.read_csv(STA_LIST)
    df_sta = df_sta[df_sta["撤站日期"].isna()][["站號", "站名", "緯度", "經度"]].drop_duplicates()

    past_days_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    past_days_end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    all_records = []
    batch_size = 80

    for i in range(0, len(df_sta), batch_size):
        group = df_sta.iloc[i : i + batch_size]
        lat_list = group["緯度"].tolist()
        lon_list = group["經度"].tolist()

        archive_results = fetch_openmeteo_archive_batch(lat_list, lon_list, start=past_days_start, end=past_days_end)
        forecast_results = fetch_openmeteo_forecast_batch(lat_list, lon_list, past_days=7, forecast_days=16)

        for idx, (_, row) in enumerate(group.iterrows()):
            df_archive = archive_results[idx]
            df_forecast = forecast_results[idx]
            df_combined = pd.concat([df_archive, df_forecast]).drop_duplicates(subset=["time"]).sort_values("time")

            for window in extract_window(df_combined, WINDOW_HOURS, WINDOW_STEP):
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

        time.sleep(60)

    if not all_records:
        print("No BLASTAM records generated.")
        return

    total_df = pd.DataFrame(all_records)
    total_df["日期"] = pd.to_datetime(total_df["日期"]) + timedelta(days=INCUBATION_PERIOD)

    for date, group in total_df.groupby("日期"):
        out_path = os.path.join(OUTPUT_DATA_DIR, f"{date.strftime('%Y%m%d')}_BLASTAM.csv")
        group.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"Saved {out_path} ({len(group)} rows)")


if __name__ == "__main__":
    main()

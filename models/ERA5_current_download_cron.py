# %%
import pandas as pd
from datetime import datetime, timedelta
import math
import os
import time

ROOT_DIR = os.getenv("PIPELINE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
ERA5_OUTPUT_DIR = os.getenv("ERA5_OUTPUT_DIR", os.path.join(ROOT_DIR, "ERA5"))
os.makedirs(ERA5_OUTPUT_DIR, exist_ok=True)
# %%
def fetch_openmeteo_archive_batch(lat_list, lon_list, start="2014-02-12", end="2014-04-08"):
    """
    批次下載歷史資料，lat_list 與 lon_list 為數值列表。
    API 回傳為多站資料，每一筆資料會包含一個 'hourly' 鍵，將其轉成 DataFrame，
    並計算風的 u, v 分量。
    """
    lat_str = ",".join(map(str, lat_list))
    lon_str = ",".join(map(str, lon_list))
    url = (
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat_str}&longitude={lon_str}&start_date={start}&end_date={end}"
        "&hourly=temperature_2m,relativehumidity_2m,precipitation,windspeed_10m,winddirection_10m"
        "&timezone=Asia%2FSingapore"
    )
    df = pd.read_json(url)
    results = []
    for _, row in df.iterrows():
        hourly = row['hourly']
        df_hourly = pd.DataFrame(hourly)
        df_hourly['time'] = pd.to_datetime(df_hourly['time'])
        # 若有風速與風向資料，則計算 u, v 分量
        if 'windspeed_10m' in df_hourly.columns and 'winddirection_10m' in df_hourly.columns:
            df_hourly['u'] = df_hourly['windspeed_10m'] * df_hourly['winddirection_10m'].apply(
                lambda x: math.cos(math.radians(270 - x))
            )
            df_hourly['v'] = df_hourly['windspeed_10m'] * df_hourly['winddirection_10m'].apply(
                lambda x: math.sin(math.radians(270 - x))
            )
        df_hourly = df_hourly.dropna()
        results.append(df_hourly)
    return results

def fetch_openmeteo_forecast_batch(lat_list, lon_list, past_days=7, forecast_days=16):
    """
    批次下載預報資料（包含過去天數資料），並計算風的 u, v 分量。
    """
    lat_str = ",".join(map(str, lat_list))
    lon_str = ",".join(map(str, lon_list))
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat_str}&longitude={lon_str}"
        "&hourly=temperature_2m,relativehumidity_2m,precipitation,windspeed_10m,winddirection_10m"
        f"&past_days={past_days}&forecast_days={forecast_days}&models=ecmwf_aifs025_single"
        "&timezone=Asia%2FSingapore"
    )
    df = pd.read_json(url)
    results = []
    for _, row in df.iterrows():
        hourly = row['hourly']
        df_hourly = pd.DataFrame(hourly)
        df_hourly['time'] = pd.to_datetime(df_hourly['time'])
        if 'windspeed_10m' in df_hourly.columns and 'winddirection_10m' in df_hourly.columns:
            df_hourly['u'] = df_hourly['windspeed_10m'] * df_hourly['winddirection_10m'].apply(
                lambda x: math.cos(math.radians(270 - x))
            )
            df_hourly['v'] = df_hourly['windspeed_10m'] * df_hourly['winddirection_10m'].apply(
                lambda x: math.sin(math.radians(270 - x))
            )
        df_hourly = df_hourly.dropna()
        results.append(df_hourly)
    return results

# %%
# 取得要下載的氣象站列表
STA_LIST = "https://raw.githubusercontent.com/Raingel/weather_station_list/refs/heads/main/data/weather_sta_list.csv"
df_sta = pd.read_csv(STA_LIST)
# 僅保留撤站日期為 nan 的資料
df_sta = df_sta[df_sta['撤站日期'].isna()]
# 只保留站號、站名、緯度、經度
df_sta = df_sta[['站號', '站名', '緯度', '經度']]
# 移除重複的資料
df_sta = df_sta.drop_duplicates()
print(f"共有 {len(df_sta)} 個有效氣象站")

# %%
# 定義歷史資料的日期範圍 (避免包含今天，因為 archive API 會出錯)
past_days_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
past_days_end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

# 設定每批下載 80 個站點
batch_size = 80

for i in range(0, len(df_sta), batch_size):
    group = df_sta.iloc[i : i + batch_size]
    lat_list = group['緯度'].tolist()
    lon_list = group['經度'].tolist()
    
    # 批次下載歷史與預報資料
    archive_results = fetch_openmeteo_archive_batch(lat_list, lon_list, start=past_days_start, end=past_days_end)
    forecast_results = fetch_openmeteo_forecast_batch(lat_list, lon_list, past_days=7, forecast_days=16)
    
    # 依序將每個站點的資料合併並儲存成 CSV
    for idx, (index, row) in enumerate(group.iterrows()):
        df_archive = archive_results[idx]
        df_forecast = forecast_results[idx]
        df_combined = pd.concat([df_archive, df_forecast])
        df_combined = df_combined.drop_duplicates(subset=['time']) #預設是keep='first'
        
        # 檔名格式: 站號_站名_緯度_經度.csv
        filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
        output_path = os.path.join(ERA5_OUTPUT_DIR, filename)
        df_combined.to_csv(output_path, index=False)
        print(f"已下載 {filename}")
    #休息60秒
    time.sleep(60)

# %%

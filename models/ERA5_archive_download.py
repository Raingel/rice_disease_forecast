# %%
import pandas as pd
from datetime import datetime, timedelta
import math
import os
import time

def fetch_openmeteo_archive_batch(lat_list, lon_list, start="2013-01-01", end="2018-03-25"):
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
# 設定每批下載 80 個站點
batch_size = 80
# 設定歷史資料的日期範圍
start_date = "2013-01-01"
end_date = "2018-03-25"

# 設定存放下載資料的資料夾
output_folder = os.path.join("..", "ERA5_2013_20180325")
os.makedirs(output_folder, exist_ok=True)

# 分批處理站點
for i in range(0, len(df_sta), batch_size):
    group = df_sta.iloc[i : i + batch_size]
    
    # 過濾出尚未下載的站點
    missing_rows = []
    for index, row in group.iterrows():
        filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
        output_path = os.path.join(output_folder, filename)
        if os.path.exists(output_path):
            print(f"{filename} 已存在，跳過下載")
        else:
            missing_rows.append(row)
    
    # 如果這批中沒有未下載的站點，則直接進入下一批
    if not missing_rows:
        continue
        
    # 將 missing_rows 轉成 DataFrame
    df_missing = pd.DataFrame(missing_rows)
    
    # 建立缺少資料的站點的緯度與經度列表
    lat_list = df_missing['緯度'].tolist()
    lon_list = df_missing['經度'].tolist()
    
    # 批次下載尚未下載的歷史資料
    archive_results = fetch_openmeteo_archive_batch(lat_list, lon_list, start=start_date, end=end_date)
    
    # 依序將每個未下載站點的資料儲存成 CSV
    for idx, (_, row) in enumerate(df_missing.iterrows()):
        filename = f"{row['站號']}_{row['站名']}_{row['緯度']}_{row['經度']}.csv"
        output_path = os.path.join(output_folder, filename)
        df_archive = archive_results[idx]
        df_archive.to_csv(output_path, index=False)
        print(f"已下載 {filename}")
        
    # 每批次下載後休息 360 秒
    time.sleep(360)

# %%

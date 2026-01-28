# %%
import os
import glob
import pandas as pd
import numpy as np
import tensorflow as tf
from datetime import datetime

# ---------------------------
# 1. 讀取正規化參數
# ---------------------------
stats_df = pd.read_csv("./data_stats.csv", index_col=0)

# 定義訓練時使用的 15 個特徵名稱
feature_list = [
    "temperature_2m (°C)_min",
    "temperature_2m (°C)_mean",
    "temperature_2m (°C)_max",
    "relative_humidity_2m (%)_min",
    "relative_humidity_2m (%)_mean",
    "relative_humidity_2m (%)_max",
    "precipitation (mm)_min",
    "precipitation (mm)_mean",
    "precipitation (mm)_max",
    "wu_min",
    "wu_mean",
    "wu_max",
    "wv_min",
    "wv_mean",
    "wv_max"
]

# 建立正規化參數字典：feature -> (min, max)
norm_stats = {}
for feat in feature_list:
    norm_stats[feat] = (stats_df.loc[feat, 'min'], stats_df.loc[feat, 'max'])
# %%
# ---------------------------
# 2. 載入訓練好的模型
# ---------------------------
# 假設模型檔案為 "./model_21_27.h5"
model = tf.keras.models.load_model("./model_21_27.h5", compile=False)
window_size = 7  # 設定視窗長度
num_features = len(feature_list)
# %%
# ---------------------------
# 3. 讀取 ERA5 資料並彙整所有滑動視窗
# ---------------------------
input_windows = []    # 儲存所有滑動視窗資料，形狀為 (window_size, num_features)
metadata_list = []    # 儲存每筆視窗對應的 meta 資訊

era5_folder = "../../ERA5/"
# 每個檔案皆為 CSV，檔名格式：站號_站名_緯度_經度.csv（或不含副檔名）
for filepath in glob.glob(os.path.join(era5_folder, "*.csv")):
    base = os.path.basename(filepath)
    name_part = os.path.splitext(base)[0]
    parts = name_part.split("_")
    if len(parts) < 4:
        print(f"檔名格式不符，跳過：{base}")
        continue
    station_id = parts[0]
    station_name = parts[1]
    lat = parts[2]
    lon = parts[3]
    
    # 讀取 CSV，並將 time 欄位轉為 datetime
    df = pd.read_csv(filepath)
    df['time'] = pd.to_datetime(df['time'])
    df['date'] = df['time'].dt.date
    
    # 每日彙整：計算各欄位的 min, mean, max
    agg_funcs = {
        'temperature_2m': ['min','mean','max'],
        'relativehumidity_2m': ['min','mean','max'],
        'precipitation': ['min','mean','max'],
        'u': ['min','mean','max'],
        'v': ['min','mean','max']
    }
    grouped = df.groupby('date').agg(agg_funcs)
    # 將多層索引欄位攤平成單層
    grouped.columns = ['_'.join(col).strip() for col in grouped.columns.values]
    grouped = grouped.reset_index()
    
    # 重新命名欄位，使其與訓練時使用的特徵名稱一致
    rename_dict = {
        'temperature_2m_min': "temperature_2m (°C)_min",
        'temperature_2m_mean': "temperature_2m (°C)_mean",
        'temperature_2m_max': "temperature_2m (°C)_max",
        'relativehumidity_2m_min': "relative_humidity_2m (%)_min",
        'relativehumidity_2m_mean': "relative_humidity_2m (%)_mean",
        'relativehumidity_2m_max': "relative_humidity_2m (%)_max",
        'precipitation_min': "precipitation (mm)_min",
        'precipitation_mean': "precipitation (mm)_mean",
        'precipitation_max': "precipitation (mm)_max",
        'u_min': "wu_min",
        'u_mean': "wu_mean",
        'u_max': "wu_max",
        'v_min': "wv_min",
        'v_mean': "wv_mean",
        'v_max': "wv_max"
    }
    grouped.rename(columns=rename_dict, inplace=True)
    # 僅保留日期與訓練時所需的特徵欄位
    grouped = grouped[['date'] + feature_list]
    grouped = grouped.sort_values('date').reset_index(drop=True)
    
    # 針對每個特徵依據正規化參數進行正規化
    for col in feature_list:
        min_val, max_val = norm_stats[col]
        grouped[col] = (grouped[col] - min_val) / (max_val - min_val)
    
    # 轉換成 numpy 陣列，形狀 (天數, 特徵數)
    data_array = grouped[feature_list].values
    dates = grouped['date'].tolist()
    n_days = data_array.shape[0]
    
    if n_days < window_size + 1:
        print(f"{station_id} {station_name} 資料不足，僅有 {n_days} 天，跳過預報。")
        continue
    
    # 以滑動視窗方式組合資料，使用連續 window_size 天作為輸入，預報接下來一天
    for i in range(n_days - window_size):
        window_data = data_array[i : i + window_size]  # shape: (window_size, num_features)
        forecast_date = dates[i + window_size]            # 預報目標日期
        # We use 21-27 in 0-31 data, so   we need to shift the forecast date by 4 days
        forecast_date = forecast_date + pd.DateOffset(days=4)
        # 將視窗資料與對應 meta 儲存起來
        input_windows.append(window_data)
        # 格式化日期字串 (YYYY-MM-DD)
        if isinstance(forecast_date, datetime):
            forecast_date_str = forecast_date.strftime("%Y-%m-%d")
        else:
            forecast_date_str = str(forecast_date)
        metadata_list.append({
            "站號": station_id,
            "站名": station_name,
            "日期": forecast_date_str,
            "lat": lat,
            "lon": lon
        })

# 若無任何可用資料則結束
if len(input_windows) == 0:
    print("無足夠資料進行預報。")
    exit()

# 將所有視窗資料合併成 numpy array，形狀 (總視窗數, window_size, num_features)
all_inputs = np.array(input_windows, dtype=np.float32)
# %%
# ---------------------------
# 4. 一次批次送進模型預測
# ---------------------------
predictions = model.predict(all_inputs)
predictions = predictions.flatten()  # shape: (總視窗數,)
# %%
# ---------------------------
# 5. 整理預報結果並依預報日期分群輸出
# ---------------------------
forecasts = {}
for meta, pred in zip(metadata_list, predictions):
    fdate = meta["日期"]
    if fdate not in forecasts:
        forecasts[fdate] = []
    meta["BLBTSLS"] = pred
    forecasts[fdate].append(meta)
# %%
# 輸出結果至 ../BLBTSLS/，檔名格式：YYYYMMDD_BLBTSLS.csv
output_folder = "../../rice_blast_prediction/data/"
os.makedirs(output_folder, exist_ok=True)

for fdate, rows in forecasts.items():
    out_df = pd.DataFrame(rows)
    filename = f"{fdate.replace('-','')}_BLBTSLS.csv"
    out_path = os.path.join(output_folder, filename)
    out_df.to_csv(out_path, index=False)
    print(f"已儲存 {fdate} 預報結果至 {out_path}")

# %%

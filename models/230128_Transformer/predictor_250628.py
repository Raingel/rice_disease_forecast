#!/usr/bin/env python3
# predictor_250628.py

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from tensorflow.keras.models import load_model

# === 統一設定 ===
ROOT = "./"
ERA5_ARCHIVE = os.getenv("ERA5_INPUT_DIR", os.path.join(ROOT, "../../ERA5"))  # 氣象資料來源資料夾
MODEL_PATH   = os.path.join(ROOT, "230207_Transformer_colab.h5")     # 你的 BlastTF 模型
OUTPUT_DIR   = os.path.join(ROOT, "../../rice_blast_prediction/data")  # 統一輸出資料夾
BACKFILL_START_DATE = pd.to_datetime(os.getenv("BACKFILL_START_DATE", "1900-01-01")).normalize()
BACKFILL_END_DATE = pd.to_datetime(os.getenv("BACKFILL_END_DATE", "2100-12-31")).normalize()

# 確保輸出資料夾存在
os.makedirs(OUTPUT_DIR, exist_ok=True)

# === 模型與參數 ===
WINDOW_SIZE = 20
# BlastTF 模型 normalization 常數 (溫度, 降水)
TF_MEAN = np.array([10.9, 0.0])
TF_STD  = np.array([24.3, 6.275])

# 載入模型
model = load_model(MODEL_PATH, compile=False)

# === 建立資料容器 ===
X_list = []
meta_list = []

# === 逐站資料處理 ===
for f in os.scandir(ERA5_ARCHIVE):
    if not f.name.endswith(".csv"):
        continue

    # 檔名格式： sta_id_sta_name_lat_lon.csv
    try:
        sta_id, sta_name, lat, lon = f.name.replace(".csv", "").split("_")
    except ValueError:
        # 如果檔名不合規則，就跳過
        continue

    df = pd.read_csv(f.path, parse_dates=["time"])
    df.set_index("time", inplace=True)

    # 計算每日統計
    daily_max  = df["temperature_2m"].resample("D").max().rename("temperature_2m_max")
    daily_mean = df["precipitation"].resample("D").mean().rename("precipitation_mean")
    df_daily = pd.concat([daily_max, daily_mean], axis=1).dropna()

    # sliding window
    n_days = len(df_daily)
    for start in range(0, n_days - WINDOW_SIZE + 1):
        window_df = df_daily.iloc[start:start + WINDOW_SIZE]

        # 用 window 本身最後一天再 +3 天作為預測日
        predict_date = (window_df.index[-1] + timedelta(days=3)).normalize()
        if predict_date < BACKFILL_START_DATE or predict_date > BACKFILL_END_DATE:
            continue

        X_list.append(window_df.values)
        meta_list.append({
            "sta_id":   sta_id,
            "sta_name": sta_name,
            "lat":      lat,
            "lon":      lon,
            "date":     predict_date
        })

# === 數值轉陣列並正規化 ===
if len(X_list) == 0:
    print("No BlastTF samples in selected BACKFILL window.")
    raise SystemExit(0)

X = np.stack(X_list, axis=0)       # shape = (n_samples, WINDOW_SIZE, 2)
X = (X - TF_MEAN) / TF_STD         # 針對 feature 0,1 分別做 normalization

# === 預測 ===
preds = model.predict(X)[:, 0]
preds = np.round(preds, 4)

# === 組成 DataFrame 並輸出 ===
df_out = pd.DataFrame({
    "站號":   [m["sta_id"]   for m in meta_list],
    "站名":   [m["sta_name"] for m in meta_list],
    "lat":    [m["lat"]      for m in meta_list],
    "lon":    [m["lon"]      for m in meta_list],
    "日期":   [m["date"]     for m in meta_list],
    "BlastTF": preds
})

# 按「日期」分檔寫入，並與既有同日期檔案合併，保留最後一筆同站號
for date, grp in df_out.groupby("日期"):
    fname = date.strftime("%Y%m%d") + "_BlastTF.csv"
    out_path = os.path.join(OUTPUT_DIR, fname)

    if os.path.exists(out_path):
        existing = pd.read_csv(out_path)
        combined = pd.concat([existing, grp], ignore_index=True)
        combined = combined.drop_duplicates(subset=["站號"], keep="last")
    else:
        combined = grp.copy()

    # 統一「日期」欄位格式
    combined["日期"] = pd.to_datetime(combined["日期"]).dt.strftime("%Y-%m-%d")
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Saved {len(combined)} records to {out_path}")


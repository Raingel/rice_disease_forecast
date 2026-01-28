# %%
import pandas as pd
import os
from datetime import datetime, timedelta

# 定義資料夾路徑
DATA_FOLDER = "/home/raingel/rice_blast_model_update/rice_blast_prediction/data"
# 指定一個輸出資料夾，用來存放各個站號的 CSV 檔案與氣象站列表
OUTPUT_FOLDER = "/home/raingel/rice_blast_model_update/rice_blast_prediction/recent_daily_by_station"

# 建立輸出資料夾（若不存在的話）
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# %%
# 用來儲存所有日期的資料
all_data = []

# 取得今日日期並設定範圍（這裡以從30天前到30天後，共60天）
today = datetime.today() - timedelta(days=30)
date_range = [today + timedelta(days=i) for i in range(60)]

# 逐天處理各個預報檔案
for target_date in date_range:
    date_str = target_date.strftime("%Y%m%d")
    
    # 各模型預測檔案路徑
    gru_file_path = os.path.join(DATA_FOLDER, f"{date_str}_BlastGRU-TW.csv")
    dt2_file_path = os.path.join(DATA_FOLDER, f"{date_str}_BlastDT2.csv")
    lstls_file_path = os.path.join(DATA_FOLDER, f"{date_str}_BlastLSTLS.csv")
    BLBTSLS_file_path = os.path.join(DATA_FOLDER, f"{date_str}_BLBTSLS.csv")
    planthopper_file_path = os.path.join("/home/raingel/planthopper/HYSPLIT-Planthopper-Forecast/prediction", f"{date_str}_max_freq.csv")
    
    merged_data = None

    # 讀取 GRU 預報檔案，作為初始資料
    if os.path.exists(gru_file_path):
        gru_data = pd.read_csv(gru_file_path)
        merged_data = gru_data.rename(columns={"BlastGRU-TW": "BlastGRU-TW"})

    # 讀取 DT2 預報檔案，並根據「站號」與「日期」進行合併
    if os.path.exists(dt2_file_path):
        dt2_data = pd.read_csv(dt2_file_path)
        if merged_data is None:
            merged_data = dt2_data.rename(columns={"BlastDT2": "BlastDT2"})
        else:
            merged_data = pd.merge(merged_data,
                                   dt2_data[["站號", "日期", "BlastDT2"]],
                                   on=["站號", "日期"],
                                   how="left")

    # 讀取 LSTLS 預報檔案，先統一日期格式再合併
    if os.path.exists(lstls_file_path):
        lstls_data = pd.read_csv(lstls_file_path)
        lstls_data["日期"] = pd.to_datetime(lstls_data["日期"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
        if merged_data is None:
            merged_data = lstls_data.rename(columns={"BlastLSTLS": "BlastLSTLS"})
        else:
            merged_data = pd.merge(merged_data,
                                   lstls_data[["站號", "日期", "BlastLSTLS"]],
                                   on=["站號", "日期"],
                                   how="left")
    # 讀取 BLBTSLS 預報檔案，先統一日期格式再合併
    if os.path.exists(BLBTSLS_file_path):
        BLBTSLS_data = pd.read_csv(BLBTSLS_file_path)
        BLBTSLS_data["日期"] = pd.to_datetime(BLBTSLS_data["日期"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
        if merged_data is None:
            merged_data = BLBTSLS_data.rename(columns={"BLBTSLS": "BLBTSLS"})
        else:
            merged_data = pd.merge(merged_data,
                                   BLBTSLS_data[["站號", "日期", "BLBTSLS"]],
                                   on=["站號", "日期"],
                                   how="left")

    # 讀取 Planthopper 預報檔案，並根據最近的經緯度進行合併
    if os.path.exists(planthopper_file_path):
        planthopper_data = pd.read_csv(planthopper_file_path)
        if merged_data is not None:
            def find_nearest_probability(lat, lon, planthopper_data):
                planthopper_data['distance'] = ((planthopper_data['y'] - lat)**2 + (planthopper_data['x'] - lon)**2)**0.5
                nearest_row = planthopper_data.loc[planthopper_data['distance'].idxmin()]
                return nearest_row['value']
            
            merged_data['planthopper'] = merged_data.apply(
                lambda row: find_nearest_probability(row['lat'], row['lon'], planthopper_data), axis=1
            )
    
    # 如果當天有資料，依據「站號」與「日期」去除重複（不同檔案中站名可能不一致，只保留第一筆）
    if merged_data is not None:
         merged_data = merged_data.drop_duplicates(subset=["站號", "日期"], keep='first')
         all_data.append(merged_data)

# %%
# 若有任何資料，合併所有日期的資料
if all_data:
    final_df = pd.concat(all_data, ignore_index=True)
    
    # 根據「站號」分組，分別存成不同的 CSV 檔案
    for station, group in final_df.groupby("站號"):
        output_file = os.path.join(OUTPUT_FOLDER, f"{station}.csv")
        group.to_csv(output_file, index=False)
        print(f"Data for 站號 {station} has been saved to {output_file}")
    
    # -----------------------------
    # 產生氣象站列表：包含站號、站名、經度、緯度
    # -----------------------------
    # 假設這些欄位名稱在原始資料中就已經存在
    station_columns = ["站號", "站名", "lon", "lat"]
    available_columns = [col for col in station_columns if col in final_df.columns]
    
    if available_columns:
        # 依據站號去除重複（若同一站號在不同預報檔案中的站名或座標不同，以第一筆為主）
        station_list = final_df[available_columns].drop_duplicates(subset=["站號"], keep="first")
        station_list_file = os.path.join(OUTPUT_FOLDER, "station_list.csv")
        station_list.to_csv(station_list_file, index=False)
        print(f"Station list has been saved to {station_list_file}")
    else:
        print("無法在資料中找到站名或經緯度相關欄位，無法產生氣象站列表。")
    
else:
    print("No data files found for the specified date range.")
# %%
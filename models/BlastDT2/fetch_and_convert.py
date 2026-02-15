# %%
import os
import subprocess
import logging
from datetime import datetime
import pandas as pd
from typing import Optional

# ----------------------------
# 設定參數
# ----------------------------
REPO_URL = 'https://github.com/Raingel/BlastDT.git'          # GitHub 倉庫網址
REPO_DIR = './BlastDT'                                      # 本地儲存倉庫路徑
DATA_SUBDIR = 'prediction_BlastDT2'                         # 候選資料資料夾
OUTPUT_DIR = os.path.join('..', '..', 'rice_blast_prediction', 'data')  # 輸出資料夾路徑

# 潛伏期設定（天數），將感染日期加上此天數作為發病日期
INCUBATION_DAYS = 5

# 最低有效預測數門檻，若某日期資料筆數少於此數量則視為異常並捨棄
MIN_RECORDS_PER_DATE = 50

# 預設只更新當前年份，如需更新其他年份，可手動設定 YEARS 變數
YEARS = [datetime.now().year]  # 要更新的年份列表

# ----------------------------
# Logging 設定
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# ----------------------------
# 下載或更新 GitHub 倉庫
# ----------------------------
if not os.path.isdir(REPO_DIR):
    logging.info(f"倉庫不存在，開始 clone：{REPO_URL}")
    subprocess.run(['git', 'clone', REPO_URL, REPO_DIR], check=True)
else:
    logging.info(f"倉庫已存在，開始 pull 更新：{REPO_DIR}")
    subprocess.run(['git', 'pull'], cwd=REPO_DIR, check=True)



def resolve_data_base_path(repo_dir: str, data_subdir: str) -> Optional[str]:
    """Resolve BlastDT2 prediction folder with fallback search."""
    direct_path = os.path.join(repo_dir, data_subdir)
    if os.path.isdir(direct_path):
        return direct_path

    # Fallback: if upstream repo structure changed, search recursively by folder name
    for root, dirs, _files in os.walk(repo_dir):
        if data_subdir in dirs:
            return os.path.join(root, data_subdir)

    return None

# ----------------------------
# 準備輸出資料夾
# ----------------------------
os.makedirs(OUTPUT_DIR, exist_ok=True)
logging.info(f"確保輸出資料夾存在：{OUTPUT_DIR}")

# ----------------------------
# 讀取並處理資料
# ----------------------------
all_records = []  # 用於累積所有站點資料的列表
base_path = resolve_data_base_path(REPO_DIR, DATA_SUBDIR)
if not base_path:
    logging.error(f"找不到資料夾：{DATA_SUBDIR}（repo: {REPO_DIR}）。本次跳過 BlastDT2 更新，保留既有輸出。")
    raise SystemExit(0)

logging.info(f"使用 BlastDT2 資料夾：{base_path}")

# 掃描所有氣象站子資料夾
for station in os.listdir(base_path):
    station_dir = os.path.join(base_path, station)
    if not os.path.isdir(station_dir):
        continue  # 非資料夾跳過

    logging.info(f"處理站點資料夾：{station}")

    # 針對指定年份檔案進行讀取
    for year in YEARS:
        csv_path = os.path.join(station_dir, f"{year}.csv")
        if not os.path.isfile(csv_path):
            logging.warning(f"檔案不存在，跳過：{csv_path}")
            continue

        logging.info(f"讀取檔案：{csv_path}")
        df = pd.read_csv(csv_path)

        # 轉換 BlastDT2 欄位，False->0.0, True->1.0, 空值視為0.0
        df['BlastDT2'] = df['BlastDT2'].apply(lambda x: 1.0 if str(x).lower() == 'true' else 0.0)

        # 篩選並重命名欄位，符合伺服器格式需求
        df = df[['站號', '站名', 'Date', 'lat', 'lon', 'BlastDT2']]
        df = df.rename(columns={'Date': '日期'})

        # 將感染日期轉為發病日期，加入潛伏期
        df['日期'] = pd.to_datetime(df['日期']) + pd.Timedelta(days=INCUBATION_DAYS)
        df['日期'] = df['日期'].dt.strftime('%Y-%m-%d')

        # 累積結果
        all_records.append(df)

# 合併所有站點資料
if all_records:
    combined = pd.concat(all_records, ignore_index=True)
    logging.info(f"總共合併 {len(combined)} 筆資料")

    # 計算各日期資料筆數，過濾異常少於門檻的日期
    counts = combined['日期'].value_counts()
    valid_dates = counts[counts >= MIN_RECORDS_PER_DATE].index.tolist()
    dropped_dates = counts[counts < MIN_RECORDS_PER_DATE].index.tolist()
    for d in dropped_dates:
        logging.warning(f"日期 {d} 資料筆數 {counts[d]} 少於 {MIN_RECORDS_PER_DATE}，已捨棄此日期所有資料")

    # 依有效日期分檔輸出
    for date_str, group in combined.groupby('日期'):
        if date_str not in valid_dates:
            continue  # 跳過不符合門檻的日期

        # 建立輸出檔名：YYYYMMDD_BlastDT2.csv
        out_name = f"{date_str.replace('-', '')}_BlastDT2.csv"
        out_path = os.path.join(OUTPUT_DIR, out_name)

        # 儲存為帶 BOM 的 UTF-8 CSV
        group.to_csv(out_path, index=False, encoding='utf-8-sig')
        logging.info(f"輸出完成：{out_path}，共 {len(group)} 筆資料")
else:
    logging.warning("沒有讀取到任何資料，請確認路徑與年份設定是否正確")

# %%

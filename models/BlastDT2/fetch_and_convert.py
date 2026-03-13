# %%
import os
import subprocess
import logging
from datetime import datetime, timedelta
import pandas as pd
from typing import Optional

# ----------------------------
# Logging 設定
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

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

# 預設更新「去年+今年」，避免跨年時遺漏潛伏期平移後的年初資料
DEFAULT_YEARS = [datetime.now().year - 1, datetime.now().year]
FAIL_ON_MISSING_DATA = os.getenv('BLASTDT2_FAIL_ON_MISSING_DATA', '1').strip() != '0'


def parse_years_from_env() -> list[int]:
    """Parse BLASTDT2_YEARS env, fallback to previous/current year."""
    years_env = os.getenv('BLASTDT2_YEARS', '').strip()
    if years_env:
        years = []
        for token in years_env.split(','):
            token = token.strip()
            if not token:
                continue
            years.append(int(token))
        if years:
            years = sorted(set(years))
            logging.info(f"使用 BLASTDT2_YEARS 指定年份：{years}")
            return years

    start_date = os.getenv('BLASTDT2_BACKFILL_START_DATE', '').strip()
    end_date = os.getenv('BLASTDT2_BACKFILL_END_DATE', '').strip()
    if start_date and end_date:
        start_year = datetime.strptime(start_date, '%Y-%m-%d').year
        end_year = datetime.strptime(end_date, '%Y-%m-%d').year
        years = list(range(min(start_year, end_year), max(start_year, end_year) + 1))
        logging.info(f"使用 backfill 日期範圍推導年份：{years} ({start_date} ~ {end_date})")
        return years

    logging.info(f"使用預設年份：{DEFAULT_YEARS}")
    return DEFAULT_YEARS


def parse_date_range_from_env() -> tuple[Optional[datetime], Optional[datetime]]:
    """Optional output-date filter by BLASTDT2_BACKFILL_START_DATE/END_DATE."""
    start_date = os.getenv('BLASTDT2_BACKFILL_START_DATE', '').strip()
    end_date = os.getenv('BLASTDT2_BACKFILL_END_DATE', '').strip()
    if not start_date or not end_date:
        return None, None

    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    logging.info(f"啟用日期範圍過濾：{start_dt.date()} ~ {end_dt.date()}")
    return start_dt, end_dt


def ensure_repo_updated(repo_dir: str) -> None:
    """Clone or pull upstream repo; if existing directory is invalid, reclone."""
    if not os.path.isdir(repo_dir):
        logging.info(f"倉庫不存在，開始 clone：{REPO_URL}")
        subprocess.run(['git', 'clone', REPO_URL, repo_dir], check=True)
        return

    # 已存在資料夾但可能不是 git repo（例如殘留空資料夾）
    git_dir = os.path.join(repo_dir, '.git')
    if not os.path.isdir(git_dir):
        logging.warning(f"{repo_dir} 存在但非 git 倉庫，將重新 clone")
        subprocess.run(['rm', '-rf', repo_dir], check=True)
        subprocess.run(['git', 'clone', REPO_URL, repo_dir], check=True)
        return

    logging.info(f"倉庫已存在，開始 pull 更新：{repo_dir}")
    subprocess.run(['git', 'pull'], cwd=repo_dir, check=True)


def resolve_data_base_path(repo_dir: str, data_subdir: str) -> Optional[str]:
    """Resolve BlastDT2 prediction folder with robust fallback search."""
    direct_path = os.path.join(repo_dir, data_subdir)
    if os.path.isdir(direct_path):
        return direct_path

    target = data_subdir.lower()
    for root, dirs, _files in os.walk(repo_dir):
        for d in dirs:
            d_lower = d.lower()
            # 支援大小寫/命名變化：prediction_BlastDT2, prediction_blastdt2, BlastDT2_prediction ...
            if target == d_lower or ('blastdt2' in d_lower and 'prediction' in d_lower):
                return os.path.join(root, d)

    return None


def iter_station_csv_paths(base_path: str, years: list[int]):
    """Yield station/year csv paths under prediction base path.

    優先走舊結構（base/station/YYYY.csv），若找不到則遞迴搜尋 YYYY.csv。
    """
    yielded = set()

    # 舊版結構：每個站點一個子資料夾，檔名為 YYYY.csv
    for station in os.listdir(base_path):
        station_dir = os.path.join(base_path, station)
        if not os.path.isdir(station_dir):
            continue
        for year in years:
            csv_path = os.path.join(station_dir, f"{year}.csv")
            if os.path.isfile(csv_path):
                yielded.add(csv_path)
                yield station, csv_path

    if yielded:
        return

    # 新版/未知結構：遞迴找 YYYY.csv
    year_names = {f"{y}.csv" for y in years}
    for root, _dirs, files in os.walk(base_path):
        for file_name in files:
            if file_name in year_names:
                csv_path = os.path.join(root, file_name)
                if csv_path in yielded:
                    continue
                station = os.path.basename(os.path.dirname(csv_path))
                yield station, csv_path


def log_repo_diagnostics(repo_dir: str) -> None:
    """Log repo state and shallow tree for incident debugging."""
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_dir, text=True).strip()
        branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo_dir, text=True).strip()
        remote = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], cwd=repo_dir, text=True).strip()
        logging.info(f"BlastDT repo state: branch={branch}, head={head}, origin={remote}")
    except Exception as exc:
        logging.warning(f"無法取得 BlastDT repo git 診斷資訊：{exc}")

    try:
        out = subprocess.check_output(['find', repo_dir, '-maxdepth', '4', '-type', 'd'], text=True)
        lines = [line for line in out.splitlines() if line]
        sample = lines[:200]
        logging.info("BlastDT 目錄樹（maxdepth=4，最多顯示200行）:\n" + "\n".join(sample))
        if len(lines) > len(sample):
            logging.info(f"BlastDT 目錄樹其餘省略 {len(lines)-len(sample)} 行")
    except Exception as exc:
        logging.warning(f"無法列出 BlastDT 目錄樹：{exc}")


YEARS = parse_years_from_env()
DATE_RANGE_START, DATE_RANGE_END = parse_date_range_from_env()

# ----------------------------
# 下載或更新 GitHub 倉庫
# ----------------------------
ensure_repo_updated(REPO_DIR)

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
    log_repo_diagnostics(REPO_DIR)
    message = f"找不到資料夾：{DATA_SUBDIR}（repo: {REPO_DIR}）。"
    if FAIL_ON_MISSING_DATA:
        raise RuntimeError(message + "中止流程，請檢查上游 BlastDT repo 結構是否變更。")
    logging.error(message + "本次跳過 BlastDT2 更新，保留既有輸出。")
    raise SystemExit(0)

logging.info(f"使用 BlastDT2 資料夾：{base_path}")

processed_files = 0
for station, csv_path in iter_station_csv_paths(base_path, YEARS):
    processed_files += 1
    logging.info(f"處理站點資料夾：{station}")
    logging.info(f"讀取檔案：{csv_path}")

    df = pd.read_csv(csv_path)

    required_columns = {'站號', '站名', 'Date', 'lat', 'lon', 'BlastDT2'}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        logging.warning(f"欄位不足，跳過：{csv_path}，缺少 {sorted(missing_columns)}")
        continue

    # 轉換 BlastDT2 欄位，False->0.0, True->1.0, 空值視為0.0
    df['BlastDT2'] = df['BlastDT2'].apply(lambda x: 1.0 if str(x).lower() == 'true' else 0.0)

    # 篩選並重命名欄位，符合伺服器格式需求
    df = df[['站號', '站名', 'Date', 'lat', 'lon', 'BlastDT2']]
    df = df.rename(columns={'Date': '日期'})

    # 將感染日期轉為發病日期，加入潛伏期
    df['日期'] = pd.to_datetime(df['日期']) + pd.Timedelta(days=INCUBATION_DAYS)

    # 若有指定 backfill 日期範圍，僅保留範圍內的發病日期
    if DATE_RANGE_START and DATE_RANGE_END:
        start_bound = DATE_RANGE_START
        end_bound = DATE_RANGE_END + timedelta(days=1) - timedelta(microseconds=1)
        df = df[(df['日期'] >= start_bound) & (df['日期'] <= end_bound)]

    df['日期'] = df['日期'].dt.strftime('%Y-%m-%d')

    # 累積結果
    all_records.append(df)

if processed_files == 0:
    log_repo_diagnostics(REPO_DIR)
    message = f"在 {base_path} 找不到任何年度檔案（年份：{YEARS}）"
    if FAIL_ON_MISSING_DATA:
        raise RuntimeError(message)
    logging.warning(message)

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

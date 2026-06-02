# %%
import logging
import os
import subprocess
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

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
REPO_URL = 'https://github.com/Raingel/BlastDT.git'
REPO_DIR = './BlastDT'
DATA_SUBDIR = 'prediction_BlastDT2'
OUTPUT_DIR = os.path.join('..', '..', 'rice_blast_prediction', 'data')

# 將感染日期平移為預估發病日期
INCUBATION_DAYS = 5

# 每日有效測站數低於此門檻時，視為上游資料尚未完整
MIN_RECORDS_PER_DATE = int(os.getenv('BLASTDT2_MIN_RECORDS_PER_DATE', '50'))

# 未指定 backfill 日期時，自動更新最近幾個有效輸出日期
AUTO_WINDOW_DAYS = int(os.getenv('BLASTDT2_WINDOW_DAYS', '14'))

# 預設讀取去年與今年，避免跨年時漏掉年初平移資料
DEFAULT_YEARS = [datetime.now().year - 1, datetime.now().year]
FAIL_ON_MISSING_DATA = os.getenv('BLASTDT2_FAIL_ON_MISSING_DATA', '1').strip() != '0'


def parse_years_from_env() -> list[int]:
    """Parse BLASTDT2_YEARS env, fallback to the years needed for the requested range."""
    years_env = os.getenv('BLASTDT2_YEARS', '').strip()
    if years_env:
        years = []
        for token in years_env.split(','):
            token = token.strip()
            if token:
                years.append(int(token))
        if years:
            years = sorted(set(years))
            logging.info(f"使用 BLASTDT2_YEARS 指定年份：{years}")
            return years

    start_date = os.getenv('BLASTDT2_BACKFILL_START_DATE', '').strip()
    end_date = os.getenv('BLASTDT2_BACKFILL_END_DATE', '').strip()
    if start_date and end_date:
        # 輸出日期已加上潛伏期，因此需向前回推來源日期年份
        source_start = datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=INCUBATION_DAYS)
        source_end = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=INCUBATION_DAYS)
        years = list(range(min(source_start.year, source_end.year), max(source_start.year, source_end.year) + 1))
        logging.info(f"使用 backfill 日期範圍推導來源年份：{years} ({start_date} ~ {end_date})")
        return years

    logging.info(f"使用預設年份：{DEFAULT_YEARS}")
    return DEFAULT_YEARS


def parse_date_range_from_env() -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """Optional output-date filter by BLASTDT2_BACKFILL_START_DATE/END_DATE."""
    start_date = os.getenv('BLASTDT2_BACKFILL_START_DATE', '').strip()
    end_date = os.getenv('BLASTDT2_BACKFILL_END_DATE', '').strip()

    if bool(start_date) != bool(end_date):
        raise ValueError('BLASTDT2_BACKFILL_START_DATE 與 BLASTDT2_BACKFILL_END_DATE 必須同時提供')

    if not start_date:
        return None, None

    start_dt = pd.Timestamp(datetime.strptime(start_date, '%Y-%m-%d')).normalize()
    end_dt = pd.Timestamp(datetime.strptime(end_date, '%Y-%m-%d')).normalize()
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    logging.info(f"啟用指定日期範圍：{start_dt.date()} ~ {end_dt.date()}")
    return start_dt, end_dt


def parse_blastdt2_value(value) -> float:
    """Preserve missing upstream values instead of treating them as low risk."""
    if pd.isna(value):
        return float('nan')

    text = str(value).strip().lower()
    if text in {'true', '1', '1.0'}:
        return 1.0
    if text in {'false', '0', '0.0'}:
        return 0.0
    return float('nan')


def ensure_repo_updated(repo_dir: str) -> None:
    """Clone or pull upstream repo; if existing directory is invalid, reclone."""
    if not os.path.isdir(repo_dir):
        logging.info(f"倉庫不存在，開始 clone：{REPO_URL}")
        subprocess.run(['git', 'clone', REPO_URL, repo_dir], check=True)
        return

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
        for directory in dirs:
            directory_lower = directory.lower()
            if target == directory_lower or ('blastdt2' in directory_lower and 'prediction' in directory_lower):
                return os.path.join(root, directory)

    return None


def iter_station_csv_paths(base_path: str, years: list[int]):
    """Yield station/year csv paths under prediction base path."""
    yielded = set()

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

    year_names = {f"{year}.csv" for year in years}
    for root, _dirs, files in os.walk(base_path):
        for file_name in files:
            if file_name not in year_names:
                continue
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
        logging.info("BlastDT 目錄樹（maxdepth=4，最多顯示200行）：\n" + "\n".join(sample))
        if len(lines) > len(sample):
            logging.info(f"BlastDT 目錄樹其餘省略 {len(lines) - len(sample)} 行")
    except Exception as exc:
        logging.warning(f"無法列出 BlastDT 目錄樹：{exc}")


def output_path_for_date(date_str: str) -> str:
    return os.path.join(OUTPUT_DIR, f"{date_str.replace('-', '')}_BlastDT2.csv")


def remove_stale_output(date_str: str, reason: str) -> None:
    """Remove stale partial output so downstream merging cannot reuse old values."""
    out_path = output_path_for_date(date_str)
    if os.path.exists(out_path):
        os.remove(out_path)
        logging.warning(f"已刪除舊檔：{out_path}；原因：{reason}")
    else:
        logging.warning(f"不輸出日期 {date_str}；原因：{reason}")


if AUTO_WINDOW_DAYS < 1:
    raise ValueError('BLASTDT2_WINDOW_DAYS 必須至少為 1')
if MIN_RECORDS_PER_DATE < 1:
    raise ValueError('BLASTDT2_MIN_RECORDS_PER_DATE 必須至少為 1')

YEARS = parse_years_from_env()
REQUESTED_RANGE_START, REQUESTED_RANGE_END = parse_date_range_from_env()

# ----------------------------
# 下載或更新上游 BlastDT repo
# ----------------------------
ensure_repo_updated(REPO_DIR)

# ----------------------------
# 準備輸出資料夾
# ----------------------------
os.makedirs(OUTPUT_DIR, exist_ok=True)
logging.info(f"確保輸出資料夾存在：{OUTPUT_DIR}")

# ----------------------------
# 讀取並處理年度檔
# ----------------------------
all_records = []
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

    df = df[['站號', '站名', 'Date', 'lat', 'lon', 'BlastDT2']].copy()
    df['BlastDT2'] = df['BlastDT2'].apply(parse_blastdt2_value)
    df = df.rename(columns={'Date': '日期'})
    df['日期'] = pd.to_datetime(df['日期'], errors='coerce') + pd.Timedelta(days=INCUBATION_DAYS)
    df = df[df['日期'].notna()]
    all_records.append(df)

if processed_files == 0:
    log_repo_diagnostics(REPO_DIR)
    message = f"在 {base_path} 找不到任何年度檔案（年份：{YEARS}）"
    if FAIL_ON_MISSING_DATA:
        raise RuntimeError(message)
    logging.warning(message)

if not all_records:
    raise RuntimeError("沒有讀取到任何 BlastDT2 資料，請確認路徑與年份設定")

# ----------------------------
# 合併資料並決定本次更新範圍
# ----------------------------
combined = pd.concat(all_records, ignore_index=True)
combined['日期'] = pd.to_datetime(combined['日期'], errors='coerce').dt.normalize()
combined = combined[combined['日期'].notna()]
combined['站號'] = combined['站號'].astype(str)
combined = combined.drop_duplicates(subset=['站號', '日期'], keep='last')
logging.info(f"總共合併 {len(combined)} 筆資料")

counts = combined.groupby('日期')['站號'].nunique().sort_index()
valid_counts = counts[counts >= MIN_RECORDS_PER_DATE]

if valid_counts.empty:
    raise RuntimeError(f"沒有任何日期達到最低有效測站數 {MIN_RECORDS_PER_DATE}")

if REQUESTED_RANGE_START is not None and REQUESTED_RANGE_END is not None:
    range_start = REQUESTED_RANGE_START
    range_end = REQUESTED_RANGE_END
    logging.info(f"使用指定 BlastDT2 輸出範圍：{range_start.date()} ~ {range_end.date()}")
else:
    range_end = valid_counts.index.max()
    range_start = range_end - pd.Timedelta(days=AUTO_WINDOW_DAYS - 1)
    logging.info(
        f"自動偵測 BlastDT2 最近 {AUTO_WINDOW_DAYS} 天："
        f"{range_start.date()} ~ {range_end.date()}；"
        f"上游最新有效發病日期：{range_end.date()}"
    )

target_dates = pd.date_range(range_start, range_end, freq='D')
window_df = combined[(combined['日期'] >= range_start) & (combined['日期'] <= range_end)].copy()
window_df['日期'] = window_df['日期'].dt.strftime('%Y-%m-%d')

# ----------------------------
# 逐日寫入；不完整或缺少的日期會移除舊檔
# ----------------------------
for target_date in target_dates:
    date_str = target_date.strftime('%Y-%m-%d')
    group = window_df[window_df['日期'] == date_str].copy()
    station_count = group['站號'].nunique()

    if station_count < MIN_RECORDS_PER_DATE:
        remove_stale_output(
            date_str,
            f"有效測站數 {station_count} 少於門檻 {MIN_RECORDS_PER_DATE}"
        )
        continue

    group = group[['站號', '站名', '日期', 'lat', 'lon', 'BlastDT2']]
    group = group.sort_values('站號')
    missing_value_count = int(group['BlastDT2'].isna().sum())

    out_path = output_path_for_date(date_str)
    group.to_csv(out_path, index=False, encoding='utf-8-sig')
    logging.info(
        f"輸出完成：{out_path}，共 {station_count} 個測站；"
        f"其中 {missing_value_count} 筆 BlastDT2 為缺值"
    )

# %%

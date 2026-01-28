# -*- coding: utf-8 -*-
"""
季節性高風險「日數」統整（Py3.8 相容，方便逐段除錯）

流程：
1) 判定今天上/下半年（上：1/1~今天；下：7/1~今天）
2) 統計本年度各站「高風險日數」：
   - BlastGRU-TW / BlastDT2 / BlastLSTLS / BLBTSLS：值 >= 0.5 算 1 日
   - planthopper：值 > 0 算 1 日
3) 近十年（不含今年）同期間的平均高風險日數：
   - 只納入該模型該年「完整期間」的年份（每日檔案齊全）
   - 完全沒檔的年份忽略
4) 維持「有出現才可能 +1」邏輯
5) 輸出 recent_summary.csv，欄位：
   站號 站名 lat lon
   BlastGRU-TW_this_year BlastDT2_this_year BlastLSTLS_this_year BLBTSLS_this_year planthopper_this_year
   BlastGRU-TW_avg BlastDT2_avg BlastLSTLS_avg BLBTSLS_avg planthopper_avg
"""
# %%
import os
import logging
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Tuple, List, Dict, Optional

import numpy as np
import pandas as pd

# ========== 參數 ==========
DATA_FOLDER = "/home/raingel/rice_blast_model_update/rice_blast_prediction/data"
PLAN_FOLDER = "/home/raingel/planthopper/HYSPLIT-Planthopper-Forecast/prediction"  # planthopper 格點資料（若沒有可設為 ""）
OUTPUT_CSV  = "/home/raingel/rice_blast_model_update/rice_blast_prediction/recent_summary.csv"

# 風險門檻
RISK_THRESHOLD_MODELS = 0.5      # 四模型：>= 0.5 算高風險
RISK_THRESHOLD_PLAN   = 0.0      # planthopper：> 0 算高風險（嚴格大於 0）

# 模型對應：每日 CSV 內的欄位名稱
MODEL_COLS: Dict[str, str] = {
    "BlastGRU-TW": "BlastGRU-TW",
    "BlastDT2": "BlastDT2",
    "BlastLSTLS": "BlastLSTLS",
    "BLBTSLS": "BLBTSLS",
    # planthopper 是格點資料（x, y, value）；將 value 對映到各站
    "planthopper": "value",
}

# Logging：輸出到螢幕
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("seasonal_risk_summary")


# ========== 小工具 ==========
def std_date_str(d: date) -> str:
    """把日期轉為 YYYYMMDD（每日檔名用）"""
    return d.strftime("%Y%m%d")

def semester_window(today: date) -> Tuple[date, date]:
    """上半年：1/1~今天；下半年：7/1~今天"""
    h2_start = date(today.year, 7, 1)
    if today < h2_start:
        start = date(today.year, 1, 1)
    else:
        start = h2_start
    return start, today

def same_period_for_year(year: int, ref_start: date, ref_end: date) -> Tuple[date, date]:
    """把今年的區間（例如 7/1~9/1）映射到指定 year 的同月日。"""
    return date(year, ref_start.month, ref_start.day), date(year, ref_end.month, ref_end.day)

def daterange(start: date, end: date):
    """[start, end] 每日迭代（含端點）"""
    days = (end - start).days
    for i in range(days + 1):
        yield start + timedelta(days=i)

def safe_read_csv(path: str) -> Optional[pd.DataFrame]:
    """讀 CSV，失敗回 None"""
    if not os.path.exists(path):
        return None
    try:
        return pd.read_csv(path)
    except Exception as e:
        log.warning(f"讀檔失敗：{path} -> {e}")
        return None

def build_station_master(rows: List[pd.DataFrame]) -> pd.DataFrame:
    """由多筆日資料匯總站點（站號/站名/lat/lon），以第一筆為準避免重複/微差。"""
    frames: List[pd.DataFrame] = []
    for df in rows:
        if df is None or df.empty:
            continue
        cols = [c for c in ["站號", "站名", "lat", "lon"] if c in df.columns]
        if cols:
            frames.append(df[cols].copy())
    if not frames:
        return pd.DataFrame(columns=["站號", "站名", "lat", "lon"])
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.drop_duplicates(subset=["站號"], keep="first")
    return all_df

def nearest_planthopper_value(ph_df: pd.DataFrame, lat: float, lon: float) -> Optional[float]:
    """planthopper：以最近格點 value 對映到 (lat, lon)；支援 lat/lon 或 x/y 欄名。"""
    if ph_df is None or ph_df.empty:
        return None

    # 欄名對應：若只有 lat/lon，就改名為 y/x
    rename_map: Dict[str, str] = {}
    if "x" not in ph_df.columns and "lon" in ph_df.columns:
        rename_map["lon"] = "x"
    if "y" not in ph_df.columns and "lat" in ph_df.columns:
        rename_map["lat"] = "y"
    if rename_map:
        ph_df = ph_df.rename(columns=rename_map)

    if not all(c in ph_df.columns for c in ["x", "y", "value"]):
        return None

    try:
        from scipy.spatial import cKDTree as KDTree
        tree = KDTree(np.c_[ph_df["x"].values, ph_df["y"].values])
        dist, idx = tree.query(np.array([[lon, lat]]), k=1)
        return float(ph_df["value"].iloc[int(idx)])
    except Exception:
        # 後備（資料量不大時也 OK）
        dx = ph_df["x"].to_numpy() - lon
        dy = ph_df["y"].to_numpy() - lat
        dist2 = dx*dx + dy*dy
        i = int(np.argmin(dist2))
        return float(ph_df["value"].iloc[i])


# ========== 核心：累計某年「該期間」各模型高風險日數 ==========
def accumulate_for_period(year: int, start_d: date, end_d: date, need_plan: bool = True):
    """
    回傳：
      counts: Dict[站號, Dict[模型, int]] -> 高風險「日數」
      station_master: DataFrame(站號, 站名, lat, lon)
      model_avail_days: Dict[模型, int] -> 此年期間有檔案的「天數」
      total_days: int -> 此年期間天數（含端點）

    ※ model_avail_days 用來判斷某「模型」該年是否「完整期間」（每日都有檔案）。
    """
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    daily_station_rows: List[pd.DataFrame] = []

    model_avail_days: Dict[str, int] = {m: 0 for m in MODEL_COLS.keys()}
    total_days: int = (end_d - start_d).days + 1

    for d in daterange(start_d, end_d):
        ds = std_date_str(d)

        # 四個模型每日檔案
        paths: Dict[str, str] = {
            "BlastGRU-TW": os.path.join(DATA_FOLDER, f"{ds}_BlastGRU-TW.csv"),
            "BlastDT2":    os.path.join(DATA_FOLDER, f"{ds}_BlastDT2.csv"),
            "BlastLSTLS":  os.path.join(DATA_FOLDER, f"{ds}_BlastLSTLS.csv"),
            "BLBTSLS":     os.path.join(DATA_FOLDER, f"{ds}_BLBTSLS.csv"),
        }

        day_frames: Dict[str, pd.DataFrame] = {}
        for model, p in paths.items():
            df = safe_read_csv(p)
            if df is not None and not df.empty:
                day_frames[model] = df
                model_avail_days[model] += 1
                daily_station_rows.append(df)

        # planthopper
        plan_df: Optional[pd.DataFrame] = None
        if need_plan and PLAN_FOLDER and os.path.isdir(PLAN_FOLDER):
            ph_path = os.path.join(PLAN_FOLDER, f"{ds}_max_freq.csv")
            plan_df = safe_read_csv(ph_path)
            if plan_df is not None and not plan_df.empty:
                model_avail_days["planthopper"] += 1

        # ---- 統計四個模型（有出現才可能 +1）----
        for model_name, df in day_frames.items():
            val_col = MODEL_COLS[model_name]
            if "站號" not in df.columns or val_col not in df.columns:
                continue
            for _, row in df.iterrows():
                sid = row["站號"]
                try:
                    val = float(row[val_col])
                except Exception:
                    continue
                if val >= RISK_THRESHOLD_MODELS:
                    counts[str(sid)][model_name] += 1

        # ---- 統計 planthopper（>0 算高風險）----
        if plan_df is not None and not plan_df.empty:
            # 需要站點經緯度：盡量從今日四模型組合出一份暫時 master
            day_master = build_station_master(list(day_frames.values()))
            if not day_master.empty:
                for _, row in day_master.iterrows():
                    sid = row["站號"]
                    lat = row.get("lat", None)
                    lon = row.get("lon", None)
                    if pd.isna(lat) or pd.isna(lon):
                        continue
                    pv = nearest_planthopper_value(plan_df, float(lat), float(lon))
                    if pv is None:
                        continue
                    if pv > RISK_THRESHOLD_PLAN:
                        counts[str(sid)]["planthopper"] += 1
                daily_station_rows.append(day_master)

    station_master = build_station_master(daily_station_rows)
    return counts, station_master, model_avail_days, total_days

# %%
# ========== 頂層流程（可逐行執行） ==========
today = date.today()
period_start, period_end = semester_window(today)
this_year = today.year

log.info(f"今天：{today}；半期區間：{period_start} ~ {period_end}")

# 1) 本年度
log.info("開始統計：本年度高風險『日數』")
counts_this, station_master, avail_this, total_days_this = accumulate_for_period(
    this_year, period_start, period_end, need_plan=True
)
log.info(f"本年度各模型有檔天數：{avail_this}；期間總天數：{total_days_this}")

# 2) 近十年（不含今年），只納入「完整期間」的年份來做平均
years_past: List[int] = list(range(this_year - 10, this_year))
yearly_counts_list: List[Tuple[int, Dict[str, Dict[str, int]], int]] = []
yearly_avail_list:  List[Tuple[int, Dict[str, int], int]] = []

for yr in years_past:
    ps, pe = same_period_for_year(yr, period_start, period_end)
    counts_y, _station_y, avail_y, total_days_y = accumulate_for_period(yr, ps, pe, need_plan=True)
    yearly_counts_list.append((yr, counts_y, total_days_y))
    yearly_avail_list.append((yr, avail_y, total_days_y))
    log.info(f"{yr} 年有檔天數：{avail_y}；期間天數：{total_days_y}")

# 3) 輸出表骨架（以「本年度」蒐集到的站點為主）
out_cols: List[str] = [
    "站號", "站名", "lat", "lon",
    "BlastGRU-TW_this_year", "BlastDT2_this_year", "BlastLSTLS_this_year", "BLBTSLS_this_year", "planthopper_this_year",
    "BlastGRU-TW_avg", "BlastDT2_avg", "BlastLSTLS_avg", "BLBTSLS_avg", "planthopper_avg",
]
if station_master.empty:
    log.warning("查無任何站點資料，本年度站點主表為空，輸出將為空表。")
    out_df = pd.DataFrame(columns=out_cols)
else:
    base = station_master.copy()
    for col in out_cols:
        if col not in base.columns:
            base[col] = np.nan
    out_df = base[out_cols].copy()

# 4) 填入 this_year 日數
for sid, model_counts in counts_this.items():
    if sid not in set(out_df["站號"]):
        # 本年度沒收錄到站點資料時，補一列（站名/座標留空）
        out_df = pd.concat([out_df, pd.DataFrame({"站號":[sid]})], ignore_index=True)
    for model in MODEL_COLS.keys():
        col = f"{model}_this_year"
        if col not in out_df.columns:
            out_df[col] = np.nan
        out_df.loc[out_df["站號"] == sid, col] = int(model_counts.get(model, 0))

# 5) 計算「近十年平均」—— 僅納入該模型「完整期間」的年份
#    定義「完整期間」：該模型此年「有檔天數」== 此年期間總天數（含端點）
model_complete_years: Dict[str, set] = {m: set() for m in MODEL_COLS.keys()}
for (yr, avail_y, total_days_y) in yearly_avail_list:
    for m in MODEL_COLS.keys():
        if avail_y.get(m, 0) == total_days_y and total_days_y > 0:
            model_complete_years[m].add(yr)

log.info("各模型納入平均的完整年份：%s",
         {m: sorted(list(yrs)) for m, yrs in model_complete_years.items()})

# 對每個站/模型，將「完整年份」的日數取平均；若沒有任何完整年份，留 NaN
for sid in out_df["站號"].astype(str).tolist():
    for m in MODEL_COLS.keys():
        elig_years = model_complete_years[m]
        if not elig_years:
            out_df.loc[out_df["站號"] == sid, f"{m}_avg"] = np.nan
            continue

        vals: List[int] = []
        for (yr, counts_y, _td) in yearly_counts_list:
            if yr not in elig_years:
                continue
            v = int(counts_y.get(sid, {}).get(m, 0))
            vals.append(v)

        if len(vals) == 0:
            out_df.loc[out_df["站號"] == sid, f"{m}_avg"] = np.nan
        else:
            out_df.loc[out_df["站號"] == sid, f"{m}_avg"] = float(np.mean(vals))

# 6) 匯出
os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
out_df.to_csv(OUTPUT_CSV, index=False)
log.info(f"已輸出：{OUTPUT_CSV}；列數：{len(out_df)}")

# %%

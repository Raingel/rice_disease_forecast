import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import numpy as np
import pandas as pd

ROOT_DIR = os.getenv("PIPELINE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
DATA_FOLDER = os.getenv("DATA_FOLDER", os.path.join(ROOT_DIR, "rice_blast_prediction", "data"))
PLAN_FOLDER = os.getenv("PLAN_FOLDER", "")
PLANTHOPPER_REMOTE_BASE_URL = os.getenv(
    "PLANTHOPPER_REMOTE_BASE_URL",
    "https://raw.githubusercontent.com/Raingel/HYSPLIT-Planthopper-Forecast/refs/heads/main/prediction/",
)
OUTPUT_CSV = os.getenv("OUTPUT_CSV", os.path.join(ROOT_DIR, "rice_blast_prediction", "recent_summary.csv"))
PLAN_AVG_SNAPSHOT_CSV = os.getenv(
    "PLAN_AVG_SNAPSHOT_CSV",
    os.path.join(ROOT_DIR, "rice_blast_prediction", "planthopper_avg_snapshot.csv"),
)

STATION_ID_COL = "站號"
STATION_NAME_COL = "站名"
DATE_COL = "日期"

DEFAULT_RISK_THRESHOLD_MODELS = 0.5
RISK_THRESHOLD_PLAN = 0.0
MODEL_RISK_THRESHOLDS: Dict[str, float] = {
    "BlastGRU-TW": DEFAULT_RISK_THRESHOLD_MODELS,
    "BlastDT2": DEFAULT_RISK_THRESHOLD_MODELS,
    "BlastLSTLS": DEFAULT_RISK_THRESHOLD_MODELS,
    "BLBTSLS": DEFAULT_RISK_THRESHOLD_MODELS,
    "BlastTF": DEFAULT_RISK_THRESHOLD_MODELS,
    "BlastGAT": 0.23,
    "BLASTAM": DEFAULT_RISK_THRESHOLD_MODELS,
}
MODEL_COLS: Dict[str, str] = {
    "BlastGRU-TW": "BlastGRU-TW",
    "BlastDT2": "BlastDT2",
    "BlastLSTLS": "BlastLSTLS",
    "BLBTSLS": "BLBTSLS",
    "BlastTF": "BlastTF",
    "BlastGAT": "BlastGAT",
    "BLASTAM": "BLASTAM",
    "planthopper": "value",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("seasonal_risk_summary")


def std_date_str(d: date) -> str:
    return d.strftime("%Y%m%d")


def semester_window(today: date) -> Tuple[date, date]:
    h2_start = date(today.year, 7, 1)
    start = date(today.year, 1, 1) if today < h2_start else h2_start
    return start, today


def same_period_for_year(year: int, ref_start: date, ref_end: date) -> Tuple[date, date]:
    return date(year, ref_start.month, ref_start.day), date(year, ref_end.month, ref_end.day)


def daterange(start: date, end: date):
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)


def safe_read_csv(path: str) -> Optional[pd.DataFrame]:
    is_remote = str(path).startswith("http://") or str(path).startswith("https://")
    if (not is_remote) and (not os.path.exists(path)):
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:
        log.warning("failed to read %s: %s", path, exc)
        return None


def load_planthopper_data(date_str: str) -> Optional[pd.DataFrame]:
    local_file = os.path.join(PLAN_FOLDER, f"{date_str}_max_freq.csv") if PLAN_FOLDER else ""
    remote_url = urljoin(PLANTHOPPER_REMOTE_BASE_URL, f"{date_str}_max_freq.csv")
    for source in [local_file, remote_url]:
        if not source:
            continue
        if source == local_file and not os.path.exists(source):
            continue
        df = safe_read_csv(source)
        if df is None or df.empty:
            continue
        rename_map = {}
        if "x" not in df.columns and "lon" in df.columns:
            rename_map["lon"] = "x"
        if "y" not in df.columns and "lat" in df.columns:
            rename_map["lat"] = "y"
        if rename_map:
            df = df.rename(columns=rename_map)
        if {"x", "y", "value"}.issubset(df.columns):
            return df
    return None


def load_planthopper_avg_snapshot(snapshot_csv: str) -> Dict[str, float]:
    df = safe_read_csv(snapshot_csv)
    if df is None or df.empty:
        return {}
    if STATION_ID_COL not in df.columns or "planthopper_avg" not in df.columns:
        return {}
    out: Dict[str, float] = {}
    for _, row in df.iterrows():
        sid = str(row.get(STATION_ID_COL, "")).strip()
        if not sid:
            continue
        try:
            out[sid] = float(row["planthopper_avg"])
        except Exception:
            continue
    return out


def build_station_master(rows: List[pd.DataFrame]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for df in rows:
        if df is None or df.empty:
            continue
        cols = [c for c in [STATION_ID_COL, STATION_NAME_COL, "lat", "lon"] if c in df.columns]
        if cols:
            frames.append(df[cols].copy())
    if not frames:
        return pd.DataFrame(columns=[STATION_ID_COL, STATION_NAME_COL, "lat", "lon"])
    all_df = pd.concat(frames, ignore_index=True)
    return all_df.drop_duplicates(subset=[STATION_ID_COL], keep="first")


def nearest_planthopper_value(ph_df: pd.DataFrame, lat: float, lon: float) -> Optional[float]:
    if ph_df is None or ph_df.empty:
        return None
    dx = ph_df["x"].to_numpy() - lon
    dy = ph_df["y"].to_numpy() - lat
    idx = int(np.argmin(dx * dx + dy * dy))
    return float(ph_df["value"].iloc[idx])


def accumulate_for_period(year: int, start_d: date, end_d: date, need_plan: bool = True):
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    daily_station_rows: List[pd.DataFrame] = []
    model_avail_days: Dict[str, int] = {m: 0 for m in MODEL_COLS.keys()}
    total_days = (end_d - start_d).days + 1

    for d in daterange(start_d, end_d):
        ds = std_date_str(d)
        paths = {
            "BlastGRU-TW": os.path.join(DATA_FOLDER, f"{ds}_BlastGRU-TW.csv"),
            "BlastDT2": os.path.join(DATA_FOLDER, f"{ds}_BlastDT2.csv"),
            "BlastLSTLS": os.path.join(DATA_FOLDER, f"{ds}_BlastLSTLS.csv"),
            "BLBTSLS": os.path.join(DATA_FOLDER, f"{ds}_BLBTSLS.csv"),
            "BlastTF": os.path.join(DATA_FOLDER, f"{ds}_BlastTF.csv"),
            "BlastGAT": os.path.join(DATA_FOLDER, f"{ds}_BlastGAT.csv"),
            "BLASTAM": os.path.join(DATA_FOLDER, f"{ds}_BLASTAM.csv"),
        }

        day_frames: Dict[str, pd.DataFrame] = {}
        for model_name, path in paths.items():
            df = safe_read_csv(path)
            if df is not None and not df.empty:
                day_frames[model_name] = df
                model_avail_days[model_name] += 1
                daily_station_rows.append(df)

        plan_df = load_planthopper_data(ds) if need_plan else None
        if plan_df is not None and not plan_df.empty:
            model_avail_days["planthopper"] += 1

        for model_name, df in day_frames.items():
            value_col = MODEL_COLS[model_name]
            if STATION_ID_COL not in df.columns or value_col not in df.columns:
                continue
            threshold = MODEL_RISK_THRESHOLDS.get(model_name, DEFAULT_RISK_THRESHOLD_MODELS)
            for _, row in df.iterrows():
                try:
                    value = float(row[value_col])
                except Exception:
                    continue
                if value >= threshold:
                    counts[str(row[STATION_ID_COL])][model_name] += 1

        if plan_df is not None and not plan_df.empty:
            day_master = build_station_master(list(day_frames.values()))
            if not day_master.empty:
                for _, row in day_master.iterrows():
                    lat = row.get("lat")
                    lon = row.get("lon")
                    if pd.isna(lat) or pd.isna(lon):
                        continue
                    pv = nearest_planthopper_value(plan_df, float(lat), float(lon))
                    if pv is not None and pv > RISK_THRESHOLD_PLAN:
                        counts[str(row[STATION_ID_COL])]["planthopper"] += 1
                daily_station_rows.append(day_master)

    station_master = build_station_master(daily_station_rows)
    return counts, station_master, model_avail_days, total_days


def main() -> None:
    today = date.today()
    period_start, period_end = semester_window(today)
    this_year = today.year
    log.info("today=%s; period=%s~%s", today, period_start, period_end)

    counts_this, station_master, avail_this, total_days_this = accumulate_for_period(this_year, period_start, period_end, need_plan=True)
    log.info("this_year availability=%s; total_days=%s", avail_this, total_days_this)

    years_past = list(range(this_year - 10, this_year))
    yearly_counts_list: List[Tuple[int, Dict[str, Dict[str, int]], int]] = []
    yearly_avail_list: List[Tuple[int, Dict[str, int], int]] = []
    for yr in years_past:
        ps, pe = same_period_for_year(yr, period_start, period_end)
        counts_y, _station_y, avail_y, total_days_y = accumulate_for_period(yr, ps, pe, need_plan=True)
        yearly_counts_list.append((yr, counts_y, total_days_y))
        yearly_avail_list.append((yr, avail_y, total_days_y))
        log.info("%s availability=%s; total_days=%s", yr, avail_y, total_days_y)

    out_cols = [
        STATION_ID_COL, STATION_NAME_COL, "lat", "lon",
        "BlastGRU-TW_this_year", "BlastDT2_this_year", "BlastLSTLS_this_year", "BLBTSLS_this_year", "BlastTF_this_year", "BlastGAT_this_year", "BLASTAM_this_year", "planthopper_this_year",
        "BlastGRU-TW_avg", "BlastDT2_avg", "BlastLSTLS_avg", "BLBTSLS_avg", "BlastTF_avg", "BlastGAT_avg", "BLASTAM_avg", "planthopper_avg",
    ]
    out_df = station_master.copy() if not station_master.empty else pd.DataFrame(columns=out_cols)
    for col in out_cols:
        if col not in out_df.columns:
            out_df[col] = np.nan
    out_df = out_df[out_cols]

    known_station_ids = set(out_df[STATION_ID_COL].astype(str)) if not out_df.empty else set()
    for sid, model_counts in counts_this.items():
        if sid not in known_station_ids:
            extra = pd.DataFrame({STATION_ID_COL: [sid]})
            out_df = pd.concat([out_df, extra], ignore_index=True)
            known_station_ids.add(sid)
        for model_name in MODEL_COLS.keys():
            out_df.loc[out_df[STATION_ID_COL].astype(str) == sid, f"{model_name}_this_year"] = int(model_counts.get(model_name, 0))

    model_complete_years: Dict[str, set] = {m: set() for m in MODEL_COLS.keys()}
    for yr, avail_y, total_days_y in yearly_avail_list:
        for model_name in MODEL_COLS.keys():
            if avail_y.get(model_name, 0) == total_days_y and total_days_y > 0:
                model_complete_years[model_name].add(yr)
    log.info("complete_years=%s", {k: sorted(v) for k, v in model_complete_years.items()})

    plan_avg_snapshot = load_planthopper_avg_snapshot(PLAN_AVG_SNAPSHOT_CSV)
    if plan_avg_snapshot:
        log.info("loaded planthopper snapshot: %s (%s stations)", PLAN_AVG_SNAPSHOT_CSV, len(plan_avg_snapshot))

    for sid in out_df[STATION_ID_COL].astype(str).tolist():
        for model_name in MODEL_COLS.keys():
            avg_col = f"{model_name}_avg"
            if model_name == "planthopper" and sid in plan_avg_snapshot:
                out_df.loc[out_df[STATION_ID_COL].astype(str) == sid, avg_col] = plan_avg_snapshot[sid]
                continue

            elig_years = model_complete_years[model_name]
            if not elig_years:
                out_df.loc[out_df[STATION_ID_COL].astype(str) == sid, avg_col] = np.nan
                continue

            vals: List[int] = []
            for yr, counts_y, _ in yearly_counts_list:
                if yr not in elig_years:
                    continue
                vals.append(int(counts_y.get(sid, {}).get(model_name, 0)))
            out_df.loc[out_df[STATION_ID_COL].astype(str) == sid, avg_col] = float(np.mean(vals)) if vals else np.nan

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    out_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    log.info("wrote %s rows to %s", len(out_df), OUTPUT_CSV)


if __name__ == "__main__":
    main()

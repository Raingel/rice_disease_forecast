import io
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

ROOT_DIR = os.getenv("PIPELINE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
OUTPUT_DATA_DIR = os.getenv("DATA_FOLDER", os.path.join(ROOT_DIR, "rice_blast_prediction", "data"))
os.makedirs(OUTPUT_DATA_DIR, exist_ok=True)

LEGACY_BASE_URL = os.getenv(
    "BLASTAM_LEGACY_BASE_URL",
    "https://raw.githubusercontent.com/Raingel/rice_blast_prediction/refs/heads/master/data",
)
START_DATE = os.getenv("BLASTAM_LEGACY_START_DATE", "2018-04-05")
END_DATE = os.getenv("BLASTAM_LEGACY_END_DATE", datetime.utcnow().strftime("%Y-%m-%d"))
REQUEST_TIMEOUT = int(os.getenv("BLASTAM_LEGACY_REQUEST_TIMEOUT", "45"))

REQUIRED_COLUMNS = ["站名", "站號", "日期", "lat", "lon", "BLASTAM"]


def _daterange(start_dt: datetime, end_dt: datetime):
    cur = start_dt
    while cur <= end_dt:
        yield cur
        cur += timedelta(days=1)


def _load_legacy_daily(date_obj: datetime):
    date_str = date_obj.strftime("%Y%m%d")
    url = f"{LEGACY_BASE_URL}/{date_str}_BLASTAM.csv"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))

    if not set(REQUIRED_COLUMNS).issubset(df.columns):
        raise RuntimeError(f"{url} 欄位不完整，缺少 {sorted(set(REQUIRED_COLUMNS) - set(df.columns))}")

    df = df[REQUIRED_COLUMNS].copy()
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["站號", "日期"])
    return df


def _merge_and_write(out_path: str, new_df: pd.DataFrame):
    if os.path.exists(out_path):
        old_df = pd.read_csv(out_path)
        for col in REQUIRED_COLUMNS:
            if col not in old_df.columns:
                old_df[col] = pd.NA
        old_df = old_df[REQUIRED_COLUMNS]
        merged = pd.concat([old_df, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["站號", "日期"], keep="last")
    else:
        merged = new_df.copy()

    merged.to_csv(out_path, index=False, encoding="utf-8-sig")


def main():
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d")
    if end_dt < start_dt:
        raise ValueError("BLASTAM_LEGACY_END_DATE must be >= BLASTAM_LEGACY_START_DATE")

    loaded_days = 0
    missing_days = 0

    for d in _daterange(start_dt, end_dt):
        try:
            df = _load_legacy_daily(d)
        except Exception as e:
            print(f"[WARN] {d.date()} 下載/解析失敗：{e}")
            continue

        if df is None:
            missing_days += 1
            continue

        out_name = f"{d.strftime('%Y%m%d')}_BLASTAM.csv"
        out_path = os.path.join(OUTPUT_DATA_DIR, out_name)
        _merge_and_write(out_path, df)
        loaded_days += 1
        print(f"[INFO] Imported legacy BLASTAM: {out_name} ({len(df)} rows)")

    print(
        "[INFO] Legacy BLASTAM import completed. "
        f"loaded_days={loaded_days}, missing_days={missing_days}, "
        f"range={START_DATE}~{END_DATE}"
    )


if __name__ == "__main__":
    main()

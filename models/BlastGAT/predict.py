#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import torch


ROOT_DIR = Path(os.getenv("PIPELINE_ROOT", Path(__file__).resolve().parents[2]))
ERA5_INPUT_DIR = Path(os.getenv("ERA5_INPUT_DIR", ROOT_DIR / "ERA5"))
OUTPUT_DIR = Path(os.getenv("DATA_FOLDER", ROOT_DIR / "rice_blast_prediction" / "data"))
BACKFILL_START_DATE = pd.to_datetime(os.getenv("BACKFILL_START_DATE", "1900-01-01")).normalize()
BACKFILL_END_DATE = pd.to_datetime(os.getenv("BACKFILL_END_DATE", "2100-12-31")).normalize()
CLOSEOUT_DIR = Path(
    os.getenv(
        "BLASTGAT_CLOSEOUT_DIR",
        ROOT_DIR / "project_closeout" / "rice_blast_model_closeout_20260308",
    )
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REFERENCE_INFERENCE_DIR = CLOSEOUT_DIR / "model" / "inference"
if str(REFERENCE_INFERENCE_DIR) not in sys.path:
    sys.path.insert(0, str(REFERENCE_INFERENCE_DIR))

from reference_tcn_attn import load_closeout_model, predict_prob  # noqa: E402


HOURLY_TO_DAILY_MAP = {
    "temperature_2m": "temperature_2m",
    "relativehumidity_2m": "relative_humidity_2m",
    "windspeed_10m": "wind_speed_10m",
}
REQUIRED_HOURLY_COLS = ["time", "temperature_2m", "relativehumidity_2m", "precipitation", "windspeed_10m"]
STATION_ID_COL = "\u7ad9\u865f"
STATION_NAME_COL = "\u7ad9\u540d"
DATE_COL = "\u65e5\u671f"
INFER_BATCH_SIZE = int(os.getenv("BLASTGAT_INFER_BATCH_SIZE", "512"))
FLUSH_EVERY_STATIONS = int(os.getenv("BLASTGAT_FLUSH_EVERY_STATIONS", "48"))


def load_preprocess_contract(closeout_dir: Path) -> tuple[dict, dict[str, tuple[float, float]]]:
    transform_rules = json.loads(
        (closeout_dir / "model" / "preprocess" / "transform_rules.json").read_text(encoding="utf-8")
    )
    norm_records = json.loads(
        (closeout_dir / "model" / "preprocess" / "norm_params.json").read_text(encoding="utf-8")
    )
    norm_params = {
        record["feature"]: (float(record["mean"]), float(record["std"]))
        for record in norm_records
    }
    return transform_rules, norm_params


def build_daily_features(hourly_df: pd.DataFrame) -> pd.DataFrame:
    hourly = hourly_df.copy()
    hourly["time"] = pd.to_datetime(hourly["time"])
    hourly = hourly.sort_values("time").set_index("time")

    daily_frames = []
    for hourly_col, daily_prefix in HOURLY_TO_DAILY_MAP.items():
        series = pd.to_numeric(hourly[hourly_col], errors="coerce")
        daily_frames.extend(
            [
                series.resample("D").max().rename(f"{daily_prefix}_max"),
                series.resample("D").mean().rename(f"{daily_prefix}_mean"),
                series.resample("D").min().rename(f"{daily_prefix}_min"),
            ]
        )

    precipitation = pd.to_numeric(hourly["precipitation"], errors="coerce")
    daily_frames.append(precipitation.resample("D").sum().rename("precipitation_sum"))

    daily = pd.concat(daily_frames, axis=1)
    return daily.dropna().sort_index()


def apply_preprocess(
    daily_df: pd.DataFrame,
    feature_order: list[str],
    transform_rules: dict,
    norm_params: dict[str, tuple[float, float]],
) -> pd.DataFrame:
    work = daily_df.copy()

    for feature_name, rule in transform_rules.items():
        if "log1p(precipitation_sum)" in str(rule):
            base = pd.to_numeric(work["precipitation_sum"], errors="coerce").fillna(0.0).clip(lower=0.0)
            work[feature_name] = np.log1p(base)
        elif "log1p(rain_sum)" in str(rule) and "rain_sum" in work.columns:
            base = pd.to_numeric(work["rain_sum"], errors="coerce").fillna(0.0).clip(lower=0.0)
            work[feature_name] = np.log1p(base)

    for z_feature in feature_order:
        raw_feature = z_feature[:-2]
        mean, std = norm_params[raw_feature]
        base = pd.to_numeric(work[raw_feature], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(mean)
        work[z_feature] = (base - mean) / (std if std != 0 else 1.0)

    return work


def batched_predict_probs(model: torch.nn.Module, windows: np.ndarray, batch_size: int) -> np.ndarray:
    probs: list[np.ndarray] = []
    with torch.inference_mode():
        for start in range(0, len(windows), batch_size):
            batch = torch.tensor(windows[start:start + batch_size], dtype=torch.float32)
            batch_probs = predict_prob(model, batch).detach().cpu().numpy().reshape(-1)
            probs.append(batch_probs)
    if not probs:
        return np.empty((0,), dtype=np.float32)
    return np.concatenate(probs, axis=0)


def load_hourly_station_data(station_file: Path) -> pd.DataFrame | None:
    hourly = pd.read_csv(station_file, usecols=REQUIRED_HOURLY_COLS)
    missing = [col for col in REQUIRED_HOURLY_COLS if col not in hourly.columns]
    if missing:
        print(f"Skipping {station_file.name}: missing columns {missing}")
        return None
    return hourly


def predict_for_station(
    station_file: Path,
    model: torch.nn.Module,
    feature_order: list[str],
    time_steps: int,
    rel_day_end: int,
    transform_rules: dict,
    norm_params: dict[str, tuple[float, float]],
) -> list[dict]:
    try:
        station_id, station_name, lat, lon = station_file.stem.split("_")
    except ValueError:
        print(f"Skipping malformed station file name: {station_file.name}")
        return []

    hourly = load_hourly_station_data(station_file)
    if hourly is None:
        return []

    daily = build_daily_features(hourly)
    if len(daily) < time_steps:
        return []

    prepared = apply_preprocess(daily, feature_order, transform_rules, norm_params)

    window_start_bound = BACKFILL_START_DATE - timedelta(days=time_steps + abs(rel_day_end) - 1)
    window_end_bound = BACKFILL_END_DATE - timedelta(days=abs(rel_day_end))
    prepared = prepared.loc[(prepared.index >= window_start_bound) & (prepared.index <= window_end_bound)]
    if len(prepared) < time_steps:
        return []

    features = prepared[feature_order].to_numpy(dtype=np.float32)
    window_count = len(features) - time_steps + 1
    if window_count <= 0:
        return []

    windows = np.stack([features[start:start + time_steps] for start in range(window_count)], axis=0)
    probs = batched_predict_probs(model, windows, batch_size=INFER_BATCH_SIZE)

    rows: list[dict] = []
    for start, prob in enumerate(probs):
        predict_date = (prepared.index[start + time_steps - 1] + timedelta(days=abs(rel_day_end))).normalize()
        if predict_date < BACKFILL_START_DATE or predict_date > BACKFILL_END_DATE:
            continue

        rows.append(
            {
                STATION_ID_COL: station_id,
                STATION_NAME_COL: station_name,
                "lat": float(lat),
                "lon": float(lon),
                DATE_COL: predict_date.strftime("%Y-%m-%d"),
                "BlastGAT": round(float(prob), 10),
            }
        )

    return rows


def flush_pending_rows(pending_rows: dict[str, list[dict]]) -> None:
    for date_str, rows in sorted(pending_rows.items()):
        if not rows:
            continue

        out_path = OUTPUT_DIR / f"{date_str.replace('-', '')}_BlastGAT.csv"
        batch_df = pd.DataFrame(rows)
        if out_path.exists():
            existing = pd.read_csv(out_path)
            combined = pd.concat([existing, batch_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=[STATION_ID_COL], keep="last")
        else:
            combined = batch_df

        combined[DATE_COL] = pd.to_datetime(combined[DATE_COL]).dt.strftime("%Y-%m-%d")
        combined.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"Saved {len(combined)} records to {out_path}")


def main() -> None:
    loaded = load_closeout_model(CLOSEOUT_DIR, device="cpu")
    model = loaded["model"]
    model.eval()
    metadata = loaded["metadata"]
    feature_order = metadata["feature_selection"]["selected_z_features"]
    time_steps = int(metadata["window"]["time_steps"])
    rel_day_end = int(metadata["window"]["rel_day_end"])
    transform_rules, norm_params = load_preprocess_contract(CLOSEOUT_DIR)

    pending_rows: dict[str, list[dict]] = {}
    processed_station_count = 0
    emitted_row_count = 0
    for station_file in sorted(ERA5_INPUT_DIR.glob("*.csv")):
        print(f"Processing {station_file.name.split('_')[0]}")
        station_rows = predict_for_station(
            station_file=station_file,
            model=model,
            feature_order=feature_order,
            time_steps=time_steps,
            rel_day_end=rel_day_end,
            transform_rules=transform_rules,
            norm_params=norm_params,
        )
        if not station_rows:
            continue

        emitted_row_count += len(station_rows)
        processed_station_count += 1
        for row in station_rows:
            pending_rows.setdefault(row[DATE_COL], []).append(row)

        if processed_station_count % FLUSH_EVERY_STATIONS == 0:
            flush_pending_rows(pending_rows)
            pending_rows.clear()

    if emitted_row_count == 0:
        print("No BlastGAT samples in selected BACKFILL window.")
        raise SystemExit(0)

    if pending_rows:
        flush_pending_rows(pending_rows)


if __name__ == "__main__":
    main()

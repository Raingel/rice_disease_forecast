"""Run county crop scenarios from transplanting; retain every model state."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

if __package__:
    from .crop_calendar import CALENDAR_URL, STATIONS_URL, crop_dates, normalize_county, parse_calendar
    from .model import simulate_epirice_blb
else:
    from crop_calendar import CALENDAR_URL, STATIONS_URL, crop_dates, normalize_county, parse_calendar
    from model import simulate_epirice_blb

MODEL_NAME = "EPIRICE_BLB"
ROOT = Path(os.getenv("PIPELINE_ROOT", Path(__file__).resolve().parents[2]))
META_COLS = ["站號", "站名", "日期", "lat", "lon"]
HOURLY_COLS = ["temperature_2m", "relativehumidity_2m", "precipitation"]


def read_source(source: str) -> str:
    if not source.startswith(("https://", "http://")):
        return Path(source).read_text(encoding="utf-8-sig")
    for attempt in range(3):
        try:
            request = Request(source, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=90) as response:
                return response.read().decode("utf-8-sig")
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("Unreachable source retry state")


def load_stations(text: str) -> pd.DataFrame:
    stations = pd.read_csv(io.StringIO(text), dtype={"站號": str})
    required = {"站號", "站名", "緯度", "經度", "城市", "撤站日期"}
    if not required.issubset(stations.columns):
        raise ValueError(f"Missing station columns: {sorted(required - set(stations.columns))}")
    stations = stations.loc[stations["撤站日期"].isna()].copy()
    stations["county"] = stations["城市"].fillna("").map(normalize_county)
    stations = stations.dropna(subset=["站號", "站名", "緯度", "經度"])
    if stations["站號"].duplicated().any():
        raise ValueError("Active station metadata contains duplicate station IDs")
    return stations


def station_weather_files(folder: Path, stations: pd.DataFrame) -> dict[str, Path]:
    """Old filenames can survive coordinate updates; require current coordinates."""
    candidates = defaultdict(list)
    for path in sorted(folder.glob("*.csv")):
        parts = path.stem.split("_")
        if len(parts) < 4:
            continue
        try:
            candidates[parts[0]].append((path, float(parts[-2]), float(parts[-1])))
        except ValueError:
            continue
    found = {}
    for _, station in stations.iterrows():
        matches = [p for p, lat, lon in candidates[str(station["站號"])]
                   if abs(lat - float(station["緯度"])) <= 1e-6
                   and abs(lon - float(station["經度"])) <= 1e-6]
        if len(matches) > 1:
            # Renamed stations leave old-name files behind in the shared folder.
            # The downloader writes the live metadata name; never choose by mtime.
            current_name = [p for p in matches
                            if "_".join(p.stem.split("_")[1:-2]) == str(station["站名"])]
            if len(current_name) == 1:
                matches = current_name
            else:
                raise ValueError(f"Multiple current-coordinate weather files for {station['站號']}: {[p.name for p in matches]}")
        if matches:
            found[str(station["站號"])] = matches[0]
    return found


def daily_weather(path: Path) -> pd.DataFrame:
    hourly = pd.read_csv(path, usecols=["time", *HOURLY_COLS])
    hourly["time"] = pd.to_datetime(hourly["time"], errors="raise")
    if hourly["time"].dt.tz is not None:
        hourly["time"] = hourly["time"].dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    if not hourly["time"].dt.floor("h").eq(hourly["time"]).all():
        raise ValueError("Weather timestamps must fall on hourly boundaries")
    if hourly["time"].duplicated().any():
        raise ValueError("Duplicate hourly weather timestamps")
    for col in HOURLY_COLS:
        hourly[col] = pd.to_numeric(hourly[col], errors="coerce")
    hourly = hourly.replace([np.inf, -np.inf], np.nan).sort_values("time").set_index("time")
    if hourly.empty:
        raise ValueError("Empty hourly weather file")
    valid = hourly[HOURLY_COLS].notna().all(axis=1)
    valid &= hourly["relativehumidity_2m"].between(0, 100) & hourly["precipitation"].ge(0)
    hourly.loc[~valid, HOURLY_COLS] = np.nan
    daily = pd.DataFrame({
        "TEMP": hourly["temperature_2m"].resample("D").mean(),
        "RHUM": hourly["relativehumidity_2m"].resample("D").mean(),
        "RAIN": hourly["precipitation"].resample("D").sum(min_count=24),
    })
    complete = valid.resample("D").sum().eq(24)
    daily.loc[~complete, ["TEMP", "RHUM", "RAIN"]] = np.nan
    return daily


def simulate_crop(daily: pd.DataFrame, config: dict, start: date, harvest: date,
                  available_end: date) -> tuple[pd.DataFrame, date]:
    # Calendar harvest is the crop boundary; the supplied 120-day maximum stays fixed.
    end = min(start + timedelta(days=int(config["duration_days"]) - 1), harvest, available_end)
    if end < start:
        raise ValueError("No weather after transplanting")
    index = pd.date_range(start, end, freq="D")
    weather = daily.reindex(index).copy()
    if weather[["TEMP", "RHUM", "RAIN"]].isna().any().any():
        missing = weather.index[weather.isna().any(axis=1)]
        raise ValueError(f"Incomplete weather from crop start: {missing[0].date()} ({len(missing)} days)")
    weather["YYYYMMDD"] = weather.index.strftime("%Y-%m-%d")
    weather["DOY"] = weather.index.dayofyear
    weather = weather.reset_index(drop=True)
    run_config = dict(config, duration_days=len(weather))
    states = simulate_epirice_blb(weather, run_config)
    states["DOY"] = weather["DOY"]
    return states, end


def to_daily_output(states: pd.DataFrame, station: pd.Series, metadata: dict) -> pd.DataFrame:
    frame = pd.DataFrame(index=states.index)
    frame["站號"], frame["站名"] = str(station["站號"]), station["站名"]
    frame["日期"] = states["date"]
    frame["lat"], frame["lon"] = float(station["緯度"]), float(station["經度"])
    frame[MODEL_NAME] = states["intensity_active"]
    for col in states.columns:
        if col != "date":
            frame[f"{MODEL_NAME}_{col}"] = states[col]
    for key, value in metadata.items():
        frame[f"{MODEL_NAME}_{key}"] = value
    return frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather-dir", type=Path, default=Path(os.getenv("ERA5_INPUT_DIR", os.getenv("ERA5_OUTPUT_DIR", ROOT / "ERA5"))))
    parser.add_argument("--calendar", default=os.getenv("EPIRICE_CALENDAR_SOURCE", CALENDAR_URL))
    parser.add_argument("--station-list", default=os.getenv("EPIRICE_STATIONS_SOURCE", STATIONS_URL))
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(os.getenv("DATA_FOLDER", ROOT / "rice_blast_prediction/data")))
    parser.add_argument("--state-dir", type=Path, default=Path(os.getenv("EPIRICE_STATE_DIR", ROOT / "rice_blast_prediction/epirice_blb_states")))
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(ZoneInfo("Asia/Taipei")).date())
    parser.add_argument("--start-date", type=date.fromisoformat, default=os.getenv("EPIRICE_BACKFILL_START_DATE") or None)
    parser.add_argument("--end-date", type=date.fromisoformat, default=os.getenv("EPIRICE_BACKFILL_END_DATE") or None)
    parser.add_argument("--station-id", action="append", help="Optional small station subset for validation")
    return parser


def run(args: argparse.Namespace) -> dict:
    if bool(args.start_date) != bool(args.end_date):
        raise ValueError("Provide both start-date and end-date")
    start_date = args.start_date or args.as_of - timedelta(days=30)
    end_date = args.end_date or args.as_of + timedelta(days=15)
    if end_date < start_date:
        raise ValueError("end-date is before start-date")
    config_text = args.config.read_text(encoding="utf-8")
    config = json.loads(config_text)
    calendar_text, station_text = read_source(args.calendar), read_source(args.station_list)
    calendars, stations = parse_calendar(calendar_text), load_stations(station_text)
    if args.station_id:
        stations = stations[stations["站號"].isin(args.station_id)]
        missing_ids = set(args.station_id) - set(stations["站號"])
        if missing_ids:
            raise ValueError(f"Requested stations are not active: {sorted(missing_ids)}")
    files = station_weather_files(args.weather_dir, stations)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = args.state_dir / "inputs"
    inputs_dir.mkdir(exist_ok=True)
    for name, text in [("regional_calendar.csv", calendar_text), ("station_list.csv", station_text), ("config.json", config_text)]:
        (inputs_dir / name).write_text(text, encoding="utf-8" if name.endswith(".json") else "utf-8-sig")
    generated_at = datetime.now(ZoneInfo("Asia/Taipei")).isoformat()
    audit, frames = [], []
    years = range(start_date.year - 1, end_date.year + 1)
    output_columns = META_COLS + [MODEL_NAME]
    for _, station in stations.iterrows():
        sid, county = str(station["站號"]), station["county"]
        calendar = calendars.get(county)
        if calendar is None or not calendar["rice_area"]:
            audit.append({"station_id": sid, "county": county, "status": "no_rice_calendar"})
            continue
        crops = []
        for year in years:
            for entry in calendar["seasons"]:
                crop_start, harvest = crop_dates(entry, year)
                crop_end = min(crop_start + timedelta(days=int(config["duration_days"]) - 1), harvest)
                if crop_start <= end_date and crop_end >= start_date:
                    crops.append((year, entry["season"], crop_start, harvest))
        if not crops:
            audit.append({"station_id": sid, "county": county, "status": "outside_crop_period"})
            continue
        if sid not in files:
            audit.append({"station_id": sid, "county": county, "status": "missing_current_weather"})
            continue
        try:
            daily = daily_weather(files[sid])
        except (ValueError, OSError) as exc:
            audit.append({"station_id": sid, "status": "invalid_weather", "reason": str(exc)})
            continue
        usable_days = daily.dropna()
        if usable_days.empty:
            audit.append({"station_id": sid, "status": "no_complete_weather_days"})
            continue
        for year, season, crop_start, harvest in crops:
            scenario = f"{sid}_{year}_{1 if season == '一期作' else 2}"
            available_end = min(usable_days.index.max().date(), end_date)
            try:
                states, simulated_end = simulate_crop(daily, config, crop_start, harvest, available_end)
            except ValueError as exc:
                audit.append({"station_id": sid, "scenario_id": scenario, "status": "incomplete_crop_weather", "reason": str(exc)})
                continue
            meta = {"county": county, "season": season, "scenario_id": scenario,
                    "transplant_date": crop_start.isoformat(), "harvest_date": harvest.isoformat(),
                    "simulation_end_date": simulated_end.isoformat(), "model_version": config["model_version"],
                    "generated_at": generated_at}
            is_forecast = pd.to_datetime(states["date"]).dt.date > args.as_of
            full = states.copy()
            full["站號"], full["站名"] = sid, station["站名"]
            full["lat"], full["lon"] = float(station["緯度"]), float(station["經度"])
            for key, value in meta.items():
                full[key] = value
            full["is_forecast"] = is_forecast
            season_dir = args.state_dir / f"{year}_{1 if season == '一期作' else 2}"
            season_dir.mkdir(exist_ok=True)
            full.to_csv(season_dir / f"{sid}.csv", index=False, encoding="utf-8-sig")
            result = to_daily_output(states, station, meta)
            result[f"{MODEL_NAME}_is_forecast"] = is_forecast
            output_columns = list(result.columns)
            selected = result[pd.to_datetime(result["日期"]).dt.date.between(start_date, end_date)]
            if not selected.empty:
                frames.append(selected)
            audit.append({"station_id": sid, "scenario_id": scenario, "status": "ok", "state_rows": len(states), "daily_rows": len(selected)})
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=output_columns)
    if combined.duplicated(["站號", "日期"]).any():
        raise ValueError("Overlapping crop scenarios for the same station and date")
    # Rewrite the requested date interval, including empty days, so a skipped
    # scenario cannot leave a previous run's score in a daily model file.
    for day in pd.date_range(start_date, end_date, freq="D"):
        rows = combined[combined["日期"].eq(day.strftime("%Y-%m-%d"))]
        rows.to_csv(args.output_dir / f"{day.strftime('%Y%m%d')}_{MODEL_NAME}.csv", index=False, encoding="utf-8-sig")
    report = {"model_version": config["model_version"], "as_of": args.as_of.isoformat(),
              "generated_at": generated_at, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
              "source_urls": {"calendar": args.calendar, "stations": args.station_list},
              "selected_weather_files": {sid: path.name for sid, path in files.items()},
              "input_sha256": {"calendar": hashlib.sha256(calendar_text.encode()).hexdigest(),
                               "stations": hashlib.sha256(station_text.encode()).hexdigest(),
                               "config": hashlib.sha256(config_text.encode()).hexdigest()},
              "input_hash_encoding": "UTF-8 source text without BOM",
              "calendar_rice_counties": sum(c["rice_area"] for c in calendars.values()),
              "calendar_seasons": sum(len(c["seasons"]) for c in calendars.values()),
              "daily_rows": len(combined), "station_scenarios": audit}
    (args.state_dir / "run_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    errors = [a for a in audit if a["status"] not in ("ok", "no_rice_calendar", "outside_crop_period")]
    print(f"EPIRICE_BLB: {len(combined)} daily rows; {len(errors)} skipped weather scenarios; full states: {args.state_dir}")
    if errors and not frames:
        raise RuntimeError("All eligible EPIRICE scenarios lack valid weather; see run_audit.json")
    return report


if __name__ == "__main__":
    run(build_parser().parse_args())

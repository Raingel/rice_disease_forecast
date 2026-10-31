"""Run LB station scenarios, reconstructing state from transplanting.

Read the existing hourly weather folder; this runner never downloads weather.
Explicit stations/output directories keep the initial integration bounded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from models.epirice.crop_calendar import CALENDAR_URL, STATIONS_URL, crop_dates, parse_calendar
from models.epirice.sources import read_source
from models.epirice.stations import load_stations, station_weather_files
from models.epirice.weather import daily_weather
from models.EPIRICE_LB.model import CONFIG_PATH, simulate_epirice_lb
from models.EPIRICE_LB.output_contract import (
    MODEL_NAME, OUTPUT_SCHEMA_VERSION, PREFIX, PRIMARY_VALUE_FIELD, complete_daily,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def simulate_crop(daily, config, start, harvest, available_end):
    end = min(harvest, available_end, start + timedelta(days=int(config["duration_days"]) - 1))
    if end < start:
        raise ValueError("No weather after transplanting")
    weather = daily.reindex(pd.date_range(start, end, freq="D")).copy()
    missing = weather[["TEMP", "RHUM", "RAIN"]].isna().any(axis=1)
    if missing.any():
        first_missing = weather.index[missing][0]
        if first_missing.date() == start:
            raise ValueError(f"Incomplete weather from transplanting: {start}")
        # Preserve the valid prefix; never bridge a gap or initialize after it.
        weather = weather.loc[weather.index < first_missing].copy()
        end = weather.index[-1].date()
    weather["YYYYMMDD"] = weather.index.strftime("%Y-%m-%d")
    return simulate_epirice_lb(weather, config), end


def to_daily_output(full):
    meta = {"站號", "站名", "lat", "lon", "date"}
    frame = full.rename(columns={c: PREFIX + c for c in full if c not in meta}).rename(columns={"date": "日期"})
    frame[MODEL_NAME] = full[PRIMARY_VALUE_FIELD]
    return frame


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather-dir", type=Path, required=True)
    parser.add_argument("--calendar", default=CALENDAR_URL)
    parser.add_argument("--station-list", default=STATIONS_URL)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--station-id", action="append")
    selection.add_argument("--all-stations", action="store_true", help="Run all active stations for the daily pipeline")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--weather-provenance", type=Path, help="Optional existing weather request/source JSON to snapshot")
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(ZoneInfo("Asia/Taipei")).date())
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    return parser


def run(args):
    if bool(args.start_date) != bool(args.end_date):
        raise ValueError("Provide both start-date and end-date")
    start = args.start_date or args.as_of - timedelta(days=30)
    end = args.end_date or args.as_of + timedelta(days=15)
    if end < start:
        raise ValueError("end-date is before start-date")
    config_text = args.config.read_text(encoding="utf-8")
    config = json.loads(config_text)
    calendar_text, station_text = read_source(args.calendar), read_source(args.station_list)
    calendars, stations = parse_calendar(calendar_text), load_stations(station_text)
    if args.station_id:
        stations = stations[stations["站號"].isin(args.station_id)].copy()
        missing_ids = set(args.station_id) - set(stations["站號"])
        if missing_ids:
            raise ValueError(f"Requested stations are not active: {sorted(missing_ids)}")
    files = station_weather_files(args.weather_dir, stations)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    inputs = args.state_dir / "inputs"
    (inputs / "weather_daily").mkdir(parents=True, exist_ok=True)
    for name, text in [("regional_calendar.csv", calendar_text), ("station_list.csv", station_text), ("config.json", config_text)]:
        (inputs / name).write_text(text, encoding="utf-8")
    provenance = {}
    if args.weather_provenance:
        provenance = json.loads(args.weather_provenance.read_text(encoding="utf-8-sig"))
        (inputs / "weather_provenance.json").write_bytes(args.weather_provenance.read_bytes())
    generated = datetime.now(ZoneInfo("Asia/Taipei")).isoformat()
    audit, frames, failures, weather_inputs = [], [], {}, {}
    for _, station in stations.iterrows():
        sid, county = str(station["站號"]), station["county"]
        calendar = calendars.get(county)
        crops = []
        if calendar and calendar["rice_area"]:
            for year in range(start.year - 1, end.year + 1):
                for entry in calendar["seasons"]:
                    planting, harvest = crop_dates(entry, year)
                    last = min(harvest, planting + timedelta(days=int(config["duration_days"]) - 1))
                    if planting <= end and last >= start:
                        crops.append((year, entry["season"], planting, harvest))
        if not crops:
            status = "missing_crop_calendar" if calendar is None else "outside_crop_period"
            audit.append({"station_id": sid, "status": status})
            continue
        try:
            if sid not in files:
                raise ValueError("Missing current-coordinate hourly weather file")
            path = files[sid]
            weather_inputs[sid] = {"path": str(path.resolve()), "sha256": sha(path)}
            daily = daily_weather(path)
            daily_path = inputs / "weather_daily" / f"{sid}.csv"
            daily.to_csv(daily_path, index_label="date", encoding="utf-8-sig")
            weather_inputs[sid]["daily_sha256"] = sha(daily_path)
        except (ValueError, OSError) as exc:
            failures[sid] = str(exc)
            audit.append({"station_id": sid, "status": "invalid_weather", "reason": str(exc)})
            continue
        for year, season, planting, harvest in crops:
            scenario = f"{sid}_{year}_{1 if season == '一期作' else 2}"
            # Validate every day and stop before the first incomplete day.
            available_end = min(daily.index.max().date(), end)
            try:
                states, simulated_end = simulate_crop(daily, config, planting, harvest, available_end)
            except ValueError as exc:
                failures[sid] = str(exc)
                audit.append({"station_id": sid, "scenario_id": scenario,
                              "status": "incomplete_crop_weather", "reason": str(exc)})
                continue
            limit = min(harvest, planting + timedelta(days=int(config["duration_days"]) - 1))
            completion = "complete" if simulated_end == limit else "weather_endpoint" if simulated_end < end else "requested_endpoint"
            stop_reason = ""
            if completion == "weather_endpoint":
                next_day = simulated_end + timedelta(days=1)
                stop_reason = f"Weather ends on {simulated_end}; first unavailable or incomplete day: {next_day}; no extrapolated states"
            metadata = {"county": county, "season": season, "scenario_id": scenario,
                        "transplant_date": planting.isoformat(), "harvest_date": harvest.isoformat(),
                        "simulation_end_date": simulated_end.isoformat(), "simulation_status": completion,
                        "generated_at": generated, "primary_value_field": PRIMARY_VALUE_FIELD,
                        "output_schema_version": OUTPUT_SCHEMA_VERSION, "status": "ok", "missing_reason": "",
                        "weather_source": str(path.resolve()), "weather_sha256": weather_inputs[sid]["sha256"],
                        "config_sha256": sha(args.config)}
            metadata.update({k: config[k] for k in ("model_version", "parameter_version", "source_commit", "site_unit")})
            full = states.copy()
            for key, value in metadata.items():
                full[key] = value
            full["站號"], full["站名"] = sid, station["站名"]
            full["lat"], full["lon"] = float(station["緯度"]), float(station["經度"])
            full["is_forecast"] = pd.to_datetime(full["date"]).dt.date > args.as_of
            season_dir = args.state_dir / f"{year}_{1 if season == '一期作' else 2}"
            season_dir.mkdir(exist_ok=True)
            full.to_csv(season_dir / f"{sid}.csv", index=False, encoding="utf-8-sig")
            frame = to_daily_output(full)
            selected = frame[pd.to_datetime(frame["日期"]).dt.date.between(start, end)]
            frames.append(selected)
            audit.append({"station_id": sid, "scenario_id": scenario, "status": "ok",
                          "simulation_status": completion, "simulation_end_date": simulated_end.isoformat(),
                          "reason": stop_reason, "state_rows": len(full), "daily_rows": len(selected)})
            if completion == "weather_endpoint":
                failures[sid] = stop_reason
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["站號", "日期"])
    if combined.duplicated(["站號", "日期"]).any():
        raise ValueError("Overlapping crop scenarios")
    counts = Counter()
    for day in pd.date_range(start, end):
        rows = complete_daily(combined[combined["日期"].eq(day.strftime("%Y-%m-%d"))],
                              day.date(), stations, calendars, config, args.as_of, failures)
        counts.update(rows[PREFIX + "status"])
        rows.to_csv(args.output_dir / f"{day:%Y%m%d}_{MODEL_NAME}.csv", index=False, encoding="utf-8-sig")
    report = {"model_version": config["model_version"], "parameter_version": config["parameter_version"],
              "source_commit": config["source_commit"], "site_unit": config["site_unit"],
              "primary_value_field": PRIMARY_VALUE_FIELD, "output_schema_version": OUTPUT_SCHEMA_VERSION,
              "as_of": args.as_of.isoformat(), "generated_at": generated,
              "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                          "pandas": pd.__version__, "local_date_timezone": "Asia/Taipei"},
              "start_date": start.isoformat(), "end_date": end.isoformat(),
              "source_urls": {"calendar": args.calendar, "stations": args.station_list},
              "weather_inputs": weather_inputs, "weather_provenance": provenance,
              "input_sha256": {p.name: sha(p) for p in inputs.iterdir() if p.is_file()},
              "implementation_sha256": {str(p.relative_to(Path(__file__).resolve().parents[2])).replace('\\', '/'): sha(p)
                                         for p in [Path(__file__), Path(__file__).with_name("model.py"),
                                                   Path(__file__).with_name("output_contract.py"),
                                                   Path(__file__).parents[1] / "EPIRICE_BLB/model.py",
                                                   Path(__file__).parents[1] / "EPIRICE_BLB/predict.py",
                                                   Path(__file__).parents[1] / "EPIRICE_BLB/crop_calendar.py",
                                                   Path(__file__).parents[1] / "EPIRICE_BLB/output_contract.py"]},
              "daily_rows": sum(counts.values()), "model_rows": len(combined),
              "output_status_counts": dict(counts), "station_scenarios": audit}
    (args.state_dir / "run_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"{MODEL_NAME}: {report['daily_rows']} station-days, {len(combined)} modeled; states: {args.state_dir}")
    if failures and not frames:
        raise RuntimeError("No eligible crop has valid weather; see run_audit.json")
    return report


if __name__ == "__main__":
    run(build_parser().parse_args())

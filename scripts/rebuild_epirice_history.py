"""Checkpointed EPIRICE history rebuild; no changes to the rolling forecast."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models.EPIRICE_BLB.crop_calendar import CALENDAR_URL, STATIONS_URL, crop_dates, parse_calendar
from models.EPIRICE_BLB.predict import build_parser, daily_weather, load_stations, read_source, run
from models.EPIRICE_BLB.output_contract import PRIMARY_VALUE_FIELD, OUTPUT_SCHEMA_VERSION, complete_daily


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(folder, end, batch_size):
    if not date(2026, 1, 1) <= end <= date(2026, 12, 31):
        raise ValueError("End date must be in 2026")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    if end > today - timedelta(days=2):
        raise ValueError("History end must allow two days for archive availability")
    folder.mkdir(parents=True, exist_ok=True)
    calendar_text, station_text = read_source(CALENDAR_URL), read_source(STATIONS_URL)
    sources = {"regional_calendar.csv": calendar_text, "station_list.csv": station_text,
               "config.json": (ROOT / "models/EPIRICE_BLB/config.json").read_text(encoding="utf-8")}
    for name, text in sources.items():
        (folder / name).write_text(text, encoding="utf-8")
    config = json.loads(sources["config.json"])
    calendars, stations = parse_calendar(calendar_text), load_stations(station_text)
    stations = stations.sort_values("站號").reset_index(drop=True)
    scenarios, excluded = {}, []
    for _, station in stations.iterrows():
        sid, county = str(station["站號"]), station["county"]
        calendar = calendars.get(county)
        if not calendar or not calendar["rice_area"]:
            excluded.append({"station_id": sid, "county": county, "reason": "no_rice_calendar"})
            continue
        for year in (2025, 2026):
            for entry in calendar["seasons"]:
                start, harvest = crop_dates(entry, year)
                finish = min(start + timedelta(days=config["duration_days"] - 1), harvest, end)
                if finish < start:
                    continue
                season = 1 if entry["season"] == "一期作" else 2
                scenarios[f"{sid}_{year}_{season}"] = {
                    "station_id": sid, "season_dir": f"{year}_{season}",
                    "start": start.isoformat(), "end": finish.isoformat(),
                    "rows": (finish - start).days + 1}
    ids = sorted({v["station_id"] for v in scenarios.values()})
    groups = [ids[i:i + batch_size] for i in range(0, len(ids), batch_size)]
    manifest = {"start": "2025-01-01", "end": end.isoformat(), "as_of": today.isoformat(),
                "model_version": config["model_version"], "active_stations": len(stations),
                "eligible_stations": len(ids), "excluded": excluded, "groups": groups,
                "expected_scenarios": scenarios,
                "source_urls": {"calendar": CALENDAR_URL, "stations": STATIONS_URL},
                "input_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sources.items()},
                "primary_value_field": PRIMARY_VALUE_FIELD, "output_schema_version": OUTPUT_SCHEMA_VERSION,
                "method": "Open-Meteo archive hourly reanalysis at current station coordinates; current calendar repeated for each year"}
    write_json(folder / "manifest.json", manifest)
    print(f"Prepared {len(stations)} active stations, {len(ids)} rice stations, {len(scenarios)} scenarios, {len(groups)} batches", flush=True)
    return manifest


def fetch_year(stations, year, end, folder):
    """A successful year is a checkpoint. A bounded retry never waits all night."""
    folder.mkdir(parents=True, exist_ok=True)
    checkpoint = folder / f"{year}.json"
    if checkpoint.exists():
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        if data["station_ids"] != stations["站號"].tolist() or data["end"] != end:
            raise ValueError("Checkpoint inputs differ")
        return data
    params = {"latitude": ",".join(stations["緯度"].astype(str)),
              "longitude": ",".join(stations["經度"].astype(str)),
              "start_date": f"{year}-01-01", "end_date": end,
              "hourly": "temperature_2m,relativehumidity_2m,precipitation",
              "timezone": "Asia/Singapore"}
    for attempt in range(5):
        try:
            response = requests.get("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=(20, 180))
            response.raise_for_status()
            payload = response.json()
            locations = payload if isinstance(payload, list) else [payload]
            if len(locations) != len(stations) or any("hourly" not in p for p in locations):
                raise ValueError("Weather API location count or hourly payload mismatch")
            data = {"station_ids": stations["站號"].tolist(), "end": end,
                    "url": response.url, "locations": locations}
            temp = checkpoint.with_suffix(".tmp")
            write_json(temp, data)
            temp.replace(checkpoint)
            print(f"Downloaded {year}: {len(stations)} locations", flush=True)
            return data
        except (requests.RequestException, ValueError) as exc:
            if attempt == 4:
                raise
            if isinstance(exc, requests.HTTPError) and exc.response.status_code not in (429, 500, 502, 503, 504):
                raise
            pause = min(30 * 2 ** attempt, 120)
            print(f"Weather retry {attempt + 1}/5 in {pause}s: {exc}", flush=True)
            time.sleep(pause)
    raise RuntimeError("Unreachable")


def batch(inputs, folder, index):
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    ids = manifest["groups"][index]
    stations = load_stations((inputs / "station_list.csv").read_text(encoding="utf-8"))
    stations = stations.set_index("站號").loc[ids].reset_index()
    yearly = [fetch_year(stations, 2025, "2025-12-31", folder / "checkpoint"),
              fetch_year(stations, 2026, manifest["end"], folder / "checkpoint")]
    weather = folder / "weather"
    weather.mkdir(parents=True, exist_ok=True)
    provenance = []
    for i, (_, station) in enumerate(stations.iterrows()):
        sid = str(station["站號"])
        hourly = pd.concat([pd.DataFrame(y["locations"][i]["hourly"]) for y in yearly], ignore_index=True)
        name = str(station["站名"]).replace("/", "_").replace("\\", "_")
        path = weather / f"{sid}_{name}_{float(station['緯度']):.6f}_{float(station['經度']):.6f}.csv"
        hourly.to_csv(path, index=False, encoding="utf-8-sig")
        daily = daily_weather(path)
        daily.index.name = "date"
        destination = folder / "result/weather_daily" / f"{sid}.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        daily.to_csv(destination, encoding="utf-8-sig")
        provenance.append({"station_id": sid, "hourly_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "request_coordinates": [float(station["緯度"]), float(station["經度"])],
                           "returned_coordinates": [[y["locations"][i]["latitude"], y["locations"][i]["longitude"]] for y in yearly],
                           "hourly_units": yearly[0]["locations"][i]["hourly_units"]})
    arguments = ["--weather-dir", str(weather), "--calendar", str(inputs / "regional_calendar.csv"),
                 "--station-list", str(inputs / "station_list.csv"), "--config", str(inputs / "config.json"),
                 "--output-dir", str(folder / "result/data"), "--state-dir", str(folder / "result/states"),
                 "--as-of", manifest["as_of"], "--start-date", manifest["start"], "--end-date", manifest["end"]]
    for sid in ids:
        arguments += ["--station-id", sid]
    audit = run(build_parser().parse_args(arguments))
    invalid = [s for s in audit["station_scenarios"] if s["status"] != "ok"]
    if invalid:
        raise RuntimeError(f"Batch incomplete: {invalid}")
    expected = {k: v for k, v in manifest["expected_scenarios"].items() if v["station_id"] in ids}
    verify_states(folder / "result/states", expected)
    write_json(folder / "result/weather_provenance.json", {"requests": [y["url"] for y in yearly], "stations": provenance})
    write_json(folder / "result/complete.json", {"batch": index, "station_ids": ids,
               "input_sha256": manifest["input_sha256"], "end": manifest["end"],
               "scenarios": len(expected), "daily_rows": audit["daily_rows"]})
    print(f"Batch {index}: verified {len(expected)} scenarios, {audit['daily_rows']} rows", flush=True)


def verify_states(folder, expected):
    actual = set()
    for path in folder.glob("20*_*/*.csv"):
        frame = pd.read_csv(path, dtype={"站號": str})
        scenario = f"{path.stem}_{path.parent.name}"
        actual.add(scenario)
        entry = expected[scenario]
        dates = pd.to_datetime(frame["date"])
        required = pd.date_range(entry["start"], entry["end"])
        if len(frame) != entry["rows"] or not dates.reset_index(drop=True).equals(pd.Series(required)):
            raise ValueError(f"Incomplete state dates: {scenario}")
        if frame["is_forecast"].any() or frame[["TEMP", "RHUM", "RAIN", "healthy_sites", "latent", "infectious", "removed"]].isna().any().any():
            raise ValueError(f"Invalid historical state: {scenario}")
    if actual != set(expected):
        raise ValueError(f"Missing state scenarios: {sorted(set(expected) - actual)}")


def merge(inputs, parts, output):
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for index, ids in enumerate(manifest["groups"]):
        result = parts / f"epirice-result-{index}"
        complete = json.loads((result / "complete.json").read_text(encoding="utf-8"))
        if complete["batch"] != index or complete["station_ids"] != ids or complete["input_sha256"] != manifest["input_sha256"] or complete["end"] != manifest["end"]:
            raise ValueError(f"Batch provenance mismatch: {index}")
        results.append(result)
    # Build only after every batch has a verified completion marker.
    if output.exists():
        raise ValueError("Merge destination must be new; do not overlay an old rebuild")
    output.mkdir(parents=True)
    shutil.copytree(inputs, output / "inputs")
    for index, result in enumerate(results):
        for subdir in ("states", "weather_daily"):
            shutil.copytree(result / subdir, output / subdir, dirs_exist_ok=True)
        for name in ("complete.json", "weather_provenance.json"):
            shutil.copy2(result / name, output / f"batch_{index}_{name}")
        shutil.copy2(result / "states/run_audit.json", output / f"batch_{index}_audit.json")
    verify_states(output / "states", manifest["expected_scenarios"])
    total, modeled_total, non_crop_total = 0, 0, 0
    stations = load_stations((inputs / "station_list.csv").read_text(encoding="utf-8-sig"))
    calendars = parse_calendar((inputs / "regional_calendar.csv").read_text(encoding="utf-8-sig"))
    config = json.loads((inputs / "config.json").read_text(encoding="utf-8"))
    (output / "data").mkdir()
    for day in pd.date_range(manifest["start"], manifest["end"]):
        filename = f"{day:%Y%m%d}_EPIRICE_BLB.csv"
        frames = [pd.read_csv(result / "data" / filename, dtype={"站號": str}) for result in results]
        frame = pd.concat(frames, ignore_index=True).sort_values("站號")
        frame = complete_daily(frame, day.date(), stations, calendars, config, date.fromisoformat(manifest["as_of"]))
        if frame.duplicated(["站號", "日期"]).any():
            raise ValueError(f"Duplicate station-day: {filename}")
        total += len(frame)
        modeled_total += int(frame.EPIRICE_BLB_status.eq("ok").sum())
        non_crop_total += int(frame.EPIRICE_BLB_status.eq("non_crop_period").sum())
        frame.to_csv(output / "data" / filename, index=False, encoding="utf-8-sig")
    expected_rows = sum(s["rows"] for s in manifest["expected_scenarios"].values())
    if modeled_total != expected_rows:
        raise ValueError(f"Daily/state row mismatch: {modeled_total} != {expected_rows}")
    report = {k: v for k, v in manifest.items() if k not in ("groups", "expected_scenarios")}
    report.update(status="complete", scenarios=len(manifest["expected_scenarios"]), daily_rows=total,
                  model_rows=modeled_total, non_crop_rows=non_crop_total,
                  primary_value_field=PRIMARY_VALUE_FIELD, output_schema_version=OUTPUT_SCHEMA_VERSION,
                  batches=len(results), skipped_weather_scenarios=0)
    write_json(output / "rebuild_audit.json", report)
    print(f"Verified archive: {len(results)} batches, {len(manifest['expected_scenarios'])} scenarios, {total} station-days", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "batch", "merge"])
    parser.add_argument("--inputs", type=Path, default=Path("rebuild-inputs"))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 10, 11))
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--batch-index", type=int, default=0)
    parser.add_argument("--work-dir", type=Path, default=Path("rebuild-batch"))
    parser.add_argument("--parts", type=Path, default=Path("rebuild-parts"))
    parser.add_argument("--output", type=Path, default=Path("rebuild-output"))
    args = parser.parse_args()
    if args.mode == "prepare":
        if not 1 <= args.batch_size <= 50:
            parser.error("Batch size must be between 1 and 50")
        prepare(args.inputs, args.end_date, args.batch_size)
    elif args.mode == "batch":
        batch(args.inputs, args.work_dir, args.batch_index)
    else:
        merge(args.inputs, args.parts, args.output)


if __name__ == "__main__":
    main()

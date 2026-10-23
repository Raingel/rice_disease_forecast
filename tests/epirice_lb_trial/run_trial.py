"""Prepare a bounded offline trial, fetch pinned R sources, or compare outputs."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent))
from epirice_lb_trial.candidate import crop_trajectory, merge_compact, simulate, station_day
from models.EPIRICE_BLB.crop_calendar import crop_dates, parse_calendar
from models.EPIRICE_BLB.predict import daily_weather

CASES = ("constant_wet", "boundary_cycle", "constant_dry")
COMPARE_COLUMNS = ["RcOpt", "Rc", "RcA", "RcT", "RcW", "healthy_sites", "latent",
                   "infectious", "removed", "rateinf_new_infections", "growth_new_sites",
                   "senescence_sites_rate", "healthy_fraction_C", "intensity_active", "AUDPC",
                   "AUDPC_cumulative", "total_sites", "senesced_total", "rtransfer_L_to_I",
                   "diseased_total", "latent_cohort_entries", "infectious_cohort_entries"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def output_folder(path):
    path = path.resolve()
    protected = [ROOT / p for p in ("models", "tests", "rice_blast_prediction", ".github", ".git")]
    if path == ROOT or any(path.is_relative_to(p) for p in protected):
        raise ValueError("Use a temporary output folder outside code and production data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def protected_hashes():
    files = list((ROOT / "models/EPIRICE_BLB").glob("*.py"))
    files += [ROOT / p for p in ("models/EPIRICE_BLB/config.json", "models/recent_forecast_organizer.py",
                                 "scripts/run_daily_pipeline.sh", ".github/workflows/daily-forecast.yml")]
    return {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in files if p.is_file()}


def make_weather(case):
    dates = pd.date_range("2026-02-01", periods=120, freq="D")
    weather = pd.DataFrame({"YYYYMMDD": dates.strftime("%Y-%m-%d"), "DOY": dates.dayofyear,
                            "TEMP": 20.0, "RHUM": 95.0, "RAIN": 0.0})
    if case == "constant_dry":
        weather["RHUM"] = 70.0
    elif case == "boundary_cycle":
        weather["TEMP"] = np.resize([8, 10, 15, 20, 25, 30, 35, 40, 45, 47], 120)
        weather["RHUM"] = np.resize([89.999, 90, 90.001, 60, 60, 60], 120)
        weather["RAIN"] = np.resize([0, 0, 0, 4.999, 5, 5.001], 120)
    elif case != "constant_wet":
        raise ValueError(case)
    return weather


def station_trial(folder):
    """Two synthetic crop seasons at one named station, without weather downloads."""
    station = {"站號": "C0G910", "站名": "花壇", "緯度": 24.032076, "經度": 120.549461}
    calendar_path = ROOT / "tests/fixtures/epirice_blb/regional_calendar.csv"
    calendar = parse_calendar(calendar_path.read_text(encoding="utf-8-sig"))["彰化縣"]
    target = folder / "station_trial"
    target.mkdir(exist_ok=True)
    records, counts = [], []
    for season in calendar["seasons"]:
        start, harvest = crop_dates(season, 2026)
        end = min(harvest, start + timedelta(days=119))
        hours = pd.date_range(start, datetime(end.year, end.month, end.day, 23), freq="h")
        hourly = pd.DataFrame({"time": hours, "temperature_2m": 20.0,
                               "relativehumidity_2m": 95.0, "precipitation": 0.0})
        key = "2026_1" if season["season"] == "一期作" else "2026_2"
        path = target / f"{key}_synthetic_hourly.csv"
        hourly.to_csv(path, index=False)
        daily = daily_weather(path)
        states = crop_trajectory(daily, start, harvest, end)
        meta = {"county": "彰化縣", "season": season["season"], "scenario_id": f"C0G910_{key}",
                "weather_source": "synthetic_hourly_trial_not_observed_weather",
                "simulation_end_date": states["date"].iloc[-1]}
        states.assign(**meta).to_csv(target / f"{key}_full_states.csv", index=False)
        # Three daily records demonstrate selecting an output window after full reconstruction.
        for day in pd.to_datetime(states["date"].iloc[-3:]).dt.date:
            frame = station_day(states, station, day, start, harvest, meta)
            frame.to_csv(target / f"{day:%Y%m%d}_EPIRICE_LB.csv", index=False, encoding="utf-8-sig")
            records.append(frame)
        counts.append({"scenario": key, "start": start.isoformat(), "harvest": harvest.isoformat(),
                       "simulation_end": states["date"].iloc[-1], "state_rows": len(states)})
    leaf = pd.concat(records, ignore_index=True)
    # Deliberate test placeholders, not predictions from the production BLB model.
    base = leaf[["站號", "站名", "日期", "lat", "lon"]].assign(EPIRICE_BLB=0.25, EPIRICE_BLB_status="ok")
    merged = merge_compact(base, leaf)
    merged.to_csv(target / "C0G910_compact_trial.csv", index=False, encoding="utf-8-sig")
    return {"station": "C0G910", "weather_kind": "synthetic", "scenarios": counts,
            "daily_model_files": len(records), "merged_rows": len(merged),
            "merged_columns": list(merged.columns), "BLB_values_are_test_placeholders": True,
            "calendar_sha256": sha(calendar_path)}


def prepare(folder):
    inputs, candidate = folder / "inputs", folder / "python"
    inputs.mkdir(exist_ok=True)
    candidate.mkdir(exist_ok=True)
    for case in CASES:
        weather = make_weather(case)
        weather.to_csv(inputs / f"{case}.csv", index=False)
        simulate(weather).to_csv(candidate / f"{case}.csv", index=False)
    write_json(folder / "prepared.json", {
        "status": "prepared_R_NOT_RUN", "cases": list(CASES), "days_per_case": 120,
        "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
        "candidate_sha256": sha(HERE / "candidate.py"), "config_sha256": sha(HERE / "config.json"),
        "upstream_manifest_sha256": sha(HERE / "upstream_manifest.json"),
        "protected_source_hashes": protected_hashes(), "station_trial": station_trial(folder),
        "source_commit": json.loads((HERE / "upstream_manifest.json").read_text())["commit"],
        "field_forecast_skill_validated": False})
    for stale in ("comparison.json", "failure.json"):
        (folder / stale).unlink(missing_ok=True)
    print("Prepared 3 x 120 synthetic days and a two-season single-station trial; R parity pending.")


def fetch_sources(folder):
    manifest = json.loads((HERE / "upstream_manifest.json").read_text())
    for path, expected in manifest["files"].items():
        target = folder / "upstream" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha(target) == expected:
            continue
        url = ("https://codeberg.org/api/v1/repos/adamhsparks/epicrop/contents/" + path
               + "?ref=" + manifest["commit"])
        for attempt in range(3):
            try:
                request = Request(url, headers={"User-Agent": "EPIRICE-LB-reproduction-trial"})
                with urlopen(request, timeout=30) as response:
                    result = json.load(response)
                content = base64.b64decode(result["content"], validate=False)
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ValueError(f"Pinned upstream hash mismatch: {path}")
                target.write_bytes(content)
                break
            except (OSError, ValueError, KeyError):
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
    write_json(folder / "upstream_verified.json", manifest)
    print("Verified all five upstream files against pinned commit and SHA-256.")


def compare(folder):
    (folder / "comparison.json").unlink(missing_ok=True)
    for case in CASES:
        path = folder / "R" / f"{case}.csv"
        if not path.is_file():
            raise ValueError(f"Original R execution is required; missing {path}")
    prepared = json.loads((folder / "prepared.json").read_text(encoding="utf-8"))
    for path, expected in prepared["protected_source_hashes"].items():
        if sha(ROOT / path) != expected:
            raise ValueError(f"Existing production source changed: {path}")
    for key, path in (("candidate_sha256", HERE / "candidate.py"), ("config_sha256", HERE / "config.json"),
                      ("upstream_manifest_sha256", HERE / "upstream_manifest.json")):
        if sha(path) != prepared[key]:
            raise ValueError(f"Trial input changed since prepare: {path}")
    manifest = json.loads((HERE / "upstream_manifest.json").read_text())
    for path, expected in manifest["files"].items():
        if sha(folder / "upstream" / path) != expected:
            raise ValueError(f"Original R source was changed: {path}")
    report = {"status": "passed", "absolute_tolerance": 1e-6, "relative_tolerance": 0,
              "source_commit": manifest["commit"], "cases": {},
              "protected_sources_unchanged": True, "field_forecast_skill_validated": False}
    for case in CASES:
        path = folder / "R" / f"{case}.csv"
        original = pd.read_csv(path, float_precision="round_trip")
        python = pd.read_csv(folder / "python" / f"{case}.csv", float_precision="round_trip")
        inputs = pd.read_csv(folder / "inputs" / f"{case}.csv")
        regenerated = simulate(inputs)
        pd.testing.assert_frame_equal(python, regenerated, check_dtype=False, check_exact=True)
        if len(original) != 120 or len(python) != 120:
            raise ValueError("Both languages must run all 120 days")
        if original["date"].tolist() != python["date"].tolist():
            raise ValueError("R and Python target dates differ")
        if original["simday"].tolist() != python["simday"].tolist():
            raise ValueError("R and Python simulation days differ")
        errors = {}
        for column in COMPARE_COLUMNS:
            left, right = python[column].to_numpy(float), original[column].to_numpy(float)
            if not np.isfinite(left).all() or not np.isfinite(right).all():
                raise ValueError(f"Non-finite values: {case}/{column}")
            delta = np.abs(left - right)
            errors[column] = {"max_absolute_error": float(delta.max()),
                              "max_error_simday": int(delta.argmax()) + 1}
            np.testing.assert_allclose(left, right, rtol=0, atol=1e-6, err_msg=f"{case}/{column}")
        report["cases"][case] = {"rows": len(python), "columns": errors}
    report["R_environment"] = (folder / "R/session_info.txt").read_text(encoding="utf-8")
    write_json(folder / "comparison.json", report)
    print(json.dumps({"status": "passed", "R_vs_Python_cases": 3, "days_per_case": 120,
                      "numeric_columns": len(COMPARE_COLUMNS), "absolute_tolerance": 1e-6,
                      "field_forecast_skill_validated": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "fetch-source", "compare"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = output_folder(args.output)
    try:
        {"prepare": prepare, "fetch-source": fetch_sources, "compare": compare}[args.mode](destination)
    except Exception as exc:
        write_json(destination / "failure.json", {"stage": args.mode, "error": str(exc)})
        raise

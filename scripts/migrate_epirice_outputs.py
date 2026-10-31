"""Convert saved outputs without rerunning weather/model or touching raw states."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models.epirice.crop_calendar import parse_calendar
from models.epirice.stations import load_stations
from models.EPIRICE_BLB.output_contract import (
    MODEL_NAME, PRIMARY_VALUE_FIELD, OUTPUT_SCHEMA_VERSION, PREFIX,
    complete_daily, merge_station_frames,
)


def sources(inputs):
    return (load_stations((inputs / "station_list.csv").read_text(encoding="utf-8-sig")),
            parse_calendar((inputs / "regional_calendar.csv").read_text(encoding="utf-8-sig")),
            json.loads((inputs / "config.json").read_text(encoding="utf-8")))


def restore_recent(folder, source_ref):
    """Fetch only station CSVs at an immutable ref and verify Git blob hashes."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_ref):
        raise ValueError("Restore source must be an immutable commit SHA")
    response = requests.get(
        "https://api.github.com/repos/Raingel/rice_disease_forecast/contents/rice_blast_prediction/recent_daily_by_station",
        params={"ref": source_ref}, timeout=(15, 60))
    response.raise_for_status()
    entries = response.json()
    if not isinstance(entries, list) or not 0 < len(entries) < 1000:
        raise ValueError("Incomplete station-file inventory")
    if any(e["type"] != "file" or not re.fullmatch(r"[A-Za-z0-9_-]+\.csv", e["name"]) for e in entries):
        raise ValueError("Unexpected station-file path")
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    local = threading.local()

    def fetch(entry):
        if not hasattr(local, "session"):
            local.session = requests.Session()
        url = f"https://raw.githubusercontent.com/Raingel/rice_disease_forecast/{source_ref}/rice_blast_prediction/recent_daily_by_station/{entry['name']}"
        for attempt in range(3):
            try:
                result = local.session.get(url, timeout=(15, 45))
                result.raise_for_status()
                content = result.content
                sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
                if sha != entry["sha"]:
                    raise ValueError(f"Git blob hash mismatch: {entry['name']}")
                (folder / entry["name"]).write_bytes(content)
                return
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, _ in enumerate(pool.map(fetch, entries), 1):
            if index % 100 == 0:
                print(f"Restored and hash-verified {index}/{len(entries)} original station tables", flush=True)
    keep = {entry["name"] for entry in entries}
    for path in folder.glob("*.csv"):
        if path.name not in keep:
            if path.resolve().parent != folder:
                raise ValueError("Unexpected restore cleanup path")
            path.unlink()
    return len(entries)


def save_verified(before, after, path):
    """Every pre-existing raw model output must survive, including empty cells."""
    left = before.set_index(["站號", "日期"])
    right = after.set_index(["站號", "日期"])
    original = [c for c in before if c.startswith(PREFIX) and c not in
                (PREFIX + "status", PREFIX + "primary_value_field", PREFIX + "output_schema_version")]
    for col in original:
        pd.testing.assert_series_equal(left[col].reset_index(drop=True), right.loc[left.index, col].reset_index(drop=True),
                                       check_dtype=False, check_names=False)
    valid = after[PREFIX + "status"].eq("ok")
    pd.testing.assert_series_equal(after.loc[valid, MODEL_NAME], after.loc[valid, PREFIX + PRIMARY_VALUE_FIELD], check_names=False, check_dtype=False)
    non_crop = after[PREFIX + "status"].eq("non_crop_period")
    if not after.loc[non_crop, MODEL_NAME].eq(-99).all():
        raise ValueError(f"Incorrect non-crop sentinel: {path}")
    missing = ~valid & ~non_crop
    if not after.loc[missing, MODEL_NAME].isna().all():
        raise ValueError(f"Missing weather/model output disguised as zero/sentinel: {path}")
    after.to_csv(path, index=False, encoding="utf-8-sig")


def migrate_daily(folder, inputs, as_of):
    stations, calendars, config = sources(inputs)
    counts = Counter()
    files = sorted(folder.glob("*_EPIRICE_BLB.csv"))
    for index, path in enumerate(files):
        day = datetime.strptime(path.name[:8], "%Y%m%d").date()
        before = pd.read_csv(path, dtype={"站號": str})
        after = complete_daily(before, day, stations, calendars, config, as_of)
        save_verified(before, after, path)
        counts.update(after[PREFIX + "status"].value_counts().to_dict())
        if (index + 1) % 100 == 0:
            print(f"{folder}: converted {index + 1}/{len(files)} daily files", flush=True)
    return {"files": len(files), "rows": sum(counts.values()), "status_counts": dict(counts)}


def migrate_recent(recent, daily, inputs, as_of):
    stations, calendars, config = sources(inputs)
    paths = [p for p in recent.glob("*.csv") if p.name != "station_list.csv"]
    if not paths:
        return {"files": 0, "rows": 0}
    before = pd.concat([pd.read_csv(p, dtype={"站號": str}) for p in paths], ignore_index=True)
    before["日期"] = pd.to_datetime(before["日期"]).dt.strftime("%Y-%m-%d")
    frames = []
    for day, base in before.groupby("日期", sort=True):
        within_window = as_of - timedelta(days=30) <= date.fromisoformat(day) <= as_of + timedelta(days=29)
        selected_stations = stations if within_window else stations[stations["站號"].isin(base["站號"])]
        cols = [c for c in base if c in ["站號", "站名", "日期", "lat", "lon"] or c == MODEL_NAME or c.startswith(PREFIX)]
        fallback = complete_daily(base[cols], day, selected_stations, calendars, config, as_of)
        path = daily / f"{day.replace('-', '')}_EPIRICE_BLB.csv"
        if path.exists():
            epi = pd.read_csv(path, dtype={"站號": str})
            if not within_window:
                epi = epi[epi["站號"].isin(base["站號"])]
            epi = pd.concat([epi, fallback[~fallback["站號"].isin(epi["站號"])]], ignore_index=True)
        else:
            epi = fallback
        frames.append(merge_station_frames(base, epi))
    after = pd.concat(frames, ignore_index=True).sort_values(["站號", "日期"])
    # Other models' historical values must not change during this migration.
    old = before.set_index(["站號", "日期"]).sort_index()
    new = after.set_index(["站號", "日期"]).sort_index()
    for col in before:
        if col in ["站號", "站名", "日期", "lat", "lon"] or col == MODEL_NAME or col.startswith(PREFIX):
            continue
        pd.testing.assert_series_equal(old[col].reset_index(drop=True), new.loc[old.index, col].reset_index(drop=True),
                                       check_dtype=False, check_names=False)
    for sid, frame in after.groupby("站號"):
        frame.to_csv(recent / f"{sid}.csv", index=False, encoding="utf-8-sig")
    after[["站號", "站名", "lon", "lat"]].drop_duplicates("站號").to_csv(recent / "station_list.csv", index=False, encoding="utf-8-sig")
    return {"files": after["站號"].nunique(), "rows": len(after),
            "added_station_days": len(after) - len(before), "other_model_values_unchanged": True,
            "station_expansion_start": (as_of - timedelta(days=30)).isoformat(),
            "station_expansion_end": (as_of + timedelta(days=29)).isoformat()}


def migrate(root):
    root = Path(root)
    rolling_inputs = root / "epirice_blb_states/inputs"
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    report = {"primary_value_field": PRIMARY_VALUE_FIELD, "output_schema_version": OUTPUT_SCHEMA_VERSION,
              "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
              "non_crop_value": -99, "raw_state_files_modified": 0, "weather_downloads": 0,
              "high_risk_summary_modified": False, "archives": {}}
    if os.getenv("EPIRICE_RECENT_BASELINE"):
        report["recent_baseline_commit"] = os.environ["EPIRICE_RECENT_BASELINE"]
    report["rolling_daily"] = migrate_daily(root / "data", rolling_inputs, today)
    for archive in sorted((root / "epirice_blb_history").glob("*")):
        if not (archive / "data").is_dir():
            continue
        audit_path = archive / "rebuild_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        cutoff = date.fromisoformat(audit.get("as_of", audit["end"]))
        result = migrate_daily(archive / "data", archive / "inputs", cutoff)
        expected_model_rows = audit.get("model_rows", audit.get("daily_rows"))
        if expected_model_rows is not None and expected_model_rows != result["status_counts"].get("ok", 0):
            raise ValueError(f"Historical modeled-row coverage changed: {archive}")
        report["archives"][archive.name] = result
        audit.update(primary_value_field=PRIMARY_VALUE_FIELD, output_schema_version=OUTPUT_SCHEMA_VERSION,
                     daily_rows=result["rows"], model_rows=result["status_counts"].get("ok", 0),
                     output_status_counts=result["status_counts"])
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    report["recent_by_station"] = migrate_recent(root / "recent_daily_by_station", root / "data", rolling_inputs, today)
    destination = root / "epirice_output_migration.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction-root", type=Path, default=ROOT / "rice_blast_prediction")
    migrate(parser.parse_args().prediction_root)

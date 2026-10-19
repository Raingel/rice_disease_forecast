"""Publish historical daily EPIRICE files and annual station-wide validation CSVs."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models.EPIRICE_BLB.output_contract import (
    KEYS, METADATA, MODEL_NAME, OUTPUT_SCHEMA_VERSION, PRIMARY_VALUE_FIELD, PREFIX,
    merge_station_frames,
)

MODELS = ["BlastGRU-TW", "BlastDT2", "BlastLSTLS", "BLBTSLS", "BlastTF", "BlastGAT", "BLASTAM"]


def read_frame(path, day, value_col, reference=None, diagnostics=None):
    frame = pd.read_csv(path, dtype={"站號": str})
    required = {"站號", "日期", value_col}
    if not required.issubset(frame):
        raise ValueError(f"Unexpected historical model columns: {path}")
    if frame["站號"].isna().any() or frame["站號"].str.strip().eq("").any():
        raise ValueError(f"Missing historical station ID: {path}")
    frame["日期"] = pd.to_datetime(frame["日期"], format="mixed", errors="raise").dt.strftime("%Y-%m-%d")
    if not frame.empty and not frame["日期"].eq(day).all():
        raise ValueError(f"Wrong target dates: {path}")
    status = value_col + "_history_status"
    frame[status] = "ok"
    if frame.duplicated(KEYS).any():
        result = []
        duplicated = frame.duplicated(KEYS, keep=False)
        result.append(frame.loc[~duplicated])
        for (sid, target_date), group in frame.loc[duplicated].groupby(KEYS, sort=False):
            nonempty = group[group[value_col].notna()]
            if nonempty[value_col].nunique() <= 1:
                result.append(nonempty.iloc[:1] if len(nonempty) else group.iloc[:1])
                continue
            if reference is None:
                raise ValueError(f"Conflicting historical model station-days: {path}")
            selected = group.iloc[:1].copy()
            selected[value_col] = float("nan")
            selected[status] = "ambiguous_coordinate_variants"
            if sid in reference.index and {"lat", "lon"}.issubset(group):
                point = reference.loc[sid]
                matched = nonempty[(nonempty["lat"] - point["lat"]).abs().le(1e-6)
                                   & (nonempty["lon"] - point["lon"]).abs().le(1e-6)]
                if matched[value_col].nunique() == 1:
                    selected = matched.iloc[:1].copy()
                    selected[status] = "current_coordinate_variant"
            if diagnostics is not None:
                diagnostics.append({"file": path.name, "station_id": sid, "date": target_date,
                                    "selected_status": selected[status].iloc[0],
                                    "variants": group.drop(columns=status).astype(object).where(group.drop(columns=status).notna(), None).to_dict("records")})
            result.append(selected)
        frame = pd.concat(result, ignore_index=True)
    return frame


def integrate_year(prediction, year, rebuilt=None):
    prediction = Path(prediction)
    if rebuilt is not None:
        target = prediction / "epirice_blb_history" / str(year)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rename(target.with_name(f"{year}-previous"))
        shutil.copytree(rebuilt, target)
        archive = target
    else:
        existing_year = prediction / "epirice_blb_history" / str(year)
        archive = existing_year if existing_year.exists() else prediction / "epirice_blb_history/2025_2026"
    simulation = json.loads((archive / "rebuild_audit.json").read_text(encoding="utf-8"))
    daily = prediction / "data"
    daily.mkdir(parents=True, exist_ok=True)
    annual = prediction / "history_daily_by_station" / str(year)
    if annual.exists():
        annual.rename(annual.with_name(f"{year}-previous"))
    annual.mkdir(parents=True, exist_ok=True)
    frames, source_files, counters, variants = [], [], {m: 0 for m in MODELS}, []
    dates, model_rows, non_crop_rows, preserved_existing, coordinate_mismatches = [], 0, 0, 0, 0
    for source in sorted((archive / "data").glob(f"{year}*_EPIRICE_BLB.csv")):
        day = pd.to_datetime(source.name[:8], format="%Y%m%d").strftime("%Y-%m-%d")
        canonical = daily / source.name
        # Reuse already-published recent outputs in 2025/2026 rather than rolling them back.
        if rebuilt is None and canonical.exists():
            preserved_existing += 1
        else:
            shutil.copy2(source, canonical)
        epi = read_frame(canonical, day, MODEL_NAME)
        if not epi[PREFIX + "output_schema_version"].eq(OUTPUT_SCHEMA_VERSION).all():
            raise ValueError(f"Old EPIRICE schema: {canonical}")
        valid = epi[PREFIX + "status"].eq("ok")
        pd.testing.assert_series_equal(epi.loc[valid, MODEL_NAME], epi.loc[valid, PREFIX + PRIMARY_VALUE_FIELD], check_names=False)
        model_rows += int(valid.sum())
        non_crop_rows += int(epi[PREFIX + "status"].eq("non_crop_period").sum())
        # Preserve the actual EPIRICE input coordinates when older models use another station position.
        epi[PREFIX + "input_lat"] = epi["lat"]
        epi[PREFIX + "input_lon"] = epi["lon"]
        reference = epi.set_index("站號")[["lat", "lon"]]
        merged = None
        for model in MODELS:
            path = daily / f"{source.name[:8]}_{model}.csv"
            if not path.exists():
                continue
            frame = read_frame(path, day, model, reference, variants)
            counters[model] += len(frame)
            source_files.append({"path": str(path.relative_to(prediction)),
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": len(frame)})
            selected = KEYS + [c for c in METADATA if c in frame] + [model, model + "_history_status"]
            if merged is None:
                merged = frame[selected]
            else:
                joined = merged.merge(frame[selected], on=KEYS, how="outer", suffixes=("", "_incoming"), validate="one_to_one")
                for col in METADATA:
                    other = col + "_incoming"
                    if other in joined:
                        joined[col] = joined[col].combine_first(joined.pop(other))
                merged = joined
            # Every source value must remain keyed to the same station and target date.
            check = merged.set_index(KEYS).loc[frame.set_index(KEYS).index, model].reset_index(drop=True)
            pd.testing.assert_series_equal(check, frame[model].reset_index(drop=True), check_dtype=False, check_names=False)
        if merged is None:
            merged = epi[KEYS + METADATA].iloc[:0]
        merged = merge_station_frames(merged, epi)
        mismatch = (merged["lat"] - merged[PREFIX + "input_lat"]).abs().gt(1e-6) | (merged["lon"] - merged[PREFIX + "input_lon"]).abs().gt(1e-6)
        coordinate_mismatches += int(mismatch.sum())
        absent = merged[PREFIX + "status"].isna()
        merged.loc[absent, PREFIX + "status"] = "no_epirice_station_metadata"
        merged.loc[absent, PREFIX + "output_schema_version"] = OUTPUT_SCHEMA_VERSION
        merged.loc[absent, PREFIX + "primary_value_field"] = PRIMARY_VALUE_FIELD
        frames.append(merged)
        dates.append(day)
    if not frames:
        raise ValueError(f"No EPIRICE dates available for {year}")
    full = pd.concat(frames, ignore_index=True).sort_values(KEYS)
    for model in MODELS:
        if model not in full:
            full[model] = float("nan")
        status = model + "_history_status"
        if status not in full:
            full[status] = "missing_source_or_station"
        else:
            full[status] = full[status].fillna("missing_source_or_station")
    if full.duplicated(KEYS).any():
        raise ValueError("Duplicate station-days after yearly integration")
    for sid, frame in full.groupby("站號", sort=True):
        frame.to_csv(annual / f"{sid}.csv", index=False, encoding="utf-8-sig")
    full[["站號", "站名", "lat", "lon"]].drop_duplicates("站號").to_csv(annual / "station_list.csv", index=False, encoding="utf-8-sig")
    (annual / "source_coordinate_variants.json").write_text(json.dumps(variants, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"year": year, "status": "complete", "start": min(dates), "end": max(dates),
              "daily_files": len(dates), "station_files": int(full["站號"].nunique()), "station_days": len(full),
              "epirice_model_rows": model_rows, "non_crop_rows": non_crop_rows,
              "other_model_rows": counters, "other_model_source_files": source_files,
              "epirice_archive": str(archive.relative_to(prediction)),
              "primary_value_field": PRIMARY_VALUE_FIELD, "output_schema_version": OUTPUT_SCHEMA_VERSION,
              "existing_epirice_daily_files_preserved": preserved_existing,
              "epirice_input_coordinate_mismatches_with_table_metadata": coordinate_mismatches,
              "historical_coordinate_variant_cells": len(variants),
              "unresolved_historical_variant_cells": sum(v["selected_status"] == "ambiguous_coordinate_variants" for v in variants),
              "model_version": simulation["model_version"], "input_sha256": simulation["input_sha256"],
              "method": simulation["method"], "high_risk_summary_modified": False}
    (annual / "integration_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "other_model_source_files"}, ensure_ascii=True), flush=True)
    return report


def build_index(prediction):
    prediction = Path(prediction)
    years = {}
    for path in sorted((prediction / "history_daily_by_station").glob("*/integration_audit.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        years[str(report["year"])] = {k: v for k, v in report.items() if k != "other_model_source_files"}
    if not set(map(str, range(2020, 2027))).issubset(years):
        raise ValueError("Historical integration is incomplete; all 2020-2026 years are required")
    result = {"status": "complete", "primary_value_field": PRIMARY_VALUE_FIELD,
              "output_schema_version": OUTPUT_SCHEMA_VERSION, "years": years}
    (prediction / "epirice_backfill_2020_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("All 2020-2026 integration stages verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["integrate", "index"])
    parser.add_argument("--year", type=int)
    parser.add_argument("--rebuilt", type=Path)
    parser.add_argument("--prediction-root", type=Path, default=ROOT / "rice_blast_prediction")
    args = parser.parse_args()
    if args.mode == "index":
        build_index(args.prediction_root)
    else:
        if args.year is None or not 2020 <= args.year <= 2026:
            parser.error("A year between 2020 and 2026 is required")
        integrate_year(args.prediction_root, args.year, args.rebuilt)

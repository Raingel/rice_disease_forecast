"""Full LB model files and an explicit, compact cross-model projection."""
from datetime import date

import numpy as np
import pandas as pd

from models.EPIRICE_BLB.output_contract import STATE_FIELDS as BLB_STATE_FIELDS, crop_context

MODEL_NAME = "EPIRICE_LB"
PREFIX = MODEL_NAME + "_"
PRIMARY_VALUE_FIELD = "rateinf_new_infections"
OUTPUT_SCHEMA_VERSION = "EPIRICE_LB_daily_v1"
KEYS = ["站號", "日期"]
META = ["站名", "lat", "lon"]
STATE_FIELDS = [*BLB_STATE_FIELDS, "RcOpt"]
COMPACT_FIELDS = [MODEL_NAME, PREFIX + "intensity_active", PREFIX + "status"]


def complete_daily(frame, day, stations, calendars, config, as_of=None, failures=None):
    """Distinguish non-crop -99, missing blank, and valid modeled zero."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    frame = frame.copy()
    if "站號" not in frame:
        frame["站號"] = pd.Series(dtype=str)
    frame["站號"] = frame["站號"].astype(str)
    if frame["站號"].duplicated().any():
        raise ValueError("Duplicate LB station-day")
    if not frame.empty and not frame["日期"].eq(day.isoformat()).all():
        raise ValueError("Unexpected LB output date")
    frame = frame.set_index("站號")
    rows = []
    for _, station in stations.iterrows():
        sid = str(station["站號"])
        row = frame.loc[sid].to_dict() if sid in frame.index else {}
        row.update({"站號": sid, "站名": station["站名"], "日期": day.isoformat(),
                    "lat": float(station["緯度"]), "lon": float(station["經度"])})
        context = crop_context(day, station["county"], calendars, int(config["duration_days"]))
        for key, value in context.items():
            if key != "status":
                row[PREFIX + key] = value
        raw = row.get(PREFIX + PRIMARY_VALUE_FIELD, np.nan)
        status = context["status"]
        if pd.notna(raw):
            raw = float(raw)
            if not np.isfinite(raw) or raw < 0 or status != "missing_model_output":
                raise ValueError("Invalid LB raw value or value outside modeled crop")
            status = "ok"
        row[MODEL_NAME] = -99.0 if status == "non_crop_period" else raw
        row[PREFIX + "status"] = status
        row[PREFIX + "missing_reason"] = ""
        if status == "missing_model_output":
            row[PREFIX + "missing_reason"] = (failures or {}).get(sid, "No model state for requested date")
        elif status not in ("ok", "non_crop_period"):
            row[PREFIX + "missing_reason"] = status
        for key in ("model_version", "parameter_version", "source_commit", "site_unit"):
            row[PREFIX + key] = config[key]
        row[PREFIX + "primary_value_field"] = PRIMARY_VALUE_FIELD
        row[PREFIX + "output_schema_version"] = OUTPUT_SCHEMA_VERSION
        if as_of is not None:
            row[PREFIX + "is_forecast"] = day > as_of
        for field in STATE_FIELDS + ["scenario_id", "transplant_date", "harvest_date",
                                    "simulation_end_date", "simulation_status", "generated_at",
                                    "weather_source", "weather_sha256", "config_sha256"]:
            row.setdefault(PREFIX + field, np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def merge_compact(base, leaf):
    """Explicit opt-in merge; retain other models, add only three LB columns."""
    base, leaf = base.copy(), leaf.copy()
    for frame in (base, leaf):
        frame["站號"] = frame["站號"].astype(str)
        if frame.duplicated(KEYS).any():
            raise ValueError("Duplicate station/date before LB merge")
    wanted = KEYS + [c for c in META if c in leaf] + COMPACT_FIELDS
    base = base.drop(columns=[c for c in base if c == MODEL_NAME or c.startswith(PREFIX)])
    merged = base.merge(leaf[wanted], on=KEYS, how="outer", suffixes=("", "_lb_meta"), validate="one_to_one")
    for col in META:
        extra = col + "_lb_meta"
        if extra in merged:
            merged[col] = merged[col].combine_first(merged.pop(extra)) if col in merged else merged.pop(extra)
    return merged

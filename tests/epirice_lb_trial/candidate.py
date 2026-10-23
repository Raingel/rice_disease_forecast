"""Test-only LB adapter; production BLB code and parameters are never modified.

The existing parameter-driven SEIR engine is a candidate for reuse. Independent
R execution must validate that choice before this trial becomes a daily model.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from models.EPIRICE_BLB.model import simulate_epirice_blb

HERE = Path(__file__).resolve().parent
MODEL = "EPIRICE_LB"
PREFIX = MODEL + "_"
SCHEMA = "EPIRICE_LB_trial_v1"
PRIMARY = "rateinf_new_infections"
KEYS = ["站號", "日期"]
META = ["站名", "lat", "lon"]


def load_config():
    return json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def simulate(weather):
    config = load_config()
    n = len(weather)
    if not 1 <= n <= config["duration_days"]:
        raise ValueError("Trial requires 1 to 120 consecutive daily rows")
    weather = weather.reset_index(drop=True).copy()
    days = pd.DatetimeIndex(pd.to_datetime(weather["YYYYMMDD"], errors="raise"))
    if (days.tz is not None or not days.equals(days.normalize())
            or not days.equals(pd.date_range(days[0], periods=n, freq="D"))):
        raise ValueError("Daily dates must be consecutive Taiwan local calendar dates")
    values = weather[["TEMP", "RHUM", "RAIN"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Missing or non-finite daily weather")
    if not weather["RHUM"].between(0, 100).all() or not weather["RAIN"].ge(0).all():
        raise ValueError("Invalid daily humidity or rainfall")
    weather["YYYYMMDD"] = days.strftime("%Y-%m-%d")
    states = simulate_epirice_blb(weather, dict(config, duration_days=n))
    states["RcOpt"] = config["RcOpt"]
    for key in ("model_version", "parameter_version", "site_unit", "source_commit"):
        states[key] = config[key]
    states["primary_value_field"] = PRIMARY
    states["output_schema_version"] = SCHEMA
    return states


def crop_trajectory(daily, start, harvest, available_end):
    """Always reconstruct from transplanting, independently of output window."""
    end = min(harvest, available_end, start + timedelta(days=119))
    if end < start:
        raise ValueError("No weather after transplanting")
    weather = daily.reindex(pd.date_range(start, end, freq="D")).copy()
    if weather[["TEMP", "RHUM", "RAIN"]].isna().any().any():
        raise ValueError("Incomplete weather from transplanting")
    weather["YYYYMMDD"] = weather.index.strftime("%Y-%m-%d")
    weather["DOY"] = weather.index.dayofyear
    return simulate(weather)


def station_day(states, station, day, start, harvest, metadata=None):
    """One full model-specific record; missing is distinct from non-crop/zero."""
    row = {"站號": str(station["站號"]), "站名": station["站名"],
           "日期": day.isoformat(), "lat": float(station["緯度"]),
           "lon": float(station["經度"]), MODEL: np.nan}
    if day < start or day > harvest:
        status = "non_crop_period"
        row[MODEL] = -99.0
    elif day > start + timedelta(days=119):
        status = "outside_model_duration"
    else:
        selected = states.loc[states["date"].eq(day.isoformat())]
        if len(selected) > 1:
            raise ValueError("Duplicate simulated date")
        status = "missing_model_output"
        if len(selected):
            status = "ok"
            row.update({PREFIX + k: v for k, v in selected.iloc[0].items() if k != "date"})
            row[MODEL] = selected.iloc[0][PRIMARY]
    row.update({PREFIX + k: v for k, v in (metadata or {}).items()})
    row.update({PREFIX + "status": status, PREFIX + "primary_value_field": PRIMARY,
                PREFIX + "output_schema_version": SCHEMA,
                PREFIX + "transplant_date": start.isoformat(),
                PREFIX + "harvest_date": harvest.isoformat()})
    for key in ("model_version", "parameter_version", "site_unit", "source_commit"):
        row[PREFIX + key] = load_config()[key]
    return pd.DataFrame([row])


def merge_compact(base, leaf):
    """Trial of a minimal station-table addition; retain every existing column."""
    base, leaf = base.copy(), leaf.copy()
    for frame in (base, leaf):
        frame["站號"] = frame["站號"].astype(str)
        if frame.duplicated(KEYS).any():
            raise ValueError("Duplicate station/date before trial merge")
    wanted = KEYS + [c for c in META if c in leaf] + [MODEL, PREFIX + "status"]
    base = base.drop(columns=[c for c in (MODEL, PREFIX + "status") if c in base])
    out = base.merge(leaf[wanted], on=KEYS, how="outer", suffixes=("", "_lb_meta"), validate="one_to_one")
    for col in META:
        extra = col + "_lb_meta"
        if extra in out:
            if col in out:
                out[col] = out[col].combine_first(out.pop(extra))
            else:
                out[col] = out.pop(extra)
    return out

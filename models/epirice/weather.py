"""Shared hourly weather -> Taiwan local daily EPIRICE inputs.

A daily value is valid only for exactly 24 valid hourly observations;
incomplete days remain NaN and are never silently zero-filled.
This retains the existing EPIRICE_BLB/LB input semantics.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HOURLY_COLS = ["temperature_2m", "relativehumidity_2m", "precipitation"]


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

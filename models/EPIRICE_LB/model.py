"""Pinned leaf-blast parameters using the independently R-verified SEIR loop.

The shared, parameter-driven loop lives in EPIRICE_BLB for compatibility.
Neither that module nor the BLB configuration is modified by this adapter.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from models.EPIRICE_BLB.model import simulate_epirice_blb

CONFIG_PATH = Path(__file__).with_name("config.json")


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def simulate_epirice_lb(weather, config=None):
    config = dict(load_config() if config is None else config)
    n = len(weather)
    if not 1 <= n <= int(config["duration_days"]):
        raise ValueError("LB requires 1 to duration_days consecutive daily rows")
    weather = weather.reset_index(drop=True).copy()
    days = pd.DatetimeIndex(pd.to_datetime(weather["YYYYMMDD"], errors="raise"))
    if (days.tz is not None or not days.equals(days.normalize())
            or not days.equals(pd.date_range(days[0], periods=n, freq="D"))):
        raise ValueError("Daily dates must be consecutive Taiwan local calendar dates")
    if not np.isfinite(weather[["TEMP", "RHUM", "RAIN"]].to_numpy(dtype=float)).all():
        raise ValueError("Missing or non-finite daily weather")
    if not weather["RHUM"].between(0, 100).all() or not weather["RAIN"].ge(0).all():
        raise ValueError("Invalid daily humidity or rainfall")
    weather["YYYYMMDD"] = days.strftime("%Y-%m-%d")
    weather["DOY"] = days.dayofyear
    states = simulate_epirice_blb(weather, dict(config, duration_days=n))
    states["DOY"] = weather["DOY"]
    states["RcOpt"] = config["RcOpt"]
    return states

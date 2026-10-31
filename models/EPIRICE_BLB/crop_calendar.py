"""Legacy EPIRICE_BLB crop calendar import path.

All crop calendar and crop-period logic now lives in models.epirice.crop_calendar.
Keep this module for historical scripts and standalone predict.py invocations.
"""
from __future__ import annotations

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from models.epirice.crop_calendar import (
    CALENDAR_URL, STATIONS_URL, crop_dates, month_day, normalize_county,
    parse_calendar, crop_context,
)

__all__ = [
    "CALENDAR_URL", "STATIONS_URL", "crop_dates", "month_day",
    "normalize_county", "parse_calendar", "crop_context",
]

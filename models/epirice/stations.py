"""Shared station metadata parsing and coordinate-safe weather CSV selection.

Filename matching and station renaming behavior are retained from the
original EPIRICE_BLB implementation.
"""
from __future__ import annotations

import io
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .crop_calendar import normalize_county


def load_stations(text: str) -> pd.DataFrame:
    stations = pd.read_csv(io.StringIO(text), dtype={"站號": str})
    required = {"站號", "站名", "緯度", "經度", "城市", "撤站日期"}
    if not required.issubset(stations.columns):
        raise ValueError(f"Missing station columns: {sorted(required - set(stations.columns))}")
    stations = stations.loc[stations["撤站日期"].isna()].copy()
    stations["county"] = stations["城市"].fillna("").map(normalize_county)
    stations = stations.dropna(subset=["站號", "站名", "緯度", "經度"])
    if stations["站號"].duplicated().any():
        raise ValueError("Active station metadata contains duplicate station IDs")
    return stations


def station_weather_files(folder: Path, stations: pd.DataFrame) -> dict[str, Path]:
    """Old filenames can survive coordinate updates; require current coordinates."""
    candidates = defaultdict(list)
    for path in sorted(folder.glob("*.csv")):
        parts = path.stem.split("_")
        if len(parts) < 4:
            continue
        try:
            candidates[parts[0]].append((path, float(parts[-2]), float(parts[-1])))
        except ValueError:
            continue
    found = {}
    for _, station in stations.iterrows():
        matches = [p for p, lat, lon in candidates[str(station["站號"])]
                   if abs(lat - float(station["緯度"])) <= 1e-6
                   and abs(lon - float(station["經度"])) <= 1e-6]
        if len(matches) > 1:
            # Renamed stations leave old-name files behind in the shared folder.
            # The downloader writes the live metadata name; never choose by mtime.
            current_name = [p for p in matches
                            if "_".join(p.stem.split("_")[1:-2]) == str(station["站名"])]
            if len(current_name) == 1:
                matches = current_name
            else:
                raise ValueError(f"Multiple current-coordinate weather files for {station['站號']}: {[p.name for p in matches]}")
        if matches:
            found[str(station["站號"])] = matches[0]
    return found

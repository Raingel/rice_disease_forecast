"""Published Taiwan county crop dates, using explicit representative dates."""
from __future__ import annotations

import csv
import io
import re
from datetime import date

CALENDAR_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vQPErPqAhitT7baUVl62ZW_jCA8SSuDzuB_CuQn3ddet_udFDtZdVoMacJqH_kx0ksgKjMlC2TpNulr/"
    "pub?gid=1654827671&single=true&output=csv"
)
STATIONS_URL = "https://raw.githubusercontent.com/Raingel/weather_station_list/refs/heads/main/data/weather_sta_list.csv"


def normalize_county(value: str) -> str:
    return str(value).strip().replace("台", "臺")


def parse_calendar(text: str) -> dict[str, dict]:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        raise ValueError("Empty crop calendar")
    header = [c.strip() for c in rows[0]]

    def last_column(name: str) -> int:
        matches = [i for i, field in enumerate(header) if field == name]
        if not matches:
            raise ValueError(f"Missing crop calendar column: {name}")
        return matches[-1]

    county_i = last_column("縣市")
    rice_i = last_column("水稻栽培區")
    def representative_column(name: str) -> int:
        if f"{name}點" in header:
            return last_column(f"{name}點")
        # Compatibility with the original published sheet's duplicated headers.
        if header.count(name) >= 2:
            return last_column(name)
        raise ValueError(f"Missing representative date column: {name}點")

    season_cols = [(s, representative_column(f"{s}插秧時間"), representative_column(f"{s}收割時間")) for s in ("一期作", "二期作")]
    calendars = {}
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(header):
            raise ValueError(f"Crop calendar row has {len(row)} columns; expected {len(header)}")
        county = normalize_county(row[county_i])
        # Exclude the published demonstration row, not a real county.
        if not county.endswith(("縣", "市")):
            continue
        rice = row[rice_i].strip()
        if rice not in ("是", "否"):
            raise ValueError(f"Unknown rice area flag for {county}: {rice}")
        if county in calendars:
            raise ValueError(f"Duplicate county in crop calendar: {county}")
        seasons = []
        if rice == "是":
            for season, transplant_i, harvest_i in season_cols:
                transplant, harvest = row[transplant_i].strip(), row[harvest_i].strip()
                if not transplant and not harvest:
                    continue
                if not transplant or not harvest:
                    raise ValueError(f"Incomplete {county} {season} dates")
                # Validate month/day using a leap year; never infer a midpoint from text ranges.
                month_day(transplant, 2024)
                month_day(harvest, 2024)
                seasons.append({"season": season, "transplant": transplant, "harvest": harvest})
            if not seasons:
                raise ValueError(f"Rice county has no representative dates: {county}")
        calendars[county] = {"rice_area": rice == "是", "seasons": seasons}
    if not calendars:
        raise ValueError("No Taiwan counties found in crop calendar")
    return calendars


def month_day(value: str, year: int) -> date:
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", value.strip())
    if not match:
        raise ValueError(f"Expected a representative M/D date, got {value!r}")
    return date(year, int(match[1]), int(match[2]))


def crop_dates(entry: dict, year: int) -> tuple[date, date]:
    start = month_day(entry["transplant"], year)
    harvest = month_day(entry["harvest"], year)
    if harvest < start:
        harvest = month_day(entry["harvest"], year + 1)
    return start, harvest

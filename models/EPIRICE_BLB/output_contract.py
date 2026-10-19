"""Shared daily-output contract for the runner and lossless output migrations."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

if __package__:
    from .crop_calendar import crop_dates
else:
    from crop_calendar import crop_dates

MODEL_NAME = "EPIRICE_BLB"
PRIMARY_VALUE_FIELD = "rateinf_new_infections"
OUTPUT_SCHEMA_VERSION = "EPIRICE_BLB_daily_v3"
NON_CROP_VALUE = -99.0
PREFIX = MODEL_NAME + "_"
KEYS = ["站號", "日期"]
METADATA = ["站名", "lat", "lon"]
STATE_FIELDS = ["simday", "TEMP", "RHUM", "RAIN", "RcW", "RcT", "RcA", "Rc",
                "healthy_sites", "latent", "infectious", "removed", "rateinf_new_infections",
                "rtransfer_L_to_I", "diseased_total", "healthy_fraction_C", "intensity_active",
                "AUDPC", "AUDPC_cumulative", "total_sites", "growth_new_sites",
                "senescence_sites_rate", "senesced_total", "latent_cohort_entries",
                "infectious_cohort_entries", "DOY"]


def crop_context(day, county, calendars, duration):
    calendar = calendars.get(county)
    if calendar is None:
        return {"status": "missing_crop_calendar", "county": county}
    if not calendar["rice_area"]:
        return {"status": "non_crop_period", "county": county}
    matches = []
    for year in (day.year - 1, day.year):
        for entry in calendar["seasons"]:
            start, harvest = crop_dates(entry, year)
            if start <= day <= harvest:
                matches.append((entry, start, harvest))
    if len(matches) > 1:
        raise ValueError(f"Overlapping crop calendar: {county} {day}")
    if not matches:
        return {"status": "non_crop_period", "county": county}
    entry, start, harvest = matches[0]
    return {"status": "missing_model_output" if day < start + timedelta(days=duration) else "outside_model_duration",
            "county": county, "season": entry["season"],
            "transplant_date": start.isoformat(), "harvest_date": harvest.isoformat()}


def complete_daily(frame, day, stations, calendars, config, as_of=None):
    """Retain all raw states; -99 belongs only in the non-crop primary value."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    frame = frame.copy()
    if "站號" not in frame:
        frame["站號"] = pd.Series(dtype=str)
    frame["站號"] = frame["站號"].astype(str)
    if frame["站號"].duplicated().any():
        raise ValueError(f"Duplicate EPIRICE station-day: {day}")
    if "日期" in frame and not frame.empty and not frame["日期"].eq(day.isoformat()).all():
        raise ValueError(f"Unexpected EPIRICE dates in {day}")
    context = []
    for _, station in stations.iterrows():
        row = {"站號": str(station["站號"]), "站名": station["站名"],
               "lat": float(station["緯度"]), "lon": float(station["經度"])}
        row.update({PREFIX + k: v for k, v in crop_context(day, station["county"], calendars, int(config["duration_days"])).items()})
        context.append(row)
    context = pd.DataFrame(context).set_index("站號") if context else pd.DataFrame(index=pd.Index([], name="站號"))
    frame = frame.set_index("站號")
    # Keep historical IDs that no longer occur in the current metadata snapshot.
    frame = frame.reindex(frame.index.union(context.index, sort=False))
    for col in context.columns:
        if col not in frame:
            frame[col] = context[col]
        else:
            frame[col] = frame[col].where(frame[col].notna(), context[col].reindex(frame.index))
    raw = PREFIX + PRIMARY_VALUE_FIELD
    if raw not in frame:
        frame[raw] = np.nan
    values = pd.to_numeric(frame[raw], errors="coerce")
    if np.isinf(values).any() or values.lt(0).any():
        raise ValueError(f"Invalid raw EPIRICE new-infection flow: {day}")
    if MODEL_NAME in frame:
        old = pd.to_numeric(frame[MODEL_NAME], errors="coerce")
        if (old.notna() & old.ne(NON_CROP_VALUE) & values.isna()).any():
            raise ValueError(f"Cannot migrate an EPIRICE score without its raw infection output: {day}")
    status = PREFIX + "status"
    if status not in frame:
        frame[status] = "missing_crop_calendar"
    # Reclassify absent values on every pass; never let an old sentinel override raw data.
    frame[status] = frame.index.map(context[status]).fillna("missing_crop_calendar") if status in context else "missing_crop_calendar"
    frame.loc[values.notna(), status] = "ok"
    frame[MODEL_NAME] = values
    frame.loc[frame[status].eq("non_crop_period"), MODEL_NAME] = NON_CROP_VALUE
    frame[PREFIX + "primary_value_field"] = PRIMARY_VALUE_FIELD
    frame[PREFIX + "output_schema_version"] = OUTPUT_SCHEMA_VERSION
    for col in STATE_FIELDS + ["county", "season", "scenario_id", "transplant_date", "harvest_date",
                              "simulation_end_date", "model_version", "generated_at", "is_forecast"]:
        if PREFIX + col not in frame:
            frame[PREFIX + col] = np.nan
    frame[PREFIX + "model_version"] = frame[PREFIX + "model_version"].fillna(config.get("model_version", "unknown"))
    frame["日期"] = day.isoformat()
    if as_of is not None:
        col = PREFIX + "is_forecast"
        frame[col] = frame[col].astype("boolean").fillna(day > as_of).astype(bool)
    return frame.reset_index()


def merge_station_frames(base, epirice):
    """Union station-days, preserving existing non-EPIRICE values and metadata."""
    base, epirice = base.copy(), epirice.copy()
    for frame in (base, epirice):
        frame["站號"] = frame["站號"].astype(str)
        if frame.duplicated(KEYS).any():
            raise ValueError("Duplicate station/date rows before EPIRICE merge")
    # Replace obsolete EPIRICE fields while keeping all other model columns.
    base = base.drop(columns=[c for c in base if c == MODEL_NAME or c.startswith(PREFIX)])
    cols = KEYS + [c for c in METADATA if c in epirice] + [c for c in epirice if c == MODEL_NAME or c.startswith(PREFIX)]
    merged = base.merge(epirice[cols], on=KEYS, how="outer", suffixes=("", "_epirice_meta"), validate="one_to_one")
    for col in METADATA:
        extra = col + "_epirice_meta"
        if extra in merged:
            merged[col] = merged[col].combine_first(merged.pop(extra))
    return merged

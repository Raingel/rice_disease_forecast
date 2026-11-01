"""Opt-in, read-only EPIRICE adapters for Internal Forecast Record v2.

This module does NOT change daily model output, the public CSV v1 interface,
website API v1, or the legacy organizer. It validates and projects completed
BLB/LB daily DataFrames into immutable, model-native internal records.

Never infer a probability, percent, or calibrated risk from an EPIRICE value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math
import re
from typing import Optional

import pandas as pd


SCHEMA_VERSION = "forecast_record_v2"
VALID_STATUSES = frozenset({
    "ok",
    "non_crop_period",
    "missing_crop_calendar",
    "missing_model_output",
    "outside_model_duration",
})
STATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class ContractValidationError(ValueError):
    """A record cannot be represented safely under the internal contract."""


@dataclass(frozen=True)
class ModelOutputSpec:
    model_id: str
    primary_field: str
    source_schema_version: str
    value_type: str = "infection_flow"
    unit: Optional[str] = None
    legacy_non_crop_sentinel: float = -99.0


# These are *source* schema versions, not model software versions. The
# physical units of rateinf_new_infections are intentionally left unresolved
# until the disease-specific scientific definitions are checked.
EPIRICE_SPECS = {
    "EPIRICE_BLB": ModelOutputSpec(
        "EPIRICE_BLB", "rateinf_new_infections", "EPIRICE_BLB_daily_v3"
    ),
    "EPIRICE_LB": ModelOutputSpec(
        "EPIRICE_LB", "rateinf_new_infections", "EPIRICE_LB_daily_v1"
    ),
}


@dataclass(frozen=True)
class ForecastRecord:
    schema_version: str
    station_id: str
    date: str
    model_id: str
    value: Optional[float]
    status: str
    value_type: str
    unit: Optional[str]
    primary_field: str
    model_version: Optional[str]
    source_schema_version: Optional[str]
    source_generated_at: Optional[str]
    source_commit: Optional[str]
    source_site_unit: Optional[str]

    def to_dict(self) -> dict:
        """Produce a JSON-safe value/status record with stable field names."""
        validate_record(self)
        return asdict(self)


def _optional_text(value: object, *, column: str) -> Optional[str]:
    if value is None or value is pd.NA:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        raise ContractValidationError(f"{column}: non-scalar metadata")
    text = str(value).strip()
    return text or None


def _number(value: object, *, column: str) -> Optional[float]:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, bool):
        raise ContractValidationError(f"{column}: boolean is not a model value")
    try:
        if bool(pd.isna(value)):
            return None
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContractValidationError(f"{column}: expected finite numeric value") from exc
    if not math.isfinite(number):
        raise ContractValidationError(f"{column}: non-finite value")
    return number


def _date(value: object) -> str:
    if not isinstance(value, str):
        raise ContractValidationError("date must be an explicit YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractValidationError("invalid YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise ContractValidationError("date must be canonical YYYY-MM-DD")
    return value


def validate_record(record: ForecastRecord) -> None:
    """Reject ambiguous/misleading normalized values before downstream use."""
    if record.schema_version != SCHEMA_VERSION:
        raise ContractValidationError("unknown normalized schema version")
    if not isinstance(record.station_id, str) or not STATION_ID_PATTERN.fullmatch(record.station_id):
        raise ContractValidationError("invalid station_id")
    _date(record.date)
    if not isinstance(record.model_id, str) or not record.model_id:
        raise ContractValidationError("invalid model_id")
    if record.status not in VALID_STATUSES:
        raise ContractValidationError(f"unknown forecast status: {record.status!r}")
    if record.value_type != "infection_flow":
        raise ContractValidationError("unsupported normalized value_type")
    if not record.primary_field:
        raise ContractValidationError("missing primary_field")
    if record.value is None:
        if record.status == "ok":
            raise ContractValidationError("ok requires a finite value (including valid zero)")
    else:
        if isinstance(record.value, bool) or not isinstance(record.value, (int, float)):
            raise ContractValidationError("value must be numeric")
        if not math.isfinite(record.value) or record.value < 0:
            raise ContractValidationError("infection_flow must be nonnegative and finite")
        if record.status != "ok":
            raise ContractValidationError("non-ok status must have null value")


def validate_records(records: list[ForecastRecord]) -> None:
    """Enforce unique (station, date, model) keys for an entire result batch."""
    seen = set()
    for record in records:
        validate_record(record)
        key = (record.station_id, record.date, record.model_id)
        if key in seen:
            raise ContractValidationError(f"duplicate forecast key: {key!r}")
        seen.add(key)


def normalize_epirice_daily(frame: pd.DataFrame, model_id: str) -> list[ForecastRecord]:
    """Validate a completed legacy daily BLB/LB frame, without mutating it.

    Primary value and raw infection flow MUST agree for status=ok.
    The public -99 sentinel is accepted *only* for non_crop_period and
    becomes a JSON-safe null. All other non-ok statuses require null in
    both the legacy primary value and the raw infection flow.

    A bare model-value column without the matching raw/status columns is
    deliberately rejected rather than guessed or silently normalized.
    """
    if model_id not in EPIRICE_SPECS:
        raise ContractValidationError(f"unregistered EPIRICE model: {model_id!r}")
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("expected pandas.DataFrame")
    spec = EPIRICE_SPECS[model_id]
    prefix = model_id + "_"
    raw_field = prefix + spec.primary_field
    status_field = prefix + "status"
    mandatory = {"站號", "日期", model_id, raw_field, status_field}
    absent = mandatory.difference(frame.columns)
    if absent:
        raise ContractValidationError(f"{model_id}: missing required columns: {sorted(absent)!r}")
    if frame.columns.duplicated().any():
        raise ContractValidationError(f"{model_id}: duplicate CSV header fields")

    results: list[ForecastRecord] = []
    for pos, row in enumerate(frame.to_dict(orient="records")):
        station_id = row["站號"]
        if not isinstance(station_id, str):
            raise ContractValidationError(
                f"{model_id} row {pos}: station ID must be read as string (dtype={{'站號': str}})"
            )
        day = _date(row["日期"])
        status = _optional_text(row[status_field], column=status_field)
        if status not in VALID_STATUSES:
            raise ContractValidationError(f"{model_id} row {pos}: invalid status {status!r}")
        raw = _number(row[raw_field], column=raw_field)
        legacy = _number(row[model_id], column=model_id)
        if raw is not None and raw < 0:
            raise ContractValidationError(f"{model_id} row {pos}: negative raw infection flow")
        if status == "ok":
            if raw is None or legacy is None or raw != legacy:
                raise ContractValidationError(
                    f"{model_id} row {pos}: ok requires equal finite raw/primary values"
                )
            normalized_value = raw
        elif status == "non_crop_period":
            if legacy != spec.legacy_non_crop_sentinel or raw is not None:
                raise ContractValidationError(
                    f"{model_id} row {pos}: non-crop must have -99 primary and blank raw flow"
                )
            normalized_value = None
        else:
            if legacy is not None or raw is not None:
                raise ContractValidationError(
                    f"{model_id} row {pos}: {status} must have blank primary and raw flow"
                )
            normalized_value = None

        recorded_primary = _optional_text(row.get(prefix + "primary_value_field"),
                                          column=prefix + "primary_value_field")
        if recorded_primary is not None and recorded_primary != spec.primary_field:
            raise ContractValidationError(f"{model_id} row {pos}: wrong source primary field")
        source_schema = _optional_text(row.get(prefix + "output_schema_version"),
                                       column=prefix + "output_schema_version")
        if source_schema is not None and source_schema != spec.source_schema_version:
            raise ContractValidationError(f"{model_id} row {pos}: unknown source schema version")

        record = ForecastRecord(
            schema_version=SCHEMA_VERSION,
            station_id=station_id,
            date=day,
            model_id=model_id,
            value=normalized_value,
            status=status,
            value_type=spec.value_type,
            unit=spec.unit,
            primary_field=spec.primary_field,
            model_version=_optional_text(row.get(prefix + "model_version"),
                                         column=prefix + "model_version"),
            source_schema_version=source_schema,
            source_generated_at=_optional_text(row.get(prefix + "generated_at"),
                                               column=prefix + "generated_at"),
            source_commit=_optional_text(row.get(prefix + "source_commit"),
                                         column=prefix + "source_commit"),
            source_site_unit=_optional_text(row.get(prefix + "site_unit"),
                                            column=prefix + "site_unit"),
        )
        validate_record(record)
        results.append(record)
    validate_records(results)
    return results

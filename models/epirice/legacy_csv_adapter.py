"""Opt-in, lossless legacy daily CSV adapters for EPIRICE BLB/LB.

Phase 3C does NOT replace the current daily writer or change the published
v1 CSV format. A normalized ForecastRecord carries only one model value:
it cannot reconstruct detailed SEIR states, weather metadata, or the original
column order. Keep the complete legacy source as an immutable sidecar.

The two supported uses are:
  * parse a published v1 daily CSV -> validated v2 records + exact v1 bytes;
  * serialize a *complete* legacy daily DataFrame with pandas' existing
    UTF-8-BOM CSV settings, after validating its semantic v2 projection.

Neither function writes any file or modifies a model runner.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
from typing import Optional, Sequence

import pandas as pd

from .output_contract_v2 import (
    ContractValidationError,
    EPIRICE_SPECS,
    ForecastRecord,
    normalize_epirice_daily,
    validate_records,
)


class UnsafeLegacyConversionError(ContractValidationError):
    """Converting a partial/new v2 record would lose or invent v1 states."""


@dataclass(frozen=True)
class LegacyDailyEnvelope:
    """Read-only normalized records coupled with the full original v1 bytes."""

    model_id: str
    records: tuple[ForecastRecord, ...]
    original_csv: bytes
    header: tuple[str, ...]
    sha256: str

    def to_v1_bytes(
        self,
        records: Optional[Sequence[ForecastRecord]] = None,
    ) -> bytes:
        """Return byte-identical original CSV only if records are unchanged.

        Any edited v2 record (including status, value, order, provenance) is
        rejected: the preserved v1 detailed states cannot be reconstructed
        from a partial normalized record. This is intentionally not an
        implicit v2->v1 model-output generator.
        """
        if hashlib.sha256(self.original_csv).hexdigest() != self.sha256:
            raise UnsafeLegacyConversionError("legacy source checksum changed")
        if records is not None:
            proposed = tuple(records)
            validate_records(list(proposed))
            if proposed != self.records:
                raise UnsafeLegacyConversionError(
                    "v2 records differ from preserved v1 CSV; full raw state "
                    "and column values cannot be safely reconstructed"
                )
        return self.original_csv


def _validate_csv_structure(blob: bytes) -> tuple[str, ...]:
    if not isinstance(blob, bytes):
        raise TypeError("legacy daily CSV must be bytes")
    if not blob or b"\0" in blob:
        raise ContractValidationError("empty or NUL-containing legacy CSV")
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContractValidationError("legacy daily CSV must be UTF-8 or UTF-8-BOM") from exc
    try:
        rows = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(rows, None)
        if not header or any(not field for field in header):
            raise ContractValidationError("missing or empty CSV header")
        if len(header) != len(set(header)):
            raise ContractValidationError("duplicate CSV header fields")
        row_count = 0
        for line_number, row in enumerate(rows, start=2):
            if len(row) != len(header):
                raise ContractValidationError(
                    f"CSV record {line_number} has {len(row)} fields, "
                    f"expected {len(header)}"
                )
            row_count += 1
        if not row_count:
            raise ContractValidationError("empty model CSV data")
    except (csv.Error, UnicodeError) as exc:
        raise ContractValidationError("malformed CSV quoting or encoding") from exc
    return tuple(header)


def read_legacy_daily_csv(blob: bytes, model_id: str) -> LegacyDailyEnvelope:
    """Parse a complete published BLB/LB CSV into a safely reversible bundle.

    The original bytes (including a BOM, CRLF, column order, and quoting) are
    kept unchanged. The validated v2 records are a semantic *view*, not a
    replacement for original CSV columns.
    """
    if model_id not in EPIRICE_SPECS:
        raise ContractValidationError(f"unsupported legacy model: {model_id!r}")
    header = _validate_csv_structure(blob)
    try:
        frame = pd.read_csv(io.BytesIO(blob), encoding="utf-8-sig", dtype={"站號": str})
    except (pd.errors.ParserError, ValueError, UnicodeError) as exc:
        raise ContractValidationError("cannot parse model CSV") from exc
    if list(frame.columns) != list(header):
        raise ContractValidationError("CSV columns changed during parsing")
    records = tuple(normalize_epirice_daily(frame, model_id))
    return LegacyDailyEnvelope(
        model_id=model_id,
        records=records,
        original_csv=blob,
        header=header,
        sha256=hashlib.sha256(blob).hexdigest(),
    )


def serialize_complete_legacy_daily(frame: pd.DataFrame, model_id: str) -> bytes:
    """Render full, validated v1 source fields using the original pandas CSV settings.

    This requires the *complete* daily model DataFrame, **not** a list of v2
    ForecastRecord values. DataFrame column order and all SEIR state columns
    remain as supplied. The caller is responsible for the destination path.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("expected a complete pandas.DataFrame")
    normalize_epirice_daily(frame, model_id)
    out = io.BytesIO()
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    result = out.getvalue()
    # Verify both the on-disk representation and the complete in-memory
    # projection; reject encoding or parser changes before returning bytes.
    read_legacy_daily_csv(result, model_id)
    return result

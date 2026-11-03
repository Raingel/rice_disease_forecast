"""Single-use, fail-closed repair of missing 2026-11-21/22 station rows.

Only append the real previously omitted station/day values (800 per date) in
existing published per-station CSVs. Preserve *all existing bytes/rows/header*,
do not rerun any disease model, do not touch the seasonal summary, model
source CSVs, other dates or website caches.

This is an operational staging script, NOT part of scheduled forecasts.
Apply only on a one-off isolated GitHub Actions PR branch. It deliberately
fails after the daily scheduled output changes its expected input coverage.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import io
import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from models import recent_forecast_organizer as organizer


ROOT = Path(__file__).resolve().parents[1]
STATIONS = ROOT / "rice_blast_prediction" / "recent_daily_by_station"
DAYS = ("20261121", "20261122")
EXPECTED_STATIONS = 892
EXPECTED_LEGACY = 92
EXPECTED_ADDED_PER_DAY = 800
EXPECTED_NEW_RECORDS = 1600
STATION = "站號"
DATE = "日期"


class UnsafeActivationError(RuntimeError):
    """An input changed or published file could not be preserved exactly."""


def _csv_line(fields: list[str], newline: str) -> bytes:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator=newline).writerow(fields)
    return output.getvalue().encode("utf-8")


def _convert(value) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _build_exact_insert(raw: bytes, station: str, inserts: dict[str, dict]) -> bytes:
    """Preserve every original byte, adding only missing sorted CSV rows."""
    if not raw or b"\0" in raw:
        raise UnsafeActivationError(f"{station}: missing or invalid original station CSV")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise UnsafeActivationError(f"{station}: original CSV not UTF-8") from e
    physical = decoded.splitlines(keepends=True)
    if not physical or any(not line.endswith("\n") for line in physical):
        raise UnsafeActivationError(f"{station}: unexpected CSV physical line endings")
    newline = "\r\n" if physical[0].endswith("\r\n") else "\n"
    if any(line.endswith("\r\n") != (newline == "\r\n") for line in physical):
        raise UnsafeActivationError(f"{station}: mixed line endings")
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    records = list(reader)
    # A newline inside a quoted field needs a different byte-preserving
    # parser, so fail rather than inadvertently rewriting the old CSV.
    if len(records) != len(physical) or not records or not records[0]:
        raise UnsafeActivationError(f"{station}: multiline or malformed CSV")
    header = records[0]
    if len(header) != len(set(header)) or not {STATION, DATE}.issubset(header):
        raise UnsafeActivationError(f"{station}: malformed or ambiguous header")
    st_col, date_col = header.index(STATION), header.index(DATE)
    existing_dates = []
    for row in records[1:]:
        if len(row) != len(header) or row[st_col] != station:
            raise UnsafeActivationError(f"{station}: invalid station row")
        existing_dates.append(row[date_col])
    if existing_dates != sorted(set(existing_dates)):
        raise UnsafeActivationError(f"{station}: existing dates unsorted or duplicate")

    headbytes = raw[:3] if raw.startswith(b"\xef\xbb\xbf") else b""
    original_lines = [s.encode("utf-8") for s in physical]
    old_body = original_lines[1:]
    insert_lines = {}
    for day, record in inserts.items():
        expected_date = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        if expected_date in existing_dates:
            raise UnsafeActivationError(f"{station}: {expected_date} already present")
        if record[STATION] != station or record[DATE] != expected_date:
            raise UnsafeActivationError(f"{station}: target record key mismatch")
        disallowed = {
            k for k, val in record.items()
            if k not in header and not pd.isna(val)
        }
        if disallowed:
            raise UnsafeActivationError(f"{station}: missing output fields {sorted(disallowed)}")
        fields = [_convert(record.get(key)) for key in header]
        new_line = _csv_line(fields, newline)
        verified = next(csv.reader(io.StringIO(new_line.decode("utf-8"))))
        if verified[st_col] != station or verified[date_col] != expected_date:
            raise UnsafeActivationError(f"{station}: inserted row failed CSV validation")
        # The inserted native score must round-trip through its textual
        # representation (zero and null remain distinct).
        for model in organizer.MODEL_FILES:
            if model in header and model in record:
                v = record[model]
                cell = verified[header.index(model)]
                if pd.isna(v):
                    if cell != "":
                        raise UnsafeActivationError("missing numeric value became populated")
                elif cell == "" or float(cell) != float(v):
                    raise UnsafeActivationError(f"{station}: model value changed for {model}")
        insert_lines[expected_date] = new_line

    output = [original_lines[0]]
    added = []
    for index, original_row in enumerate(records[1:]):
        date = original_row[date_col]
        while insert_lines and min(insert_lines) < date:
            at_date = min(insert_lines)
            output.append(insert_lines.pop(at_date))
            added.append(at_date)
        output.append(old_body[index])
    for at_date in sorted(insert_lines):
        output.append(insert_lines[at_date])
        added.append(at_date)

    candidate = headbytes + b"".join(output)
    if candidate == raw:
        raise UnsafeActivationError(f"{station}: no change produced")
    if len(added) != len(inserts):
        raise UnsafeActivationError(f"{station}: rows were not added as expected")
    # Strong byte-level guarantee: remove ONLY the inserted lines from the
    # modified file and recover the original file byte for byte.
    parsed = list(csv.reader(io.StringIO(candidate.decode("utf-8-sig"), newline=""), strict=True))
    expected_dates = {f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in inserts}
    if len(parsed) != len(records) + len(inserts) or [r[date_col] for r in parsed[1:]] != sorted(set(existing_dates) | expected_dates):
        raise UnsafeActivationError(f"{station}: final CSV integrity test failed")
    filtered = [output[0]]
    for raw_line, row in zip(output[1:], parsed[1:]):
        if row[date_col] not in expected_dates:
            filtered.append(raw_line)
    if headbytes + b"".join(filtered) != raw:
        raise UnsafeActivationError(f"{station}: existing bytes were changed")
    return candidate


def prepare_patch() -> tuple[dict[Path, bytes], dict]:
    """Validate entire 1,600-row intended patch before modifying any file."""
    additions: dict[str, dict[str, dict]] = {}
    daily_report = {}
    with patch.object(organizer, "load_planthopper_data", return_value=None):
        for day in DAYS:
            legacy = organizer.merge_daily_predictions(day, coverage_mode="legacy")
            joined = organizer.merge_daily_predictions(day, coverage_mode="union")
            if legacy is None or joined is None:
                raise UnsafeActivationError(f"{day}: published input missing")
            if len(legacy) != EXPECTED_LEGACY or len(joined) != EXPECTED_STATIONS:
                raise UnsafeActivationError(
                    f"{day}: unexpectedly changed source coverage "
                    f"legacy={len(legacy)} union={len(joined)}"
                )
            base = legacy.set_index(STATION, verify_integrity=True)
            expanded = joined.set_index(STATION, verify_integrity=True)
            if not set(base.index).issubset(expanded.index):
                raise UnsafeActivationError(f"{day}: lost legacy station")
            old = expanded.loc[base.index, base.columns]
            pd.testing.assert_frame_equal(
                base.sort_index(), old.sort_index(), check_dtype=False
            )
            extra = set(expanded.index) - set(base.index)
            if len(extra) != EXPECTED_ADDED_PER_DAY:
                raise UnsafeActivationError(f"{day}: expected 800 missing stations, got {len(extra)}")
            for station in extra:
                station_record = expanded.loc[station].to_dict()
                station_record[STATION] = station
                additions.setdefault(station, {})[day] = station_record
            daily_report[day] = {"existing": len(base), "union": len(expanded), "added": len(extra)}

    if sum(len(v) for v in additions.values()) != EXPECTED_NEW_RECORDS:
        raise UnsafeActivationError("total additions not exactly 1,600")
    if len(additions) != EXPECTED_ADDED_PER_DAY:
        raise UnsafeActivationError("expected both dates missing for the same 800 stations")
    if "C0G910" not in additions:
        raise UnsafeActivationError("C0G910 should be in the missing-station set")
    if (float(additions["C0G910"]["20261121"]["BLASTAM"]) != 0.0
            or float(additions["C0G910"]["20261122"]["BLASTAM"]) != 1.0):
        raise UnsafeActivationError("C0G910 BLASTAM reference mismatch")

    output = {}
    for station in sorted(additions):
        path = STATIONS / f"{station}.csv"
        if not path.is_file():
            raise UnsafeActivationError(f"existing published station CSV missing: {path}")
        output[path] = _build_exact_insert(path.read_bytes(), station, additions[station])
    return output, {
        "per_day": daily_report,
        "files": len(output),
        "new_rows": sum(len(x) for x in additions.values()),
        "c0g910_blastam": [0.0, 1.0],
        "unchanged_existing_rows": True,
        "new_mode_persistently_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write only when explicitly approved")
    args = parser.parse_args()
    if args.apply and os.environ.get("ONE_TIME_UNION_APPROVED") != "yes":
        raise UnsafeActivationError("missing explicit one-time approval marker")
    outputs, report = prepare_patch()
    if args.apply:
        import tempfile
        for path, blob in outputs.items():
            fd, temp_path = tempfile.mkstemp(prefix=".one-time-union-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(blob)
                os.replace(temp_path, path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
    import json
    print(json.dumps({"mode": "applied" if args.apply else "dry_run", **report}, sort_keys=True))


if __name__ == "__main__":
    main()

"""Phase 3D: offline, pinned, multi-date/multi-station public-data regression.

Evidence: six published BLB/LB daily CSVs from immutable upstream commit,
48 geographically distributed stations per file (288 source records), plus
actual relocated C0AC60 double rows. No external HTTP or production writes.
This verifies the *sampled* real data, NOT a full-country or live website
47-day API replay.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

# The legacy organizer makes its output directory on import. Keep this
# path firmly outside the repository or production output.
os.environ.setdefault("RECENT_OUTPUT_FOLDER", str(Path(tempfile.gettempdir()) / "phase3d-organizer"))
from models import recent_forecast_organizer as organizer
from models.epirice.legacy_csv_adapter import (
    read_legacy_daily_csv,
    serialize_complete_legacy_daily,
)
from models.epirice.output_contract_v2 import (
    ContractValidationError,
    EPIRICE_SPECS,
    SCHEMA_VERSION,
    validate_records,
)


ROOT = Path(__file__).parent / "fixtures" / "public_contract_v1"
MULTIDAY = json.loads((ROOT / "phase3d_epirice_multiday_2026.json").read_text(encoding="utf-8"))
RELOCATION = json.loads((ROOT / "phase3d_relocated_station_20261019.json").read_text(encoding="utf-8"))
PINNED_REVISION = "9addfbb00deeacae501b2bfda2426fe23e90e3d2"


def sample_frame(source):
    return pd.read_csv(io.StringIO(source["csv"]), dtype={"站號": str})


def sample_envelope(source):
    return read_legacy_daily_csv(source["csv"].encode("utf-8"), source["model_id"])


class PublishedMultidayCoverageTests(unittest.TestCase):
    def test_source_provenance_and_coverage_are_declared_not_live_guessed(self):
        self.assertEqual(MULTIDAY["source_revision"], PINNED_REVISION)
        self.assertEqual(len(MULTIDAY["selected_station_ids"]), 48)
        selected = set(MULTIDAY["selected_station_ids"])
        self.assertEqual(len(selected), 48)
        self.assertEqual({(r["date"], r["model_id"]) for r in MULTIDAY["sources"]}, {
            ("2026-06-04", "EPIRICE_BLB"),
            ("2026-08-30", "EPIRICE_BLB"),
            ("2026-10-19", "EPIRICE_BLB"),
            ("2026-10-19", "EPIRICE_LB"),
            ("2026-11-01", "EPIRICE_BLB"),
            ("2026-11-01", "EPIRICE_LB"),
        })
        for source in MULTIDAY["sources"]:
            with self.subTest(day=source["date"], model=source["model_id"]):
                self.assertRegex(source["git_blob_sha"], r"^[0-9a-f]{40}$")
                self.assertEqual(source["original_rows"], 892)
                self.assertEqual(sum(source["original_status_counts"].values()), 892)
                self.assertEqual(sum(source["sample_status_counts"].values()), 48)
                self.assertEqual(len(source["header"]), len(set(source["header"])))
                self.assertTrue(source["path"].startswith("rice_blast_prediction/data/"))
                frame = sample_frame(source)
                self.assertEqual(list(frame.columns), source["header"])
                self.assertEqual(len(frame), 48)
                self.assertEqual(set(frame["站號"]), selected)
                self.assertEqual(frame["日期"].tolist(), [source["date"]] * 48)
                self.assertFalse(frame.duplicated(["站號", "日期"]).any())

    def test_real_multiday_v2_normalization_is_lossless_and_json_safe(self):
        results = []
        for source in MULTIDAY["sources"]:
            with self.subTest(day=source["date"], model=source["model_id"]):
                raw = source["csv"].encode("utf-8")
                envelope = sample_envelope(source)
                self.assertEqual(envelope.to_v1_bytes(), raw)
                self.assertEqual(envelope.to_v1_bytes(envelope.records), raw)
                self.assertEqual(envelope.header, tuple(source["header"]))
                self.assertEqual(len(envelope.records), 48)
                self.assertEqual({r.date for r in envelope.records}, {source["date"]})
                self.assertEqual({r.model_id for r in envelope.records}, {source["model_id"]})
                self.assertEqual({r.schema_version for r in envelope.records}, {SCHEMA_VERSION})
                self.assertTrue(all(r.primary_field == "rateinf_new_infections"
                                    and r.value_type == "infection_flow" and r.unit is None
                                    for r in envelope.records))
                json.dumps([r.to_dict() for r in envelope.records], allow_nan=False)
                results.extend(envelope.records)
        self.assertEqual(len(results), 288)
        validate_records(results)

    def test_full_state_and_provenance_columns_remain_in_exact_v1_bytes(self):
        for source in MULTIDAY["sources"]:
            with self.subTest(day=source["date"], model=source["model_id"]):
                model = source["model_id"]
                blob = sample_envelope(source).to_v1_bytes()
                frame = sample_frame(source)
                for field in ("站號", "站名", "日期", "lat", "lon", model,
                              model + "_status", model + "_rateinf_new_infections",
                              model + "_latent", model + "_healthy_sites",
                              model + "_output_schema_version"):
                    self.assertIn(field, frame.columns)
                    self.assertIn(field.encode("utf-8"), blob.split(b"\n", 1)[0])
                self.assertEqual(list(frame.columns), source["header"])
                self.assertEqual(len(frame.columns), 44 if model.endswith("BLB") else 53)

    def test_actual_status_counts_and_source_native_values_match_v2(self):
        statuses_seen = set()
        for source in MULTIDAY["sources"]:
            frame = sample_frame(source)
            model = source["model_id"]
            records = sample_envelope(source).records
            self.assertEqual(dict(Counter(frame[model + "_status"])),
                             source["sample_status_counts"])
            self.assertEqual(dict(Counter(record.status for record in records)),
                             source["sample_status_counts"])
            self.assertEqual({record.station_id for record in records}, set(frame["站號"]))
            for row, rec in zip(frame.to_dict("records"), records):
                with self.subTest(day=source["date"], model=model, station=rec.station_id):
                    self.assertEqual(row["站號"], rec.station_id)
                    self.assertEqual(row[model + "_status"], rec.status)
                    val, raw = row[model], row[model + "_rateinf_new_infections"]
                    if rec.status == "ok":
                        self.assertIsNotNone(rec.value)
                        self.assertGreaterEqual(rec.value, 0.0)
                        self.assertEqual(rec.value, float(raw))
                        self.assertEqual(rec.value, float(val))
                    elif rec.status == "non_crop_period":
                        self.assertIsNone(rec.value)
                        self.assertEqual(float(val), -99.0)
                        self.assertTrue(pd.isna(raw))
                    else:
                        self.assertIsNone(rec.value)
                        self.assertTrue(pd.isna(val))
                        self.assertTrue(pd.isna(raw))
                    statuses_seen.add(rec.status)
        self.assertEqual(statuses_seen, {
            "ok", "non_crop_period", "missing_crop_calendar", "outside_model_duration"
        })

    def test_published_outside_duration_status_in_june_not_recast_as_non_crop(self):
        source = next(x for x in MULTIDAY["sources"]
                      if x["date"] == "2026-06-04" and x["model_id"] == "EPIRICE_BLB")
        records = sample_envelope(source).records
        beyond = [r for r in records if r.status == "outside_model_duration"]
        self.assertEqual(len(beyond), 6)
        self.assertTrue(all(r.value is None for r in beyond))
        self.assertEqual(source["original_status_counts"]["outside_model_duration"], 129)

    def test_cross_day_station_model_keys_remain_distinct(self):
        all_records = [r for source in MULTIDAY["sources"] for r in sample_envelope(source).records]
        self.assertEqual(len({(r.station_id, r.date, r.model_id) for r in all_records}), 288)
        for sid in MULTIDAY["selected_station_ids"]:
            by_station = [r for r in all_records if r.station_id == sid]
            self.assertEqual(len(by_station), 6)
            self.assertEqual({r.date for r in by_station},
                             {"2026-06-04", "2026-08-30", "2026-10-19", "2026-11-01"})
            self.assertEqual({r.model_id for r in by_station}, {"EPIRICE_BLB", "EPIRICE_LB"})

    def test_source_full_frame_serialization_uses_legacy_pandas_settings(self):
        for source in MULTIDAY["sources"]:
            with self.subTest(day=source["date"], model=source["model_id"]):
                frame = sample_frame(source)
                before = frame.copy(deep=True)
                expected = io.BytesIO()
                frame.to_csv(expected, index=False, encoding="utf-8-sig")
                actual = serialize_complete_legacy_daily(frame, source["model_id"])
                self.assertEqual(actual, expected.getvalue())
                self.assertEqual(read_legacy_daily_csv(actual, source["model_id"]).to_v1_bytes(),
                                 actual)
                pd.testing.assert_frame_equal(frame, before)

    def test_bad_multiday_source_row_is_rejected_without_repair(self):
        source = next(x for x in MULTIDAY["sources"]
                      if x["date"] == "2026-11-01" and x["model_id"] == "EPIRICE_BLB")
        frame = sample_frame(source)
        bad = frame.copy()
        good = bad.index[bad["EPIRICE_BLB_status"].eq("ok")][0]
        bad.loc[good, "EPIRICE_BLB"] = -99.0
        with self.assertRaises(ContractValidationError):
            serialize_complete_legacy_daily(bad, "EPIRICE_BLB")

    def test_synthetic_cross_year_and_leap_day_keys_are_not_collapsed(self):
        source = next(x for x in MULTIDAY["sources"]
                      if x["date"] == "2026-11-01" and x["model_id"] == "EPIRICE_LB")
        frame = sample_frame(source)
        good = frame.loc[frame["EPIRICE_LB_status"].eq("ok")].iloc[:1].copy()
        samples = []
        for day in ("2024-02-29", "2026-12-31", "2027-01-01"):
            item = good.copy()
            item["日期"] = day
            samples.append(item)
        synthetic = pd.concat(samples, ignore_index=True)
        bundle = read_legacy_daily_csv(
            serialize_complete_legacy_daily(synthetic, "EPIRICE_LB"), "EPIRICE_LB")
        self.assertEqual({r.date for r in bundle.records},
                         {"2024-02-29", "2026-12-31", "2027-01-01"})
        self.assertEqual(len(bundle.records), 3)
        # This verifies identifiers/format only; crop calendar is not rerun.
        self.assertEqual(date.fromisoformat(bundle.records[0].date).year, 2024)


class RealRelocationTests(unittest.TestCase):
    def test_published_double_rows_use_current_station_coordinates(self):
        row_text = "站號,站名,日期,lat,lon,BlastGRU-TW\n" + "\n".join(
            RELOCATION["source_rows"]) + "\n"
        frame = pd.read_csv(io.StringIO(row_text), dtype={"站號": str})
        station = pd.DataFrame({
            "站號": ["C0AC60"],
            "緯度": [RELOCATION["current_coordinates"]["lat"]],
            "經度": [RELOCATION["current_coordinates"]["lon"]],
        }).set_index("站號")
        self.assertEqual(RELOCATION["source_revision"], PINNED_REVISION)
        self.assertRegex(RELOCATION["git_blob_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(len(frame), 2)
        for candidate in (frame, frame.iloc[::-1].copy()):
            result = organizer.resolve_station_days(candidate, stations=station)
            self.assertEqual(len(result), 1)
            self.assertEqual(result["站號"].iloc[0], "C0AC60")
            self.assertEqual(result["BlastGRU-TW"].iloc[0],
                             RELOCATION["expected_selected_value"])
            self.assertEqual(result["lat"].iloc[0], station["緯度"].iloc[0])
            self.assertEqual(result["lon"].iloc[0], station["經度"].iloc[0])

    def test_ambiguous_current_station_coordinates_fail_closed(self):
        row_text = "站號,站名,日期,lat,lon,BlastGRU-TW\n" + "\n".join(
            RELOCATION["source_rows"]) + "\n"
        frame = pd.read_csv(io.StringIO(row_text), dtype={"站號": str})
        unknown = pd.DataFrame({"站號": ["C0AC60"], "緯度": [22.0],
                                "經度": [120.0]}).set_index("站號")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            organizer.resolve_station_days(frame, stations=unknown)


if __name__ == "__main__":
    unittest.main()

"""Read-only shadow validation for the opt-in station coverage repair.

Pinned upstream November 20-22 datasets and public 3A golden snapshots.
This is a preview: the scheduled daily organizer still defaults to legacy.
The test never writes production CSVs, touches the website or fetches URLs.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault(
    "RECENT_OUTPUT_FOLDER",
    os.path.join(tempfile.gettempdir(), "coverage-repair-test-output"),
)
from models import recent_forecast_organizer as organizer


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/coverage_shadow/20261120_22_published.json")
    .read_text(encoding="utf-8")
)
V1 = json.loads(
    (ROOT / "tests/fixtures/public_contract_v1/snapshot_20261101.json")
    .read_text(encoding="utf-8")
)
SOURCE_REVISION = "51fb7be702a28ae2133cbc466d409b47a8e5dee8"
KEYS = ["站號", "日期"]


def expected_all_station_ids(day):
    return set().union(*(set(source["all_station_ids"]) for source in day["sources"]))


def source_frame(source, *, use_full_source=False):
    if use_full_source:
        file = ROOT / source["source_path"]
        if not file.is_file():
            raise AssertionError(f"Full-source test requires checkout of {file}")
        return pd.read_csv(file, dtype={"站號": str})
    return pd.read_csv(io.StringIO(source["sample_csv"]), dtype={"站號": str})


def shadow_run(day, *, use_full_source=False):
    """Use only isolated temp CSVs; never change repository source files."""
    frames = {source["model_id"]: source_frame(source, use_full_source=use_full_source)
              for source in day["sources"]}
    with tempfile.TemporaryDirectory() as directory:
        for name, frame in frames.items():
            frame.to_csv(Path(directory) / f'{day["day"]}_{name}.csv', index=False)
        with patch.object(organizer, "DATA_FOLDER", directory), \
             patch.object(organizer, "load_planthopper_data", return_value=None):
            original = organizer.merge_daily_predictions(day["day"])
            explicit_legacy = organizer.merge_daily_predictions(day["day"], coverage_mode="legacy")
            repaired = organizer.merge_daily_predictions(day["day"], coverage_mode="union")
    return frames, original, explicit_legacy, repaired


def key_sorted(frame):
    return frame.sort_values(KEYS, kind="stable").reset_index(drop=True)


def assert_native_values(testcase, frames, repaired):
    """No source score may be changed or synthesized by coverage joins."""
    lookup = repaired.set_index("站號", verify_integrity=True)
    expected_all = set().union(*(set(f["站號"]) for f in frames.values()))
    testcase.assertEqual(set(lookup.index), expected_all)
    for model, source in frames.items():
        native = source.set_index("站號", verify_integrity=True)[model]
        for station, value in native.items():
            with testcase.subTest(model=model, station=station):
                testcase.assertIn(station, lookup.index)
                found = lookup.loc[station, model]
                if pd.isna(value):
                    testcase.assertTrue(pd.isna(found))
                else:
                    testcase.assertEqual(float(found), float(value))
        for station in expected_all - set(native.index):
            testcase.assertTrue(pd.isna(lookup.loc[station, model]),
                                f"{model}/{station}: missing source must remain null")


class CoverageFixtureProvenanceTests(unittest.TestCase):
    def test_real_sources_have_full_station_set_evidence_not_fake_filled_values(self):
        self.assertEqual(FIXTURE["source_revision"], SOURCE_REVISION)
        self.assertEqual(len(FIXTURE["sample_station_ids"]), 20)
        days = {day["day"]: day for day in FIXTURE["days"]}
        self.assertEqual(set(days), {"20261120", "20261121", "20261122"})
        for day in FIXTURE["days"]:
            with self.subTest(day=day["day"]):
                models = [src["model_id"] for src in day["sources"]]
                self.assertEqual(models,
                                 [m for m in organizer.MODEL_FILES if m in models])
                self.assertEqual(len(expected_all_station_ids(day)), 892)
                for source in day["sources"]:
                    self.assertRegex(source["source_blob_sha"], r"^[a-f0-9]{40}$")
                    self.assertEqual(len(source["all_station_ids"]),
                                     source["original_station_count"])
                    self.assertEqual(len(set(source["all_station_ids"])),
                                     source["original_station_count"])
                    self.assertTrue(set(source_frame(source)["站號"])
                                    <= set(source["all_station_ids"]))
                    self.assertEqual(len(source_frame(source)),
                                     source["sample_station_count"])
        self.assertEqual([s["original_station_count"] for s in days["20261121"]["sources"]],
                         [92, 892, 892, 92, 92, 892])
        self.assertEqual([s["original_station_count"] for s in days["20261122"]["sources"]],
                         [92, 92, 892])

    def test_default_is_legacy_and_sample_shadow_recovers_station_and_values(self):
        for day in FIXTURE["days"]:
            with self.subTest(day=day["day"]):
                frames, default, legacy, union = shadow_run(day)
                pd.testing.assert_frame_equal(default, legacy)
                default_ids = set(default["站號"])
                added_ids = set(union["站號"]) - default_ids
                self.assertFalse(union.duplicated(KEYS).any())
                self.assertTrue(set(default_ids) <= set(union["站號"]))
                self.assertEqual(set(union["站號"]), set(FIXTURE["sample_station_ids"]))
                assert_native_values(self, frames, union)
                # Existing observations must remain exactly equal after opt-in
                # including labels, source score 0, and real null values.
                base = legacy.set_index("站號")
                expanded = union.set_index("站號")
                for sid in default_ids:
                    for col in base.columns:
                        a, b = base.at[sid, col], expanded.at[sid, col]
                        if pd.isna(a):
                            self.assertTrue(pd.isna(b), (day["day"], sid, col))
                        else:
                            self.assertEqual(a, b, (day["day"], sid, col))
                if day["day"] == "20261120":
                    self.assertEqual(len(default), 20)
                    self.assertFalse(added_ids)
                    pd.testing.assert_frame_equal(key_sorted(default), key_sorted(union),
                                                  check_dtype=False)
                else:
                    self.assertEqual(len(default), 6)
                    self.assertEqual(len(union), 20)
                    self.assertEqual(len(added_ids), 14)
                    self.assertIn("C0G910", added_ids)
                    self.assertEqual(union.set_index("站號").at["C0G910", "BLASTAM"],
                                     0.0 if day["day"] == "20261121" else 1.0)

    def test_real_missing_model_values_are_blank_not_synthesized(self):
        for day in FIXTURE["days"]:
            if day["day"] == "20261120":
                continue
            frames, legacy, explicit, union = shadow_run(day)
            target = union.set_index("站號").loc["C0G910"]
            self.assertEqual(target["BLASTAM"], 0.0 if day["day"] == "20261121" else 1.0)
            if day["day"] == "20261121":
                self.assertEqual(target["BlastLSTLS"], 0.88)
                self.assertEqual(target["BLBTSLS"], 0.9660381)
                for model in ("BlastGRU-TW", "BlastTF", "BlastGAT"):
                    self.assertTrue(pd.isna(target[model]), model)
            else:
                self.assertTrue(pd.isna(target["BlastLSTLS"]))
                self.assertTrue(pd.isna(target["BLBTSLS"]))
            self.assertEqual(target["站名"], "花壇")
            self.assertEqual(target["日期"], "2026-" + day["day"][4:6] + "-" + day["day"][6:])

    def test_mixed_epirice_source_native_zero_and_non_crop_sentinel_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            for model, csv in V1["daily_csv"].items():
                (Path(tmp) / f"20261101_{model}.csv").write_text(csv, encoding="utf-8")
            with patch.object(organizer, "DATA_FOLDER", tmp), \
                 patch.object(organizer, "load_planthopper_data", return_value=None):
                before = organizer.merge_daily_predictions("20261101")
                after = organizer.merge_daily_predictions("20261101", coverage_mode="union")
        pd.testing.assert_frame_equal(key_sorted(before), key_sorted(after), check_dtype=False)
        actual = after.set_index("站號")
        for model in ("EPIRICE_BLB", "EPIRICE_LB"):
            self.assertEqual(actual.at["466881", model], -99.0)
            self.assertEqual(actual.at["466881", model + "_status"], "non_crop_period")
            self.assertEqual(actual.at["466990", model], 0.0)
            self.assertEqual(actual.at["466990", model + "_status"], "ok")
            self.assertTrue(pd.isna(actual.at["467990", model]))
            self.assertEqual(actual.at["467990", model + "_status"],
                             "missing_crop_calendar")

    def test_union_fills_missing_metadata_only_and_preserves_base_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = "20261121"
            base = pd.DataFrame([{"站號": "00123", "站名": "base",
                                  "日期": "2026-11-21", "lat": 23.0, "lon": 120.0,
                                  "BlastGRU-TW": 0.0}])
            other = pd.DataFrame([
                {"站號": "00123", "站名": "renamed", "日期": "2026-11-21",
                 "lat": 24.0, "lon": 121.0, "BlastLSTLS": 0.91},
                {"站號": "00999", "站名": "new", "日期": "2026-11-21",
                 "lat": 25.0, "lon": 122.0, "BlastLSTLS": 0.12},
            ])
            base.to_csv(Path(tmp) / f"{d}_BlastGRU-TW.csv", index=False)
            other.to_csv(Path(tmp) / f"{d}_BlastLSTLS.csv", index=False)
            with patch.object(organizer, "DATA_FOLDER", tmp), \
                 patch.object(organizer, "load_planthopper_data", return_value=None):
                old = organizer.merge_daily_predictions(d)
                result = organizer.merge_daily_predictions(d, coverage_mode="union")
        self.assertEqual(old["站號"].tolist(), ["00123"])
        got = result.set_index("站號")
        self.assertEqual(set(got.index), {"00123", "00999"})
        self.assertEqual(got.at["00123", "站名"], "base")
        self.assertEqual(got.at["00123", "lat"], 23.0)
        self.assertEqual(got.at["00123", "BlastGRU-TW"], 0.0)
        self.assertEqual(got.at["00123", "BlastLSTLS"], 0.91)
        self.assertEqual(got.at["00999", "站名"], "new")
        self.assertEqual(got.at["00999", "lat"], 25.0)
        self.assertTrue(pd.isna(got.at["00999", "BlastGRU-TW"]))
        self.assertEqual(got.at["00999", "BlastLSTLS"], 0.12)

    def test_unknown_mode_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "coverage mode"):
            organizer.merge_daily_predictions("20261121", coverage_mode="anything")
        with patch.dict(os.environ, {"RECENT_STATION_COVERAGE_MODE": "typo"}):
            with self.assertRaisesRegex(ValueError, "RECENT_STATION_COVERAGE_MODE"):
                organizer.main()


class FullPublishedCsvShadowTests(unittest.TestCase):
    """Direct whole-file tests run in the dedicated sparse-checkout CI job."""

    def test_full_actual_source_csvs_recover_all_892_without_fabricating_values(self):
        # Fail (rather than skip) if a CI job did not checkout whole source files.
        if os.environ.get("REQUIRE_FULL_COVERAGE_SHADOW") != "1":
            self.skipTest("Dedicated CI job enables REQUIRE_FULL_COVERAGE_SHADOW=1")
        for day in FIXTURE["days"]:
            with self.subTest(date=day["day"]):
                for source in day["sources"]:
                    f = ROOT / source["source_path"]
                    self.assertTrue(f.is_file(), str(f))
                    raw_sha = subprocess.check_output(
                        ["git", "hash-object", str(f)], cwd=ROOT, text=True
                    ).strip()
                    self.assertEqual(raw_sha, source["source_blob_sha"])
                frames, legacy, explicit, repaired = shadow_run(day, use_full_source=True)
                pd.testing.assert_frame_equal(legacy, explicit)
                self.assertEqual(len(repaired), 892)
                self.assertEqual(set(repaired["站號"]), expected_all_station_ids(day))
                self.assertFalse(repaired.duplicated(KEYS).any())
                self.assertTrue(set(legacy["站號"]) <= set(repaired["站號"]))
                self.assertEqual(set(repaired["日期"]), {
                    f'{day["day"][:4]}-{day["day"][4:6]}-{day["day"][6:]}'
                })
                assert_native_values(self, frames, repaired)
                # Validate every existing baseline row and metadata field,
                # not just the 20 frozen value samples.
                baseline = legacy.set_index("站號").sort_index()
                retained = repaired.set_index("站號").loc[baseline.index]
                pd.testing.assert_frame_equal(
                    baseline, retained[baseline.columns], check_dtype=False
                )
                # Explicitly audit a known site missing from incomplete model input.
                self.assertEqual(repaired.set_index("站號").at["C0G910", "BLASTAM"],
                                 0.0 if day["day"] != "20261122" else 1.0)
                if day["day"] == "20261120":
                    self.assertEqual(len(legacy), 892)
                    pd.testing.assert_frame_equal(
                        key_sorted(legacy), key_sorted(repaired), check_dtype=False
                    )
                else:
                    self.assertEqual(len(legacy), 92)
                    self.assertNotIn("C0G910", set(legacy["站號"]))
                    self.assertIn("C0G910", set(repaired["站號"]))
                    self.assertEqual(len(set(repaired["站號"]) - set(legacy["站號"])), 800)


if __name__ == "__main__":
    unittest.main()

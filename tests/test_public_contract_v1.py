"""Read-only golden contract tests for public forecast products used by blast_forecast.

The reference is a *published* 2026-11-01 CSV sample from commit
76bc679bb3225e24b2c7eaa45b4cdb4332a1792b (website audit).
No network, live weather data, production output, or website files are touched.
"""
from __future__ import annotations

import io
import json
import math
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

# The legacy organizer creates OUTPUT_FOLDER during import. Keep that side
# effect outside the checkout and outside published data directories.
os.environ.setdefault("RECENT_OUTPUT_FOLDER", str(Path(tempfile.gettempdir()) / "contract-v1-test-output"))
from models import recent_forecast_organizer as organizer
from models import crop_season_avg as summary

FIXTURE = Path(__file__).parent / "fixtures" / "public_contract_v1" / "snapshot_20261101.json"
SNAPSHOT = json.loads(FIXTURE.read_text(encoding="utf-8"))
DAY = "20261101"
KEYS = ["站號", "日期"]
LEGACY_MODELS = [
    "BlastGRU-TW", "BlastDT2", "BlastLSTLS", "BLBTSLS",
    "BlastTF", "BlastGAT", "BLASTAM", "EPIRICE_BLB", "EPIRICE_LB",
]


class PublicDailyContractTests(unittest.TestCase):
    def test_archive_has_all_legacy_daily_headers_and_five_station_scenarios(self):
        self.assertEqual(SNAPSHOT["source_revision"], "76bc679bb3225e24b2c7eaa45b4cdb4332a1792b")
        self.assertEqual(SNAPSHOT["snapshot_date"], "2026-11-01")
        self.assertEqual(set(SNAPSHOT["daily_csv"]), set(LEGACY_MODELS))
        self.assertEqual(len(SNAPSHOT["stations"]), 5)
        for name in LEGACY_MODELS:
            raw = SNAPSHOT["daily_csv"][name]
            frame = pd.read_csv(io.StringIO(raw), dtype={"站號": str})
            self.assertEqual(list(frame.columns), SNAPSHOT["daily_headers"][name])
            self.assertEqual(set(frame["站號"]), set(SNAPSHOT["stations"]))
            self.assertEqual(frame["日期"].astype(str).unique().tolist(), ["2026-11-01"])
            self.assertTrue({*KEYS, "站名", "lat", "lon", name}.issubset(frame))
            self.assertGreater(SNAPSHOT["daily_snapshot_counts"][name], len(frame))

    def test_legacy_model_identifiers_and_order_are_stable(self):
        actual = list(organizer.MODEL_FILES)
        self.assertEqual([model for model in actual if model in LEGACY_MODELS], LEGACY_MODELS)
        for name in LEGACY_MODELS:
            self.assertEqual(organizer.MODEL_FILES[name], name)
        self.assertEqual(organizer.STATION_ID_COL, "站號")
        self.assertEqual(organizer.DATE_COL, "日期")

    def test_organizer_matches_published_c0g910_csv_without_planthopper(self):
        """Compare actual organizer output to independently published wide CSV.

        The external planthopper feed is explicitly omitted; its absence must
        not alter existing disease/model columns, status or station/day keys.
        """
        with tempfile.TemporaryDirectory() as tmp:
            for model, raw in SNAPSHOT["daily_csv"].items():
                (Path(tmp) / f"{DAY}_{model}.csv").write_text(raw, encoding="utf-8")
            with patch.object(organizer, "DATA_FOLDER", tmp), \
                 patch.object(organizer, "load_planthopper_data", return_value=None):
                actual = organizer.merge_daily_predictions(DAY)
        self.assertEqual(len(actual), len(SNAPSHOT["stations"]))
        self.assertEqual(set(actual["站號"]), set(SNAPSHOT["stations"]))
        self.assertFalse(actual.duplicated(KEYS).any())
        self.assertTrue(actual["日期"].eq("2026-11-01").all())
        row = actual.set_index("站號").loc["C0G910"]
        headers = SNAPSHOT["published_station"]["header"]
        values = SNAPSHOT["published_station"]["row"]
        self.assertEqual(len(headers), len(values))
        for key, recorded in zip(headers, values):
            if key in ("站號", "planthopper"):
                continue
            with self.subTest(field=key):
                self.assertIn(key, row.index)
                value = row[key]
                if recorded == "" or recorded.lower() in ("nan", "null"):
                    self.assertTrue(pd.isna(value), (key, value))
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.assertTrue(math.isclose(float(value), float(recorded), rel_tol=1e-9, abs_tol=1e-10),
                                    (key, value, recorded))
                else:
                    self.assertEqual(str(value), recorded, (key, value, recorded))

    def test_epirice_status_and_valid_zero_retain_distinct_legacy_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            for model, raw in SNAPSHOT["daily_csv"].items():
                (Path(tmp) / f"{DAY}_{model}.csv").write_text(raw, encoding="utf-8")
            with patch.object(organizer, "DATA_FOLDER", tmp), \
                 patch.object(organizer, "load_planthopper_data", return_value=None):
                data = organizer.merge_daily_predictions(DAY).set_index("站號")
        for model in ("EPIRICE_BLB", "EPIRICE_LB"):
            self.assertEqual(data.loc["466881", model], -99.0)
            self.assertEqual(data.loc["466881", f"{model}_status"], "non_crop_period")
            self.assertTrue(pd.isna(data.loc["467990", model]))
            self.assertEqual(data.loc["467990", f"{model}_status"], "missing_crop_calendar")
            self.assertEqual(data.loc["466990", model], 0.0)
            self.assertEqual(data.loc["466990", f"{model}_status"], "ok")

    def test_left_merge_for_classic_models_outer_merge_for_epirice(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pd.DataFrame({"站號": ["S1"], "站名": ["base"], "日期": ["2026-11-01"],
                                 "lat": [24.0], "lon": [120.0], "BlastGRU-TW": [0.4]})
            classic_only = pd.DataFrame({"站號": ["S2"], "站名": ["classic"], "日期": ["2026-11-01"],
                                         "lat": [24.1], "lon": [120.1], "BlastDT2": [0.9]})
            epi_only = pd.DataFrame({"站號": ["S3"], "站名": ["epirice"], "日期": ["2026-11-01"],
                                     "lat": [24.2], "lon": [120.2], "EPIRICE_BLB": [0.0],
                                     "EPIRICE_BLB_status": ["ok"]})
            for model, frame in (("BlastGRU-TW", base), ("BlastDT2", classic_only), ("EPIRICE_BLB", epi_only)):
                frame.to_csv(Path(tmp) / f"{DAY}_{model}.csv", index=False)
            with patch.object(organizer, "DATA_FOLDER", tmp), \
                 patch.object(organizer, "load_planthopper_data", return_value=None):
                result = organizer.merge_daily_predictions(DAY).set_index("站號")
        self.assertEqual(set(result.index), {"S1", "S3"})
        self.assertEqual(result.loc["S1", "BlastGRU-TW"], 0.4)
        self.assertTrue(pd.isna(result.loc["S1", "BlastDT2"]))
        self.assertEqual(result.loc["S3", "EPIRICE_BLB"], 0.0)

    def test_leading_zero_station_id_is_never_cast_to_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = pd.DataFrame({"站號": ["00123"], "站名": ["test"], "日期": ["2026-11-01"],
                                  "lat": [24.0], "lon": [120.0], "BlastGRU-TW": [0.0]})
            frame.to_csv(Path(tmp) / f"{DAY}_BlastGRU-TW.csv", index=False)
            with patch.object(organizer, "DATA_FOLDER", tmp), \
                 patch.object(organizer, "load_planthopper_data", return_value=None):
                result = organizer.merge_daily_predictions(DAY)
        self.assertEqual(result["站號"].tolist(), ["00123"])
        self.assertEqual(result["BlastGRU-TW"].tolist(), [0.0])


class PublicSummaryContractTests(unittest.TestCase):
    def test_seasonal_thresholds_and_coverage_are_pinned(self):
        self.assertEqual(summary.MODEL_RISK_THRESHOLDS["BlastGAT"], 0.23)
        self.assertEqual(summary.DEFAULT_RISK_THRESHOLD_MODELS, 0.5)
        self.assertEqual(summary.RISK_THRESHOLD_PLAN, 0.0)
        with tempfile.TemporaryDirectory() as tmp:
            for ds, gat, gru in (("20261101", .23, .5), ("20261102", .229, .499)):
                pd.DataFrame({"站號": ["C0G910"], "站名": ["花壇"], "lat": [24.0],
                              "lon": [120.0], "BlastGAT": [gat]}).to_csv(
                                  Path(tmp) / f"{ds}_BlastGAT.csv", index=False)
                pd.DataFrame({"站號": ["C0G910"], "站名": ["花壇"], "lat": [24.0],
                              "lon": [120.0], "BlastGRU-TW": [gru]}).to_csv(
                                  Path(tmp) / f"{ds}_BlastGRU-TW.csv", index=False)
            with patch.object(summary, "DATA_FOLDER", tmp):
                counts, stations, coverage, days = summary.accumulate_for_period(
                    2026, date(2026, 11, 1), date(2026, 11, 2), need_plan=False)
        self.assertEqual(days, 2)
        self.assertEqual(counts["C0G910"]["BlastGAT"], 1)
        self.assertEqual(counts["C0G910"]["BlastGRU-TW"], 1)
        self.assertEqual(coverage["BlastGAT"], 2)
        self.assertEqual(coverage["BlastGRU-TW"], 2)
        self.assertEqual(coverage["BLASTAM"], 0)
        self.assertEqual(stations["站號"].tolist(), ["C0G910"])

    def test_summary_output_header_matches_published_v1(self):
        class FrozenDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 1)

        columns = ["站號", "站名", "lat", "lon"]
        empty = pd.DataFrame(columns=columns)
        coverage = {name: 0 for name in summary.MODEL_COLS}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recent_summary.csv"
            with patch.object(summary, "date", FrozenDate), \
                 patch.object(summary, "OUTPUT_CSV", str(path)), \
                 patch.object(summary, "accumulate_for_period",
                              return_value=({}, empty, coverage, 1)), \
                 patch.object(summary, "load_planthopper_avg_snapshot", return_value={}):
                summary.main()
            header = pd.read_csv(path, nrows=0).columns.tolist()
        self.assertEqual(header, SNAPSHOT["recent_summary_header"])
        self.assertEqual(len(header), 20)


if __name__ == "__main__":
    unittest.main()

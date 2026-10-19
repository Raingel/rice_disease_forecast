import json
import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from models.EPIRICE_BLB.output_contract import complete_daily, merge_station_frames
from scripts.migrate_epirice_outputs import migrate_daily, migrate_recent, restore_recent


class OutputContractTests(unittest.TestCase):
    def setUp(self):
        self.stations = pd.DataFrame({"站號": ["S1", "S2", "S3"], "站名": ["甲", "乙", "丙"],
                                      "緯度": [24., 25., 23.], "經度": [120., 121., 122.],
                                      "county": ["臺中市", "臺北市", "未知縣"]})
        self.calendars = {"臺中市": {"rice_area": True, "seasons": [
            {"season": "二期作", "transplant": "8/1", "harvest": "12/31"}]},
                          "臺北市": {"rice_area": False, "seasons": []}}
        self.config = {"duration_days": 120}
        self.frame = pd.DataFrame({"站號": ["S1"], "站名": ["甲"], "日期": ["2026-08-10"],
                                   "lat": [24.], "lon": [120.], "EPIRICE_BLB": [0.12],
                                   "EPIRICE_BLB_rateinf_new_infections": [0.],
                                   "EPIRICE_BLB_intensity_active": [0.12],
                                   "EPIRICE_BLB_latent": [1.5]})

    def test_zero_flow_retains_states_non_crop_is_minus99_unknown_is_missing(self):
        actual = complete_daily(self.frame, "2026-08-10", self.stations, self.calendars, self.config).set_index("站號")
        self.assertEqual(actual.loc["S1", "EPIRICE_BLB"], 0.)
        self.assertEqual(actual.loc["S1", "EPIRICE_BLB_latent"], 1.5)
        self.assertEqual(actual.loc["S2", "EPIRICE_BLB"], -99.)
        self.assertTrue(pd.isna(actual.loc["S2", "EPIRICE_BLB_rateinf_new_infections"]))
        self.assertTrue(pd.isna(actual.loc["S3", "EPIRICE_BLB"]))
        self.assertEqual(actual.loc["S3", "EPIRICE_BLB_status"], "missing_crop_calendar")

    def test_outside_planting_to_harvest_is_non_crop(self):
        actual = complete_daily(pd.DataFrame(), "2026-07-31", self.stations, self.calendars, self.config).set_index("站號")
        self.assertEqual(actual.loc["S1", "EPIRICE_BLB"], -99.)

    def test_missing_in_crop_and_beyond_duration_are_not_sentinels(self):
        for day, expected in [("2026-08-10", "missing_model_output"), ("2026-12-01", "outside_model_duration")]:
            actual = complete_daily(pd.DataFrame(), day, self.stations, self.calendars, self.config).set_index("站號")
            self.assertTrue(pd.isna(actual.loc["S1", "EPIRICE_BLB"]))
            self.assertEqual(actual.loc["S1", "EPIRICE_BLB_status"], expected)

    def test_unsafe_score_only_migration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "without its raw infection"):
            complete_daily(self.frame.drop(columns="EPIRICE_BLB_rateinf_new_infections"), "2026-08-10", self.stations, self.calendars, self.config)

    def test_union_keeps_epirice_only_station_and_existing_model(self):
        base = self.frame[["站號", "站名", "日期", "lat", "lon"]].assign(BlastGAT=0.7)
        epi = complete_daily(self.frame, "2026-08-10", self.stations, self.calendars, self.config)
        merged = merge_station_frames(base, epi).set_index("站號")
        self.assertEqual(set(merged.index), {"S1", "S2", "S3"})
        self.assertEqual(merged.loc["S1", "BlastGAT"], 0.7)
        self.assertEqual(merged.loc["S2", "站名"], "乙")
        self.assertEqual(merged.loc["S2", "lat"], 25.)
        self.assertTrue(pd.isna(merged.loc["S2", "BlastGAT"]))

    def test_duplicate_station_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            complete_daily(pd.concat([self.frame, self.frame]), "2026-08-10", self.stations, self.calendars, self.config)

    def test_unknown_empty_county_remains_empty_on_csv_roundtrip(self):
        stations = self.stations.iloc[:1].copy()
        stations["county"] = ""
        first = complete_daily(pd.DataFrame(), "2026-08-10", stations, self.calendars, self.config)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "daily.csv"
            first.to_csv(path, index=False)
            saved = pd.read_csv(path, dtype={"站號": str})
        again = complete_daily(saved, "2026-08-10", stations, self.calendars, self.config)
        self.assertTrue(again.EPIRICE_BLB_county.isna().all())
        self.assertTrue(again.EPIRICE_BLB.isna().all())
        self.assertEqual(again.EPIRICE_BLB_status.iloc[0], "missing_crop_calendar")


class MigrationTests(unittest.TestCase):
    def test_restore_verifies_git_blob_content_and_removes_only_extra_csv(self):
        content = b"station,date,value\nS1,2026-08-30,2\n"
        sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        inventory = SimpleNamespace(raise_for_status=lambda: None,
                                    json=lambda: [{"type": "file", "name": "S1.csv", "sha": sha}])
        downloaded = SimpleNamespace(raise_for_status=lambda: None, content=content)
        session = SimpleNamespace(get=lambda *args, **kwargs: downloaded)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "extra.csv").write_text("old")
            (path / "keep.txt").write_text("keep")
            with patch("scripts.migrate_epirice_outputs.requests.get", return_value=inventory), patch("scripts.migrate_epirice_outputs.requests.Session", return_value=session):
                self.assertEqual(restore_recent(path, "0" * 40), 1)
            self.assertEqual((path / "S1.csv").read_bytes(), content)
            self.assertFalse((path / "extra.csv").exists())
            self.assertTrue((path / "keep.txt").exists())

    def test_conversion_and_recent_union_are_repeatable_and_preserve_other_values(self):
        fixtures = Path(__file__).parent / "fixtures/epirice_blb"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs, daily, recent = [root / name for name in ["inputs", "data", "recent"]]
            for p in [inputs, daily, recent]:
                p.mkdir()
            (inputs / "regional_calendar.csv").write_bytes((fixtures / "regional_calendar.csv").read_bytes())
            pd.DataFrame({"站號": ["S1", "S2"], "站名": ["甲", "乙"], "緯度": [24., 24.1],
                          "經度": [120., 120.1], "城市": ["臺中市", "臺中市"],
                          "撤站日期": [None, None]}).to_csv(inputs / "station_list.csv", index=False)
            (inputs / "config.json").write_text(json.dumps({"duration_days": 120}))
            old = pd.DataFrame({"站號": ["S1", "S2"], "站名": ["甲", "乙"], "日期": ["2026-08-30"] * 2,
                                "lat": [24., 24.1], "lon": [120., 120.1], "EPIRICE_BLB": [0.1, 0.2],
                                "EPIRICE_BLB_rateinf_new_infections": [2., 0.],
                                "EPIRICE_BLB_intensity_active": [0.1, 0.2], "EPIRICE_BLB_latent": [3., 4.]})
            path = daily / "20260830_EPIRICE_BLB.csv"
            old.to_csv(path, index=False)
            old.iloc[:0].to_csv(daily / "20260101_EPIRICE_BLB.csv", index=False)
            base = old.iloc[:1].copy().assign(BlastGAT=0.75)
            stale = base.copy()
            stale["日期"] = "2026-01-01"
            for col in stale:
                if col == "EPIRICE_BLB" or col.startswith("EPIRICE_BLB_"):
                    stale[col] = float("nan")
            base = pd.concat([base, stale], ignore_index=True)
            base.to_csv(recent / "S1.csv", index=False)
            first = migrate_daily(daily, inputs, date(2026, 8, 30))
            converted = pd.read_csv(path)
            self.assertEqual(converted.EPIRICE_BLB.tolist(), [2., 0.])
            self.assertTrue(pd.read_csv(daily / "20260101_EPIRICE_BLB.csv").EPIRICE_BLB.eq(-99).all())
            second = migrate_daily(daily, inputs, date(2026, 8, 30))
            self.assertEqual(first, second)
            pd.testing.assert_frame_equal(converted, pd.read_csv(path))
            result = migrate_recent(recent, daily, inputs, date(2026, 8, 30))
            self.assertEqual(result["added_station_days"], 1)
            self.assertEqual(pd.read_csv(recent / "S1.csv").BlastGAT.iloc[0], 0.75)
            self.assertEqual(pd.read_csv(recent / "S2.csv").EPIRICE_BLB.iloc[0], 0.)
            self.assertEqual(pd.read_csv(recent / "S2.csv")["日期"].tolist(), ["2026-08-30"])
            self.assertEqual(migrate_recent(recent, daily, inputs, date(2026, 8, 30))["added_station_days"], 0)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from models.EPIRICE_BLB.output_contract import complete_daily
from scripts.integrate_epirice_history_year import integrate_year, read_frame
from scripts.rebuild_epirice_history import prepare


class YearStageTests(unittest.TestCase):
    def test_prepare_2020_uses_frozen_sources_and_keeps_leap_year(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "seed"
            source.mkdir()
            fixtures = Path(__file__).parent / "fixtures/epirice_blb"
            (source / "regional_calendar.csv").write_bytes((fixtures / "regional_calendar.csv").read_bytes())
            pd.DataFrame({"站號": ["S1"], "站名": ["站"], "緯度": [24.], "經度": [120.],
                          "城市": ["臺中市"], "撤站日期": [None]}).to_csv(source / "station_list.csv", index=False)
            (source / "config.json").write_text(json.dumps({"duration_days": 120, "model_version": "test"}))
            with patch("models.EPIRICE_BLB.predict.urlopen", side_effect=AssertionError("Frozen-source test must be offline")):
                manifest = prepare(root / "inputs", date(2020, 12, 31), 25, 2020, source)
            self.assertEqual(manifest["start"], "2020-01-01")
            self.assertEqual(len(pd.date_range(manifest["start"], manifest["end"])), 366)
            self.assertEqual(len(manifest["expected_scenarios"]), 2)
            self.assertTrue(all("2020" in key for key in manifest["expected_scenarios"]))

    def test_annual_union_preserves_existing_models_and_epirice_input_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rebuilt = root / "rebuilt"
            (rebuilt / "data").mkdir(parents=True)
            pd.DataFrame({"站號": ["S1"], "站名": ["站"], "日期": ["2020-08-10"],
                          "lat": [24.], "lon": [120.], "EPIRICE_BLB": [0.1],
                          "EPIRICE_BLB_rateinf_new_infections": [2.],
                          "EPIRICE_BLB_intensity_active": [0.1]}).pipe(
                lambda f: complete_daily(f, "2020-08-10", pd.DataFrame({
                    "站號": ["S1", "S2"], "站名": ["站", "第二站"], "緯度": [24., 24.1],
                    "經度": [120., 120.1], "county": ["臺中市", "臺中市"]}),
                    {"臺中市": {"rice_area": True, "seasons": [
                        {"season": "二期作", "transplant": "8/1", "harvest": "11/1"}]}},
                    {"duration_days": 120, "model_version": "test"}, date(2026, 10, 19))
            ).to_csv(rebuilt / "data/20200810_EPIRICE_BLB.csv", index=False)
            (rebuilt / "rebuild_audit.json").write_text(json.dumps({"model_version": "test", "input_sha256": {}, "method": "test"}))
            prediction = root / "prediction"
            (prediction / "data").mkdir(parents=True)
            path = prediction / "data/20200810_BlastGAT.csv"
            pd.DataFrame({"站號": ["S1", "OLD"], "站名": ["站", "舊站"], "日期": ["2020-08-10"] * 2,
                          "lat": [23.9, 25.], "lon": [120., 121.], "BlastGAT": [0.75, 0.2]}).to_csv(path, index=False)
            original = path.read_bytes()
            report = integrate_year(prediction, 2020, rebuilt)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(report["station_files"], 3)
            self.assertEqual(report["epirice_model_rows"], 1)
            self.assertEqual(report["epirice_input_coordinate_mismatches_with_table_metadata"], 1)
            station = pd.read_csv(prediction / "history_daily_by_station/2020/S1.csv")
            self.assertEqual(station.BlastGAT.iloc[0], 0.75)
            self.assertEqual(station.EPIRICE_BLB.iloc[0], 2.)
            self.assertEqual(station.EPIRICE_BLB_input_lat.iloc[0], 24.)
            old = pd.read_csv(prediction / "history_daily_by_station/2020/OLD.csv")
            self.assertTrue(old.EPIRICE_BLB.isna().all())
            self.assertEqual(old.EPIRICE_BLB_status.iloc[0], "no_epirice_station_metadata")
            reused = integrate_year(prediction, 2020)
            self.assertEqual(reused["existing_epirice_daily_files_preserved"], 1)
            self.assertEqual(reused["epirice_model_rows"], 1)

    def test_conflicting_legacy_values_fail_instead_of_silent_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "daily.csv"
            pd.DataFrame({"站號": ["S1", "S1"], "日期": ["2020-01-01"] * 2,
                          "BlastGAT": [0.2, 0.9]}).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                read_frame(path, "2020-01-01", "BlastGAT")

    def test_empty_renamed_station_alias_cannot_hide_valid_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "daily.csv"
            pd.DataFrame({"站號": ["S1", "S1"], "日期": ["2020-01-01"] * 2,
                          "BlastGAT": [float("nan"), 0.29]}).to_csv(path, index=False)
            frame = read_frame(path, "2020-01-01", "BlastGAT")
            self.assertEqual(len(frame), 1)
            self.assertEqual(frame.BlastGAT.iloc[0], 0.29)

    def test_coordinate_variants_select_only_unique_match_and_keep_other_cases_blank(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "daily.csv"
            pd.DataFrame({"站號": ["S1", "S1", "OLD", "OLD"], "日期": ["2020-01-01"] * 4,
                          "lat": [24., 24.01, 23., 23.01], "lon": [120.] * 4,
                          "BlastGAT": [0.2, 0.9, 0.3, 0.8]}).to_csv(path, index=False)
            reference = pd.DataFrame({"站號": ["S1"], "lat": [24.], "lon": [120.]}).set_index("站號")
            diagnostics = []
            frame = read_frame(path, "2020-01-01", "BlastGAT", reference, diagnostics).set_index("站號")
            self.assertEqual(frame.loc["S1", "BlastGAT"], 0.2)
            self.assertTrue(pd.isna(frame.loc["OLD", "BlastGAT"]))
            self.assertEqual(len(diagnostics), 2)
            self.assertEqual(len(diagnostics[0]["variants"]), 2)


if __name__ == "__main__":
    unittest.main()

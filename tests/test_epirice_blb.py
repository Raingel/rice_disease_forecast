"""Numerical parity, real crop calendar semantics, and stateful integration."""
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from models.EPIRICE_BLB.crop_calendar import crop_dates, parse_calendar
from models.EPIRICE_BLB.model import simulate_epirice_blb
from models.EPIRICE_BLB.predict import build_parser, daily_weather, run, simulate_crop

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "epirice_blb"
CONFIG = ROOT / "models/EPIRICE_BLB/config.json"


class CoreParityTests(unittest.TestCase):
    def test_all_supplied_numeric_columns_match_120_day_reference(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        weather = pd.read_csv(FIXTURES / "example_input.csv")
        expected = pd.read_csv(FIXTURES / "example_output.csv")
        # The integration does not require the NumPy 2-only trapezoid API.
        with patch.object(np, "trapezoid", None, create=True):
            actual = simulate_epirice_blb(weather, config)
        self.assertEqual(actual["date"].tolist(), expected["date"].tolist())
        for col in expected.select_dtypes(include="number"):
            np.testing.assert_allclose(actual[col], expected[col], rtol=0, atol=1e-6, err_msg=col)
        self.assertAlmostEqual(actual["AUDPC_cumulative"].iloc[-1], actual["AUDPC"].iloc[-1])
        self.assertEqual(actual.loc[actual["infectious"].gt(1e-9), "simday"].iloc[0], 27)

    def test_prefix_run_retains_states_but_only_integrates_available_period(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        weather = pd.read_csv(FIXTURES / "example_input.csv")
        full = simulate_epirice_blb(weather, config)
        short = simulate_epirice_blb(weather.iloc[:40].reset_index(drop=True), dict(config, duration_days=40))
        np.testing.assert_allclose(short["infectious"], full["infectious"].iloc[:40])
        np.testing.assert_allclose(short["AUDPC_cumulative"], full["AUDPC_cumulative"].iloc[:40])
        self.assertLess(short["AUDPC"].iloc[0], full["AUDPC"].iloc[0])


class CalendarTests(unittest.TestCase):
    def test_live_sheet_snapshot_uses_timepoint_columns(self):
        calendars = parse_calendar((FIXTURES / "regional_calendar.csv").read_text(encoding="utf-8-sig"))
        self.assertEqual(sum(c["rice_area"] for c in calendars.values()), 16)
        self.assertEqual(sum(len(c["seasons"]) for c in calendars.values()), 31)
        self.assertEqual(calendars["桃園市"]["seasons"][0]["transplant"], "3/6")
        self.assertEqual(calendars["新竹縣"]["seasons"], calendars["桃園市"]["seasons"])
        self.assertEqual(calendars["臺南市"]["seasons"][0]["harvest"], "6/4")
        self.assertFalse(calendars["臺北市"]["rice_area"])
        self.assertEqual(len(calendars["宜蘭縣"]["seasons"]), 1)
        self.assertNotIn("我是範例", calendars)

    def test_renamed_points_take_priority_and_legacy_remains_compatible(self):
        text = (FIXTURES / "regional_calendar.csv").read_text(encoding="utf-8-sig")
        # A malformed descriptive range must not affect representative date parsing.
        changed = text.replace("2月底-3月中", "這是文字範圍")
        self.assertEqual(parse_calendar(text), parse_calendar(changed))
        legacy = (FIXTURES / "regional_calendar_legacy.csv").read_text(encoding="utf-8-sig")
        self.assertEqual(parse_calendar(text), parse_calendar(legacy))
        self.assertEqual(crop_dates({"transplant": "12/15", "harvest": "4/15"}, 2025), (date(2025, 12, 15), date(2026, 4, 15)))


class IntegrationTests(unittest.TestCase):
    def test_hourly_units_complete_days_and_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "weather.csv"
            hourly = pd.DataFrame({"time": pd.date_range("2026-08-02", periods=48, freq="h"),
                                   "temperature_2m": 28, "relativehumidity_2m": 92, "precipitation": 0.5})
            hourly.to_csv(path, index=False)
            daily = daily_weather(path)
            self.assertEqual(daily.iloc[0]["TEMP"], 28)
            self.assertEqual(daily.iloc[0]["RHUM"], 92)
            self.assertEqual(daily.iloc[0]["RAIN"], 12)
            hourly.drop(index=7).to_csv(path, index=False)
            broken = daily_weather(path)
            self.assertTrue(broken.iloc[0].isna().all())
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "Incomplete weather from crop start"):
                simulate_crop(broken, config, date(2026, 8, 2), date(2026, 11, 3), date(2026, 8, 3))

    def test_calendar_to_full_states_daily_files_and_station_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            weather_dir = tmp / "weather"
            weather_dir.mkdir()
            station_path = tmp / "stations.csv"
            pd.DataFrame({"站號": ["C0F9U0"], "站名": ["南屯"], "緯度": [24.137006],
                          "經度": [120.637969], "城市": ["台中市"], "撤站日期": [None]}).to_csv(station_path, index=False)
            hourly = pd.DataFrame({"time": pd.date_range("2026-08-02", periods=60 * 24, freq="h"),
                                   "temperature_2m": 28, "relativehumidity_2m": 92, "precipitation": 0.5})
            hourly.to_csv(weather_dir / "C0F9U0_南屯_24.137006_120.637969.csv", index=False)
            parser = build_parser()
            args = parser.parse_args(["--weather-dir", str(weather_dir), "--calendar", str(FIXTURES / "regional_calendar.csv"),
                                      "--station-list", str(station_path), "--as-of", "2026-09-30", "--start-date", "2026-09-28",
                                      "--end-date", "2026-09-30", "--output-dir", str(tmp / "daily"), "--state-dir", str(tmp / "states")])
            with patch("models.EPIRICE_BLB.predict.urlopen", side_effect=AssertionError("Local run must not fetch remote data")):
                report = run(args)
            self.assertEqual(report["daily_rows"], 3)
            saved_config = json.loads((tmp / "states/inputs/config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_config["aggregation_a"], 4)
            full = pd.read_csv(tmp / "states/2026_2/C0F9U0.csv")
            self.assertEqual(full["date"].iloc[0], "2026-08-02")
            self.assertEqual(full["simday"].iloc[-1], 60)
            self.assertEqual(full["healthy_sites"].iloc[0], 100)
            self.assertIn("senesced_total", full)
            self.assertIn("AUDPC_cumulative", full)
            daily_path = tmp / "daily/20260930_EPIRICE_BLB.csv"
            daily = pd.read_csv(daily_path)
            self.assertEqual(daily["EPIRICE_BLB_simday"].iloc[0], 60)
            self.assertEqual(daily["EPIRICE_BLB"].iloc[0], full["intensity_active"].iloc[-1])
            self.assertIn("EPIRICE_BLB_senescence_sites_rate", daily)
            organizer_path = ROOT / "models/recent_forecast_organizer.py"
            spec = importlib.util.spec_from_file_location("organizer_epirice_test", organizer_path)
            organizer = importlib.util.module_from_spec(spec)
            with patch.dict("os.environ", {"RECENT_OUTPUT_FOLDER": str(tmp / "recent")}):
                spec.loader.exec_module(organizer)
            # Include a legacy base model to exercise the actual multi-model merge.
            base = daily[["站號", "站名", "日期", "lat", "lon"]].assign(**{"BlastGRU-TW": 0.1})
            base.to_csv(tmp / "daily/20260930_BlastGRU-TW.csv", index=False)
            with patch.object(organizer, "DATA_FOLDER", str(tmp / "daily")), patch.object(organizer, "load_planthopper_data", return_value=None):
                merged = organizer.merge_daily_predictions("20260930")
            self.assertIn("EPIRICE_BLB_latent", merged)
            self.assertIn("EPIRICE_BLB_transplant_date", merged)
            self.assertEqual(merged["EPIRICE_BLB_simday"].iloc[0], 60)

    def test_missing_start_cannot_publish_stale_scores(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            (tmp / "weather").mkdir()
            (tmp / "daily").mkdir()
            pd.DataFrame({"站號": ["S1"], "站名": ["站"], "緯度": [24.0], "經度": [120.0],
                          "城市": ["臺中市"], "撤站日期": [None]}).to_csv(tmp / "stations.csv", index=False)
            pd.DataFrame({"time": pd.date_range("2026-09-30", periods=24, freq="h"), "temperature_2m": 28,
                          "relativehumidity_2m": 92, "precipitation": 0}).to_csv(tmp / "weather/S1_站_24.0_120.0.csv", index=False)
            (tmp / "daily/20260930_EPIRICE_BLB.csv").write_text("old stale score", encoding="utf-8")
            args = build_parser().parse_args(["--weather-dir", str(tmp / "weather"), "--calendar", str(FIXTURES / "regional_calendar.csv"),
                                             "--station-list", str(tmp / "stations.csv"), "--start-date", "2026-09-30", "--end-date", "2026-09-30",
                                             "--output-dir", str(tmp / "daily"), "--state-dir", str(tmp / "states")])
            with self.assertRaisesRegex(RuntimeError, "All eligible"):
                run(args)
            self.assertTrue(pd.read_csv(tmp / "daily/20260930_EPIRICE_BLB.csv").empty)
            report = json.loads((tmp / "states/run_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["station_scenarios"][0]["status"], "incomplete_crop_weather")


if __name__ == "__main__":
    unittest.main()

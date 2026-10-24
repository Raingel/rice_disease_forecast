"""Bounded runner integration tests; no network or production writes."""
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from epirice_lb_trial.candidate import load_config as trial_config
from epirice_lb_trial.run_trial import COMPARE_COLUMNS, make_weather
from models.EPIRICE_BLB.crop_calendar import parse_calendar
from models.EPIRICE_BLB.predict import daily_weather
from models.EPIRICE_LB.model import load_config, simulate_epirice_lb
from models.EPIRICE_LB.output_contract import complete_daily, merge_compact
from models.EPIRICE_LB.predict import build_parser, run, simulate_crop

ROOT = Path(__file__).resolve().parents[1]
CALENDAR = ROOT / "tests/fixtures/epirice_blb/regional_calendar.csv"


class LeafBlastRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.weather = self.folder / "weather"
        self.weather.mkdir()
        self.station = pd.DataFrame([{"站號": "C0G910", "站名": "花壇", "緯度": 24.032076,
                                      "經度": 120.549461, "城市": "彰化縣", "撤站日期": np.nan}])
        self.station.to_csv(self.folder / "stations.csv", index=False, encoding="utf-8-sig")
        self.path = self.weather / "C0G910_花壇_24.032076_120.549461.csv"

    def hours(self, start="2026-02-05", end="2026-06-15", drop=None):
        frame = pd.DataFrame({"time": pd.date_range(start, end + "T23:00:00", freq="h"),
                              "temperature_2m": 20, "relativehumidity_2m": 95, "precipitation": 0})
        if drop is not None:
            frame = frame.drop(index=drop)
        frame.to_csv(self.path, index=False)

    def args(self, start="2026-06-02", end="2026-06-04", suffix=""):
        return build_parser().parse_args([
            "--weather-dir", str(self.weather), "--calendar", str(CALENDAR),
            "--station-list", str(self.folder / "stations.csv"), "--station-id", "C0G910",
            "--as-of", "2026-10-23", "--start-date", start, "--end-date", end,
            "--output-dir", str(self.folder / ("data" + suffix)),
            "--state-dir", str(self.folder / ("states" + suffix))])

    def read(self, day, suffix=""):
        return pd.read_csv(self.folder / ("data" + suffix) / f"{day}_EPIRICE_LB.csv")

    def test_parameter_file_matches_independently_verified_trial(self):
        actual, expected = load_config(), trial_config()
        actual.pop("model_version")
        expected.pop("model_version")
        self.assertEqual(actual, expected)

    def test_recent_window_rebuilds_all_120_days_and_preserves_provenance(self):
        self.hours()
        output = self.folder / "data"
        output.mkdir()
        existing_blb = output / "20260604_EPIRICE_BLB.csv"
        existing_blb.write_bytes(b"existing BLB output must remain unchanged\n")
        report = run(self.args())
        self.assertEqual(existing_blb.read_bytes(), b"existing BLB output must remain unchanged\n")
        states = pd.read_csv(self.folder / "states/2026_1/C0G910.csv")
        self.assertEqual((len(states), states.date.iloc[0], states.date.iloc[-1]), (120, "2026-02-05", "2026-06-04"))
        self.assertEqual(report["model_rows"], 3)
        self.assertEqual(report["output_status_counts"], {"ok": 3})
        self.assertEqual(self.read("20260604").EPIRICE_LB_simday.iloc[0], 120)
        self.assertEqual(states.weather_sha256.iloc[0], hashlib.sha256(self.path.read_bytes()).hexdigest())
        expected, _ = simulate_crop(daily_weather(self.path), load_config(), date(2026, 2, 5), date(2026, 6, 15), date(2026, 6, 15))
        np.testing.assert_allclose(states[COMPARE_COLUMNS], expected[COMPARE_COLUMNS], rtol=0, atol=1e-10)
        self.assertTrue((self.folder / "states/inputs/weather_daily/C0G910.csv").is_file())

    def test_non_crop_duration_cap_and_zero_are_distinct(self):
        self.hours()
        report = run(self.args("2026-02-04", "2026-06-16"))
        self.assertEqual(report["output_status_counts"], {"non_crop_period": 2, "ok": 120, "outside_model_duration": 11})
        self.assertEqual(self.read("20260204").EPIRICE_LB.iloc[0], -99)
        self.assertEqual(self.read("20260205").EPIRICE_LB.iloc[0], 0)
        capped = self.read("20260605").iloc[0]
        self.assertTrue(pd.isna(capped.EPIRICE_LB))
        self.assertEqual(capped.EPIRICE_LB_status, "outside_model_duration")

    def test_missing_hour_from_crop_start_fails_without_zero_filling(self):
        self.hours(drop=7)
        with self.assertRaisesRegex(RuntimeError, "valid weather"):
            run(self.args())
        row = self.read("20260604").iloc[0]
        self.assertTrue(pd.isna(row.EPIRICE_LB))
        self.assertEqual(row.EPIRICE_LB_status, "missing_model_output")
        self.assertIn("2026-02-05", row.EPIRICE_LB_missing_reason)
        self.assertFalse((self.folder / "states/2026_1/C0G910.csv").exists())

    def test_incomplete_final_hour_is_not_silently_trimmed(self):
        self.hours(end="2026-06-04", drop=120 * 24 - 1)
        report = run(self.args())
        self.assertEqual(report["output_status_counts"], {"ok": 2, "missing_model_output": 1})
        states = pd.read_csv(self.folder / "states/2026_1/C0G910.csv")
        self.assertEqual((len(states), states.date.iloc[-1]), (119, "2026-06-03"))
        self.assertEqual(report["station_scenarios"][0]["simulation_status"], "weather_endpoint")
        self.assertTrue(pd.isna(self.read("20260604").EPIRICE_LB.iloc[0]))
        self.assertIn("2026-06-04", self.read("20260604").EPIRICE_LB_missing_reason.iloc[0])

    def test_internal_gap_stops_prefix_and_never_resumes_after_gap(self):
        self.hours(end="2026-06-04", drop=118 * 24 + 7)
        run(self.args())
        self.assertEqual(self.read("20260602").EPIRICE_LB_status.iloc[0], "ok")
        for day in ["20260603", "20260604"]:
            row = self.read(day).iloc[0]
            self.assertTrue(pd.isna(row.EPIRICE_LB))
            self.assertIn("2026-06-03", row.EPIRICE_LB_missing_reason)
        # A later refresh fills the gap and rebuilds from transplanting.
        self.hours(end="2026-06-04")
        run(self.args())
        rebuilt = pd.read_csv(self.folder / "states/2026_1/C0G910.csv")
        expected, _ = simulate_crop(daily_weather(self.path), load_config(), date(2026, 2, 5), date(2026, 6, 15), date(2026, 6, 4))
        self.assertEqual(len(rebuilt), 120)
        np.testing.assert_allclose(rebuilt[COMPARE_COLUMNS], expected[COMPARE_COLUMNS], rtol=0, atol=1e-10)

    def test_weather_endpoint_preserves_valid_history_and_marks_later_dates_missing(self):
        self.hours(end="2026-06-02")
        report = run(self.args())
        self.assertEqual(report["output_status_counts"], {"ok": 1, "missing_model_output": 2})
        self.assertEqual(report["station_scenarios"][0]["simulation_status"], "weather_endpoint")
        self.assertIn("Weather ends", self.read("20260603").EPIRICE_LB_missing_reason.iloc[0])

    def test_second_crop_stops_at_harvest_before_120_days(self):
        self.hours("2026-07-27", "2026-11-20")
        report = run(self.args("2026-11-16", "2026-11-17"))
        states = pd.read_csv(self.folder / "states/2026_2/C0G910.csv")
        self.assertEqual(len(states), 113)
        self.assertEqual(states.date.iloc[-1], "2026-11-16")
        self.assertEqual(report["output_status_counts"], {"ok": 1, "non_crop_period": 1})
        self.assertTrue(states.is_forecast.any())

    def test_cross_year_crop_and_early_harvest(self):
        dates = pd.date_range("2025-12-20", "2026-01-10")
        daily = pd.DataFrame({"TEMP": 20, "RHUM": 95, "RAIN": 0}, index=dates)
        states, end = simulate_crop(daily, load_config(), date(2025, 12, 20), date(2026, 1, 5), date(2026, 1, 10))
        self.assertEqual((len(states), end), (17, date(2026, 1, 5)))
        self.assertEqual(states.DOY.iloc[12], 1)

    def test_compact_merge_keeps_blb_and_does_not_copy_full_states(self):
        base = pd.DataFrame({"站號": ["C0G910"], "日期": ["2026-02-05"], "EPIRICE_BLB": [.25], "old_model": [.8]})
        leaf = pd.DataFrame({"站號": ["C0G910", "C0G910"], "日期": ["2026-02-05", "2026-02-06"],
                             "EPIRICE_LB": [0., 1.], "EPIRICE_LB_intensity_active": [0., .1],
                             "EPIRICE_LB_status": ["ok", "ok"], "EPIRICE_LB_healthy_sites": [600, 652.8]})
        merged = merge_compact(base, leaf)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged.EPIRICE_BLB.iloc[0], .25)
        self.assertNotIn("EPIRICE_LB_healthy_sites", merged)
        self.assertEqual(len(merged.columns), len(base.columns) + 3)
        with self.assertRaises(ValueError):
            merge_compact(base, pd.concat([leaf, leaf]))

    def test_missing_weather_file_and_calendar_are_explicit(self):
        with self.assertRaises(RuntimeError):
            run(self.args())
        self.assertIn("Missing current-coordinate", self.read("20260604").EPIRICE_LB_missing_reason.iloc[0])
        stations = self.station.assign(county="未提供縣")
        out = complete_daily(pd.DataFrame(), date(2026, 6, 4), stations, {}, load_config())
        self.assertEqual(out.EPIRICE_LB_status.iloc[0], "missing_crop_calendar")
        self.assertTrue(out.EPIRICE_LB.isna().all())


if __name__ == "__main__":
    unittest.main()

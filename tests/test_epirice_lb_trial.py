"""Bounded leaf-blast trial; no production output or weather downloads."""
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from epirice_lb_trial.candidate import (
    crop_trajectory, load_config, merge_compact, simulate, station_day,
)
from epirice_lb_trial.run_trial import compare, make_weather, output_folder
from models.EPIRICE_BLB.crop_calendar import crop_dates
from models.EPIRICE_BLB.predict import daily_weather

ROOT = Path(__file__).resolve().parents[1]


class LeafBlastTrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wet = simulate(make_weather("constant_wet"))
        cls.dry = simulate(make_weather("constant_dry"))

    def test_fixed_parameter_version_and_full_control_points(self):
        config = load_config()
        self.assertEqual(config["source_commit"], "b331d7a1947e4137ff19f888149ac7078d504765")
        self.assertEqual(len(config["RcA_points"]), 25)
        self.assertEqual(len(config["RcT_points"]), 8)
        self.assertEqual((config["H0"], config["I0"], config["RcOpt"], config["aggregation_a"]), (600, 1, 1.14, 1))
        self.assertEqual((config["latent_period_p_days"], config["infectious_period_i_days"]), (5, 20))

    def test_source_indexing_onset_first_secondary_infection_and_first_last_day(self):
        wet, dry = self.wet.set_index("simday"), self.dry.set_index("simday")
        self.assertEqual(wet.loc[1, "healthy_sites"], 600)
        self.assertEqual(wet.loc[1, "growth_new_sites"], 58.8)
        self.assertEqual(wet.loc[1, "senescence_sites_rate"], 6)
        self.assertAlmostEqual(wet.loc[2, "healthy_sites"], 652.8)
        self.assertEqual(wet.loc[:14, "rateinf_new_infections"].sum(), 0)
        self.assertEqual(wet.loc[15, "rateinf_new_infections"], 1)
        self.assertEqual(wet.loc[15, "infectious"], 0)
        self.assertEqual(dry.loc[15, "RcW"], 0)
        self.assertEqual(dry.loc[15, "rateinf_new_infections"], 1)  # imposed initial scenario
        self.assertEqual(wet.loc[16:21, "rateinf_new_infections"].sum(), 0)
        self.assertGreater(wet.loc[22, "rateinf_new_infections"], 0)
        self.assertEqual(wet.index.tolist(), list(range(1, 121)))
        self.assertEqual(wet.loc[120, "date"], "2026-05-31")

    def test_source_cohort_entry_and_exit_without_secondary_infections(self):
        dry = self.dry.set_index("simday")
        self.assertEqual(dry.loc[16, "latent_cohort_entries"], 1)
        self.assertEqual(dry.loc[21, "rtransfer_L_to_I"], 1)
        self.assertEqual(dry.loc[22, "infectious_cohort_entries"], 1)
        self.assertTrue(dry.loc[22:42, "infectious"].eq(1).all())
        self.assertEqual(dry.loc[43, "infectious"], 0)
        self.assertEqual(dry.loc[43, "removed"], 1)

    def test_temperature_control_points_and_zero_outside_curve(self):
        weather = make_weather("constant_wet").iloc[:10].copy()
        weather["TEMP"] = [8, 10, 15, 20, 25, 30, 35, 40, 45, 47]
        actual = simulate(weather)
        np.testing.assert_allclose(actual.RcT, [0, 0, .5, 1, .6, .2, .05, .01, 0, 0], rtol=0, atol=0)

    def test_wetness_exact_boundaries_and_dry_zero(self):
        weather = make_weather("constant_wet").iloc[:6].copy()
        weather["RHUM"] = [89.999, 90, 90.001, 60, 60, 60]
        weather["RAIN"] = [0, 0, 0, 4.999, 5, 5.001]
        self.assertEqual(simulate(weather).RcW.tolist(), [0, 1, 1, 0, 1, 1])
        self.assertTrue(self.dry.Rc.eq(0).all())

    def test_invalid_missing_or_nonconsecutive_daily_weather_is_rejected(self):
        for field, value in [("TEMP", np.nan), ("RHUM", 101), ("RAIN", -1), ("TEMP", np.inf)]:
            weather = make_weather("constant_wet")
            weather.loc[2, field] = value
            with self.assertRaises(ValueError):
                simulate(weather)
        with self.assertRaisesRegex(ValueError, "consecutive"):
            simulate(make_weather("constant_wet").drop(index=4))

    def test_hourly_missing_data_and_taiwan_day_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weather.csv"
            times = pd.date_range("2026-01-31T16:00:00Z", periods=48, freq="h")
            hourly = pd.DataFrame({"time": times, "temperature_2m": 20,
                                   "relativehumidity_2m": 95, "precipitation": .25})
            hourly.to_csv(path, index=False)
            complete = daily_weather(path)
            self.assertEqual(complete.index[0], pd.Timestamp("2026-02-01"))
            self.assertEqual(complete.RAIN.tolist(), [6., 6.])
            hourly.drop(index=8).to_csv(path, index=False)
            missing = daily_weather(path)
            self.assertTrue(missing.iloc[0].isna().all())
            with self.assertRaisesRegex(ValueError, "Incomplete weather"):
                crop_trajectory(missing, date(2026, 2, 1), date(2026, 5, 1), date(2026, 2, 2))

    def test_first_second_cross_year_crop_harvest_and_weather_end(self):
        for entry, year in [({"transplant": "2/5", "harvest": "6/15"}, 2026),
                            ({"transplant": "7/27", "harvest": "11/16"}, 2026),
                            ({"transplant": "12/15", "harvest": "2/15"}, 2025)]:
            start, harvest = crop_dates(entry, year)
            daily = pd.DataFrame({"TEMP": 20., "RHUM": 95., "RAIN": 0.}, index=pd.date_range(start, harvest))
            result = crop_trajectory(daily, start, harvest, harvest)
            self.assertEqual(result.date.iloc[0], start.isoformat())
            self.assertEqual(len(result), min(120, (harvest - start).days + 1))
            short = crop_trajectory(daily, start, harvest, start + timedelta(days=39))
            np.testing.assert_array_equal(short.infectious, result.infectious.iloc[:40])
            np.testing.assert_array_equal(short.AUDPC_cumulative, result.AUDPC_cumulative.iloc[:40])
        self.assertLess(len(result), 120)

    def test_output_zero_non_crop_missing_and_duration_are_distinct(self):
        station = {"站號": "S1", "站名": "test", "緯度": 24., "經度": 120.}
        start, harvest = date(2026, 2, 1), date(2026, 6, 15)
        cases = [(date(2026, 1, 31), self.wet, -99., "non_crop_period"),
                 (date(2026, 2, 1), self.wet, 0., "ok"),
                 (date(2026, 2, 1), self.wet.iloc[:0], np.nan, "missing_model_output"),
                 (date(2026, 6, 1), self.wet, np.nan, "outside_model_duration"),
                 (date(2026, 6, 16), self.wet, -99., "non_crop_period")]
        for day, states, expected, status in cases:
            frame = station_day(states, station, day, start, harvest)
            self.assertEqual(frame.EPIRICE_LB_status.iloc[0], status)
            actual = frame.EPIRICE_LB.iloc[0]
            self.assertTrue(pd.isna(actual)) if pd.isna(expected) else self.assertEqual(actual, expected)

    def test_full_daily_file_and_compact_merge_do_not_overwrite_blb(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            blb = tmp / "20260222_EPIRICE_BLB.csv"
            original = b"existing BLB output must stay byte-identical\n"
            blb.write_bytes(original)
            station = {"站號": "S1", "站名": "test", "緯度": 24., "經度": 120.}
            leaf = station_day(self.wet, station, date(2026, 2, 22), date(2026, 2, 1), date(2026, 6, 15))
            leaf.to_csv(tmp / "20260222_EPIRICE_LB.csv", index=False)
            self.assertIn("EPIRICE_LB_intensity_active", leaf)
            self.assertIn("EPIRICE_LB_RcOpt", leaf)
            self.assertEqual(leaf.EPIRICE_LB.iloc[0], leaf.EPIRICE_LB_rateinf_new_infections.iloc[0])
            base = leaf[["站號", "站名", "日期", "lat", "lon"]].assign(EPIRICE_BLB=.25, EPIRICE_BLB_latent=7.)
            merged = merge_compact(base, leaf)
            self.assertEqual(merged.EPIRICE_BLB.tolist(), [.25])
            self.assertEqual(merged.EPIRICE_BLB_latent.tolist(), [7.])
            self.assertEqual(merged.EPIRICE_LB.tolist(), leaf.EPIRICE_LB.tolist())
            self.assertNotIn("EPIRICE_LB_intensity_active", merged)
            self.assertEqual(blb.read_bytes(), original)
            again = merge_compact(merged, leaf)
            pd.testing.assert_frame_equal(merged, again)
            extra = leaf.assign(**{"站號": "S2"})
            self.assertEqual(set(merge_compact(base, extra)["站號"]), {"S1", "S2"})
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                merge_compact(base, pd.concat([leaf, leaf]))

    def test_blb_parameters_are_unchanged_by_trial(self):
        path = ROOT / "models/EPIRICE_BLB/config.json"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        blb = json.loads(path.read_text(encoding="utf-8"))
        simulate(make_weather("constant_wet"))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual((blb["H0"], blb["aggregation_a"], blb["onset_index"]), (100, 4, 20))

    def test_trial_refuses_writing_into_production_data_or_code(self):
        for path in [ROOT, ROOT / "rice_blast_prediction/lb_trial", ROOT / "models/lb_trial"]:
            with self.assertRaises(ValueError):
                output_folder(path)

    def test_cross_language_check_cannot_pass_without_R_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Original R execution is required"):
                compare(Path(tmp))
            self.assertFalse((Path(tmp) / "comparison.json").exists())


if __name__ == "__main__":
    unittest.main()

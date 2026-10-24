import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault("RECENT_OUTPUT_FOLDER", os.path.join(tempfile.gettempdir(), "epirice-recent-test"))
from models.recent_forecast_organizer import resolve_station_days, normalize_daily_frame, merge_daily_predictions


class RelocatedStationTests(unittest.TestCase):
    def setUp(self):
        self.stations = pd.DataFrame({"站號": ["C0AC60"], "緯度": [24.934969], "經度": [121.367013]}).set_index("站號")
        self.frame = pd.DataFrame({"站號": ["C0AC60"] * 2, "日期": ["2026-10-19"] * 2,
                                   "lat": [24.939025, 24.934969], "lon": [121.369728, 121.367013],
                                   "BlastGRU-TW": [.1867639, .20114526]})

    def test_current_coordinate_value_selected_in_either_order(self):
        for frame in [self.frame, self.frame.iloc[::-1]]:
            out = resolve_station_days(frame, self.stations)
            self.assertEqual(len(out), 1)
            self.assertEqual(out["BlastGRU-TW"].iloc[0], .20114526)

    def test_identical_repeats_collapse_without_station_download(self):
        frame = pd.concat([self.frame.iloc[:1]] * 2)
        with patch("models.recent_forecast_organizer.current_station_coordinates", side_effect=AssertionError):
            self.assertEqual(len(resolve_station_days(frame)), 1)

    def test_conflicting_same_coordinate_values_are_rejected(self):
        f = pd.concat([self.frame.iloc[1:]] * 2, ignore_index=True)
        f.loc[1, "BlastGRU-TW"] = .9
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            resolve_station_days(f, self.stations)

    def test_no_matching_current_coordinate_is_rejected(self):
        stations = self.stations.copy()
        stations["緯度"] = 22
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            resolve_station_days(self.frame, stations)

    def test_unique_historical_coordinate_is_preserved(self):
        pd.testing.assert_frame_equal(resolve_station_days(self.frame.iloc[:1]), self.frame.iloc[:1])

    def test_merge_all_models_including_epirice_without_cartesian_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.frame.to_csv(os.path.join(tmp, "20261019_BlastGRU-TW.csv"), index=False)
            self.frame.rename(columns={"BlastGRU-TW": "BLBTSLS"}).to_csv(os.path.join(tmp, "20261019_BLBTSLS.csv"), index=False)
            ep = self.frame.iloc[1:].drop(columns="BlastGRU-TW").assign(EPIRICE_BLB=0., EPIRICE_BLB_rateinf_new_infections=0.)
            ep.to_csv(os.path.join(tmp, "20261019_EPIRICE_BLB.csv"), index=False)
            with patch("models.recent_forecast_organizer.DATA_FOLDER", tmp), patch("models.recent_forecast_organizer.current_station_coordinates", return_value=self.stations), patch("models.recent_forecast_organizer.load_planthopper_data", return_value=None):
                out = merge_daily_predictions("20261019")
            self.assertEqual(len(out), 1)
            self.assertEqual(out.EPIRICE_BLB.iloc[0], 0)
            self.assertEqual(out.BLBTSLS.iloc[0], .20114526)


if __name__ == "__main__":
    unittest.main()

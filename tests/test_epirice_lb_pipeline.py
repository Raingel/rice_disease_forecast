"""Daily-entry and compact organizer regression using frozen real weather."""
import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from models.EPIRICE_LB.daily import daily_arguments
from models.EPIRICE_LB.predict import run
from models import recent_forecast_organizer as organizer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/epirice_lb_huatan"


class DailyLeafBlastTests(unittest.TestCase):
    def test_entry_defaults_and_manual_date_contract(self):
        args = daily_arguments({"PIPELINE_ROOT": "/project", "ERA5_OUTPUT_DIR": "/weather", "DATA_FOLDER": "/data"})
        self.assertTrue(args.all_stations)
        self.assertIsNone(args.station_id)
        self.assertEqual(str(args.weather_dir), str(Path("/weather")))
        self.assertEqual(args.state_dir, Path("/project/rice_blast_prediction/epirice_lb_states"))
        self.assertIsNone(args.start_date)
        for env in [{"EPIRICE_BACKFILL_START_DATE": "2026-01-01"}, {"EPIRICE_LB_BACKFILL_END_DATE": "2026-01-02"}]:
            with self.assertRaises(ValueError):
                daily_arguments(env)
        args = daily_arguments({"EPIRICE_BACKFILL_START_DATE": "2026-06-02", "EPIRICE_BACKFILL_END_DATE": "2026-06-04"})
        self.assertEqual(args.start_date.isoformat(), "2026-06-02")

    def test_all_station_run_to_three_compact_columns_preserves_other_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weather = root / "weather"
            weather.mkdir()
            (weather / "C0G910_花壇_24.032076_120.549461.csv").write_bytes(gzip.decompress((FIXTURE / "hourly.csv.gz").read_bytes()))
            stations = pd.read_csv(FIXTURE / "station_list.csv", dtype={"站號": str})
            missing = stations.copy()
            missing["站號"] = "NO_WEATHER"
            pd.concat([stations, missing]).to_csv(root / "stations.csv", index=False)
            env = {"PIPELINE_ROOT": str(root), "ERA5_OUTPUT_DIR": str(weather),
                   "EPIRICE_CALENDAR_SOURCE": str(FIXTURE / "regional_calendar.csv"),
                   "EPIRICE_STATIONS_SOURCE": str(root / "stations.csv"),
                   "EPIRICE_BACKFILL_START_DATE": "2026-06-02", "EPIRICE_BACKFILL_END_DATE": "2026-06-04"}
            report = run(daily_arguments(env))
            self.assertEqual(report["output_status_counts"], {"ok": 3, "missing_model_output": 3})
            data = root / "rice_blast_prediction/data"
            full = pd.read_csv(data / "20260604_EPIRICE_LB.csv")
            self.assertIn("EPIRICE_LB_healthy_sites", full)
            self.assertIn("EPIRICE_LB_weather_sha256", full)
            states = pd.read_csv(root / "rice_blast_prediction/epirice_lb_states/2026_1/C0G910.csv")
            self.assertEqual(len(states), 120)
            base = pd.DataFrame({"站號": ["C0G910"], "站名": ["花壇"], "日期": ["2026-06-04"],
                                 "lat": [24.032076], "lon": [120.549461], "BlastGRU-TW": [.8]})
            base.to_csv(data / "20260604_BlastGRU-TW.csv", index=False)
            blb = base.drop(columns="BlastGRU-TW").assign(EPIRICE_BLB=.25, EPIRICE_BLB_rateinf_new_infections=.25, EPIRICE_BLB_healthy_sites=100.)
            blb.to_csv(data / "20260604_EPIRICE_BLB.csv", index=False)
            no_lb = {k: v for k, v in organizer.MODEL_FILES.items() if k != "EPIRICE_LB"}
            with patch.object(organizer, "DATA_FOLDER", str(data)), patch.object(organizer, "load_planthopper_data", return_value=None):
                with patch.object(organizer, "MODEL_FILES", no_lb):
                    before = organizer.merge_daily_predictions("20260604")
                after = organizer.merge_daily_predictions("20260604")
            self.assertEqual(len(after), 2)  # LB-only missing-weather station survives union.
            valid = after[after["站號"].eq("C0G910")].reset_index(drop=True)
            pd.testing.assert_frame_equal(valid[before.columns], before.reset_index(drop=True))
            self.assertEqual(set(after.columns) - set(before.columns), {"EPIRICE_LB", "EPIRICE_LB_intensity_active", "EPIRICE_LB_status"})
            unavailable = after[after["站號"].eq("NO_WEATHER")].iloc[0]
            self.assertTrue(pd.isna(unavailable.EPIRICE_LB))
            self.assertEqual(unavailable.EPIRICE_LB_status, "missing_model_output")
            self.assertNotIn("EPIRICE_LB_healthy_sites", after)
            # Daily and BLASTAM share this same organizer path.
            with patch.object(organizer, "DATA_FOLDER", str(data)), patch.object(organizer, "load_planthopper_data", return_value=None):
                (data / "20260604_EPIRICE_LB.csv").unlink()
                unchanged = organizer.merge_daily_predictions("20260604")
            pd.testing.assert_frame_equal(unchanged, before)


if __name__ == "__main__":
    unittest.main()

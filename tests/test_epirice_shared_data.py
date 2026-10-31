"""Shared EPIRICE crop, station, weather, and source regression tests.

Run against temp files, not live station/weather endpoints or production output.
"""
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from models.epirice import crop_calendar, stations, weather, sources
from models.EPIRICE_BLB import crop_calendar as legacy_crop
from models.EPIRICE_BLB import output_contract as blb_contract
from models.EPIRICE_BLB import predict as blb_predict

FIXTURES = Path(__file__).parent / "fixtures" / "epirice_blb"


class SharedDataTests(unittest.TestCase):
    def test_original_import_paths_still_refer_to_shared_functions(self):
        self.assertIs(legacy_crop.parse_calendar, crop_calendar.parse_calendar)
        self.assertIs(legacy_crop.crop_dates, crop_calendar.crop_dates)
        self.assertIs(legacy_crop.normalize_county, crop_calendar.normalize_county)
        self.assertIs(blb_contract.crop_context, crop_calendar.crop_context)
        self.assertIs(blb_predict.daily_weather, weather.daily_weather)
        self.assertIs(blb_predict.load_stations, stations.load_stations)
        self.assertIs(blb_predict.station_weather_files, stations.station_weather_files)
        self.assertEqual(blb_predict.HOURLY_COLS, weather.HOURLY_COLS)

    def test_representative_crop_dates_and_cross_year_status(self):
        text = (FIXTURES / "regional_calendar.csv").read_text(encoding="utf-8-sig")
        calendars = crop_calendar.parse_calendar(text)
        self.assertEqual(sum(v["rice_area"] for v in calendars.values()), 16)
        self.assertEqual(sum(len(v["seasons"]) for v in calendars.values()), 31)
        self.assertEqual(crop_calendar.normalize_county(" 台中市 "), "臺中市")
        self.assertEqual(crop_calendar.crop_dates({"transplant": "12/15", "harvest": "4/15"}, 2025),
                         (date(2025, 12, 15), date(2026, 4, 15)))
        cross = {"臺中市": {"rice_area": True, "seasons": [
            {"season": "二期作", "transplant": "12/15", "harvest": "4/15"}]}}
        active = crop_calendar.crop_context(date(2026, 1, 3), "臺中市", cross, 120)
        self.assertEqual(active["status"], "missing_model_output")
        self.assertEqual(active["transplant_date"], "2025-12-15")
        self.assertEqual(active["harvest_date"], "2026-04-15")
        self.assertEqual(crop_calendar.crop_context(date(2026, 4, 14), "臺中市", cross, 120)["status"],
                         "outside_model_duration")
        self.assertEqual(crop_calendar.crop_context(date(2026, 4, 16), "臺中市", cross, 120)["status"],
                         "non_crop_period")
        self.assertEqual(crop_calendar.crop_context(date(2026, 1, 3), "未知縣", cross, 120)["status"],
                         "missing_crop_calendar")

    def test_station_list_filters_retired_and_rejects_duplicates(self):
        incoming = pd.DataFrame({
            "站號": ["S01", "S02"],
            "站名": ["現在站", "撤站"],
            "緯度": [24.0, 24.1], "經度": [120.0, 120.1],
            "城市": ["台中市", "台中市"],
            "撤站日期": [np.nan, "2020-01-01"],
        })
        selected = stations.load_stations(incoming.to_csv(index=False))
        self.assertEqual(selected["站號"].tolist(), ["S01"])
        self.assertEqual(selected["county"].iloc[0], "臺中市")
        incoming.loc[1, ["站號", "撤站日期"]] = ["S01", np.nan]
        with self.assertRaisesRegex(ValueError, "duplicate station IDs"):
            stations.load_stations(incoming.to_csv(index=False))

    def test_weather_csv_requires_24_valid_taiwan_hours(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "weather.csv"
            timestamps = pd.date_range("2026-01-31T16:00:00Z", periods=48, freq="h")
            hourly = pd.DataFrame({
                "time": timestamps, "temperature_2m": 27.0,
                "relativehumidity_2m": 90.0, "precipitation": 0.25,
            })
            hourly.to_csv(path, index=False)
            daily = weather.daily_weather(path)
            self.assertEqual(daily.index[0], pd.Timestamp("2026-02-01"))
            self.assertEqual(daily["RAIN"].tolist(), [6.0, 6.0])
            self.assertEqual(daily["TEMP"].tolist(), [27.0, 27.0])
            hourly.drop(index=7).to_csv(path, index=False)
            self.assertTrue(weather.daily_weather(path).iloc[0].isna().all())
            hourly.loc[7, "relativehumidity_2m"] = 101
            hourly.to_csv(path, index=False)
            self.assertTrue(weather.daily_weather(path).iloc[0].isna().all())
            hourly.loc[7, "relativehumidity_2m"] = 90
            hourly.loc[8, "time"] = hourly.loc[9, "time"]
            hourly.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Duplicate hourly weather timestamps"):
                weather.daily_weather(path)

    def test_current_coordinates_and_live_station_name(self):
        frame = pd.DataFrame({"站號": ["S01"], "站名": ["現名"],
                              "緯度": [24.0], "經度": [120.0]})
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            old = folder / "S01_舊名_24.000000_120.000000.csv"
            live = folder / "S01_現名_24.0_120.0.csv"
            stale = folder / "S01_現名_24.001_120.0.csv"
            for file in (old, live, stale):
                file.touch()
            self.assertEqual(stations.station_weather_files(folder, frame), {"S01": live})
            (folder / "S01_現名_24.000000_120.000000.csv").touch()
            with self.assertRaisesRegex(ValueError, "Multiple current-coordinate"):
                stations.station_weather_files(folder, frame)

    def test_local_source_bom_and_legacy_urlopen_patch(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sheet.csv"
            path.write_bytes(b"\xef\xbb\xbfhello,world\n")
            self.assertEqual(sources.read_source(str(path)), "hello,world\n")
            self.assertEqual(blb_predict.read_source(str(path)), "hello,world\n")
        with patch("models.EPIRICE_BLB.predict.urlopen") as opener:
            opener.return_value.__enter__.return_value.read.return_value = b"\xef\xbb\xbfok"
            self.assertEqual(blb_predict.read_source("https://example.org/source.csv"), "ok")
            opener.assert_called_once()
            self.assertEqual(opener.call_args.kwargs["timeout"], 90)


if __name__ == "__main__":
    unittest.main()

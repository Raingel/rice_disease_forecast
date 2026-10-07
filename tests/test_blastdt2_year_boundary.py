"""Regressions for annual warm-up rows and missing upstream predictions."""

import importlib.util
from pathlib import Path
import unittest

import pandas as pd

MODULE = Path(__file__).resolve().parents[1] / 'models/BlastDT2/record_normalization.py'
spec = importlib.util.spec_from_file_location('blastdt2_records', MODULE)
records = importlib.util.module_from_spec(spec)
spec.loader.exec_module(records)


def annual_frame(dates, values):
    return pd.DataFrame({'站號': 'C2M920', '站名': '朴子農改',
                         'Date': dates, 'lat': 23.469889, 'lon': 120.259383,
                         'BlastDT2': values})


class YearBoundaryTests(unittest.TestCase):
    def test_puzi_dec31_valid_value_survives_next_year_warmup(self):
        previous = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31'], [True]), 2025)
        current = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31', '2026-01-01'], [None, False]), 2026)
        result = records.merge_station_predictions([previous, current])
        jan5 = result[result['日期'].eq(pd.Timestamp('2026-01-05'))]
        self.assertEqual(len(jan5), 1)
        self.assertEqual(jan5.iloc[0]['BlastDT2'], 1.0)
        self.assertEqual(result.iloc[1]['日期'], pd.Timestamp('2026-01-06'))
        self.assertEqual(result.iloc[1]['BlastDT2'], 0.0)

    def test_missing_actual_year_prediction_stays_missing(self):
        result = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31'], [None]), 2025)
        self.assertTrue(pd.isna(result.iloc[0]['BlastDT2']))

    def test_warmup_does_not_create_prediction_when_previous_year_is_absent(self):
        result = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31'], [None]), 2026)
        self.assertTrue(result.empty)

    def test_exact_repeats_can_be_collapsed(self):
        frame = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31'], [True]), 2025)
        self.assertEqual(len(records.merge_station_predictions([frame, frame])), 1)

    def test_real_value_conflict_is_rejected(self):
        positive = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31'], [True]), 2025)
        negative = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31'], [False]), 2025)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            records.merge_station_predictions([positive, negative])

    def test_coordinate_conflict_is_rejected(self):
        frame = records.normalize_station_year_predictions(
            annual_frame(['2025-12-31'], [True]), 2025)
        moved = frame.copy()
        moved['lat'] = 23.5
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            records.merge_station_predictions([frame, moved])

    def test_unknown_prediction_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            records.normalize_station_year_predictions(
                annual_frame(['2025-12-31'], ['unknown']), 2025)

    def test_bad_source_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Invalid infection date'):
            records.normalize_station_year_predictions(
                annual_frame(['not-a-date'], [True]), 2025)


if __name__ == '__main__':
    unittest.main()

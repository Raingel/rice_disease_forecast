"""Annual multi-location transport tests; no live API or production writes."""
import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from maintain_weather_archive import FIELDS
from weather_archive_batch import estimated_calls, supplement
from weather_archive_action import run
from test_weather_archive_action import MemoryGitHub


class AnnualBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inventory = self.root / 'inputs'
        self.inventory.mkdir()
        self.stations = [{'station_id': 'A', 'lat': 24, 'lon': 120, 'archive_entries': []},
                         {'station_id': 'B', 'lat': 25, 'lon': 121, 'archive_entries': []}]
        self.first, self.last = date(2025, 1, 1), date(2025, 12, 31)
        self.write_inputs()
        self.args = SimpleNamespace(inventory=self.inventory, cache=self.root / 'cache',
            start=self.first, through=self.last, station=None, max_stations=2,
            max_seconds=600, max_requests=10, request_pause_seconds=0)
        self.reserved = []

    def write_inputs(self):
        for name, content in {
            'plan.json': {'start': str(self.first), 'through': str(self.last)},
            'source_snapshot.json': {'repository': 'test/repo', 'snapshot_commit': 'frozen'},
            'station_matches.json': {'matched': [], 'missing_current_coordinates': self.stations},
        }.items():
            (self.inventory / name).write_text(json.dumps(content), encoding='utf-8')

    def reserve(self, ids, first, last, weight):
        self.reserved.append((ids, first, last, weight))
        return True

    def response(self, params, hole=False):
        times = pd.date_range(params['start_date'], pd.Timestamp(params['end_date']) + pd.Timedelta(hours=23), freq='h')
        locations = []
        for index, latitude in enumerate(params['latitude'].split(',')):
            temperature = 20. if float(latitude) == 24 else 30.
            hourly = {'time': times.astype(str).tolist(), 'temperature_2m': [temperature] * len(times),
                      'relative_humidity_2m': [80.] * len(times), 'precipitation': [0.] * len(times),
                      'wind_speed_10m': [3.] * len(times), 'wind_direction_10m': [90.] * len(times)}
            if hole:
                hourly['temperature_2m'][-1] = None
            locations.append({'location_id': index, 'latitude': float(latitude), 'longitude': 120.,
                'utc_offset_seconds': 28800, 'hourly': hourly,
                'hourly_units': {'temperature_2m': '°C', 'relative_humidity_2m': '%',
                                'precipitation': 'mm', 'wind_speed_10m': 'km/h', 'wind_direction_10m': '°'}})
        payload = locations if len(locations) > 1 else locations[0]
        response = Mock(content=json.dumps(payload).encode(), url='https://archive-api.open-meteo.com/v1/archive')
        response.json.return_value = payload
        return response

    def test_two_stations_full_year_in_one_request_with_correct_mapping(self):
        session = Mock()
        session.get.side_effect = lambda url, params, **kw: self.response(params)
        result = supplement(self.args, self.stations, self.reserve, session)
        self.assertEqual(result['http_requests'], 1)
        self.assertAlmostEqual(result['estimated_weighted_calls'], 2 * 365 / 14)
        self.assertEqual(self.reserved[0][:3], (['A', 'B'], self.first, self.last))
        for sid, lat, value in [('A', 24, 20), ('B', 25, 30)]:
            frame = pd.read_csv(self.args.cache / f'{sid}_{lat}_{120 if sid == "A" else 121}/2025.csv.gz')
            self.assertEqual(len(frame), 8760)
            self.assertTrue(frame.temperature_2m.eq(value).all())

    def test_partial_year_resume_preserves_old_cells_and_only_fills_missing(self):
        self.first = self.last = date(2025, 1, 1)
        self.write_inputs()
        self.args.start = self.args.through = self.first
        session = Mock()
        session.get.side_effect = lambda url, params, **kw: self.response(params, hole=True)
        supplement(self.args, self.stations, self.reserve, session)
        session.get.side_effect = lambda url, params, **kw: self.response(params)
        # Existing cells differ from the later response; only the missing one changes.
        path = self.args.cache / 'A_24_120/2025.csv.gz'
        from maintain_weather_archive import clean, save_year
        meta = json.loads(path.with_suffix('.json').read_text())
        frame = clean(pd.read_csv(path))
        frame.iloc[0, 0] = 99
        save_year(path, frame, meta)
        result = supplement(self.args, self.stations, self.reserve, session)
        frame = pd.read_csv(path)
        self.assertEqual(frame.temperature_2m.iloc[0], 99)
        self.assertEqual(frame.temperature_2m.iloc[-1], 20)
        self.assertEqual(result['errors'], {})

    def test_failed_multi_location_request_splits_without_swapping_stations(self):
        session = Mock()
        def reply(url, params, **kwargs):
            if ',' in params['latitude']:
                raise requests.ReadTimeout('temporary batch timeout')
            return self.response(params)
        session.get.side_effect = reply
        result = supplement(self.args, self.stations, self.reserve, session)
        self.assertEqual(result['http_requests'], 3)
        self.assertFalse(result['rate_limited'])
        self.assertEqual(result['errors'], {})

    def test_wrong_units_do_not_become_valid_output(self):
        session = Mock()
        def reply(url, params, **kwargs):
            response = self.response(params)
            payload = response.json()
            for item in payload if isinstance(payload, list) else [payload]:
                item['hourly_units']['wind_speed_10m'] = 'm/s'
            response.json.return_value = payload
            return response
        session.get.side_effect = reply
        result = supplement(self.args, self.stations, self.reserve, session)
        self.assertEqual(set(result['errors']), {'A', 'B'})
        self.assertTrue(pd.read_csv(self.args.cache / 'A_24_120/2025.csv.gz').temperature_2m.isna().all())

    def test_rate_limit_stops_and_quota_denial_makes_no_request(self):
        session = Mock()
        response = Mock(status_code=429)
        session.get.side_effect = requests.HTTPError('quota', response=response)
        result = supplement(self.args, self.stations, self.reserve, session)
        self.assertTrue(result['rate_limited'])
        self.assertEqual(session.get.call_count, 1)
        session.reset_mock()
        result = supplement(self.args, self.stations, lambda *a: False, session)
        self.assertEqual(result['http_requests'], 0)
        session.get.assert_not_called()

    def test_action_persists_quota_before_http_and_reuses_release_checkpoint(self):
        self.first = self.last = date(2025, 1, 1)
        self.write_inputs()
        github = MemoryGitHub()
        args = SimpleNamespace(inventory=self.inventory, cache=self.root / 'cloud-cache',
            max_stations=2, max_seconds=600, max_requests=10, batch_size=2,
            max_weighted_calls=800, daily_weighted_calls=8000)
        def first_reply(url, params, **kwargs):
            self.assertEqual(len(github.progress['weather_request_ledger']), 1)
            return self.response(params, hole=True)
        with patch('weather_archive_batch.requests.Session') as session, patch('weather_archive_batch.time.sleep'):
            session.return_value.get.side_effect = first_reply
            partial = run(args, github)
        self.assertFalse(partial['summary']['complete'])
        self.assertEqual(partial['latest_batch_metrics']['http_requests'], 1)
        args.cache = self.root / 'fresh-cloud-cache'
        with patch('weather_archive_batch.requests.Session') as session, patch('weather_archive_batch.time.sleep'):
            session.return_value.get.side_effect = lambda url, params, **kw: self.response(params)
            final = run(args, github)
        self.assertTrue(final['summary']['complete'])
        self.assertEqual(github.disabled, 1)
        self.assertEqual(len(final['weather_request_ledger']), 2)

    def test_weighted_budget_is_by_days_and_locations(self):
        self.assertEqual(estimated_calls(5, '2025-01-01', '2025-01-14'), 5)
        self.assertAlmostEqual(estimated_calls(5, '2025-01-01', '2025-12-31'), 5 * 365 / 14)
        self.assertAlmostEqual(estimated_calls(5, '2024-01-01', '2024-12-31'), 5 * 366 / 14)

    def test_daily_budget_stops_before_extra_request_and_keeps_progress(self):
        self.first = self.last = date(2025, 1, 1)
        self.write_inputs()
        github = MemoryGitHub()
        args = SimpleNamespace(inventory=self.inventory, cache=self.root / 'limited-cache',
            max_stations=2, max_seconds=600, max_requests=10, batch_size=2,
            max_weighted_calls=800, daily_weighted_calls=1)
        with patch('weather_archive_batch.requests.Session') as session, patch('weather_archive_batch.time.sleep'):
            session.return_value.get.side_effect = lambda url, params, **kw: self.response(params)
            result = run(args, github)
            self.assertEqual(session.return_value.get.call_count, 1)
        self.assertEqual(result['summary']['stations_complete'], 1)
        self.assertFalse(result['summary']['complete'])
        self.assertEqual(sum(e['estimated_weighted_calls'] for e in result['weather_request_ledger']), 1)
        self.assertIn('retry_after_utc', result)
        self.assertEqual(github.disabled, 0)

    def test_wrong_location_order_never_exchanges_station_values(self):
        session = Mock()
        def reply(url, params, **kwargs):
            response = self.response(params)
            payload = response.json()
            if isinstance(payload, list):
                response.json.return_value = list(reversed(payload))
            return response
        session.get.side_effect = reply
        result = supplement(self.args, self.stations, self.reserve, session)
        self.assertEqual(result['http_requests'], 3)
        self.assertEqual(result['errors'], {})
        a = pd.read_csv(self.args.cache / 'A_24_120/2025.csv.gz')
        b = pd.read_csv(self.args.cache / 'B_25_121/2025.csv.gz')
        self.assertTrue(a.temperature_2m.eq(20).all())
        self.assertTrue(b.temperature_2m.eq(30).all())


if __name__ == '__main__':
    unittest.main()

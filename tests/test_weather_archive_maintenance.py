import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from scripts.maintain_weather_archive import clean, FIELDS, run


class WeatherMaintenanceTests(unittest.TestCase):
    def test_invalid_values_are_missing_and_duplicate_times_rejected(self):
        raw = pd.DataFrame({'time': ['2026-01-01'], 'temperature_2m': [np.inf],
                            'relativehumidity_2m': [101], 'precipitation': [-1],
                            'windspeed_10m': [-1], 'winddirection_10m': [361]})
        self.assertTrue(clean(raw).isna().all().all())
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            clean(pd.concat([raw, raw]))

    def test_reuse_patch_preserves_existing_cells_and_resume_skips_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            timestamps = pd.date_range('2026-01-01', periods=48, freq='h')
            old = pd.DataFrame({'time': timestamps, **{c: [20.] * 48 for c in FIELDS}})
            old.loc[7, 'temperature_2m'] = np.nan
            raw = old.to_csv(index=False).encode()
            entry = {'path': 'TEST_station_24_120.csv', 'sha': hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()}
            station = {'station_id': 'TEST', 'lat': 24, 'lon': 120, 'archive_entries': [entry]}
            (root / 'boundaries.json').write_text(json.dumps({'repository': 'owner/repo', 'snapshot_commit': 'frozen'}))
            (root / 'station_matches.json').write_text(json.dumps({'matched': [station], 'missing_current_coordinates': []}))
            fresh = {'hourly': {'time': timestamps.astype(str).tolist(), **{c: [30.] * 48 for c in FIELDS}}}
            legacy_response = Mock(content=raw)
            api_response = Mock(content=json.dumps(fresh).encode(), url='https://archive-api.open-meteo.com/v1/archive')
            api_response.json.return_value = fresh
            args = SimpleNamespace(inventory=root, cache=root / 'cache', start=timestamps[0].date(),
                                   through=timestamps[-1].date(), station=None, max_stations=1,
                                   max_requests=1, max_seconds=60)
            with patch('scripts.maintain_weather_archive.requests.Session') as session:
                session.return_value.get.side_effect = [legacy_response, api_response]
                run(args)
                self.assertEqual(session.return_value.get.call_count, 2)
            saved = pd.read_csv(root / 'cache/TEST_24_120/2026.csv.gz')
            self.assertEqual(saved.temperature_2m.iloc[7], 30.)
            self.assertEqual(saved.temperature_2m.iloc[6], 20.)
            with patch('scripts.maintain_weather_archive.requests.Session') as session:
                run(args)
                session.return_value.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()

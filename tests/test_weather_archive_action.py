import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from maintain_weather_archive import FIELDS, save_year
from weather_archive_action import blob_sha, run, station_folder, pack_archive, unpack_archive, fingerprint, verify_inputs


class MemoryGitHub:
    def __init__(self):
        self.progress = None
        self.blobs = {}
        self.assets = {}
        self.disabled = 0
        self.published = 0

    def snapshot(self):
        return ('head' if self.progress else None, 'tree' if self.progress else None, copy.deepcopy(self.progress))

    def read_blob(self, sha):
        return self.blobs[sha]

    def save_archive(self, station, raw, old=None):
        sha = hashlib.sha256(raw).hexdigest()
        self.assets[sha] = raw
        return {'sha256': sha, 'id': sha}

    def read_archive(self, asset):
        return self.assets[asset['sha256']]

    def call(self, method, path, data=None):
        if method == 'POST' and path == '/git/blobs':
            import base64
            raw = base64.b64decode(data['content'])
            sha = blob_sha(raw)
            self.blobs[sha] = raw
            return {'sha': sha}
        if method == 'GET' and path.startswith('/git/commits/'):
            return {'tree': {'sha': 'tree'}}
        if method == 'PUT' and path.endswith('/disable'):
            self.disabled += 1
            return None
        raise AssertionError((method, path))

    def publish(self, head, tree, entries, progress):
        self.published += 1
        self.progress = copy.deepcopy(progress)
        return 'new-head'


class WeatherActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        inventory = root / 'inputs'
        inventory.mkdir()
        self.station = {'station_id': 'TEST', 'lat': 24, 'lon': 120, 'archive_entries': []}
        for name, value in {
            'plan.json': {'start': '2026-01-01', 'through': '2026-01-01'},
            'source_snapshot.json': {'repository': 'a/b', 'snapshot_commit': 'frozen'},
            'station_matches.json': {'matched': [], 'missing_current_coordinates': [self.station]},
        }.items():
            (inventory / name).write_text(json.dumps(value), encoding='utf-8')
        self.args = SimpleNamespace(inventory=inventory, cache=root / 'cache', max_stations=1,
                                    max_seconds=360, max_requests=2)
        self.github = MemoryGitHub()

    def worker(self, args, missing=False, rate_limited=False):
        folder = args.cache / station_folder(self.station)
        folder.mkdir(exist_ok=True)
        path = folder / '2026.csv.gz'
        frame = pd.DataFrame(20., index=pd.date_range('2026-01-01', periods=24, freq='h'), columns=FIELDS)
        if missing:
            frame.iloc[-1, 0] = float('nan')
        metadata = {'start': '2026-01-01', 'end': '2026-01-01', 'supplements': []}
        save_year(path, frame, metadata)
        return {'rate_limited': rate_limited, 'latest_run_supplement_requests': 1}

    def test_partial_checkpoint_restores_and_disables_only_after_complete(self):
        with patch('weather_archive_action.maintain', side_effect=lambda a: self.worker(a, missing=True)):
            first = run(self.args, self.github)
        self.assertFalse(first['summary']['complete'])
        self.assertEqual(self.github.disabled, 0)
        # A fresh runner has no previous workspace: restore from durable blobs.
        self.args.cache = self.args.cache.parent / 'fresh-cache'
        def resume(args):
            path = args.cache / station_folder(self.station) / '2026.csv.gz'
            self.assertTrue(path.exists())
            self.assertTrue(pd.isna(pd.read_csv(path).temperature_2m.iloc[-1]))
            return self.worker(args)
        with patch('weather_archive_action.maintain', side_effect=resume):
            final = run(self.args, self.github)
        self.assertTrue(final['summary']['complete'])
        self.assertEqual(self.github.disabled, 1)
        with patch('weather_archive_action.maintain') as worker:
            run(self.args, self.github)
            worker.assert_not_called()

    def test_frozen_plan_change_is_rejected(self):
        with patch('weather_archive_action.maintain', side_effect=lambda a: self.worker(a, missing=True)):
            run(self.args, self.github)
        (self.args.inventory / 'plan.json').write_text(json.dumps({'start': '2025-01-01', 'through': '2026-01-01'}))
        with self.assertRaisesRegex(ValueError, 'Frozen plan changed'):
            run(self.args, self.github)

    def test_windows_seed_hashes_migrate_on_linux_without_accepting_changed_plan(self):
        names = ['source_snapshot.json', 'station_matches.json', 'plan.json']
        old = {}
        for name in names:
            path = self.args.inventory / name
            content = json.loads(path.read_text())
            lf = (json.dumps(content, indent=2) + '\n').encode()
            old[name] = hashlib.sha256(lf.replace(b'\n', b'\r\n')).hexdigest()
            path.write_bytes(lf)
        state = {'inputs_sha256': old}
        verify_inputs(state, self.args.inventory)
        self.assertEqual(state['inputs_sha256'], fingerprint(self.args.inventory))
        self.assertEqual(state['input_fingerprint_format'], 'canonical_json_v1')
        changed = {'inputs_sha256': old}
        (self.args.inventory / 'plan.json').write_text(json.dumps({'start': '2013-01-01', 'through': '2026-01-01'}))
        with self.assertRaisesRegex(ValueError, 'Frozen plan changed'):
            verify_inputs(changed, self.args.inventory)

    def test_rate_limit_persists_cooldown_and_next_run_does_no_weather_work(self):
        with patch('weather_archive_action.maintain', side_effect=lambda a: self.worker(a, missing=True, rate_limited=True)):
            run(self.args, self.github)
        self.assertIn('retry_after_utc', self.github.progress)
        with patch('weather_archive_action.maintain') as worker:
            run(self.args, self.github)
            worker.assert_not_called()
        self.assertEqual(self.github.disabled, 0)

    def test_corrupt_year_is_never_marked_or_published_complete(self):
        def corrupt(args):
            result = self.worker(args)
            path = args.cache / station_folder(self.station) / '2026.csv.gz'
            path.write_bytes(b'corrupt')
            return result
        with patch('weather_archive_action.maintain', side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                run(self.args, self.github)
        self.assertEqual(self.github.published, 0)
        self.assertEqual(self.github.disabled, 0)

    def test_archive_is_deterministic_and_restores_only_expected_year_files(self):
        self.args.cache.mkdir()
        self.worker(self.args)
        folder = self.args.cache / station_folder(self.station)
        raw = pack_archive(folder)
        self.assertEqual(raw, pack_archive(folder))
        fresh = self.args.cache / 'restored'
        fresh.mkdir()
        unpack_archive(raw, fresh, date(2026, 1, 1), date(2026, 1, 1))
        self.assertEqual((fresh / '2026.csv.gz').read_bytes(), (folder / '2026.csv.gz').read_bytes())

    def test_archive_rejects_path_traversal(self):
        import io, tarfile
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as tar:
            info = tarfile.TarInfo('../escaped')
            info.size = 1
            tar.addfile(info, io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            unpack_archive(stream.getvalue(), self.args.cache, date(2026, 1, 1), date(2026, 1, 1))


if __name__ == '__main__':
    unittest.main()

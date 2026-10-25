"""Checkpoint weather on a data-only Git branch; no model or forecast writes."""
import argparse
import base64
from datetime import datetime, timedelta, timezone, date
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import sys
import time
import tarfile
from types import SimpleNamespace

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maintain_weather_archive import clean, run as maintain

BRANCH = 'weather-archive-2013'
WORKFLOW = 'weather-archive-backfill.yml'


def blob_sha(data):
    return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()


def fingerprint(folder):
    return {name: hashlib.sha256(json.dumps(json.loads((folder / name).read_text(encoding='utf-8-sig')),
                ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
            for name in ['source_snapshot.json', 'station_matches.json', 'plan.json']}


def verify_inputs(progress, folder):
    current = fingerprint(folder)
    if progress.get('inputs_sha256') == current:
        return
    # Import the initial raw-byte fingerprints only when every input matches
    # exactly apart from LF/CRLF. Do not accept changed coordinates or dates.
    previous = progress.get('inputs_sha256', {})
    compatible = set(previous) == set(current)
    for name in current:
        raw = (folder / name).read_bytes()
        lf = raw.replace(b'\r\n', b'\n')
        variants = {hashlib.sha256(data).hexdigest() for data in [raw, lf, lf.replace(b'\n', b'\r\n')]}
        compatible = compatible and previous.get(name) in variants
    if not compatible:
        raise ValueError('Frozen plan changed; explicit migration is required')
    progress['inputs_sha256'] = current
    progress['input_fingerprint_format'] = 'canonical_json_v1'
    print('Verified legacy input hashes; normalized LF/CRLF fingerprints')


def station_folder(station):
    return f"{station['station_id']}_{station['lat']}_{station['lon']}"


class GitHub:
    def __init__(self, repo, token):
        self.repo = repo
        self.api = f'https://api.github.com/repos/{repo}'
        self.session = requests.Session()
        self.last_write = 0.0
        self.session.headers.update({'Authorization': f'Bearer {token}',
                                     'Accept': 'application/vnd.github+json',
                                     'X-GitHub-Api-Version': '2022-11-28'})

    def call(self, method, path, data=None, missing_ok=False):
        if method in ('POST', 'PATCH', 'PUT'):
            pause = max(0, 1.1 - (time.monotonic() - self.last_write))
            if pause:
                time.sleep(pause)
            self.last_write = time.monotonic()
        response = self.session.request(method, self.api + path, json=data, timeout=(15, 90))
        if missing_ok and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json() if response.content else None

    def read_blob(self, sha):
        result = self.call('GET', '/git/blobs/' + sha)
        if result.get('encoding') != 'base64':
            raise ValueError('Unexpected Git blob encoding')
        raw = base64.b64decode(result['content'])
        if blob_sha(raw) != sha:
            raise ValueError('Git blob checksum mismatch')
        return raw

    def read_archive(self, asset):
        response = self.session.get(self.api + '/releases/assets/' + str(asset['id']),
                                    headers={'Accept': 'application/octet-stream'}, timeout=(15, 120))
        response.raise_for_status()
        raw = response.content
        if hashlib.sha256(raw).hexdigest() != asset['sha256']:
            raise ValueError('Release archive checksum mismatch')
        return raw

    def save_archive(self, station, raw, old=None):
        digest = hashlib.sha256(raw).hexdigest()
        if old and old.get('sha256') == digest:
            return old
        # Hash buckets keep asset counts manageable; old immutable versions remain.
        bucket = hashlib.sha256(station['station_id'].encode()).hexdigest()[0]
        tag = 'weather-archive-2013-' + bucket
        release = self.call('GET', '/releases/tags/' + tag, missing_ok=True)
        if release is None:
            release = self.call('POST', '/releases', {'tag_name': tag,
                'name': 'Hourly weather archive bucket ' + bucket,
                'body': 'Immutable station bundles; see weather-archive-2013 branch progress.json for current hashes.',
                'make_latest': 'false'})
        name = station['station_id'] + '_' + digest + '.tar.gz'
        asset = next((a for a in release.get('assets', []) if a['name'] == name), None)
        if asset is None:
            pause = max(0, 1.1 - (time.monotonic() - self.last_write))
            if pause:
                time.sleep(pause)
            self.last_write = time.monotonic()
            response = self.session.post(f"https://uploads.github.com/repos/{self.repo}/releases/{release['id']}/assets",
                params={'name': name}, data=raw, headers={'Content-Type': 'application/gzip'}, timeout=(15, 120))
            response.raise_for_status()
            asset = response.json()
        if asset['size'] != len(raw) or (asset.get('digest') and asset['digest'] != 'sha256:' + digest):
            raise ValueError('Uploaded release asset identity mismatch')
        return {'id': asset['id'], 'name': name, 'tag': tag, 'size': len(raw),
                'sha256': digest, 'browser_download_url': asset['browser_download_url']}

    def snapshot(self):
        ref = self.call('GET', '/git/ref/heads/' + BRANCH, missing_ok=True)
        if ref is None:
            return None, None, None
        head = ref['object']['sha']
        commit = self.call('GET', '/git/commits/' + head)
        tree = commit['tree']['sha']
        entries = self.call('GET', '/git/trees/' + tree)
        item = next((e for e in entries['tree'] if e['path'] == 'progress.json'), None)
        if item is None:
            raise ValueError('Existing data branch has no progress.json; refusing to initialize over it')
        return head, tree, json.loads(self.read_blob(item['sha']))

    def publish(self, head, tree, entries, progress):
        raw = (json.dumps(progress, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
        entries.append({'path': 'progress.json', 'mode': '100644', 'type': 'blob', 'content': raw.decode()})
        body = {'tree': entries}
        if tree:
            body['base_tree'] = tree
        new_tree = self.call('POST', '/git/trees', body)['sha']
        commit = self.call('POST', '/git/commits', {'message': 'data: checkpoint hourly weather archive',
                           'tree': new_tree, 'parents': [head] if head else []})['sha']
        # Lease plus non-force update: refuse concurrent or manually changed state.
        current = self.call('GET', '/git/ref/heads/' + BRANCH, missing_ok=True)
        if (current['object']['sha'] if current else None) != head:
            raise ValueError('Weather data branch changed concurrently; checkpoint not published')
        if head:
            self.call('PATCH', '/git/refs/heads/' + BRANCH, {'sha': commit, 'force': False})
        else:
            self.call('POST', '/git/refs', {'ref': 'refs/heads/' + BRANCH, 'sha': commit})
        return commit


def summarize(progress, stations, start, through):
    complete, partial = 0, 0
    expected_years = list(range(start.year, through.year + 1))
    for station in stations:
        task = progress['stations'].get(station['station_id'], {})
        years = task.get('years', {})
        done = True
        for year in expected_years:
            item = years.get(str(year), {})
            first, last = max(start, date(year, 1, 1)), min(through, date(year, 12, 31))
            expected = ((last - first).days + 1) * 24
            if (item.get('missing_hours') != 0 or item.get('missing_cells') != 0
                    or item.get('expected_hours') != expected or item.get('start') != str(first)
                    or item.get('end') != str(last) or not item.get('csv_blob') or not item.get('metadata_blob')):
                done = False
                break
        task['complete'] = done
        if task:
            progress['stations'][station['station_id']] = task
        complete += done
        partial += bool(years) and not done
    return {'stations_expected': len(stations), 'stations_complete': complete,
            'stations_partial': partial, 'stations_not_started': len(stations) - complete - partial,
            'complete': complete == len(stations) and bool(stations)}


def validate_year(path, metadata, start, through, year):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata['output_sha256']:
        raise ValueError('Year hash mismatch: ' + str(path))
    frame = clean(pd.read_csv(path))
    first, last = max(start, date(year, 1, 1)), min(through, date(year, 12, 31))
    expected = pd.date_range(first, pd.Timestamp(last) + pd.Timedelta(hours=23), freq='h')
    if not frame.index.equals(expected):
        raise ValueError('Year hourly axis mismatch: ' + str(path))
    if (len(frame) != metadata['expected_hours'] or int(frame.isna().any(axis=1).sum()) != metadata['missing_hours']
            or int(frame.isna().sum().sum()) != metadata['missing_cells']):
        raise ValueError('Year completeness metadata mismatch')
    return raw


def pack_archive(folder):
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as tar:
            for path in sorted(folder.glob('*.csv.*')):
                if not (path.name.endswith('.csv.gz') or path.name.endswith('.csv.json')):
                    continue
                raw = path.read_bytes()
                info = tarfile.TarInfo(path.name)
                info.size, info.mtime = len(raw), 0
                tar.addfile(info, io.BytesIO(raw))
    return stream.getvalue()


def unpack_archive(raw, folder, start, through):
    allowed = {f'{year}.csv.{suffix}' for year in range(start.year, through.year + 1) for suffix in ['gz', 'json']}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
        for member in tar:
            if member.name not in allowed or member.name in seen or not member.isfile() or member.size > 8_000_000:
                raise ValueError('Unsafe or unexpected weather archive entry')
            seen.add(member.name)
            (folder / member.name).write_bytes(tar.extractfile(member).read())


def run_batched(args, github, progress, stations, start, through, head, tree, started):
    from weather_archive_batch import supplement
    batch_size = args.batch_size
    now = datetime.now(timezone.utc)
    horizon = now - timedelta(hours=24)
    progress['weather_request_ledger'] = [entry for entry in progress.get('weather_request_ledger', [])
        if datetime.fromisoformat(entry['at_utc']) > horizon]
    metrics = {'mode': 'annual_multi_location_v1', 'http_requests': 0,
               'estimated_weighted_calls': 0.0, 'stations_processed': 0,
               'max_weighted_calls': args.max_weighted_calls, 'daily_weighted_calls': args.daily_weighted_calls}
    pending = [s for s in stations if not progress['stations'][s['station_id']]['complete']]
    pending.sort(key=lambda s: progress['stations'][s['station_id']].get('last_attempt_utc', ''))
    selected = pending[:args.max_stations]
    args.cache.mkdir(parents=True, exist_ok=True)
    day_blocked = False

    def checkpoint():
        nonlocal head, tree
        progress.update(summary=summarize(progress, stations, start, through),
                        updated_at_utc=datetime.now(timezone.utc).isoformat(),
                        latest_run_id=os.environ.get('GITHUB_RUN_ID'), latest_batch_metrics=metrics.copy())
        head = github.publish(head, tree, [], progress)
        tree = github.call('GET', '/git/commits/' + head)['tree']['sha']

    def reserve(ids, begin, end, weight):
        nonlocal day_blocked
        daily_used = sum(entry['estimated_weighted_calls'] for entry in progress['weather_request_ledger'])
        if metrics['http_requests'] >= args.max_requests or metrics['estimated_weighted_calls'] + weight > args.max_weighted_calls + 1e-9:
            return False
        if daily_used + weight > args.daily_weighted_calls + 1e-9:
            day_blocked = True
            return False
        entry = {'at_utc': datetime.now(timezone.utc).isoformat(), 'station_ids': ids,
                 'start': str(begin), 'end': str(end), 'estimated_weighted_calls': weight}
        progress['weather_request_ledger'].append(entry)
        metrics['http_requests'] += 1
        metrics['estimated_weighted_calls'] += weight
        # Reserve even failed requests and persist before HTTP, so cancellation
        # cannot silently reset the rolling quota estimate on the next runner.
        checkpoint()
        return True

    for offset in range(0, len(selected), batch_size):
        if time.monotonic() - started >= args.max_seconds - 240:
            break
        ready = []
        for station in selected[offset:offset + batch_size]:
            task = progress['stations'][station['station_id']]
            folder = args.cache / station_folder(station)
            folder.mkdir(exist_ok=True)
            task['last_attempt_utc'] = datetime.now(timezone.utc).isoformat()
            try:
                if task.get('archive_asset'):
                    unpack_archive(github.read_archive(task['archive_asset']), folder, start, through)
                else:
                    for year, item in task.get('years', {}).items():
                        for name, key in [(f'{year}.csv.gz', 'csv_blob'), (f'{year}.csv.json', 'metadata_blob')]:
                            (folder / name).write_bytes(github.read_blob(item[key]))
                ready.append(station)
            except (requests.RequestException, ValueError, OSError) as exc:
                task['last_error'] = {'reason': 'Checkpoint restore: ' + str(exc), 'station_id': station['station_id']}
        if not ready:
            checkpoint()
            continue
        remaining = args.max_seconds - (time.monotonic() - started)
        if remaining < 240:
            checkpoint()
            break
        result = supplement(SimpleNamespace(inventory=args.inventory, cache=args.cache, start=start,
            through=through, max_seconds=int(remaining - 90),
            max_requests=args.max_requests - metrics['http_requests'], request_pause_seconds=1), ready, reserve)
        if result['rate_limited']:
            progress['retry_after_utc'] = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        for station in ready:
            sid = station['station_id']
            folder = args.cache / station_folder(station)
            task = progress['stations'][sid]
            task['last_error'] = result['errors'].get(sid)
            for path in sorted(folder.glob('*.csv.gz')):
                year = int(path.name.split('.')[0])
                metadata_path = path.with_suffix('.json')
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                raw = validate_year(path, metadata, start, through, year)
                item = {k: metadata[k] for k in ['start', 'end', 'expected_hours', 'missing_hours', 'missing_cells', 'output_sha256']}
                item.update(csv_blob=blob_sha(raw), metadata_blob=blob_sha(metadata_path.read_bytes()))
                task.setdefault('years', {})[str(year)] = item
            if task.get('years'):
                task['archive_asset'] = github.save_archive(station, pack_archive(folder), task.get('archive_asset'))
            metrics['stations_processed'] += 1
            checkpoint()
        print(json.dumps({'summary': progress['summary'], 'metrics': metrics}), flush=True)
        daily_used = sum(entry['estimated_weighted_calls'] for entry in progress['weather_request_ledger'])
        if day_blocked or daily_used + 1 > args.daily_weighted_calls:
            oldest = min((datetime.fromisoformat(entry['at_utc']) for entry in progress['weather_request_ledger']),
                         default=datetime.now(timezone.utc))
            progress['retry_after_utc'] = (oldest + timedelta(hours=24, minutes=1)).isoformat()
            checkpoint()
        if (result['rate_limited'] or result['exhausted'] or day_blocked or metrics['http_requests'] >= args.max_requests
                or metrics['estimated_weighted_calls'] + 1 > args.max_weighted_calls
                or daily_used + 1 > args.daily_weighted_calls):
            break
    if progress['summary']['complete']:
        github.call('PUT', '/actions/workflows/' + WORKFLOW + '/disable')
    set_output(progress['summary']['complete'])
    return progress


def run(args, github=None):
    started = time.monotonic()
    github = github or GitHub(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'])
    plan = json.loads((args.inventory / 'plan.json').read_text(encoding='utf-8'))
    start, through = date.fromisoformat(plan['start']), date.fromisoformat(plan['through'])
    matched = json.loads((args.inventory / 'station_matches.json').read_text(encoding='utf-8'))
    stations = matched['matched'] + matched['missing_current_coordinates']
    if len({s['station_id'] for s in stations}) != len(stations) or not stations:
        raise ValueError('Station plan must contain unique station ids')
    head, tree, progress = github.snapshot()
    inputs = fingerprint(args.inventory)
    if progress is None:
        progress = {'schema_version': 1, 'inputs_sha256': inputs, 'plan': plan, 'stations': {}}
    verify_inputs(progress, args.inventory)
    summary = summarize(progress, stations, start, through)
    now = datetime.now(timezone.utc)
    cooldown = progress.get('retry_after_utc')
    if cooldown and now < datetime.fromisoformat(cooldown) and not summary['complete']:
        print('Archive cooldown active until ' + cooldown)
        set_output(False)
        return progress
    if hasattr(args, 'batch_size'):
        progress['summary'] = summary
        return run_batched(args, github, progress, stations, start, through, head, tree, started)
    pending = [s for s in stations if not progress['stations'][s['station_id']]['complete']]
    pending.sort(key=lambda s: progress['stations'][s['station_id']].get('last_attempt_utc', ''))
    selected = pending[:args.max_stations]
    args.cache.mkdir(parents=True, exist_ok=True)
    entries = []
    requests_left = args.max_requests
    for station in selected:
        if time.monotonic() - started > args.max_seconds - 180:
            break
        sid, folder_name = station['station_id'], station_folder(station)
        folder = args.cache / folder_name
        folder.mkdir(exist_ok=True)
        task = progress['stations'][sid]
        if task.get('archive_asset'):
            unpack_archive(github.read_archive(task['archive_asset']), folder, start, through)
        else:
            # One-time import of the five checksum-verified local seed stations.
            for year, item in task.get('years', {}).items():
                for name, key in [(f'{year}.csv.gz', 'csv_blob'), (f'{year}.csv.json', 'metadata_blob')]:
                    (folder / name).write_bytes(github.read_blob(item[key]))
        remaining = args.max_seconds - (time.monotonic() - started)
        if remaining < 180:
            break
        task['last_attempt_utc'] = now.isoformat()
        try:
            result = maintain(SimpleNamespace(inventory=args.inventory, cache=args.cache, start=start, through=through,
                station=[sid], max_stations=1, max_requests=requests_left,
                max_seconds=int(remaining - 100), request_pause_seconds=3))
            requests_left -= result['latest_run_supplement_requests']
            task['last_error'] = None
            error_path = folder / 'last_error.json'
            if error_path.exists():
                task['last_error'] = json.loads(error_path.read_text(encoding='utf-8'))
            if result['rate_limited']:
                progress['retry_after_utc'] = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        finally:
            # Publish valid partial years as well, so the next runner can resume.
            for path in sorted(folder.glob('*.csv.gz')):
                year = int(path.name.split('.')[0])
                metadata_path = path.with_suffix('.json')
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                raw = validate_year(path, metadata, start, through, year)
                item = {k: metadata[k] for k in ['start', 'end', 'expected_hours', 'missing_hours', 'missing_cells', 'output_sha256']}
                for file_path, data, key in [(path, raw, 'csv_blob'), (metadata_path, metadata_path.read_bytes(), 'metadata_blob')]:
                    sha = blob_sha(data)
                    item[key] = sha
                task.setdefault('years', {})[str(year)] = item
            if task.get('years'):
                task['archive_asset'] = github.save_archive(station, pack_archive(folder), task.get('archive_asset'))
            summary = summarize(progress, stations, start, through)
            progress.update(summary=summary, updated_at_utc=datetime.now(timezone.utc).isoformat(),
                            latest_run_id=os.environ.get('GITHUB_RUN_ID'))
            head = github.publish(head, tree, entries, progress)
            # Each station is a durable checkpoint, not just a job-end checkpoint.
            tree = github.call('GET', '/git/commits/' + head)['tree']['sha']
            entries = []
            print(json.dumps(summary), flush=True)
        if progress.get('retry_after_utc') and datetime.now(timezone.utc) < datetime.fromisoformat(progress['retry_after_utc']):
            break
        if requests_left <= 0:
            break
    if summary['complete']:
        github.call('PUT', '/actions/workflows/' + WORKFLOW + '/disable')
        print('All planned weather years complete; disabled only ' + WORKFLOW)
    set_output(summary['complete'])
    return progress


def set_output(complete):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
            handle.write(f'complete={str(complete).lower()}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--max-seconds', type=int, default=2100)
    parser.add_argument('--max-stations', type=int, default=50)
    parser.add_argument('--max-requests', type=int, default=120)
    parser.add_argument('--batch-size', type=int, default=5)
    parser.add_argument('--max-weighted-calls', type=float, default=800)
    parser.add_argument('--daily-weighted-calls', type=float, default=8000)
    args = parser.parse_args()
    if (args.max_seconds < 240 or min(args.max_stations, args.max_requests, args.batch_size) < 1
            or args.batch_size > 10 or min(args.max_weighted_calls, args.daily_weighted_calls) < 1):
        parser.error('Require >=240 seconds and positive station/request limits')
    run(args)

"""Local resumable weather cache; never writes to the repository's data folders.

Uses a frozen coordinate match inventory. Imports legacy archive once, validates
hourly values, then patches missing cells in at most 31-day requests. No model runs.
"""
import argparse
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

FIELDS = ['temperature_2m', 'relativehumidity_2m', 'precipitation', 'windspeed_10m', 'winddirection_10m']
API_FIELDS = ['temperature_2m', 'relative_humidity_2m', 'precipitation', 'wind_speed_10m', 'wind_direction_10m']


def write_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def clean(frame):
    frame = frame.rename(columns=dict(zip(API_FIELDS, FIELDS)))
    frame['time'] = pd.to_datetime(frame['time'], errors='raise')
    if frame['time'].dt.tz is not None:
        frame['time'] = frame['time'].dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    if not frame['time'].dt.floor('h').eq(frame['time']).all():
        raise ValueError('Non-hourly timestamps; explicit review required')
    if frame['time'].duplicated().any():
        raise ValueError('Duplicate timestamps; explicit review required')
    frame = frame.set_index('time').sort_index().reindex(columns=FIELDS)
    frame = frame.apply(pd.to_numeric, errors='coerce').replace([np.inf, -np.inf], np.nan)
    frame.loc[~frame.relativehumidity_2m.between(0, 100), 'relativehumidity_2m'] = np.nan
    for col in ['precipitation', 'windspeed_10m']:
        frame.loc[frame[col].lt(0), col] = np.nan
    frame.loc[~frame.winddirection_10m.between(0, 360), 'winddirection_10m'] = np.nan
    return frame


def save_year(path, frame, meta):
    result = frame.copy()
    angle = np.deg2rad(270 - result.winddirection_10m)
    result['u'] = result.windspeed_10m * np.cos(angle)
    result['v'] = result.windspeed_10m * np.sin(angle)
    tmp = path.with_suffix('.tmp')
    result.to_csv(tmp, index_label='time', compression={'method': 'gzip', 'mtime': 0})
    tmp.replace(path)
    meta.update(output_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                expected_hours=len(frame), missing_hours=int(frame.isna().any(axis=1).sum()),
                missing_cells=int(frame.isna().sum().sum()),
                valid_through=str(frame.dropna().index.max()) if not frame.dropna().empty else None)
    write_json(path.with_suffix('.json'), meta)


def run(args):
    start_clock = time.monotonic()
    source = args.inventory / 'source_snapshot.json'
    if not source.exists():
        source = args.inventory / 'boundaries.json'
    boundaries = json.loads(source.read_text(encoding='utf-8'))
    matches = json.loads((args.inventory / 'station_matches.json').read_text(encoding='utf-8'))
    all_stations = matches['matched'] + matches['missing_current_coordinates']
    if args.station:
        all_stations = [s for s in all_stations if s['station_id'] in args.station]
        if set(args.station) != {s['station_id'] for s in all_stations}:
            raise ValueError('Unknown station selection')
    args.cache.mkdir(parents=True, exist_ok=True)
    def last_attempt(station):
        stamp = args.cache / f"{station['station_id']}_{station['lat']}_{station['lon']}" / 'last_attempt.json'
        return stamp.stat().st_mtime if stamp.exists() else 0
    # Rotate incomplete stations so repeated failures cannot starve the queue.
    all_stations.sort(key=last_attempt)
    session = requests.Session()
    requests_used = 0
    processed = 0
    rate_limited = False
    for station in all_stations:
        if processed >= args.max_stations or time.monotonic() - start_clock >= args.max_seconds:
            break
        sid = station['station_id']
        folder = args.cache / f"{sid}_{station['lat']}_{station['lon']}"
        folder.mkdir(exist_ok=True)
        years = range(args.start.year, args.through.year + 1)
        complete = True
        for year in years:
            path = folder / f'{year}.csv.gz'
            manifest = path.with_suffix('.json')
            if not path.exists() or not manifest.exists():
                complete = False
                break
            meta = json.loads(manifest.read_text(encoding='utf-8'))
            if (meta.get('missing_hours') != 0 or meta.get('start') != str(max(args.start, date(year, 1, 1)))
                    or meta.get('end') != str(min(args.through, date(year, 12, 31)))
                    or hashlib.sha256(path.read_bytes()).hexdigest() != meta.get('output_sha256')):
                complete = False
                break
        if complete:
            continue
        write_json(folder / 'last_attempt.json', {'time_utc': pd.Timestamp.now(tz='UTC').isoformat()})
        try:
            legacy = pd.DataFrame(columns=FIELDS)
            entry = station['archive_entries'][0] if station['archive_entries'] else None
            needs_import = any(not (folder / f'{year}.csv.gz').exists() for year in years)
            if entry and needs_import:
                # Only a matching coordinate archive is imported; filenames alone are not sufficient.
                url = f"https://raw.githubusercontent.com/{boundaries['repository']}/{boundaries['snapshot_commit']}/ERA5_archive/" + quote(entry['path'])
                response = session.get(url, timeout=(15, 90))
                response.raise_for_status()
                raw = response.content
                blob_sha = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
                if blob_sha != entry['sha']:
                    raise ValueError('Legacy archive Git blob hash mismatch')
                legacy = clean(pd.read_csv(io.BytesIO(raw)))
            for year in years:
                if time.monotonic() - start_clock >= args.max_seconds:
                    break
                first, last = max(args.start, date(year, 1, 1)), min(args.through, date(year, 12, 31))
                index = pd.date_range(first, pd.Timestamp(last) + pd.Timedelta(hours=23), freq='h')
                path = folder / f'{year}.csv.gz'
                if path.exists():
                    meta = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
                    if hashlib.sha256(path.read_bytes()).hexdigest() != meta['output_sha256']:
                        raise ValueError('Cache hash mismatch: ' + str(path))
                    frame = clean(pd.read_csv(path)).reindex(index)
                else:
                    frame = legacy.reindex(index).copy()
                    meta = {'schema_version': 1, 'station': station, 'timezone': 'Asia/Taipei',
                            'wind_speed_unit': 'km/h', 'start': str(first), 'end': str(last),
                            'legacy_snapshot': boundaries['snapshot_commit'], 'legacy_blob': entry,
                            'supplements': []}
                meta.update(start=str(first), end=str(last))
                save_year(path, frame, meta)
                missing = frame.isna().any(axis=1)
                while missing.any() and requests_used < args.max_requests and time.monotonic() - start_clock < args.max_seconds:
                    pause = getattr(args, 'request_pause_seconds', 0)
                    if pause and time.monotonic() - start_clock + pause + 100 >= args.max_seconds:
                        break
                    if pause:
                        time.sleep(pause)
                    begin = missing[missing].index[0].normalize()
                    finish = min(begin + pd.Timedelta(days=30, hours=23), index[-1])
                    params = {'latitude': station['lat'], 'longitude': station['lon'],
                              'start_date': str(begin.date()), 'end_date': str(finish.date()),
                              'hourly': ','.join(API_FIELDS), 'timezone': 'Asia/Taipei', 'wind_speed_unit': 'kmh'}
                    requests_used += 1
                    response = session.get('https://archive-api.open-meteo.com/v1/archive', params=params, timeout=(15, 90))
                    response.raise_for_status()
                    payload = response.json()
                    patch = clean(pd.DataFrame(payload['hourly'])).reindex(index)
                    before = int(frame.isna().sum().sum())
                    frame = frame.combine_first(patch).reindex(index)
                    if int(frame.isna().sum().sum()) >= before:
                        raise ValueError('Archive returned no additional valid cells; retry on a later run')
                    meta['supplements'].append({'url': response.url,
                        'fetched_at_utc': pd.Timestamp.now(tz='UTC').isoformat(),
                        'response_sha256': hashlib.sha256(response.content).hexdigest(),
                        'latitude': payload.get('latitude'), 'longitude': payload.get('longitude'),
                        'elevation': payload.get('elevation'), 'hourly_units': payload.get('hourly_units'),
                        'product': 'Open-Meteo archive default; no specific model selected'})
                    save_year(path, frame, meta)
                    missing = frame.isna().any(axis=1)
                save_year(path, frame, meta)
                print(f'{sid} {year}: {meta["missing_hours"]}/{len(frame)} hours still incomplete', flush=True)
                if args.max_requests and requests_used >= args.max_requests and missing.any():
                    break
            processed += 1
        except (ValueError, KeyError, OSError, requests.RequestException) as exc:
            write_json(folder / 'last_error.json', {'station_id': sid, 'reason': str(exc),
                       'time_utc': pd.Timestamp.now(tz='UTC').isoformat()})
            print(f'{sid}: stopped: {exc}', flush=True)
            processed += 1
            if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 429:
                rate_limited = True
                break
        if args.max_requests and requests_used >= args.max_requests:
            break
    summary = {'through': str(args.through), 'start': str(args.start), 'stations_expected': len(all_stations),
               'stations_complete': 0, 'stations_partial': 0, 'stations_not_started': 0,
               'latest_run_supplement_requests': requests_used, 'latest_run_stations_processed': processed,
               'rate_limited': rate_limited,
               'generated_at_utc': pd.Timestamp.now(tz='UTC').isoformat()}
    for station in all_stations:
        folder = args.cache / f"{station['station_id']}_{station['lat']}_{station['lon']}"
        manifests = [folder / f'{year}.csv.json' for year in range(args.start.year, args.through.year + 1)]
        existing = [json.loads(p.read_text(encoding='utf-8')) for p in manifests if p.exists()]
        full = len(existing) == len(manifests) and all(m.get('missing_hours') == 0 for m in existing)
        # Summary is a progress hint; reuse still verifies hash and requested dates.
        summary['stations_complete' if full else 'stations_partial' if existing else 'stations_not_started'] += 1
    write_json(args.cache / 'progress.json', summary)
    print(f'Finished bounded run: stations={processed}, supplement_requests={requests_used}', flush=True)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--start', type=date.fromisoformat, default=date(2013, 1, 1))
    p.add_argument('--through', type=date.fromisoformat, required=True)
    p.add_argument('--station', action='append')
    p.add_argument('--max-stations', type=int, default=5)
    p.add_argument('--max-requests', type=int, default=4)
    p.add_argument('--max-seconds', type=int, default=1800)
    args = p.parse_args()
    if args.start > args.through or min(args.max_stations, args.max_seconds) < 1 or args.max_requests < 0:
        p.error('Invalid dates or limits')
    run(args)

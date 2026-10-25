"""Annual multi-location supplementation of the existing validated weather cache."""
from collections import deque
from datetime import date
import hashlib
import json
import time
from types import SimpleNamespace

import pandas as pd
import requests

from maintain_weather_archive import API_FIELDS, clean, run as initialize, save_year


def estimated_calls(locations, start, end):
    """Conservative planning weight for five hourly variables, one default model."""
    days = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days + 1
    if locations < 1 or days < 1:
        raise ValueError('Invalid request size')
    return locations * max(1.0, days / 14.0)


def supplement(args, stations, reserve, session=None):
    """reserve(ids,start,end,weight) must persist quota before allowing a request."""
    clock = time.monotonic()
    session = session or requests.Session()
    ids = [station['station_id'] for station in stations]
    prepared = initialize(SimpleNamespace(inventory=args.inventory, cache=args.cache,
        start=args.start, through=args.through, station=ids, max_stations=len(ids),
        max_requests=0, max_seconds=args.max_seconds))
    errors, rate_limited, calls, weight_used = {}, False, 0, 0.0
    for station in stations:
        folder = args.cache / f"{station['station_id']}_{station['lat']}_{station['lon']}"
        error = folder / 'last_error.json'
        if error.exists():
            errors[station['station_id']] = json.loads(error.read_text(encoding='utf-8'))
    exhausted = False
    for year in range(args.start.year, args.through.year + 1):
        if exhausted or time.monotonic() - clock > args.max_seconds - 170:
            break
        first, last = max(args.start, date(year, 1, 1)), min(args.through, date(year, 12, 31))
        expected = pd.date_range(first, pd.Timestamp(last) + pd.Timedelta(hours=23), freq='h')
        work, buckets = {}, {}
        for station in stations:
            sid = station['station_id']
            folder = args.cache / f"{sid}_{station['lat']}_{station['lon']}"
            path = folder / f'{year}.csv.gz'
            if not path.exists():
                continue  # Legacy import failed; keep its explicit error for retry.
            meta = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            if hashlib.sha256(path.read_bytes()).hexdigest() != meta['output_sha256']:
                raise ValueError('Cache checksum mismatch: ' + sid)
            frame = clean(pd.read_csv(path))
            if not frame.index.equals(expected):
                raise ValueError('Unexpected cache hourly axis: ' + sid)
            missing = frame.isna().any(axis=1)
            if not missing.any():
                continue
            begin = missing[missing].index[0].date()
            # Group stations with similar gaps to avoid fetching a whole year for
            # one station that only lacks December when another lacks January.
            buckets.setdefault((begin.month - 1) // 3, []).append(sid)
            work[sid] = {'station': station, 'path': path, 'meta': meta, 'frame': frame, 'begin': begin}
        pending = deque()
        for members in buckets.values():
            pending.append((members, min(work[sid]['begin'] for sid in members), last))
        while pending:
            if calls >= args.max_requests or time.monotonic() - clock > args.max_seconds - 170:
                exhausted = True
                break
            members, begin, end = pending.popleft()
            cost = estimated_calls(len(members), begin, end)
            if not reserve(members, begin, end, cost):
                # A smaller request can still fit the remaining quota.
                if len(members) > 1:
                    mid = len(members) // 2
                    pending.appendleft((members[mid:], begin, end))
                    pending.appendleft((members[:mid], begin, end))
                    continue
                exhausted = True
                break
            if getattr(args, 'request_pause_seconds', 0):
                time.sleep(args.request_pause_seconds)
            params = {'latitude': ','.join(str(work[sid]['station']['lat']) for sid in members),
                      'longitude': ','.join(str(work[sid]['station']['lon']) for sid in members),
                      'start_date': str(begin), 'end_date': str(end), 'hourly': ','.join(API_FIELDS),
                      'timezone': 'Asia/Taipei', 'wind_speed_unit': 'kmh'}
            calls += 1
            weight_used += cost
            try:
                response = session.get('https://archive-api.open-meteo.com/v1/archive', params=params, timeout=(30, 120))
                response.raise_for_status()
                payload = response.json()
                locations = payload if isinstance(payload, list) else [payload]
                if len(locations) != len(members):
                    raise ValueError('API location count does not match request')
                patches = []
                request_index = pd.date_range(begin, pd.Timestamp(end) + pd.Timedelta(hours=23), freq='h')
                for index, (sid, location) in enumerate(zip(members, locations)):
                    if location.get('location_id', index) != index:
                        raise ValueError('API location ordering mismatch')
                    if location.get('utc_offset_seconds') != 28800:
                        raise ValueError('Unexpected API timezone offset')
                    units = location.get('hourly_units', {})
                    wanted = {'temperature_2m': '°C', 'relative_humidity_2m': '%',
                              'precipitation': 'mm', 'wind_speed_10m': 'km/h', 'wind_direction_10m': '°'}
                    if any(units.get(key) != value for key, value in wanted.items()):
                        raise ValueError('Unexpected API weather units')
                    patch = clean(pd.DataFrame(location['hourly']))
                    if not patch.index.equals(request_index):
                        raise ValueError('Incomplete or duplicate API hourly axis')
                    patches.append((sid, location, patch))
                response_sha = hashlib.sha256(response.content).hexdigest()
                for index, (sid, location, patch) in enumerate(patches):
                    item = work[sid]
                    previous = item['frame']
                    updated = previous.combine_first(patch).reindex(expected)
                    if updated.isna().sum().sum() >= previous.isna().sum().sum():
                        errors[sid] = {'station_id': sid, 'reason': f'No additional valid weather for {begin} through {end}'}
                        continue
                    item['frame'] = updated
                    item['meta']['supplements'].append({'url': response.url,
                        'fetched_at_utc': pd.Timestamp.now(tz='UTC').isoformat(),
                        'response_sha256': response_sha, 'request_station_ids': members,
                        'location_index': index, 'latitude': location.get('latitude'),
                        'longitude': location.get('longitude'), 'elevation': location.get('elevation'),
                        'hourly_units': location['hourly_units'], 'estimated_api_calls': cost,
                        'product': 'Open-Meteo archive default; no specific model selected'})
                    save_year(item['path'], updated, item['meta'])
                    errors.pop(sid, None)
                    print(f"{sid} {year}: {item['meta']['missing_hours']} incomplete hours after annual batch", flush=True)
            except (requests.RequestException, ValueError, KeyError) as exc:
                for sid in members:
                    errors[sid] = {'station_id': sid, 'reason': str(exc)}
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 429:
                    rate_limited, exhausted = True, True
                    break
                # Split failed multi-location requests; a failure cannot silently
                # exchange station results or prevent other stations from retrying.
                if len(members) > 1:
                    mid = len(members) // 2
                    pending.appendleft((members[mid:], begin, end))
                    pending.appendleft((members[:mid], begin, end))
    return {'rate_limited': rate_limited, 'http_requests': calls,
            'estimated_weighted_calls': weight_used, 'errors': errors,
            'stations_prepared': prepared['latest_run_stations_processed'], 'exhausted': exhausted}

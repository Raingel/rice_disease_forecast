"""Reproduce the reviewed historical repair from pinned upstream annual files.

Usage: python scripts/reproduce_blastdt2_20260105_repair.py --output-dir /new/dir
The script never overwrites existing outputs or the preserved original archive.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/data_repairs/20261007_blastdt2_year_boundary'
spec = importlib.util.spec_from_file_location(
    'blastdt2_records', ROOT / 'models/BlastDT2/record_normalization.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    metadata = json.loads((DOC / 'repair_manifest.json').read_text(encoding='utf-8'))
    original = ROOT / metadata['original_snapshot_path']
    assert hashlib.sha256(original.read_bytes()).hexdigest() == metadata['original_sha256']
    raw = pd.read_csv(original, dtype={'站號': str})
    audit = pd.read_csv(DOC / 'station_audit.csv', dtype={'station_id': str})
    output = args.output_dir / '20260105_BlastDT2.csv'
    if output.exists():
        raise FileExistsError(output)

    def reconstruct(entry):
        url = f"https://raw.githubusercontent.com/Raingel/BlastDT/{entry.upstream_commit}/{entry.source_path}"
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != entry.source_sha256:
            raise ValueError(f'Source checksum mismatch: {entry.station_id}')
        annual = pd.read_csv(BytesIO(response.content), dtype={'站號': str})
        selected = module.normalize_station_year_predictions(annual, 2025)
        selected = selected[selected['日期'].eq(pd.Timestamp('2026-01-05'))]
        if len(selected) > 1:
            raise ValueError(f'Multiple actual-year source rows: {entry.station_id}')
        old = raw[raw['站號'].eq(entry.station_id)].iloc[0].copy()
        if len(selected):
            source = selected.iloc[0]
            if (str(source['站號']), float(source.lat), float(source.lon)) != (entry.station_id, float(old.lat), float(old.lon)):
                raise ValueError(f'Source station or coordinate mismatch: {entry.station_id}')
            old['BlastDT2'] = source.BlastDT2
        else:
            old['BlastDT2'] = float('nan')
        return old

    with ThreadPoolExecutor(max_workers=8) as pool:
        result = pd.DataFrame(pool.map(reconstruct, audit.itertuples(index=False)))
    result = result[raw.columns].sort_values('站號').reset_index(drop=True)
    assert len(result) == metadata['corrected_rows']
    assert not result.duplicated(['站號', '日期']).any()
    # Serialize in memory and check the reviewed artifact before creating it.
    payload = result.to_csv(index=False).encode('utf-8-sig')
    if hashlib.sha256(payload).hexdigest() != metadata['corrected_sha256']:
        raise ValueError('Reproduced CSV differs from the reviewed repair')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(payload)
    print(f'Reproduced {len(result)} station predictions: {output}')


if __name__ == '__main__':
    main()

"""Keep annual BlastDT2 warm-up rows out of downstream daily predictions."""

import pandas as pd


def parse_blastdt2_value(value) -> float:
    """Preserve missing source predictions, including annual warm-up values."""
    if pd.isna(value):
        return float('nan')
    text = str(value).strip().lower()
    if text in {'true', '1', '1.0'}:
        return 1.0
    if text in {'false', '0', '0.0'}:
        return 0.0
    raise ValueError(f'Unexpected BlastDT2 source value: {value!r}')


def normalize_station_year_predictions(df, source_year, incubation_days=5):
    """Select the annual file's own infection dates before shifting dates."""
    # Upstream emits one metadata-only row when a station has no annual data.
    # Accept only that exact shape; malformed dates with real data still fail.
    metadata = {'站號', '站名', 'lat', 'lon'}
    payload_columns = [c for c in df.columns if c not in metadata]
    if len(df) == 1 and df[payload_columns].isna().all().all():
        return pd.DataFrame(columns=['站號', '站名', '日期', 'lat', 'lon', 'BlastDT2'])
    dates = pd.to_datetime(df['Date'], errors='coerce')
    if dates.isna().any():
        raise ValueError(f'Invalid infection date in source year {source_year}')
    # Upstream includes previous-year warm-up dates and clears its first value.
    # Those dates belong to the previous annual file, not this file's outputs.
    own_year = dates.dt.year.eq(source_year)
    result = df.loc[own_year, ['站號', '站名', 'lat', 'lon', 'BlastDT2']].copy()
    result['日期'] = dates.loc[own_year].dt.normalize() + pd.Timedelta(days=incubation_days)
    result['BlastDT2'] = result['BlastDT2'].map(parse_blastdt2_value)
    result['站號'] = result['站號'].astype(str)
    return result[['站號', '站名', '日期', 'lat', 'lon', 'BlastDT2']]


def merge_station_predictions(records):
    """Collapse exact repeats and reject conflicting station/date records."""
    combined = pd.concat(records, ignore_index=True).drop_duplicates()
    conflicting = combined.duplicated(['站號', '日期'], keep=False)
    if conflicting.any():
        sample = combined.loc[conflicting, ['站號', '日期', 'lat', 'lon', 'BlastDT2']].head(10)
        raise ValueError('Conflicting BlastDT2 station/date records:\n' + sample.to_string(index=False))
    return combined

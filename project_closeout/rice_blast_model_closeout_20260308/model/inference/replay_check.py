from __future__ import annotations

from pathlib import Path
import json
import pandas as pd
import numpy as np
import torch

from reference_tcn_attn import load_closeout_model, predict_prob


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    input_fp = root / 'evidence' / 'replay_example_input_weather.csv'
    output_fp = root / 'evidence' / 'replay_example_output_prediction.csv'
    norm_fp = root / 'model' / 'preprocess' / 'norm_params.json'

    loaded = load_closeout_model(root, device='cpu')
    model = loaded['model']
    threshold = float(loaded['threshold'])
    feature_cols = loaded['metadata']['feature_selection']['selected_z_features']

    df = pd.read_csv(input_fp)
    if len(df) != 28:
        raise ValueError(f'replay input must have 28 rows, got {len(df)}')

    norm = json.loads(norm_fp.read_text(encoding='utf-8'))
    param_map = {x['feature']: (float(x['mean']), float(x['std'])) for x in norm}

    work = df.copy()
    work['precipitation_sum_log1p'] = np.log1p(pd.to_numeric(work['precipitation_sum'], errors='coerce').fillna(0).clip(lower=0))
    for z in feature_cols:
        b = z[:-2]
        mean, std = param_map[b]
        x = pd.to_numeric(work[b], errors='coerce').astype(float)
        x = x.replace([np.inf, -np.inf], np.nan).fillna(mean)
        work[z] = (x - mean) / (std if std != 0 else 1.0)

    x = torch.tensor(work[feature_cols].to_numpy(dtype=np.float32)).unsqueeze(0)
    prob = float(predict_prob(model, x).cpu().numpy().reshape(-1)[0])
    pred = int(prob >= threshold)

    ref = pd.read_csv(output_fp).iloc[0]
    ref_prob = float(ref['risk_prob'])
    ref_pred = int(ref['pred_class'])
    diff = abs(prob - ref_prob)

    print(f'computed_prob={prob:.10f}')
    print(f'reference_prob={ref_prob:.10f}')
    print(f'abs_diff={diff:.10f}')
    print(f'computed_pred={pred}, reference_pred={ref_pred}')

    if diff > 1e-6 or pred != ref_pred:
        raise SystemExit('REPLAY_CHECK_FAILED')
    print('REPLAY_CHECK_OK')


if __name__ == '__main__':
    main()

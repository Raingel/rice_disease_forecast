# AI Agent 接手指南 / Agent Handoff Guide

## 中文（台灣）
你可以直接使用這個資料夾做部署與推論。

### 最重要檔案
- 模型權重：`model/final_model.pt`
- 模型 metadata：`model/model_metadata.json`
- 前處理參數：`model/preprocess/norm_params.json`
- 轉換規則：`model/preprocess/transform_rules.json`
- 相容設定：`model/final_model_config.json`

### 實際資料流怎麼接
1. 取得每日天氣欄位（溫度/濕度/風速/降雨）。
2. 依 `transform_rules.json` 做轉換（包含降雨 log1p）。
3. 依 `norm_params.json` 做 z-score。
4. 依 `model_metadata.json.feature_selection.selected_z_features` 排序成 10 維特徵。
5. 用 -30 到 -3 日組成 28-step 視窗。
6. 丟進 `final_model.pt` 得到 `risk_prob`。
7. 用 threshold=0.23 轉為 0/1 風險。

### 一致性驗證
- 先跑 `docs/REPLAY_20080816_20090913.md`
- 比對 `evidence/replay_example_output_prediction.csv`

## English
This folder is ready for deployment handoff.

### Required files
- Model weights: `model/final_model.pt`
- Model metadata: `model/model_metadata.json`
- Normalization params: `model/preprocess/norm_params.json`
- Transform rules: `model/preprocess/transform_rules.json`
- Compatibility config: `model/final_model_config.json`

### How to connect real-world data flow
1. Ingest daily weather features (temperature/humidity/wind/precipitation).
2. Apply transforms from `transform_rules.json` (including log1p for precipitation).
3. Apply z-score normalization using `norm_params.json`.
4. Build feature vectors in the exact order from `model_metadata.json.feature_selection.selected_z_features`.
5. Build 28-step windows using relative days -30 to -3.
6. Run inference with `final_model.pt` to get `risk_prob`.
7. Apply threshold 0.23 to derive binary risk.

### Reproducibility check
- Run the replay case in `docs/REPLAY_20080816_20090913.md`
- Compare against `evidence/replay_example_output_prediction.csv`


## 門檻與模型載入修正（2026-03-22）
- 正式門檻以 0.23 為準（不要使用 legacy 0.42）。
- 架構與載入請用：model/inference/reference_tcn_attn.py。
- 規格文件：docs/MODEL_ARCHITECTURE_SPEC.md。
- 權重 fingerprint：model/model_state_fingerprint.json。


- Replay 一致性建議直接執行：model/inference/replay_check.py。


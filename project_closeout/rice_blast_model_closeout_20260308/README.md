# 稻熱病模型結案包（v1.2-deployable-fixed）
# Rice Blast Model Closeout Package (v1.2-deployable-fixed)

## 你要複製哪個資料夾 / Which folder to copy
- 請整包複製這個資料夾：
  `project_closeout/rice_blast_model_closeout_20260308`
- Copy this entire folder as-is:
  `project_closeout/rice_blast_model_closeout_20260308`

## 中文（台灣）
建議先開：`docs/index.html`

### 內容
- `model/`：定版模型、metadata、相容設定、前處理參數（normalization）
- `evidence/`：模型比較、特徵重要性、年度嚴重度、固定年份分析與重跑範例
- `docs/`：部署與流程說明（人讀 + LLM 可讀）
- `CLOSEOUT_MANIFEST.json`：結案檔案清單與契約摘要

### AI agent 接手順序
1. 讀 `CLOSEOUT_MANIFEST.json`
2. 讀 `docs/AGENT_HANDOFF.md`
3. 讀 `docs/INFERENCE_CONTRACT.json`
4. 讀 `model/model_metadata.json` 與 `model/preprocess/*.json`

## English
Start here: `docs/index.html`

### Contents
- `model/`: finalized model, metadata, compatibility config, and preprocessing params (normalization)
- `evidence/`: model comparison, feature importance, yearly severity, fixed-year analysis, and replay samples
- `docs/`: deployment/process docs (human-readable + LLM-readable)
- `CLOSEOUT_MANIFEST.json`: closeout inventory and contract summary

### AI agent handoff order
1. Read `CLOSEOUT_MANIFEST.json`
2. Read `docs/AGENT_HANDOFF.md`
3. Read `docs/INFERENCE_CONTRACT.json`
4. Read `model/model_metadata.json` and `model/preprocess/*.json`

## 修正版重點 / Fix Notes (2026-03-22)
- 正式部署門檻統一為 `0.23`（已修正 `model/final_metrics.csv`）。
- 保留舊衝突指標於 `model/final_metrics_legacy_conflict.csv`（僅供追溯，不可作部署真值）。
- 新增可直接載入 state_dict 的參考推論程式：`model/inference/reference_tcn_attn.py`。
- 新增架構規格：`docs/MODEL_ARCHITECTURE_SPEC.md`。
- 新增權重 fingerprint：`model/model_state_fingerprint.json`。

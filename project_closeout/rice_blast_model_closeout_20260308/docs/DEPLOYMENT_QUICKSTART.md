# 部署快速說明

## 核心檔案
- 模型權重：`../model/final_model.pt`
- 模型 metadata：`../model/model_metadata.json`
- 結案 manifest：`../CLOSEOUT_MANIFEST.json`

## 推論輸入契約（摘要）
- 每筆樣本需提供 28 個時間步（-30 到 -3 日）
- 每個時間步需要 10 個特徵（見 metadata `feature_selection.selected_z_features`）
- 特徵需使用訓練同版的標準化參數

## 推論輸出契約（摘要）
- 輸出 `risk_prob`（0~1）
- 以 `decision_threshold=0.23` 轉為二元風險
- 建議同時保留連續風險值供後續年度/區域分析

## 建議部署檢查
1. 檢查輸入特徵順序是否與 metadata 一致。
2. 檢查 normalization 版本是否一致。
3. 檢查門檻值是否使用 0.23。
4. 先以已知樣本做 smoke test，再接上正式資料流。

## 一致性重跑範例
- 參考文件：`REPLAY_20080816_20090913.md`
- 固定區間：`2008-08-16` 到 `2009-09-13`
- 目的：在新環境驗證下載、特徵、預測流程是否一致
- 範例輸入/輸出：
  - `../evidence/replay_example_input_weather.csv`
  - `../evidence/replay_example_output_prediction.csv`

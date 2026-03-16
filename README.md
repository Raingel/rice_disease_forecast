# 水稻病害預報自動化（rice_disease_forecast）

這個 repo 主要用來做**台灣水稻病害風險預報資料的每日更新與彙整**，包含多個模型輸出、站點日資料整理、近期摘要與作期平均統計。

---

## 這個 repo 在做什麼

核心目標：

1. 下載與更新氣象資料（ERA5 / Open-Meteo / 上游資料來源）。
2. 執行多個病害預測模型產生每日風險結果。
3. 整理成可下游使用的彙整檔案（例如 `recent_summary.csv` 與 `recent_daily_by_station/`）。
4. 透過 GitHub Actions 自動提交更新結果。

主要輸出位置：

- `rice_blast_prediction/recent_daily_by_station/`
- `rice_blast_prediction/recent_summary.csv`
- `rice_blast_prediction/data/`

---

## 自動化排程（GitHub Actions）

### 1) 每日主流程（全模型，不含 BLASTAM）

- Workflow: `.github/workflows/daily-forecast.yml`
- 觸發時間：**每天台灣時間 00:00**（cron: `0 16 * * *`，UTC）
- 執行腳本：`scripts/run_daily_pipeline.sh`

`run_daily_pipeline.sh` 會執行：

1. `models/ERA5_current_download_cron.py`
2. `models/BlastLSTLS/cron_predict.py`
3. `models/230127_GRU/predictor.py`
4. `models/BLBTSLS/predict.py`
5. `models/230128_Transformer/predictor_250628.py`
6. `models/BlastDT2/fetch_and_convert.py`
7. `models/recent_forecast_organizer.py`
8. `models/crop_season_avg.py`

---

### 2) 每日 BLASTAM 流程（獨立）

- Workflow: `.github/workflows/blastam-forecast.yml`
- 觸發時間：**每天台灣時間 00:30**（cron: `30 16 * * *`，UTC）
- 執行腳本：`scripts/run_blastam_pipeline.sh`

`run_blastam_pipeline.sh` 會執行：

1. `models/BLASTAM/run_blastam.py`
2. `models/recent_forecast_organizer.py`
3. `models/crop_season_avg.py`

---

## Backfill / 一次性流程（已整理到 legacy）

為了讓主流程更乾淨，已將「一次性回補」與「舊版排程」腳本移到 `legacy/`：

- `legacy/scripts/run_one_time_backfill.sh`
- `legacy/scripts/run_blastam_backfill_2024_2025.sh`
- `legacy/scripts/run_blastdt2_backfill.sh`
- `legacy/scripts/cron_update_legacy.sh`（舊 server cron 流程，僅保留參考）

對應 workflow 仍可手動觸發，並已改為呼叫 `legacy/scripts/`：

- `.github/workflows/one-time-backfill-all-models.yml`
- `.github/workflows/blastam-backfill-2024-2025.yml`
- `.github/workflows/blastdt2-backfill.yml`

> 這些都屬於手動／一次性用途，不影響每日自動更新。

---

## 目錄建議

- `scripts/`：保留「目前仍在每日自動流程使用」的腳本。
- `legacy/`：放已完成階段性任務、一次性回補、舊機制腳本。
- `.github/workflows/`：排程與手動工作流定義。
- `models/`：各模型與彙整程式。


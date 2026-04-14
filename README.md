# rice_disease_forecast

Automated rice disease forecasting for Taiwan  
台灣水稻病害風險自動化預報系統

## Overview | 專案簡介

This repository is an operational pipeline for daily rice disease forecasting in Taiwan. It uses GitHub Actions to fetch weather data, run multiple forecasting models, organize station-based outputs, and write updated results back to the repository.

本 repository 用於台灣水稻病害風險的每日自動化更新。系統會透過 GitHub Actions 定期抓取氣象資料、執行多個預報模型、整理逐站結果，並將更新後的輸出檔回寫到 repository。

At present, the operational pipeline includes the following forecast outputs:

- BlastGRU-TW
- BlastDT2
- BlastLSTLS
- BLBTSLS
- BlastTF
- BlastGAT
- BLASTAM
- optional planthopper integration in summary outputs

目前的自動化流程以實際每日運行與結果整理為主。一次性回填、初始化或舊版流程則保留在 `legacy/` 或其他手動 workflow 中。

---

## Automated workflows | 自動化排程

### 1. Daily forecast pipeline

- Workflow: `.github/workflows/daily-forecast.yml`
- Schedule: **00:00 Asia/Taipei every day**
- Entry script: `scripts/run_daily_pipeline.sh`

Execution order:

1. `models/ERA5_current_download_cron.py`
2. `models/BlastLSTLS/cron_predict.py`
3. `models/230127_GRU/predictor.py`
4. `models/BLBTSLS/predict.py`
5. `models/230128_Transformer/predictor_250628.py`
6. `models/BlastGAT/predict.py`
7. `models/BlastDT2/fetch_and_convert.py`
8. `models/recent_forecast_organizer.py`
9. `models/crop_season_avg.py`

### 2. Daily BLASTAM pipeline

- Workflow: `.github/workflows/blastam-forecast.yml`
- Schedule: **01:30 Asia/Taipei every day**
- Entry script: `scripts/run_blastam_pipeline.sh`

Execution order:

1. `models/BLASTAM/run_blastam.py`
2. `models/recent_forecast_organizer.py`
3. `models/crop_season_avg.py`

---

## Data sources | 資料來源

### Weather data

The daily downloader uses Open-Meteo APIs for both archive and forecast data.

- Archive: `https://archive-api.open-meteo.com`
- Forecast: `https://api.open-meteo.com`

The downloaded hourly weather variables currently include:

- `temperature_2m`
- `relativehumidity_2m`
- `precipitation`
- `windspeed_10m`
- `winddirection_10m`

The downloader also computes wind vector components `u` and `v` and stores per-station hourly files in the `ERA5/` folder.

雖然資料夾名稱為 `ERA5/`，但目前每日自動化下載腳本實際上是透過 Open-Meteo 的 archive 與 forecast API 取得資料，再整理成每站一個 CSV 檔。

### Station list

Weather station metadata are loaded from:

- `Raingel/weather_station_list`

Only active stations are retained in the daily downloader.

### Planthopper data

Planthopper data are optional.

- local source: `PLAN_FOLDER`
- remote fallback: `Raingel/HYSPLIT-Planthopper-Forecast`

These values are merged into station summaries when available.

---

## Output files | 主要輸出檔案

### 1. Daily model outputs

Daily model outputs are written to:

```text
rice_blast_prediction/data/YYYYMMDD_<model>.csv
```

Examples:

- `YYYYMMDD_BlastGRU-TW.csv`
- `YYYYMMDD_BlastDT2.csv`
- `YYYYMMDD_BlastLSTLS.csv`
- `YYYYMMDD_BLBTSLS.csv`
- `YYYYMMDD_BlastTF.csv`
- `YYYYMMDD_BlastGAT.csv`
- `YYYYMMDD_BLASTAM.csv`

### 2. Station-based recent daily files

Merged station-based daily outputs are written to:

```text
rice_blast_prediction/recent_daily_by_station/<station_id>.csv
```

A station list file is also generated at:

```text
rice_blast_prediction/recent_daily_by_station/station_list.csv
```

### 3. Summary output

The main summary file is:

```text
rice_blast_prediction/recent_summary.csv
```

This file contains station metadata and seasonal summary fields such as:

- `BlastGRU-TW_this_year`
- `BlastDT2_this_year`
- `BlastLSTLS_this_year`
- `BLBTSLS_this_year`
- `BlastTF_this_year`
- `BlastGAT_this_year`
- `BLASTAM_this_year`
- `planthopper_this_year`
- corresponding `*_avg` fields for historical same-period averages

中文說明如下：

- `*_this_year` 代表目前季節視窗內的高風險日數
- `*_avg` 代表歷史同期間的平均高風險日數

For the summary script, the current period is defined as:

- **January 1 to today**, when today is before July 1
- **July 1 to today**, when today is on or after July 1

這樣的設計是為了對應一年中的前後兩段主要作期視窗。

---

## Processing logic | 處理流程

The operational logic of this repository can be summarized as follows:

1. Load active weather stations
2. Download recent archive and forecast weather data
3. Save hourly weather files for each station
4. Run each disease model
5. Convert or normalize model outputs into a common daily station format
6. Merge daily outputs across models
7. Produce station-level recent daily files
8. Generate a seasonal summary with current counts and historical averages
9. Commit updated outputs back to the repository through GitHub Actions when changes are detected

簡單來說，這個 repo 的角色不是單一模型程式，而是把資料下載、模型推論、結果整併與輸出更新接成一條可每天自動執行的雲端流程。

---

## Repository structure | 目錄說明

```text
.github/workflows/        GitHub Actions workflows
scripts/                  pipeline entry scripts
models/                   model runners and organizing logic
legacy/                   one-time backfill or older workflows
ERA5/                     per-station hourly weather files
rice_blast_prediction/    generated prediction outputs
project_closeout/         model-related closeout assets
docs/                     supplementary documentation
```

---

## Attribution and citation | 出處標註與引用方式

### Required attribution | 使用時請註明之出處

If this repository, any part of its workflow, or any forecasting outputs generated from it are used in research, reports, services, presentations, dashboards, or derivative systems, please clearly acknowledge the source as:

**臺灣水稻防疫工作團隊**  
**Taiwan Rice Disease Management Task Force**

若使用本 repository、其中任一部分工作流程，或由本系統產生之預報結果於研究、報告、簡報、網站、儀表板、服務或衍生系統中，請清楚註明出處為：

**臺灣水稻防疫工作團隊**  
**Taiwan Rice Disease Management Task Force**

### Citation | 學術引用

This repository includes a `CITATION.cff` file for GitHub citation support.

If you use this repository in academic work, please cite the following publication:

Ou, J. H., Kuo, C. H., Wu, Y. F., Lin, G. C., Lee, M. H., Chen, R. K., ... & Chen, C. Y. (2023).  
**Application-oriented deep learning model for early warning of rice blast in Taiwan.**  
*Ecological Informatics, 73*, 101950.  
https://doi.org/10.1016/j.ecoinf.2022.101950

若於學術研究中使用本 repository，請引用下列論文：

Ou, J. H., Kuo, C. H., Wu, Y. F., Lin, G. C., Lee, M. H., Chen, R. K., ... & Chen, C. Y. (2023).  
**Application-oriented deep learning model for early warning of rice blast in Taiwan.**  
*Ecological Informatics, 73*, 101950.  
https://doi.org/10.1016/j.ecoinf.2022.101950

Repository URL:  
https://github.com/Raingel/rice_disease_forecast

### Recommended acknowledgement text | 建議致謝文字

**English**

This work used the `rice_disease_forecast` repository and/or forecasting outputs developed by the **Taiwan Rice Disease Management Task Force**. Please also cite Ou et al. (2023).

**中文**

本工作使用了由 **臺灣水稻防疫工作團隊** 開發之 `rice_disease_forecast` repository 及／或其預報結果，並請一併引用 Ou et al. (2023)。

---

## License | 授權

The source code in this repository is licensed under the **Apache License 2.0**.  
See [`LICENSE`](./LICENSE) for details.

本 repository 的原始程式碼採用 **Apache License 2.0**。詳細內容請參見 `LICENSE`。

---

## Notes | 備註

- This repository is designed for operational daily updates.
- Historical backfill or one-time setup jobs are separated from the daily pipeline.
- Output files may change as models, station lists, or data availability are updated.
- For scientific background of the rice blast forecasting framework, please refer to the cited publication above.

- 本 repository 主要面向每日運行與結果更新。
- 歷史回填、初始化與部分舊流程已另外整理，不直接影響每日排程。
- 隨著模型版本、站點清單或資料來源更新，輸出內容可能會調整。
- 若需要了解稻熱病預報模型的研究背景，請參考上方引用論文。

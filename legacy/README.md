# legacy

此資料夾存放「一次性 backfill」與「舊版本機排程」相關程式，避免干擾目前每日自動預報主流程。

## 內容

- `legacy/scripts/run_one_time_backfill.sh`：全模型一次性回補流程（手動觸發）。
- `legacy/scripts/run_blastam_backfill_2024_2025.sh`：BLASTAM 指定期間回補流程（手動觸發）。
- `legacy/scripts/run_blastdt2_backfill.sh`：BlastDT2 指定期間回補流程（手動觸發）。
- `legacy/scripts/cron_update_legacy.sh`：早期在伺服器上用 cron + conda 的舊流程，保留供參考，不再建議使用。

## 注意

- 每日自動執行仍以 `.github/workflows/daily-forecast.yml` 與 `scripts/run_daily_pipeline.sh` 為主。
- BLASTAM 每日流程仍以 `.github/workflows/blastam-forecast.yml` 與 `scripts/run_blastam_pipeline.sh` 為主。

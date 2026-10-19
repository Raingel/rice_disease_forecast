# EPIRICE_BLB

每日白葉枯病機制模型。採用提供的 EPIRICE_GUIDE 程式版參數，固定版本名稱
`EPIRICE_BLB_source2023_guide_v1`：a=4、日平均 RH、RH>=90 或日總雨量>=5、
onset 模擬第 20 日、最多 120 日。這個版本不會自動跟隨其他套件的參數更新。
onset 是初始感染情境；intensity 是活動病害比例，非發病機率。

## 地區作期資料

使用使用者提供的 [公開 Google Sheets CSV](https://docs.google.com/spreadsheets/d/e/2PACX-1vQPErPqAhitT7baUVl62ZW_jCA8SSuDzuB_CuQn3ddet_udFDtZdVoMacJqH_kx0ksgKjMlC2TpNulr/pub?gid=1654827671&single=true&output=csv)。
每次執行重讀表格，使用以下明確日期欄位：

- 一期作插秧時間點、一期作收割時間點
- 二期作插秧時間點、二期作收割時間點

不從前半部的文字時間區間推算日期。相容表格原先後半部重複欄名的格式，
但新版「時間點」優先。`水稻栽培區=否`、範例列、無作期的欄位略過；
宜蘭二期空白會保持無二期。站點依 `weather_station_list` 的城市欄匹配縣市，
台／臺字形統一。未列出的離島不自行配置作期。系統開放／關閉時間不作為作物日期，
因此不解析來源中的 `11/31`；使用合法的收割時間點。

從插秧代表日期視為 age=0、simday=1，直到收割時間點、120 日上限或可用氣象終日
三者中最早的一日。收割日期是作物存在的邊界，模型內的參數／曲線不改動。
這是縣市代表作期情境，不是每田區實際插秧日。

## 氣象與執行

讀取共用 ERA5 逐站 CSV（實際來源為 Open-Meteo），TEMP=日平均氣溫 °C，
RHUM=日平均相對濕度 %，RAIN=逐時 mm 加總。每一天需要 24 個有效小時，
不填補缺日、不將部分日當成完整日。跨日狀態必須從插秧日重建，不能從輸出
視窗重設感染與健康葉片數。正常每日流程將共用歷史氣象延長至 150 日。

預設輸出今天−30 至今天+15 日，以台灣日期為基準，不沿用其他模型「氣象末日+4」
的日期位移。超出作期或氣象可用範圍不產生數值；有缺日的情境跳過並列入 audit。
所有情境均缺氣象時執行失敗。新 runner 不會額外向氣象 API 下載全站資料。

```bash
python models/EPIRICE_BLB/predict.py
python models/EPIRICE_BLB/predict.py --weather-dir ERA5_archive --start-date 2025-07-01 --end-date 2025-12-01
```

可覆寫 `EPIRICE_CALENDAR_SOURCE`、`EPIRICE_STATIONS_SOURCE` 為本地 CSV，
`ERA5_INPUT_DIR`、`DATA_FOLDER`、`EPIRICE_STATE_DIR` 為輸入／輸出目錄。
`--station-id` 可重複傳入以驗證少量站點。請勿用目前近期氣象跑超出涵蓋範圍的手動回填。

## 完整狀態與對外欄位

- `rice_blast_prediction/epirice_blb_states/YYYY_1/<站號>.csv`：一期完整軌跡。
- `rice_blast_prediction/epirice_blb_states/YYYY_2/<站號>.csv`：二期完整軌跡。
- `rice_blast_prediction/epirice_blb_states/inputs/`：當次日曆、站點清單與參數快照。
- `rice_blast_prediction/epirice_blb_states/run_audit.json`：各站情境成功／略過原因、來源及 SHA-256。
- `rice_blast_prediction/data/YYYYMMDD_EPIRICE_BLB.csv`：每日全站結果，包含全部前綴狀態。
- `rice_blast_prediction/recent_daily_by_station/<站號>.csv`：合併保留全部 `EPIRICE_BLB_*` 欄位。

完整狀態保留 GUIDE 的所有輸出，另含 total_sites、生長／衰老流量與感染批次進入量、
截至該日的 `AUDPC_cumulative`、地區／作期／插秧及收割日期、模型版本和生成時間。
`EPIRICE_BLB` 是 `rateinf_new_infections`（每日新增感染量，模型位點數／日）的對外代表欄，
不是機率或百分比；其他欄位用 `EPIRICE_BLB_` 前綴，包含原始新增感染量與
`EPIRICE_BLB_intensity_active` 活動病害比例。
`EPIRICE_BLB_primary_value_field=rateinf_new_infections`、
`EPIRICE_BLB_output_schema_version=EPIRICE_BLB_daily_v3` 記錄輸出定義；
完整狀態與 audit 亦保留無前綴的對應欄位。核心模型版本與參數沒有改變。
舊版輸出沒有 schema 標記，其 `EPIRICE_BLB` 代表 `intensity_active`，部署前須轉換，
避免在同一條歷史曲線混用兩種意義。
模擬第 20 日的新增感染量是設定的初始感染量 I0，非當日氣象造成的新傳播；
本次保留 GUIDE 的原始行為，設定警戒門檻時需區分此情境輸入。
沒有額外提前天數。`is_forecast` 表示目標日是否晚於 as-of，並非逐列 API 資料來源標籤。

`AUDPC` 保留原實作的整個模擬期間積分，會在每列重複同一終值，搭配
`simulation_end_date` 解讀；截至該日的歷史分析請用 `AUDPC_cumulative`。
若末段包含預報，整段 AUDPC 同樣包含預報，不是已觀測的完整作期總量。

每日指定區間會寫出所有指定有效測站。主值使用原始新增感染量，沒有標準化或截斷。
非作期（插秧前、收割後，或明確標示不種稻）主值為 -99，status=non_crop_period。
原始狀態欄位不填 -99；沒有模擬值就留空。沒有日曆設定為 missing_crop_calendar、
作期內缺氣象／計算結果為 missing_model_output；仍在作期但超過模型120日上限為
outside_model_duration，以上主值均留空，不冒充零或非作期。
有效的零新增感染量保持 0，status=ok。模型預設第20日初始感染不另做排除。
每站合併採 EPIRICE 與既有模型站日聯集，保留 EPIRICE 獨有測站及其名稱／座標。

每日指定區間以本次情境重寫，避免重新使用失敗情境上次的每日分數。
完整作期檔依情境更新；舊情境檔可能保留，最新有效性以 run_audit
及生成時間為準。同一日同一站若有重疊作期會報錯，避免合併時覆蓋。

未設定操作性警戒門檻，也未加入 recent_summary 的高風險日數。
原有模型的門檻及摘要方式維持原設定。

`scripts/migrate_epirice_outputs.py` 可從已存的原始新增感染欄位轉換每日檔、歷史檔
及每站合併檔；不重跑氣象／模型、不改完整狀態檔，也不更動其他模型數值或
recent_summary。對應一次性 workflow 會驗證原始狀態欄位完整保留，並輸出
`rice_blast_prediction/epirice_output_migration.json` 紀錄轉換範圍與各種狀態筆數。

## 驗證

```bash
python -m unittest discover -s tests -p 'test_epirice*.py'
```

核心回歸以 GUIDE 的 120 列合成輸入／輸出逐欄比較，絕對容許誤差 1e-6。
此測試確認軟體重現，不代表台灣田間預報能力已驗證。


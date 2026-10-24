# EPIRICE_LB：葉稻熱病 baseline 與單站 runner

本模組重現固定開源版本的計算，尚未進行臺灣田間校正。使用獨立 LB 設定，呼叫既有
`EPIRICE_BLB/model.py` 的可參數化 SEIR 核心；未修改 BLB 的公式、參數或輸出定義。
支援指定測站與每日全站入口；每日 pipeline 已接入 LB，近期每站表採三欄精簡輸出。

## 來源與版本

- Savary et al. (2012), *Modeling and mapping potential epidemics of rice diseases globally*,
  Crop Protection 34:6–17，[論文 DOI](https://doi.org/10.1016/j.cropro.2011.11.009)。
- 實際採用 [epicrop v0.0.2，commit b331d7a1947e4137ff19f888149ac7078d504765](https://codeberg.org/adamhsparks/epicrop/src/commit/b331d7a1947e4137ff19f888149ac7078d504765)：
  `R/predict_leaf_blast.R`、`R/SEIR.R`、`R/calculate_audpc.R`。
- 五份原始檔與授權的 SHA-256 見 `tests/epirice_lb_trial/upstream_manifest.json`。
  上游為 `GPL-3 + file LICENSE`；數值測試保留原始 R 檔，未改寫其狀態更新。
- 模型版本 `EPIRICE_LB_epicrop_0.0.2_v1`；參數版本 `epicrop_v0.0.2_b331d7a`；
  schema `EPIRICE_LB_daily_v1`。完整設定為本資料夾的 `config.json`。

論文 Table 2 的最適溫度列為 25°C，正文測試與固定 R helper 採 20°C；R helper
註解指出表格誤植。本模組遵循 R helper。濕潤條件亦依該版 R 的**日平均** RH 與
`>=` 判定，與論文部分日最大 RH、嚴格 `>` 的敘述不同，未混用版本。

## 參數與 BLB 差異

下表的 LB 參數均取自上述固定 `predict_leaf_blast.R` 與 `SEIR.R`；BLB 保持現有 GUIDE 版本。

| 參數 | EPIRICE_BLB | EPIRICE_LB |
|---|---|---|
| site 單位（每 1 m² 作物群落） | 1 片葉 | 45 mm² 葉面積 |
| H0 / I0 | 100 / 1 | 600 / 1 |
| RcOpt | 0.87 | 1.14 |
| 潛伏期 p / 傳染期 i（日） | 5 / 30 | 5 / 20 |
| Sx | 3200 | 30000 |
| 聚集指數 a | 4 | 1 |
| RRG / RRS（每日） | 0.1 / 0.01 | 0.1 / 0.01 |
| onset（1 起算模擬日） | 20 | 15 |
| 最長模擬 | 120 日 | 120 日 |
| 濕潤條件 | 日平均 RH ≥90% 或日雨量 ≥5 mm | 相同 |
| RcA / RcT | BLB 專用控制點 | 下列 LB 控制點 |

LB `RcA` 的橫軸為 `simday - 1`（日齡），控制點：

```text
age: 0,5,10,15,20,25,30,35,40,45,50,55,60,65,70,75,80,85,90,95,100,105,110,115,120
RcA: 1,1,1,.9,.8,.7,.64,.59,.53,.43,.32,.22,.16,.09,.03,.02,.02,.02,.01,.01,.01,.01,.01,.01,.01
```

LB `RcT` 控制點：`(10,0),(15,.5),(20,1),(25,.6),(30,.2),(35,.05),(40,.01),(45,0)`。
兩曲線均線性內插、控制點範圍外為 0。`RcW = 1` 當 RHUM ≥90 或 RAIN ≥5，否則為 0；
剛好等於門檻包含在濕潤條件內。這是原模型的氣象條件，未新增防治警戒門檻。

## 數學與時間索引

每日時間步長一日；H 為健康可感染 sites、L 為潛伏 sites、I 為傳染 sites、removed
為已退出傳染期的 sites。原程式另有 `diseased_total`、`senesced_total` 與兩組 cohort。

```text
TS = H + diseased_total
C = 1 - diseased_total / TS
Rc = RcOpt * RcA * RcT * RcW
RI = Rc * I * C**a             # onset 之後
RG = RRG * H * (1 - TS / Sx)
H[t+1] = H[t] + RG[t] - RI[t] - RS[t]
intensity_active = (diseased_total - removed) / (TS - removed)
```

分母為零時沿用原程式的 0。RS 在首日為 `RRS*H`，其後包含原程式的退出 cohort 項，
並非始終只有 `RRS*H`。完整 removed、senescence 與 cohort 更新沿用已驗證核心，
沒有另寫一套 SEIR 離散化。

onset 之前 RI=0；onset 當日指定 `RI=I0`，即使當日 RcW=0 也會注入，**不是當日
I pool 直接變成 I0**。p 與 i 表示模型的潛伏與傳染批次時間，但原始切片包含端點。
在無次級感染情境中：第 15 日 RI=1，第 16 日進入 L，第 21 日 L→I 移轉，第 22 日
進入 I；第 22–42 日 I=1，第 43 日 I=0、removed=1。保留這些索引，未改成另一種期長定義。

RI、RG、RS 是 sites/day；狀態是 sites；intensity 為模型內的比例；AUDPC 是以一日
步長對 intensity 做梯形積分。`AUDPC` 每列為本次整段模擬積分，`AUDPC_cumulative`
則為截至該日的積分；分析逐日歷史時應留意這個差別。

手算例：首日 20°C、RH=95%、雨=0，RcA=RcT=RcW=1，Rc=1.14，H=600，RI=0，
RS=6，RG=0.1×600×(1−600/30000)=58.8；次日 H=600+58.8−0−6=652.8。

## 輸入、輸出與執行

從 repo 根目錄執行，環境使用 numpy、pandas（Windows 另需可用的 IANA tzdata）。
runner **不下載氣象**，讀取既有 `<站號>_<站名>_<緯度>_<經度>.csv`：
`time,temperature_2m,relativehumidity_2m,precipitation`。
逐時資料依臺灣日期分組，溫度及 RH 取平均、雨量加總，每日必須 24 個有效整點。
缺小時、重複時間、異常 RH／雨量或缺日均不補零。時區時間戳會轉為 Asia/Taipei；
無時區輸入必須已是臺灣當地時間。

```powershell
python -m models.EPIRICE_LB.predict `
  --weather-dir ERA5 --station-id C0G910 `
  --calendar path/to/regional_calendar.csv --station-list path/to/station_list.csv `
  --as-of 2026-10-23 --start-date 2026-02-04 --end-date 2026-06-16 `
  --output-dir path/to/lb-test/data --state-dir path/to/lb-test/states
```

也可直接 `python models/EPIRICE_LB/predict.py ...`。未傳日曆或站表時沿用 BLB 的
既有雲端來源。`--station-id` 可重複，也可改用 `--all-stations`；兩者擇一，並指定兩個輸出資料夾。
請以每次測試獨立資料夾保存結果。`--weather-provenance existing_request.json`
可將先前下載器的請求來源一併保存；資料夾叫 ERA5 並不保證其內容是 ERA5-Land。

作期採既有縣市代表日期，插秧當日為第 1 日，每次從插秧完整重建；結束點為
收割、120 日、氣象終點、所需輸出終點中最早者。只輸出近期三日也會重算前面狀態。
一期、二期分別初始化，跨年度作期沿用既有日曆方法。`is_forecast` 只依目標日與
`--as-of` 比較，氣象產品來源由 provenance 表達，不將再分析資料冒充田間觀測。

- `data/YYYYMMDD_EPIRICE_LB.csv`：每站每日完整模型輸出，主值為原始 RI；所有狀態及
  氣象加 `EPIRICE_LB_` 前綴，包含 `EPIRICE_LB_intensity_active`。
- `states/YYYY_1|2/<站號>.csv`：整個模擬作期的完整狀態、不截斷成近期視窗。
- `states/inputs/`：日曆、站表、參數及聚合後每日氣象快照、可選來源 JSON。
- `states/run_audit.json`：版本、來源、原始逐時檔 SHA-256、實作與輸入 SHA-256、
  各作期模擬完成情形及缺漏原因。

狀態 `ok` 可包含有效 0；`non_crop_period` 主值 -99；`missing_model_output` 或
`missing_crop_calendar` 主值空白；作期內超過 120 日為 `outside_model_duration`、空白。
資料缺漏不寫成零感染。`simulation_status` 區分完整模擬、氣象終點、指定日期終點。
有作期但完全無有效氣象時寫出 audit 與缺值每日檔後，以非零退出碼結束。

`output_contract.merge_compact()` 已接入 organizer，只新增主值、
intensity、status 三欄，其他模型保持原值；完整狀態留在模型自己的檔案。
近期表新增 `EPIRICE_LB`、`EPIRICE_LB_intensity_active`、`EPIRICE_LB_status`。
年度歷史表、高風險日數及網站規則維持既有方式，未執行歷史回填。

### 每日全站入口

`python models/EPIRICE_LB/daily.py` 使用既有 `ERA5_INPUT_DIR`／`ERA5_OUTPUT_DIR`，
完整每日檔寫入 `DATA_FOLDER`，完整作期狀態寫入 `EPIRICE_LB_STATE_DIR`
（預設 `rice_blast_prediction/epirice_lb_states`），不另下載氣象。
站表、作期沿用 `EPIRICE_STATIONS_SOURCE`、`EPIRICE_CALENDAR_SOURCE`。
一般每日輸出臺灣當日往前 30 日至往後 15 日，仍從各作插秧日起完整重建。
手動日期沿用 `EPIRICE_BACKFILL_START_DATE`／`EPIRICE_BACKFILL_END_DATE`，
或以 `EPIRICE_LB_BACKFILL_START_DATE`／`EPIRICE_LB_BACKFILL_END_DATE` 覆寫。
Daily Action 的推送已包含 LB 狀態資料夾。BLASTAM pipeline 使用相同 organizer，
會一併保留最新已存在的 LB 精簡欄位。

## 驗證證據與限制

[GitHub Action 39940530608](https://github.com/Raingel/rice_disease_forecast/actions/runs/39940530608)
已實際執行固定上游 R（R 4.4.3、data.table 1.16.4），三組各 120 日、22 個數值欄位
通過逐日比對；最大絕對誤差 `5.093170329928398e-11`，容許值 `1e-6`、rtol=0。
當時驗證的是 trial adapter；本次正式 LB adapter 再直接比對該次附件中保存的原始
R 輸出，得到相同誤差。新實作沒有在本機重新啟動 R；不能稱為新的一次 R 執行。
現有 trial harness 已改呼叫本模組，workflow 稀疏 checkout 已加入本資料夾，後續
手動觸發會針對本模組重做跨語言驗證。

目前本機 52 項 EPIRICE 測試通過，包括既有回歸及新增 10 項 runner 測試。
以既有 BLASTAM 模組路徑加入測試 import path 後，全體 69 項測試通過（零失敗、零略過）。
花壇實際歷史氣象小測：2026/2/5–6/4，共 120 日；使用先前保留的 Open-Meteo archive
逐時再分析資料，原請求未指定單一再分析產品，故不宣稱為固定 ERA5-Land。
日曆為當時保存的彰化縣代表日期，非該田區實際插秧紀錄。

測試通過僅支持軟體重現與介接正確，不代表臺灣田間預報能力已驗證。RI 不是發病機率，
intensity 不是經田間校正的實際發病率；BLB 與 LB site 單位不同，不能直接比較其絕對
感染量。I0/onset 為預設接種情境，無田間初始感染觀測；格點濕度亦無法取代冠層濕潤。
後續應先取得實際作期、觀測單位及獨立驗證資料，再分別檢查初始感染時點／量、氣象
代表性、齡期反應與 RcOpt 的可辨識性；任何校正應另立版本，保留此 baseline。

# EPIRICE 葉稻熱病：獨立小範圍測試套件

2026/10/23 更新：首次 [Action 39940530608](https://github.com/Raingel/rice_disease_forecast/actions/runs/39940530608)
已完成原始 R 執行及跨語言比對，三組各 120 日、22 欄最大誤差為 `5.093170329928398e-11`。
目前 candidate 已改呼叫 `models/EPIRICE_LB/model.py`；獨立 runner 與花壇歷史氣象測試
說明見 `models/EPIRICE_LB/README.md`。以下保留初始測試設計及執行方式。

這是研究用測試候選，尚未接入正式每日 pipeline。首次準備檔案時，只完成本機 Python
測試；**原始 R／Python 數值一致性仍待實際執行 R 後判定**。不能將 Python 測試通過
解釋為台灣田間預報能力已驗證。

## 範圍與來源

- 原始論文：Savary et al. (2012), *Crop Protection* 34:6–17，
  [DOI](https://doi.org/10.1016/j.cropro.2011.11.009)。
- 固定開源來源：[epicrop v0.0.2](https://codeberg.org/adamhsparks/epicrop/src/commit/b331d7a1947e4137ff19f888149ac7078d504765)，
  commit `b331d7a1947e4137ff19f888149ac7078d504765`。
- 原始 helper：`R/predict_leaf_blast.R`；核心：`R/SEIR.R`；AUDPC：`R/calculate_audpc.R`。
  另下載 DESCRIPTION 與 LICENSE。五份檔案均使用固定 SHA-256 驗證，見 `upstream_manifest.json`。
  上游授權為 `GPL-3 + file LICENSE`；下載內容保留原始署名與授權，未重寫上游 R 程式。
- 測試 adapter `candidate.py` 將獨立 LB 設定傳入正式 LB 模組，再使用既有可參數化的 SEIR 函數。
  **沒有改動 BLB 程式或 BLB config，也沒有把 BLB 疫病參數套用到 LB。**
  這種核心重用是否忠實，須由獨立 R 執行驗證，不靠 Python 自產答案當基準。

全部 LB 參數與控制點在 `config.json`。主要差異如下：

| 參數 | 現有 BLB GUIDE 版本 | 本次 LB 原始碼版本 |
|---|---|---|
| site | 1 片葉／site | 45 mm² 葉面積／site |
| H0、I0 | 100、1 | 600、1 |
| RcOpt | 0.87 | 1.14 |
| p、i | 5、30 日 | 5、20 日 |
| Sx | 3200 | 30000 |
| a | 4 | 1 |
| RRG、RRS | 0.1、0.01 | 0.1、0.01 |
| onset | 第 20 模擬日 | 第 15 模擬日 |
| 最多模擬天數 | 120 | 120 |
| RcA、RcT | BLB 專用曲線 | LB 專用 25、8 個控制點 |

不同 site 單位下，兩種模型的新增感染量不能直接當同單位數值比較。

已知來源差異：論文 Table 2 的葉稻熱病最適溫度寫 25°C，但正文測試設定為 20°C；
固定 R helper 註解明確說明表格誤植並採 20°C。本測試跟隨該 R helper，未混入其他版本。
R 核心輸入 RHUM 為日平均 RH，濕潤條件用 `RHUM >= 90 OR RAIN >= 5`；
這也不同於論文部分日最大 RH 與嚴格大於門檻的描述。日雨量由完整逐時資料加總。

## 測試內容

1. 三組各 120 日的合成氣象：固定條件、溫濕雨邊界循環、固定乾燥條件。
2. 原始 R helper 與 Python 使用同一份輸入，逐日比對 22 個數值欄位：
   RcOpt、Rc、RcA、RcT、RcW、H、L、I、removed、新增感染量、生長量、衰老流量、
   健康比例、intensity、AUDPC、累積 AUDPC、總 sites、累積 senesced、L→I 移轉量、
   diseased、兩種感染批次進入量。絕對容許誤差 `1e-6`、相對容許誤差 `0`。
3. 花壇站一期、二期各一個合成氣象情境，使用 repo 既有縣市作期快照。
   氣象是測試資料，**不是實際 2026 年花壇氣象**。
4. 另測跨年度作期、提早收割、氣象終點、缺一小時、缺日、非作期 -99、有效零、
   模型資料不足及 120 日上限。
5. 模型專屬每日 CSV 保留完整狀態，試驗合併表只加入 `EPIRICE_LB` 與
   `EPIRICE_LB_status`；保留既有 BLB 欄位及站日聯集。合併範例內的 BLB=0.25
   是標示清楚的測試占位值，不是正式 BLB 模型預測。

`reference.R` 用 R 的 `trace(..., exit=...)` 讀出原函數內部向量，沒有重寫迴圈。
同時核對 trace 取得的主要向量與原函數正式回傳值一致。
`AUDPC_cumulative` 是額外輸出的逐日梯形積分；全期間 AUDPC 直接取原函數回傳值。
缺少 R 執行結果會報錯，不會略過比對後宣告通過。

onset 日的 I0 是被指定的初始新增感染流量，不是當天直接設置 infectious pool，
也不是由當天氣象決定的新增傳播。原程式的 cohort 索引有包含端點的行為；
本測試保留這個行為，不另外改成直覺上的 p/i 天離散化。
`senescence_sites_rate` 也保留原程式包含移出 cohort 的計算，不能簡化成只有 RRS×H。

日一手算：20°C、RH=95%、雨=0 時，RcA=RcT=RcW=1，Rc=1.14；H=600，
初期沒有感染，RI=0，RS=0.01×600=6，RG=0.1×600×(1−600/30000)=58.8。
因此第二日 H=600+58.8−0−6=652.8。

## 本機執行

在 repo 根目錄，安裝 Python 套件：

```powershell
python -m pip install numpy==1.26.4 pandas==2.2.2 requests==2.32.3
python -m unittest discover -s tests -p 'test_epirice*.py' -v
python tests/epirice_lb_trial/run_trial.py prepare --output repo_review/epirice_lb_trial_results
```

上面沒有執行 R，`prepared.json` 會明確標示 `prepared_R_NOT_RUN`。
如果已安裝 R 及 data.table，可繼續：

```powershell
python tests/epirice_lb_trial/run_trial.py fetch-source --output repo_review/epirice_lb_trial_results
Rscript tests/epirice_lb_trial/reference.R repo_review/epirice_lb_trial_results
python tests/epirice_lb_trial/run_trial.py compare --output repo_review/epirice_lb_trial_results
```

R 參考測試環境固定為 R 4.4.3、data.table 1.16.4，workflow 會負責安裝。
任何語言、套件版本及原始來源都會保留在輸出紀錄。
`fetch-source` 只下載上游五個小檔案，不抓氣象、不下載整個 repo。

## 手動上傳與 GitHub Action

最初獨立測試新增下列檔案，不包含工作目錄內其他既有變更；目前版本另需一併保留
`models/EPIRICE_LB/` 與 `tests/test_epirice_lb_runner.py`：

```text
.github/workflows/epirice-lb-trial.yml
tests/test_epirice_lb_trial.py
tests/epirice_lb_trial/__init__.py
tests/epirice_lb_trial/candidate.py
tests/epirice_lb_trial/config.json
tests/epirice_lb_trial/upstream_manifest.json
tests/epirice_lb_trial/run_trial.py
tests/epirice_lb_trial/reference.R
tests/epirice_lb_trial/README.md
```

1. 依照上述 repo 相對路徑上傳，檔案不要全部放在根目錄，也不要只上傳 ZIP。
2. 新的 `workflow_dispatch` workflow 通常必須先存在預設分支 `main`，GitHub UI 才會顯示
   **Run workflow**。可先透過測試分支／PR 審查，再合入這組測試檔；它沒有 push 或定時觸發。
3. 在 Actions 選 **EPIRICE LB isolated R parity trial**，手動按 **Run workflow**。
4. 完成後下載 `epirice-lb-trial-<run_id>-<attempt>` 附件，檢查 `comparison.json`。
   只有該檔 `status=passed` 才表示三組跨語言比對通過。

Action 只有 `contents: read` 權限、25 分鐘上限、稀疏 checkout，不 commit、不 push。
所有結果寫到 runner 暫存目錄，包含失敗時的診斷附件。
下載來源或安裝 R 失敗時應保持失敗狀態，不能改成跳過 R。

## 輸出與未完成事項

- `inputs/`、`python/`、`R/`：同一組輸入及兩種語言的逐日結果。
- `station_trial/`：兩個作期完整狀態、六份每日模型檔與一份精簡合併範例。
- `prepared.json`：版本、輸入來源、受保護正式程式的 SHA-256。
- `upstream_verified.json`、`upstream/`：上游版本、實際下載來源與原始授權。
- `comparison.json`：各情境、各欄最大絕對誤差與對應模擬日。
- `failure.json`：執行失敗的階段與原因。

目前已完成首次 GitHub Action、R/Python 跨語言驗證與花壇歷史氣象單站 runner 測試。
仍未完成全站效能測量、每日正式 pipeline 接入及網站呈現。未制定任何警戒門檻或防治建議。
本機範例與數值一致性皆不代表田間預報能力；臺灣品種與田區校正屬後續獨立工作。

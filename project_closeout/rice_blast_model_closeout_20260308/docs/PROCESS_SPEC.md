# 產製流程規格（到模型訓練定版）

## 1. 專案目標
建立可部署的稻熱病二元風險預測模型（class 0/1），主指標為 MCC，並輸出模型與 metadata。

## 2. 資料流程
1. 標註與清理：將調查資料整理為事件級資料。
2. 氣象對齊：以 Open-Meteo 取得對應時序天氣。
3. 特徵打包：產生標準化特徵與滑動視窗資料。
4. 訓練驗證：使用 held-out 年份驗證模型泛化能力。
5. 定版輸出：輸出模型權重與 metadata、報告與分析證據。

## 3. 模型定版資訊
- 架構：TCN + Attention
- 驗證年份（held-out）：2024, 2025
- 最佳指標：MCC = 0.6872
- 決策門檻：0.23
- 任務：二元分類（發生/不發生）

## 4. 特徵與視窗
- 視窗：監測日前 -30 至 -3 天（time steps = 28）
- 特徵：
  - temperature_2m_max_z
  - temperature_2m_mean_z
  - temperature_2m_min_z
  - relative_humidity_2m_max_z
  - relative_humidity_2m_mean_z
  - relative_humidity_2m_min_z
  - wind_speed_10m_max_z
  - wind_speed_10m_mean_z
  - wind_speed_10m_min_z
  - precipitation_sum_log1p_z

## 5. 已完成延伸分析
- 高低發年固定比較（2013/2019 vs 2016/2020）
- 西南部關鍵田區 2-5 月風險提早上升分析
- 氣象差異指標（溫度、濕度、風、降雨）比較

## 6. 已知限制
- 尚未納入品種與田間管理因子
- 目前為發生風險，不是嚴重度（AUDPC）模型
- 空間解析度仍受氣象資料網格限制

## 7. 結案建議
1. 部署先以目前二元風險模型上線（MCC 最佳版本）。
2. 後續新增嚴重度分級時，先從長期指標田資料建立 AUDPC 子模型。
3. 若導入 SEAS5 季節預報，建議優先使用累積/分位數降雨特徵。

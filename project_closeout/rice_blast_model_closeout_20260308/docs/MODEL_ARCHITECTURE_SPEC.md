# 模型架構規格（Closeout 定版） / Model Architecture Spec (Closeout)

## 中文（台灣）
本 closeout 的 `model/final_model.pt` 是 PyTorch `state_dict`，不是含程式碼的完整序列化模型。

為避免下游 agent 重新猜架構，已提供可直接載入的參考程式：
- `model/inference/reference_tcn_attn.py`

### 部署正式門檻（唯一真值）
- `decision_threshold = 0.23`
- 來源：`model/model_metadata.json`、`model/final_model_config.json`、`docs/INFERENCE_CONTRACT.json`

### 架構（定版契約）
- 類型：TCN + Attention（二元分類）
- 輸入：`[B, T, F]`，其中 `T=28`，`F=10`
- 子網路：
  1. Conv1d(F -> 32, kernel_size=3, padding=1) + ReLU
  2. Conv1d(32 -> 32, kernel_size=3, padding=1) + ReLU
- Attention：Linear(32 -> 1) 對時間步做 softmax 權重
- Head：Dropout(0.2) + Linear(32 -> 1)
- 輸出：logit，經 sigmoid 得到 `risk_prob`

### 權重鍵值 fingerprint（用於驗證檔案一致性）
- `net.0.weight`: [32, 10, 3]
- `net.0.bias`: [32]
- `net.2.weight`: [32, 32, 3]
- `net.2.bias`: [32]
- `attn.weight`: [1, 32]
- `attn.bias`: [1]
- `out.weight`: [1, 32]
- `out.bias`: [1]

## English
`model/final_model.pt` in this closeout is a PyTorch `state_dict` (weights-only checkpoint).

To avoid architecture guessing in downstream integration, a direct loader implementation is provided:
- `model/inference/reference_tcn_attn.py`

### Deployment threshold (single source of truth)
- `decision_threshold = 0.23`
- Source files: `model/model_metadata.json`, `model/final_model_config.json`, `docs/INFERENCE_CONTRACT.json`

### Architecture contract
- Type: TCN + Attention (binary classification)
- Input: `[B, T, F]`, with `T=28`, `F=10`
- Backbone:
  1. Conv1d(F -> 32, kernel_size=3, padding=1) + ReLU
  2. Conv1d(32 -> 32, kernel_size=3, padding=1) + ReLU
- Attention: Linear(32 -> 1), softmax over time
- Head: Dropout(0.2) + Linear(32 -> 1)
- Output: logits -> sigmoid => `risk_prob`

### State_dict fingerprint
- `net.0.weight`: [32, 10, 3]
- `net.0.bias`: [32]
- `net.2.weight`: [32, 32, 3]
- `net.2.bias`: [32]
- `attn.weight`: [1, 32]
- `attn.bias`: [1]
- `out.weight`: [1, 32]
- `out.bias`: [1]

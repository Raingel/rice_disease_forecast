from __future__ import annotations

from pathlib import Path
import json

import torch
import torch.nn as nn


class TCNAttnBinary(nn.Module):
    """Reference deployment model for closeout final_model.pt (state_dict)."""

    def __init__(self, input_dim: int = 10, channels: int = 32, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(input_dim, channels, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(channels, channels, kernel_size=3, padding=1),
            nn.ReLU(),
        )
        self.attn = nn.Linear(channels, 1)
        self.dropout = nn.Dropout(dropout)
        self.out = nn.Linear(channels, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.net(x.transpose(1, 2)).transpose(1, 2)
        a = self.attn(h).squeeze(-1)
        w = torch.softmax(a, dim=1)
        ctx = torch.sum(h * w.unsqueeze(-1), dim=1)
        ctx = self.dropout(ctx)
        return self.out(ctx).squeeze(-1)


def load_closeout_model(closeout_dir: str | Path, device: str = "cpu") -> dict:
    closeout_dir = Path(closeout_dir)
    meta_path = closeout_dir / "model" / "model_metadata.json"
    weight_path = closeout_dir / "model" / "final_model.pt"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    features = meta["feature_selection"]["selected_z_features"]
    channels = int(meta["architecture"].get("channels", 32))
    dropout = float(meta["architecture"].get("dropout", 0.2))
    threshold = float(meta["inference_contract"]["decision_threshold"])

    model = TCNAttnBinary(input_dim=len(features), channels=channels, dropout=dropout)
    state = torch.load(weight_path, map_location=device)
    model.load_state_dict(state, strict=True)
    model.to(device)
    model.eval()
    return {
        "model": model,
        "threshold": threshold,
        "metadata": meta,
    }


@torch.no_grad()
def predict_prob(model: nn.Module, x_bt_f: torch.Tensor) -> torch.Tensor:
    logits = model(x_bt_f)
    return torch.sigmoid(logits)

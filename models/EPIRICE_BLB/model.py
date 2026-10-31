"""Backward-compatible bacterial blight model import path.

The EPIRICE numerical kernel now lives in models.epirice.engine.
Preserve both package imports and the legacy direct predict.py invocation
used by scripts/run_daily_pipeline.sh.
"""
from __future__ import annotations

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from models.epirice.engine import interp_zero, simulate_epirice

simulate_epirice_blb = simulate_epirice

__all__ = ["interp_zero", "simulate_epirice_blb"]

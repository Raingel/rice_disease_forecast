"""Shared, parameter-driven EPIRICE SEIR numerical engine."""

from .engine import interp_zero, simulate_epirice

__all__ = ["interp_zero", "simulate_epirice"]

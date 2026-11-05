"""Phase 4B: opt-in, fail-closed bridge from Model Registry to v1 pipelines.

Legacy remains the default. Registry mode uses validated upstream metadata
for the exact already-approved model set while *preserving the frozen v1
model processing order*, CSV filenames, and seasonal columns. New models
cannot silently change a production CSV; that requires a new reviewed
external-contract version and an explicit adapter.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from models.model_registry import ModelRegistryError, load_registry


CATALOG_LEGACY = "legacy"
CATALOG_REGISTRY = "registry"
_ALLOWED = frozenset((CATALOG_LEGACY, CATALOG_REGISTRY))


def _mode(mode: str) -> str:
    if mode not in _ALLOWED:
        raise ModelRegistryError(
            f"Unknown FORECAST_MODEL_CATALOG_MODE: {mode!r} "
            "(expected 'legacy' or 'registry')"
        )
    return mode


def _document(registry_path: str | Path | None):
    # Always read the validated local snapshot; never fetch a mutable URL.
    return load_registry() if registry_path is None else load_registry(registry_path)


def _guard_exact_mapping(
    purpose: str, legacy: Mapping[str, str], registered: dict[str, str],
) -> dict[str, str]:
    if set(legacy) != set(registered):
        raise ModelRegistryError(
            f"{purpose}: registry and published v1 model sets differ; "
            f"missing={sorted(set(legacy) - set(registered))}, "
            f"extra={sorted(set(registered) - set(legacy))}"
        )
    for model_id, old_field in legacy.items():
        if registered[model_id] != old_field:
            raise ModelRegistryError(
                f"{purpose}: {model_id} changes a published field/filename "
                f"from {old_field!r} to {registered[model_id]!r}"
            )
    # The registry may have a different order. Keep the public v1 model
    # precedence exactly, because the organizer's legacy mode left-joins
    # ordinary models in this order.
    return {model_id: registered[model_id] for model_id in legacy}


def resolve_daily_model_files(
    legacy_files: Mapping[str, str], *,
    mode: str = CATALOG_LEGACY,
    registry_path: str | Path | None = None,
) -> dict[str, str]:
    """Source v1 daily CSV suffixes from the registry only when opted in."""
    _mode(mode)
    if mode == CATALOG_LEGACY:
        return dict(legacy_files)
    models = _document(registry_path)["models"]
    registered = {}
    for item in models:
        source = item["source"]
        if "station_series" not in item["capabilities"]:
            continue
        suffix = source.get("daily_filename_suffix")
        if suffix is None:
            if item["model_id"] != "planthopper":
                raise ModelRegistryError(
                    f"daily: missing daily filename suffix for {item['model_id']}"
                )
            continue
        if item["model_id"] == "planthopper":
            raise ModelRegistryError("daily: planthopper is an external spatial feed")
        if item["primary_field"] != item["model_id"]:
            raise ModelRegistryError("daily: source primary_field mismatch")
        registered[item["model_id"]] = suffix
    return _guard_exact_mapping("daily", legacy_files, registered)


def resolve_seasonal_model_columns(
    legacy_columns: Mapping[str, str], *,
    mode: str = CATALOG_LEGACY,
    registry_path: str | Path | None = None,
) -> dict[str, str]:
    """Use approved registry model IDs/fields, never inferred risk scores."""
    _mode(mode)
    if mode == CATALOG_LEGACY:
        return dict(legacy_columns)
    models = _document(registry_path)["models"]
    registered = {}
    for item in models:
        if "season_summary" not in item["capabilities"]:
            continue
        if not item["public"]:
            raise ModelRegistryError(
                f"seasonal: private model {item['model_id']} cannot be published"
            )
        name = item["model_id"]
        profiles = item["risk_profiles"]
        if name == "planthopper":
            if profiles.get("pest_migration") != "legacy_planthopper":
                raise ModelRegistryError("seasonal: unsupported planthopper profile")
            registered[name] = "value"
        else:
            profile = (
                "legacy_season_blastgat" if name == "BlastGAT"
                else "legacy_season_disease"
            )
            if item["value_type"] != "legacy_score" or profiles.get("season_summary") != profile:
                raise ModelRegistryError(
                    f"seasonal: {name} lacks the existing approved risk profile"
                )
            if item["source"].get("daily_filename_suffix") != name:
                raise ModelRegistryError(f"seasonal: unapproved source for {name}")
            registered[name] = item["primary_field"]
    return _guard_exact_mapping("seasonal", legacy_columns, registered)

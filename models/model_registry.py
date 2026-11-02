"""Strict, read-only metadata validation for the upstream Model Registry.

No scheduled prediction runner imports this module. The registry is metadata,
NOT an instruction to automatically execute or publish a model.
"""
from __future__ import annotations

import json
from pathlib import Path
import re


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "registry" / "models.json"
REGISTRY_SCHEMA_VERSION = "2.0"
KNOWN_CAPABILITIES = frozenset({
    "station_series", "legacy_warning_map", "season_summary",
    "mechanistic_states", "pest_grid",
})
KNOWN_RISK_PROFILES = frozenset({
    "legacy_homepage_grid_disease",
    "legacy_homepage_chart",
    "legacy_warning_map_disease",
    "legacy_warning_map_blastgat",
    "legacy_season_disease",
    "legacy_season_blastgat",
    "legacy_planthopper",
})
KNOWN_PROFILE_PURPOSES = frozenset({
    "homepage_grid", "homepage_chart", "warning_map",
    "warning_transform", "season_summary", "pest_migration",
})
ALLOWED_VALUE_TYPES = frozenset({
    "legacy_score", "migration_index", "new_infection_flow",
})
ALLOWED_MODEL_TYPES = frozenset({
    "statistical", "decision_tree", "rule_based", "migration", "mechanistic",
})
ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ModelRegistryError(ValueError):
    """Missing or ambiguous model metadata; reject rather than guess."""


def _nonempty_string(value, key):
    if not isinstance(value, str) or not value.strip():
        raise ModelRegistryError(f"{key}: expected nonempty string")
    return value


def _identifier(value, key):
    value = _nonempty_string(value, key)
    if not ID_RE.fullmatch(value):
        raise ModelRegistryError(f"{key}: unsafe identifier")
    return value


def validate_registry(document: dict) -> dict:
    """Validate a full upstream registry and return it without modification."""
    if not isinstance(document, dict):
        raise ModelRegistryError("registry must be an object")
    if document.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ModelRegistryError("unsupported registry schema_version")
    _nonempty_string(document.get("registry_version"), "registry_version")
    if document.get("authority") != "upstream":
        raise ModelRegistryError("registry must identify the upstream authority")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise ModelRegistryError("missing registry provenance")
    if provenance.get("repository") != "Raingel/rice_disease_forecast":
        raise ModelRegistryError("untrusted registry provenance")
    _nonempty_string(provenance.get("source_revision"), "source_revision")
    models = document.get("models")
    if not isinstance(models, list) or not models:
        raise ModelRegistryError("models must be a nonempty array")

    seen = set()
    for pos, model in enumerate(models):
        if not isinstance(model, dict):
            raise ModelRegistryError(f"model[{pos}]: expected object")
        model_id = _identifier(model.get("model_id"), "model_id")
        if model_id in seen:
            raise ModelRegistryError(f"duplicate model_id: {model_id}")
        seen.add(model_id)
        _identifier(model.get("disease_id"), "disease_id")
        _identifier(model.get("primary_field"), "primary_field")
        _nonempty_string(model.get("display_name"), "display_name")
        _nonempty_string(model.get("unit"), "unit")
        _nonempty_string(model.get("model_version"), "model_version")
        if model.get("model_type") not in ALLOWED_MODEL_TYPES:
            raise ModelRegistryError(f"{model_id}: unknown model_type")
        if model.get("value_type") not in ALLOWED_VALUE_TYPES:
            raise ModelRegistryError(f"{model_id}: unknown value_type")
        if not isinstance(model.get("public"), bool):
            raise ModelRegistryError(f"{model_id}: public must be boolean")
        capabilities = model.get("capabilities")
        if (not isinstance(capabilities, list) or not capabilities
                or any(not isinstance(c, str) or c not in KNOWN_CAPABILITIES
                       for c in capabilities)
                or len(capabilities) != len(set(capabilities))):
            raise ModelRegistryError(f"{model_id}: invalid capabilities")
        profiles = model.get("risk_profiles")
        if not isinstance(profiles, dict):
            raise ModelRegistryError(f"{model_id}: risk_profiles must be object")
        for purpose, name in profiles.items():
            if purpose not in KNOWN_PROFILE_PURPOSES or name not in KNOWN_RISK_PROFILES:
                raise ModelRegistryError(f"{model_id}: unknown risk profile mapping")
        source = model.get("source")
        if not isinstance(source, dict) or source.get("kind") != "legacy_github_csv":
            raise ModelRegistryError(f"{model_id}: unknown source adapter")
        _nonempty_string(source.get("revision"), "source.revision")

        if "legacy_warning_map" in capabilities:
            if model["value_type"] != "legacy_score" or profiles.get("warning_map") != "legacy_warning_map_disease":
                raise ModelRegistryError(f"{model_id}: legacy warning-map profile required")
        if model["value_type"] == "legacy_score":
            if (model["primary_field"] != model_id
                    or source.get("daily_filename_suffix") != model_id
                    or not {"station_series", "season_summary", "legacy_warning_map"}.issubset(capabilities)):
                raise ModelRegistryError(f"{model_id}: inconsistent legacy-score mapping")
            if "warning_transform" in profiles and model_id != "BlastGAT":
                raise ModelRegistryError(f"{model_id}: unapproved warning transform")
        elif model["value_type"] == "new_infection_flow":
            if (model["model_type"] != "mechanistic" or model["public"]
                    or model["primary_field"] != model_id
                    or source.get("primary_field") != "rateinf_new_infections"
                    or not source.get("source_schema_version")
                    or profiles or "legacy_warning_map" in capabilities):
                raise ModelRegistryError(f"{model_id}: mechanistic model must stay private and uncalibrated")
        elif model["value_type"] == "migration_index":
            if (model_id != "planthopper"
                    or source.get("upstream_repository") != "Raingel/HYSPLIT-Planthopper-Forecast"
                    or "pest_migration" not in profiles):
                raise ModelRegistryError(f"{model_id}: incomplete pest migration source")
    return document


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> dict:
    try:
        with Path(path).open(encoding="utf-8") as f:
            document = json.load(f)
    except (OSError, ValueError) as exc:
        raise ModelRegistryError("cannot read model registry JSON") from exc
    return validate_registry(document)


def visible_models(document: dict, *, include_private: bool = False) -> list[dict]:
    """Never publish private models unless the caller explicitly opts in."""
    validate_registry(document)
    return [
        item for item in document["models"]
        if item["public"] or include_private
    ]


def get_model(document: dict, model_id: str, *, include_private: bool = False) -> dict:
    _identifier(model_id, "model_id")
    for item in visible_models(document, include_private=include_private):
        if item["model_id"] == model_id:
            return item
    raise ModelRegistryError(f"model not published or not registered: {model_id}")

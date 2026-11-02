"""Phase 4 registry contract and legacy pipeline parity tests (read-only).

No live files or services are written. This tests a *metadata* registry,
not an automatic activation of a model in current schedulers or the website.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import unittest

from models.model_registry import (
    ModelRegistryError,
    get_model,
    load_registry,
    validate_registry,
    visible_models,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "registry/models.json"
PUBLIC = {
    "BlastGRU-TW", "BlastTF", "BlastLSTLS", "BlastGAT",
    "BlastDT2", "BLASTAM", "BLBTSLS", "planthopper",
}
MECHANISTIC = {"EPIRICE_BLB", "EPIRICE_LB"}
DAILY = PUBLIC - {"planthopper"} | MECHANISTIC


def extract_literal(path: Path, target: str):
    """Read the existing published module's literal constant without import side effects."""
    program = ast.parse(path.read_text(encoding="utf-8"))
    for node in program.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == target for t in node.targets
        ):
            return ast.literal_eval(node.value)
        if (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name) and node.target.id == target):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing {target} in {path}")


class UpstreamModelRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()

    def test_registry_document_and_ids_match_observed_legacy_website(self):
        self.assertEqual(self.registry["schema_version"], "2.0")
        self.assertEqual(self.registry["authority"], "upstream")
        self.assertEqual(self.registry["provenance"]["repository"], "Raingel/rice_disease_forecast")
        ids = [m["model_id"] for m in self.registry["models"]]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)
        self.assertEqual(set(ids), PUBLIC | MECHANISTIC)
        self.assertEqual(
            ids,
            ["BlastGRU-TW", "BlastTF", "BlastLSTLS", "BlastGAT",
             "BlastDT2", "BLASTAM", "BLBTSLS", "planthopper",
             "EPIRICE_BLB", "EPIRICE_LB"],
        )
        self.assertEqual(json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
                         self.registry)

    def test_visibility_is_explicit_and_private_models_are_not_exposed(self):
        self.assertEqual(
            {m["model_id"] for m in visible_models(self.registry)}, PUBLIC)
        self.assertEqual(
            {m["model_id"] for m in visible_models(self.registry, include_private=True)},
            PUBLIC | MECHANISTIC,
        )
        for name in MECHANISTIC:
            with self.assertRaises(ModelRegistryError):
                get_model(self.registry, name)
            self.assertEqual(get_model(self.registry, name, include_private=True)["model_id"],
                             name)
        with self.assertRaises(ModelRegistryError):
            get_model(self.registry, "NOT_A_MODEL")
        with self.assertRaises(ModelRegistryError):
            get_model(self.registry, "../secrets")

    def test_model_csv_suffix_parity_with_unmodified_daily_organizer(self):
        actual = extract_literal(ROOT / "models/recent_forecast_organizer.py", "MODEL_FILES")
        self.assertEqual(set(actual), DAILY)
        for model in self.registry["models"]:
            mid = model["model_id"]
            if mid == "planthopper":
                self.assertEqual(model["source"]["upstream_repository"],
                                 "Raingel/HYSPLIT-Planthopper-Forecast")
                continue
            self.assertEqual(actual[mid], model["source"]["daily_filename_suffix"])
            self.assertEqual(actual[mid], mid)
            self.assertEqual(model["primary_field"], mid)

    def test_seasonal_output_model_ids_and_risk_profiles_stay_separate(self):
        # MODEL_COLS is defined as a literal; thresholds require import as
        # existing constants reference DEFAULT_RISK_THRESHOLD_MODELS.
        seasonal = extract_literal(ROOT / "models/crop_season_avg.py", "MODEL_COLS")
        self.assertEqual(set(seasonal), PUBLIC)
        self.assertEqual(seasonal["planthopper"], "value")
        for name in PUBLIC - {"planthopper"}:
            self.assertEqual(seasonal[name], name)
        from models import crop_season_avg as season
        self.assertEqual(season.MODEL_RISK_THRESHOLDS["BlastGAT"], 0.23)
        self.assertEqual(season.DEFAULT_RISK_THRESHOLD_MODELS, 0.5)
        self.assertEqual(season.RISK_THRESHOLD_PLAN, 0.0)
        gat = get_model(self.registry, "BlastGAT")
        self.assertEqual(gat["risk_profiles"]["warning_transform"],
                         "legacy_warning_map_blastgat")
        self.assertEqual(gat["risk_profiles"]["season_summary"],
                         "legacy_season_blastgat")
        self.assertEqual(get_model(self.registry, "planthopper")["risk_profiles"],
                         {"pest_migration": "legacy_planthopper"})

    def test_model_types_do_not_invent_probability_or_biological_units(self):
        for m in self.registry["models"]:
            self.assertNotIn("probability", m["value_type"])
            self.assertNotIn("percent", m["value_type"])
            self.assertEqual(m["model_version"], "unknown")
            if m["model_id"] in MECHANISTIC:
                self.assertFalse(m["public"])
                self.assertEqual(m["value_type"], "new_infection_flow")
                self.assertEqual(m["source"]["primary_field"],
                                 "rateinf_new_infections")
                self.assertEqual(m["unit"], "unknown")
                self.assertEqual(m["risk_profiles"], {})
                self.assertEqual(m["capabilities"], ["station_series"])
            elif m["model_id"] == "planthopper":
                self.assertEqual(m["value_type"], "migration_index")
                self.assertEqual(m["unit"], "legacy_percent_or_fraction_ambiguous")
            else:
                self.assertEqual(m["value_type"], "legacy_score")
                self.assertEqual(m["unit"], "unknown")

    def test_readonly_validation_does_not_mutate_document(self):
        before = deepcopy(self.registry)
        self.assertIs(validate_registry(self.registry), self.registry)
        visible_models(self.registry)
        get_model(self.registry, "BlastTF")
        self.assertEqual(self.registry, before)

    def test_dynamic_registration_is_possible_only_when_metadata_is_complete(self):
        registry = deepcopy(self.registry)
        item = deepcopy(get_model(registry, "BlastTF"))
        item.update({
            "model_id": "TEST_MODEL_X",
            "primary_field": "TEST_MODEL_X",
            "display_name": "Isolated regression only",
            "public": False,
        })
        item["source"]["daily_filename_suffix"] = "TEST_MODEL_X"
        registry["models"].append(item)
        validate_registry(registry)
        self.assertNotIn("TEST_MODEL_X", {m["model_id"] for m in visible_models(registry)})
        self.assertEqual(
            get_model(registry, "TEST_MODEL_X", include_private=True)["model_id"],
            "TEST_MODEL_X",
        )

    def test_unknown_model_and_unapproved_visibility_are_rejected(self):
        for variant in (
            lambda r: r["models"].append(deepcopy(r["models"][0])),
            lambda r: r["models"][-1].__setitem__("public", True),
            lambda r: r["models"][0].__setitem__("public", "false"),
            lambda r: r["models"][0].__setitem__("model_id", "../BAD"),
            lambda r: r["models"][0].__setitem__("primary_field", "../BAD"),
            lambda r: r["models"][0].__setitem__("value_type", "probability"),
            lambda r: r["models"][0].__setitem__("capabilities", ["legacy_warning_map", "legacy_warning_map"]),
            lambda r: r["models"][0]["risk_profiles"].__setitem__(
                "warning_map", "unapproved_profile"
            ),
            lambda r: r["models"][1]["risk_profiles"].__setitem__(
                "warning_transform", "legacy_warning_map_blastgat"
            ),
            lambda r: r["models"][0]["source"].__setitem__(
                "daily_filename_suffix", "WRONG"
            ),
            lambda r: r.__setitem__("authority", "website_local_bootstrap_non_authoritative"),
            lambda r: r.__setitem__("schema_version", "3.0"),
        ):
            registry = deepcopy(self.registry)
            variant(registry)
            with self.subTest(variant=repr(variant)):
                with self.assertRaises(ModelRegistryError):
                    validate_registry(registry)

    def test_missing_metadata_raises_instead_of_guessing(self):
        for field in ("display_name", "model_version", "risk_profiles", "source",
                      "capabilities", "unit", "disease_id"):
            changed = deepcopy(self.registry)
            del changed["models"][0][field]
            with self.subTest(field=field):
                with self.assertRaises(ModelRegistryError):
                    validate_registry(changed)

    def test_model_registration_never_implicitly_executes_or_republishes(self):
        # A static JSON+validator layer must not import any model prediction
        # runner or write to rice_blast_prediction.
        src = (ROOT / "models/model_registry.py").read_text(encoding="utf-8")
        registry_file = REGISTRY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("to_csv(", src)
        self.assertNotIn("subprocess", src)
        self.assertNotIn("predict.run(", src)
        self.assertNotIn("output_dir", registry_file)


if __name__ == "__main__":
    unittest.main()

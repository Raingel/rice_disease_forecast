"""Phase 4B: shadow-test Registry-backed model catalog against public v1.

All tests use immutable public snapshots or temporary local CSVs. Neither
the website nor scheduled forecast output is modified or fetched.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

os.environ.setdefault(
    "RECENT_OUTPUT_FOLDER", str(Path(tempfile.gettempdir()) / "phase4b-registry-output")
)
from models import recent_forecast_organizer as organizer
from models import crop_season_avg as season
from models.model_registry import ModelRegistryError, load_registry
from models.registry_legacy_bridge import (
    resolve_daily_model_files, resolve_seasonal_model_columns,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = json.loads(
    (ROOT / "tests/fixtures/public_contract_v1/snapshot_20261101.json").read_text(
        encoding="utf-8"
    )
)
COVERAGE = json.loads(
    (ROOT / "tests/fixtures/coverage_shadow/20261120_22_published.json").read_text(
        encoding="utf-8"
    )
)


def _byte_csv(frame):
    out = io.BytesIO()
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    return out.getvalue()


class RegistryBridgeContractTests(unittest.TestCase):
    def test_exact_known_model_sets_fields_and_frozen_processing_order(self):
        daily = resolve_daily_model_files(organizer.MODEL_FILES, mode="registry")
        seasonal = resolve_seasonal_model_columns(season.MODEL_COLS, mode="registry")
        self.assertEqual(daily, organizer.MODEL_FILES)
        self.assertEqual(list(daily), list(organizer.MODEL_FILES))
        self.assertEqual(seasonal, season.MODEL_COLS)
        self.assertEqual(list(seasonal), list(season.MODEL_COLS))
        self.assertEqual(seasonal["planthopper"], "value")
        self.assertNotIn("planthopper", daily)
        self.assertNotIn("EPIRICE_BLB", seasonal)
        self.assertNotIn("EPIRICE_LB", seasonal)
        self.assertEqual(season.MODEL_RISK_THRESHOLDS["BlastGAT"], 0.23)
        self.assertEqual(season.DEFAULT_RISK_THRESHOLD_MODELS, 0.5)
        self.assertEqual(season.RISK_THRESHOLD_PLAN, 0.0)

    def test_legacy_mode_never_depends_on_registry_load(self):
        with patch("models.registry_legacy_bridge.load_registry",
                   side_effect=AssertionError("must not load registry")):
            self.assertEqual(
                resolve_daily_model_files(organizer.MODEL_FILES),
                organizer.MODEL_FILES,
            )
            self.assertEqual(
                resolve_seasonal_model_columns(season.MODEL_COLS),
                season.MODEL_COLS,
            )

    def test_unknown_catalog_fails_closed(self):
        for f, legacy in (
            (resolve_daily_model_files, organizer.MODEL_FILES),
            (resolve_seasonal_model_columns, season.MODEL_COLS),
        ):
            with self.subTest(f=f.__name__):
                with self.assertRaises(ModelRegistryError):
                    f(legacy, mode="enabled")
        with patch.dict(os.environ, {"FORECAST_MODEL_CATALOG_MODE": "bad"}):
            with patch.object(organizer, "merge_daily_predictions",
                              side_effect=AssertionError("must reject before pipeline")):
                with self.assertRaises(ModelRegistryError):
                    organizer.main()
            with patch.object(season, "accumulate_for_period",
                              side_effect=AssertionError("must reject before pipeline")):
                with self.assertRaises(ModelRegistryError):
                    season.main()

    def test_missing_or_unreviewed_registry_mappings_fail_instead_of_falling_back(self):
        document = load_registry()
        cases = [
            ("remove_existing", lambda d: d["models"].pop(0)),
            ("new_public_model", lambda d: _append_test_model(d)),
            ("wrong_daily_suffix", lambda d: d["models"][0]["source"].__setitem__(
                "daily_filename_suffix", "CHANGED")),
            ("unapproved_gat_season", lambda d: next(
                m for m in d["models"] if m["model_id"] == "BlastGAT"
            )["risk_profiles"].__setitem__("season_summary", "legacy_season_disease")),
            ("private_season", lambda d: d["models"][-1]["capabilities"].append(
                "season_summary")),
        ]
        for name, alter in cases:
            with self.subTest(case=name):
                changed = deepcopy(document)
                alter(changed)
                with tempfile.TemporaryDirectory() as folder:
                    file = Path(folder) / "models.json"
                    file.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
                    for function, baseline in (
                        (resolve_daily_model_files, organizer.MODEL_FILES),
                        (resolve_seasonal_model_columns, season.MODEL_COLS),
                    ):
                        if name == "unapproved_gat_season" and function is resolve_daily_model_files:
                            continue
                        if name == "private_season" and function is resolve_daily_model_files:
                            continue
                        with self.assertRaises(ModelRegistryError):
                            function(baseline, mode="registry", registry_path=file)

    def test_registry_missing_file_and_json_error_raise_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "absent.json"
            with self.assertRaises(ModelRegistryError):
                resolve_daily_model_files(
                    organizer.MODEL_FILES, mode="registry", registry_path=file
                )
            file.write_text("{broken json", encoding="utf-8")
            with self.assertRaises(ModelRegistryError):
                resolve_seasonal_model_columns(
                    season.MODEL_COLS, mode="registry", registry_path=file
                )

    def test_published_20261101_daily_projection_is_byte_equivalent(self):
        with tempfile.TemporaryDirectory() as folder:
            for model, raw in PUBLIC["daily_csv"].items():
                (Path(folder) / f"20261101_{model}.csv").write_text(
                    raw, encoding="utf-8"
                )
            with patch.object(organizer, "DATA_FOLDER", folder), \
                 patch.object(organizer, "load_planthopper_data", return_value=None):
                for coverage in ("legacy", "union"):
                    with self.subTest(coverage=coverage):
                        a = organizer.merge_daily_predictions(
                            "20261101", coverage_mode=coverage
                        )
                        b = organizer.merge_daily_predictions(
                            "20261101", coverage_mode=coverage, catalog_mode="registry"
                        )
                        pd.testing.assert_frame_equal(a, b)
                        self.assertEqual(_byte_csv(a), _byte_csv(b))
                        ref = b.set_index("站號")
                        for model in ("EPIRICE_BLB", "EPIRICE_LB"):
                            self.assertEqual(ref.at["466881", model], -99.0)
                            self.assertEqual(
                                ref.at["466881", model + "_status"], "non_crop_period"
                            )
                            self.assertEqual(ref.at["466990", model], 0.0)
                            self.assertEqual(ref.at["466990", model + "_status"], "ok")
                            self.assertTrue(pd.isna(ref.at["467990", model]))

    def test_20261121_22_real_coverage_samples_match_both_modes(self):
        for day in COVERAGE["days"]:
            with self.subTest(day=day["day"]):
                with tempfile.TemporaryDirectory() as folder:
                    for source in day["sources"]:
                        (Path(folder) / f'{day["day"]}_{source["model_id"]}.csv').write_text(
                            source["sample_csv"], encoding="utf-8"
                        )
                    with patch.object(organizer, "DATA_FOLDER", folder), \
                         patch.object(organizer, "load_planthopper_data",
                                      return_value=None):
                        for coverage in ("legacy", "union"):
                            baseline = organizer.merge_daily_predictions(
                                day["day"], coverage_mode=coverage
                            )
                            reg = organizer.merge_daily_predictions(
                                day["day"], coverage_mode=coverage, catalog_mode="registry"
                            )
                            pd.testing.assert_frame_equal(baseline, reg)
                            self.assertEqual(_byte_csv(baseline), _byte_csv(reg))

    def test_seasonal_counts_and_availability_match_at_threshold_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            for d, gat, gru, blastam in (
                ("20261101", 0.23, 0.5, 0.0),
                ("20261102", 0.229, 0.499, 1.0),
            ):
                for model, value in (
                    ("BlastGAT", gat), ("BlastGRU-TW", gru), ("BLASTAM", blastam)
                ):
                    pd.DataFrame([{
                        "站號": "C0G910", "站名": "花壇",
                        "lat": 24.032, "lon": 120.549, model: value,
                    }]).to_csv(Path(folder) / f"{d}_{model}.csv", index=False)
            with patch.object(season, "DATA_FOLDER", folder):
                legacy = season.accumulate_for_period(
                    2026, date(2026, 11, 1), date(2026, 11, 2), need_plan=False
                )
                registry = season.accumulate_for_period(
                    2026, date(2026, 11, 1), date(2026, 11, 2),
                    need_plan=False, catalog_mode="registry"
                )
        self.assertEqual(dict(legacy[0]), dict(registry[0]))
        pd.testing.assert_frame_equal(legacy[1], registry[1])
        self.assertEqual(legacy[2:], registry[2:])
        self.assertEqual(registry[0]["C0G910"]["BlastGAT"], 1)
        self.assertEqual(registry[0]["C0G910"]["BlastGRU-TW"], 1)
        self.assertEqual(registry[0]["C0G910"]["BLASTAM"], 1)

    def test_existing_workflows_do_not_enable_registry_mode(self):
        for path in (
            ".github/workflows/daily-forecast.yml",
            ".github/workflows/blastam-forecast.yml",
            "scripts/run_daily_pipeline.sh",
            "scripts/run_blastam_pipeline.sh",
        ):
            with self.subTest(path=path):
                source = (ROOT / path).read_text(encoding="utf-8")
                self.assertNotIn("FORECAST_MODEL_CATALOG_MODE", source)


def _append_test_model(doc):
    existing = deepcopy(next(m for m in doc["models"] if m["model_id"] == "BlastTF"))
    existing.update({
        "model_id": "UNREVIEWED_MODEL", "display_name": "unreviewed",
        "primary_field": "UNREVIEWED_MODEL", "public": False,
    })
    existing["source"]["daily_filename_suffix"] = "UNREVIEWED_MODEL"
    doc["models"].append(existing)


if __name__ == "__main__":
    unittest.main()

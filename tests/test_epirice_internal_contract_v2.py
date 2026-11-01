"""Phase 3B internal EPIRICE v2 contract tests.

Exercises public daily CSV examples pinned in Phase 3A, not newly generated
model predictions. All failure tests use in-memory/isolated data.
"""
from dataclasses import FrozenInstanceError, replace
import io
import json
import math
import unittest
from pathlib import Path

import pandas as pd

from models.EPIRICE_BLB.output_contract import normalized_v2_records as blb_to_v2
from models.EPIRICE_LB.output_contract import normalized_v2_records as lb_to_v2
from models.epirice.output_contract_v2 import (
    ContractValidationError,
    EPIRICE_SPECS,
    ForecastRecord,
    SCHEMA_VERSION,
    normalize_epirice_daily,
    validate_record,
    validate_records,
)


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/public_contract_v1/snapshot_20261101.json").read_text(
        encoding="utf-8"
    )
)


def published(model):
    return pd.read_csv(io.StringIO(FIXTURE["daily_csv"][model]), dtype={"站號": str})


def base(model="EPIRICE_BLB", *, value=0.0, status="ok"):
    primary = value if status == "ok" else (-99.0 if status == "non_crop_period" else None)
    raw = value if status == "ok" else None
    return pd.DataFrame([{
        "站號": "00123",
        "日期": "2026-11-01",
        model: primary,
        model + "_rateinf_new_infections": raw,
        model + "_status": status,
        model + "_primary_value_field": "rateinf_new_infections",
        model + "_output_schema_version": EPIRICE_SPECS[model].source_schema_version,
        model + "_model_version": "pinned-1",
        model + "_generated_at": "2026-11-01T08:00:00+08:00",
        model + "_source_commit": "test-git-rev",
        model + "_site_unit": "source-native",
    }])


class InternalContractPublishedTests(unittest.TestCase):
    def test_published_daily_blb_lb_convert_without_dataframe_mutation(self):
        for model, adapter in [("EPIRICE_BLB", blb_to_v2), ("EPIRICE_LB", lb_to_v2)]:
            with self.subTest(model=model):
                frame = published(model)
                untouched = frame.copy(deep=True)
                records = adapter(frame)
                pd.testing.assert_frame_equal(frame, untouched)
                self.assertEqual(len(records), len(FIXTURE["stations"]))
                self.assertEqual({r.station_id for r in records}, set(FIXTURE["stations"]))
                self.assertEqual({r.model_id for r in records}, {model})
                self.assertEqual({r.date for r in records}, {"2026-11-01"})
                self.assertEqual({r.schema_version for r in records}, {SCHEMA_VERSION})
                self.assertEqual({r.primary_field for r in records}, {"rateinf_new_infections"})
                self.assertEqual({r.value_type for r in records}, {"infection_flow"})
                self.assertEqual({r.unit for r in records}, {None})
                self.assertEqual({r.source_schema_version for r in records},
                                 {EPIRICE_SPECS[model].source_schema_version})
                serialized = json.dumps([record.to_dict() for record in records], allow_nan=False)
                self.assertIn('"model_id": "' + model + '"', serialized)

    def test_published_epirice_zero_non_crop_missing_are_distinct(self):
        for model in ("EPIRICE_BLB", "EPIRICE_LB"):
            with self.subTest(model=model):
                rows = {r.station_id: r for r in normalize_epirice_daily(published(model), model)}
                self.assertEqual((rows["466990"].value, rows["466990"].status), (0.0, "ok"))
                self.assertEqual((rows["466881"].value, rows["466881"].status),
                                 (None, "non_crop_period"))
                self.assertEqual((rows["467990"].value, rows["467990"].status),
                                 (None, "missing_crop_calendar"))

    def test_source_provenance_is_carried_without_invented_generation_time(self):
        one = blb_to_v2(base())[0]
        self.assertEqual(one.model_version, "pinned-1")
        self.assertEqual(one.source_generated_at, "2026-11-01T08:00:00+08:00")
        self.assertEqual(one.source_commit, "test-git-rev")
        self.assertEqual(one.source_site_unit, "source-native")
        self.assertIsNone(one.unit)  # unverified physical units are not invented
        absent = base()
        absent = absent.drop(columns=["EPIRICE_BLB_generated_at",
                                      "EPIRICE_BLB_source_commit",
                                      "EPIRICE_BLB_site_unit"])
        row = blb_to_v2(absent)[0]
        self.assertIsNone(row.source_generated_at)
        self.assertIsNone(row.source_commit)
        self.assertIsNone(row.source_site_unit)

    def test_statuses_with_no_value_remain_null(self):
        for model in ("EPIRICE_BLB", "EPIRICE_LB"):
            for status in ("missing_crop_calendar", "missing_model_output",
                           "outside_model_duration", "non_crop_period"):
                with self.subTest(model=model, status=status):
                    record = normalize_epirice_daily(base(model, status=status), model)[0]
                    self.assertIsNone(record.value)
                    self.assertEqual(record.status, status)


class InternalContractRejectionTests(unittest.TestCase):
    def assert_bad(self, frame, model="EPIRICE_BLB"):
        with self.assertRaises(ContractValidationError):
            normalize_epirice_daily(frame, model)

    def test_requires_status_and_raw_flow_not_only_primary_score(self):
        for column in ["EPIRICE_BLB_status", "EPIRICE_BLB_rateinf_new_infections",
                       "站號", "日期", "EPIRICE_BLB"]:
            with self.subTest(column=column):
                self.assert_bad(base().drop(columns=[column]))

    def test_reject_raw_primary_disagreement_and_missing_ok(self):
        f = base()
        f.loc[0, "EPIRICE_BLB"] = .8
        self.assert_bad(f)
        f = base()
        f.loc[0, "EPIRICE_BLB_rateinf_new_infections"] = None
        self.assert_bad(f)
        f = base()
        f.loc[0, "EPIRICE_BLB"] = None
        self.assert_bad(f)

    def test_reject_non_crop_sentinel_in_other_status_and_non_crop_with_raw(self):
        f = base()
        f.loc[0, "EPIRICE_BLB"] = -99.0
        self.assert_bad(f)
        f = base(status="non_crop_period")
        f.loc[0, "EPIRICE_BLB_rateinf_new_infections"] = 0.0
        self.assert_bad(f)
        f = base(status="non_crop_period")
        f.loc[0, "EPIRICE_BLB"] = 0.0
        self.assert_bad(f)

    def test_reject_missing_status_with_nonnull_legacy_value(self):
        for status in ("missing_model_output", "outside_model_duration", "missing_crop_calendar"):
            f = base(status=status)
            f.loc[0, "EPIRICE_BLB"] = 0.0
            self.assert_bad(f)

    def test_reject_unknown_status_and_wrong_schema_metadata(self):
        f = base()
        f.loc[0, "EPIRICE_BLB_status"] = "invalid_status"
        self.assert_bad(f)
        f = base()
        f.loc[0, "EPIRICE_BLB_output_schema_version"] = "not-the-legacy-version"
        self.assert_bad(f)
        f = base()
        f.loc[0, "EPIRICE_BLB_primary_value_field"] = "intensity_active"
        self.assert_bad(f)

    def test_reject_invalid_numeric_values_and_nonfinite(self):
        for value in (float("inf"), float("-inf"), -1.0, -99.0, "not-a-number", True, "nan"):
            with self.subTest(value=value):
                f = base()
                f.at[0, "EPIRICE_BLB_rateinf_new_infections"] = value
                self.assert_bad(f)
        for value in (float("inf"), float("-inf"), -1.0):
            f = base()
            f.at[0, "EPIRICE_BLB"] = value
            self.assert_bad(f)

    def test_reject_invalid_station_date_and_implicitly_numeric_station_ids(self):
        for station in ("", " ", "../abc", "C0G/910"):
            f = base()
            f.loc[0, "站號"] = station
            self.assert_bad(f)
        f = base()
        f.loc[0, "站號"] = 123
        self.assert_bad(f)
        for bad_date in ("2026-11-1", "2026/11/01", "2026-02-30", "2026-11-01T00:00:00"):
            with self.subTest(date=bad_date):
                f = base()
                f.loc[0, "日期"] = bad_date
                self.assert_bad(f)

    def test_reject_duplicate_keys_but_allow_same_key_different_models(self):
        duplicate = pd.concat([base(), base()], ignore_index=True)
        self.assert_bad(duplicate)
        blb = blb_to_v2(base())[0]
        lb = lb_to_v2(base("EPIRICE_LB"))[0]
        validate_records([blb, lb])
        with self.assertRaises(ContractValidationError):
            validate_records([blb, blb])

    def test_record_is_immutable_and_validated_independently(self):
        record = blb_to_v2(base())[0]
        with self.assertRaises(FrozenInstanceError):
            record.value = 123
        for edited in [
            replace(record, status="non_crop_period", value=0.0),
            replace(record, status="ok", value=None),
            replace(record, value=float("nan")),
            replace(record, value=-99.0),
            replace(record, schema_version="v1"),
        ]:
            with self.subTest(edited=edited):
                with self.assertRaises(ContractValidationError):
                    validate_record(edited)

    def test_unknown_model_and_duplicate_headers_are_not_silently_accepted(self):
        with self.assertRaises(ContractValidationError):
            normalize_epirice_daily(base(), "EPIRICE_SB")
        f = base()
        f.insert(2, "duplicate", 0.0)
        f.columns = list(f.columns[:-1]) + [f.columns[2]]
        self.assert_bad(f)


if __name__ == "__main__":
    unittest.main()

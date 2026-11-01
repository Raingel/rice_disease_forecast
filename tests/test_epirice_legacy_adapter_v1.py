"""Phase 3C: validated and byte-preserving EPIRICE legacy CSV adapters.

These are offline tests against published 2026-11-01 sample rows and
synthetic hostile cases. They never write to the production data directory.
"""
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest

import pandas as pd

from models.epirice.legacy_csv_adapter import (
    LegacyDailyEnvelope,
    UnsafeLegacyConversionError,
    read_legacy_daily_csv,
    serialize_complete_legacy_daily,
)
from models.epirice.output_contract_v2 import ContractValidationError, EPIRICE_SPECS


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/public_contract_v1/snapshot_20261101.json").read_text(
        encoding="utf-8"
    )
)


def complete_frame(model="EPIRICE_BLB"):
    return pd.DataFrame([
        {
            "站號": "00123",
            "站名": "花壇,中央站",
            "日期": "2026-11-01",
            "lat": 24.032,
            "lon": 120.549,
            model: 0.0,
            model + "_rateinf_new_infections": 0.0,
            model + "_status": "ok",
            model + "_primary_value_field": "rateinf_new_infections",
            model + "_output_schema_version": EPIRICE_SPECS[model].source_schema_version,
            model + "_healthy_sites": 102.3,
            model + "_latent": 1.5,
            model + "_weather_sha256": "abc123",
            model + "_generated_at": "2026-11-01T08:00:00+08:00",
        },
        {
            "站號": "C0G910",
            "站名": "花壇",
            "日期": "2026-11-01",
            "lat": 24.032,
            "lon": 120.549,
            model: -99.0,
            model + "_rateinf_new_infections": None,
            model + "_status": "non_crop_period",
            model + "_primary_value_field": "rateinf_new_infections",
            model + "_output_schema_version": EPIRICE_SPECS[model].source_schema_version,
            model + "_healthy_sites": None,
            model + "_latent": None,
            model + "_weather_sha256": None,
            model + "_generated_at": None,
        },
    ])


class LegacyDailyAdapterTests(unittest.TestCase):
    def test_published_five_station_blb_lb_files_roundtrip_identical_bytes(self):
        for model in ("EPIRICE_BLB", "EPIRICE_LB"):
            with self.subTest(model=model):
                data = FIXTURE["daily_csv"][model].encode("utf-8")
                envelope = read_legacy_daily_csv(data, model)
                self.assertIsInstance(envelope, LegacyDailyEnvelope)
                self.assertEqual(len(envelope.records), 5)
                self.assertEqual(envelope.model_id, model)
                self.assertEqual(envelope.to_v1_bytes(), data)
                self.assertEqual(envelope.to_v1_bytes(envelope.records), data)
                self.assertEqual(set(record.station_id for record in envelope.records),
                                 set(FIXTURE["stations"]))
                self.assertEqual(envelope.header, tuple(FIXTURE["daily_headers"][model]))
                self.assertTrue(all(record.value is None or record.value >= 0
                                    for record in envelope.records))

    def test_full_daily_dataframe_writer_matches_unmodified_pandas_v1_output(self):
        for model in ("EPIRICE_BLB", "EPIRICE_LB"):
            with self.subTest(model=model):
                frame = complete_frame(model)
                before = frame.copy(deep=True)
                expected = io.BytesIO()
                frame.to_csv(expected, index=False, encoding="utf-8-sig")
                result = serialize_complete_legacy_daily(frame, model)
                self.assertEqual(result, expected.getvalue())
                self.assertTrue(result.startswith(b"\xef\xbb\xbf"))
                self.assertEqual(read_legacy_daily_csv(result, model).to_v1_bytes(), result)
                pd.testing.assert_frame_equal(frame, before)
                output = pd.read_csv(io.BytesIO(result), dtype={"站號": str})
                self.assertEqual(list(output.columns), list(frame.columns))
                self.assertEqual(output["站號"].tolist(), ["00123", "C0G910"])
                self.assertEqual(output.loc[0, model + "_latent"], 1.5)
                self.assertEqual(output.loc[0, model + "_healthy_sites"], 102.3)
                self.assertEqual(output.loc[0, model + "_weather_sha256"], "abc123")

    def test_exact_original_bom_crlf_header_order_and_quoted_station_label(self):
        frame = complete_frame()
        output = io.StringIO()
        frame.to_csv(output, index=False, lineterminator="\r\n")
        original = b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
        envelope = read_legacy_daily_csv(original, "EPIRICE_BLB")
        self.assertEqual(envelope.to_v1_bytes(), original)
        self.assertIn(b"\r\n", envelope.to_v1_bytes())
        self.assertIn('"花壇,中央站"'.encode("utf-8"), original)
        self.assertEqual(envelope.records[0].station_id, "00123")

    def test_edited_v2_record_cannot_silently_overwrite_legacy_source(self):
        original = serialize_complete_legacy_daily(complete_frame(), "EPIRICE_BLB")
        envelope = read_legacy_daily_csv(original, "EPIRICE_BLB")
        changed = (replace(envelope.records[0], value=10.0), *envelope.records[1:])
        with self.assertRaisesRegex(UnsafeLegacyConversionError, "cannot be safely reconstructed"):
            envelope.to_v1_bytes(changed)
        # A frozen record alone does not contain the original full SEIR data.
        self.assertEqual(envelope.to_v1_bytes(), original)

    def test_original_source_integrity_checksum_checked_before_return(self):
        original = serialize_complete_legacy_daily(complete_frame("EPIRICE_LB"), "EPIRICE_LB")
        envelope = read_legacy_daily_csv(original, "EPIRICE_LB")
        with self.assertRaisesRegex(UnsafeLegacyConversionError, "checksum changed"):
            replace(envelope, original_csv=original + b"tamper").to_v1_bytes()

    def test_non_crop_and_valid_zero_are_not_interchanged(self):
        envelope = read_legacy_daily_csv(
            serialize_complete_legacy_daily(complete_frame(), "EPIRICE_BLB"),
            "EPIRICE_BLB",
        )
        self.assertEqual((envelope.records[0].value, envelope.records[0].status),
                         (0.0, "ok"))
        self.assertEqual((envelope.records[1].value, envelope.records[1].status),
                         (None, "non_crop_period"))
        altered = replace(envelope.records[1], value=0.0, status="ok")
        with self.assertRaises(UnsafeLegacyConversionError):
            envelope.to_v1_bytes((envelope.records[0], altered))

    def test_reject_broken_encoding_headers_or_csv_quoting(self):
        data = serialize_complete_legacy_daily(complete_frame(), "EPIRICE_BLB")
        for raw in [
            b"",
            b"\xff\xfe",
            data + b"\x00",
            b"station,date\nfoo\n",
            b'"unclosed,date\nfoo',
            b"\xef\xbb\xbf" + b"station,station\nfoo,bar\n",
            data + b"unexpected,extra,row,fields\n",
        ]:
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(ContractValidationError):
                    read_legacy_daily_csv(raw, "EPIRICE_BLB")

    def test_reject_ambiguous_or_incomplete_source_data(self):
        f = complete_frame()
        f.loc[0, "EPIRICE_BLB_rateinf_new_infections"] = None
        with self.assertRaises(ContractValidationError):
            serialize_complete_legacy_daily(f, "EPIRICE_BLB")
        f = complete_frame().drop(columns=["EPIRICE_BLB_status"])
        with self.assertRaises(ContractValidationError):
            serialize_complete_legacy_daily(f, "EPIRICE_BLB")
        f = complete_frame()
        f.loc[0, "站號"] = "C0G910"  # duplicate day + station
        with self.assertRaises(ContractValidationError):
            serialize_complete_legacy_daily(f, "EPIRICE_BLB")

    def test_reject_unknown_model_and_nonframe_writer_input(self):
        with self.assertRaises(ContractValidationError):
            read_legacy_daily_csv(b"station,date\nfoo,bar\n", "EPIRICE_SB")
        with self.assertRaises(TypeError):
            serialize_complete_legacy_daily(["not a complete model frame"], "EPIRICE_BLB")

    def test_full_csv_fields_stay_outside_minimal_v2_record(self):
        original = serialize_complete_legacy_daily(complete_frame(), "EPIRICE_LB")
        envelope = read_legacy_daily_csv(original, "EPIRICE_LB")
        flattened = envelope.records[0].to_dict()
        self.assertNotIn("EPIRICE_LB_latent", flattened)
        self.assertNotIn("EPIRICE_LB_healthy_sites", flattened)
        self.assertNotIn("EPIRICE_LB_weather_sha256", flattened)
        self.assertIn(b"EPIRICE_LB_latent", envelope.to_v1_bytes())


if __name__ == "__main__":
    unittest.main()

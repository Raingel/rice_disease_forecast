"""Regression checks for shared EPIRICE SEIR engine and compatibility APIs."""
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from models.epirice.engine import simulate_epirice
from models.EPIRICE_BLB.model import simulate_epirice_blb
from models.EPIRICE_LB.model import simulate_epirice_lb

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "epirice_blb"


class SharedEpiriceEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.weather = pd.read_csv(FIXTURES / "example_input.csv")
        cls.expected_blb = pd.read_csv(FIXTURES / "example_output.csv")
        cls.blb_config = json.loads((ROOT / "models/EPIRICE_BLB/config.json").read_text(encoding="utf-8"))
        cls.lb_config = json.loads((ROOT / "models/EPIRICE_LB/config.json").read_text(encoding="utf-8"))

    def test_backward_compatible_blb_function_is_same_kernel(self):
        self.assertIs(simulate_epirice_blb, simulate_epirice)

    def test_shared_engine_matches_pinned_blb_reference_all_numeric_fields(self):
        actual = simulate_epirice(self.weather, self.blb_config)
        self.assertEqual(actual["date"].tolist(), self.expected_blb["date"].tolist())
        for col in self.expected_blb.select_dtypes(include="number").columns:
            np.testing.assert_allclose(
                actual[col].to_numpy(), self.expected_blb[col].to_numpy(),
                rtol=0, atol=1e-6, err_msg=col,
            )

    def test_leaf_blast_adapter_preserves_shared_states_and_provenance(self):
        actual = simulate_epirice_lb(self.weather, self.lb_config)
        raw = simulate_epirice(self.weather, self.lb_config)
        pd.testing.assert_frame_equal(actual[raw.columns], raw, check_exact=True)
        self.assertTrue((actual["RcOpt"] == self.lb_config["RcOpt"]).all())
        self.assertEqual(actual["DOY"].tolist(), pd.to_datetime(self.weather["YYYYMMDD"]).dt.dayofyear.tolist())

    def test_parameters_are_disease_specific(self):
        self.assertNotEqual(self.blb_config["H0"], self.lb_config["H0"])
        self.assertNotEqual(self.blb_config["aggregation_a"], self.lb_config["aggregation_a"])


if __name__ == "__main__":
    unittest.main()

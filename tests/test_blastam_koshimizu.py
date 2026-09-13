import unittest

import numpy as np

from run_blastam import koshimizu_model


def base_arrays(temp=22.0):
    return (
        np.full(120, temp, dtype=float),
        np.zeros(120, dtype=float),
        np.zeros(120, dtype=float),
        np.zeros(120, dtype=float),
    )


class KoshimizuModelTest(unittest.TestCase):
    def test_early_morning_wind_rule_does_not_apply_all_night(self):
        temp, wind, rain, sun = base_arrays()
        rain[89] = 0.5
        wind[92] = 3.0

        leaf_wet, _ = koshimizu_model(temp, wind, rain, sun)

        self.assertIs(leaf_wet[20], True)

    def test_exact_four_mm_rain_is_invalidating(self):
        temp, wind, rain, sun = base_arrays()
        rain[89] = 0.5
        rain[95] = 4.0

        leaf_wet, _ = koshimizu_model(temp, wind, rain, sun)

        self.assertEqual(leaf_wet[23], -2)

    def test_two_consecutive_three_mm_rain_hours_are_invalidating(self):
        temp, wind, rain, sun = base_arrays()
        rain[89] = 0.5
        rain[96] = 3.0
        rain[97] = 3.0

        leaf_wet, _ = koshimizu_model(temp, wind, rain, sun)

        self.assertEqual(leaf_wet[0], -2)

    def test_twenty_one_degrees_requires_eleven_wet_hours(self):
        temp, wind, rain, sun = base_arrays(temp=21.0)
        rain[89] = 0.5
        sun[98] = 0.3

        _, result = koshimizu_model(temp, wind, rain, sun)

        self.assertEqual(result["wet_period_hrs"], 10)
        self.assertEqual(result["blast_score"], 0.4)

    def test_threshold_wet_hours_are_suitable_conditions(self):
        temp, wind, rain, sun = base_arrays(temp=21.0)
        rain[89] = 0.5
        sun[99] = 0.3

        _, result = koshimizu_model(temp, wind, rain, sun)

        self.assertEqual(result["wet_period_hrs"], 11)
        self.assertEqual(result["blast_score"], 1.0)

    def test_rain_with_exact_three_mps_wind_is_treated_as_two_mps(self):
        temp, wind, rain, sun = base_arrays()
        rain[89] = 0.5
        wind[97:100] = 3.0
        rain[98] = 0.5

        leaf_wet, _ = koshimizu_model(temp, wind, rain, sun)

        self.assertIs(leaf_wet[2], True)


if __name__ == "__main__":
    unittest.main()

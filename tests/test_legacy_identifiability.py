import unittest

import numpy as np

from chem_suite.modules.eis.legacy.eis_identifiability import bias_aware_status, characteristic_support


class IdentifiabilityTests(unittest.TestCase):
    def test_characteristic_support_detects_cpe_outside_window(self):
        frequencies = np.logspace(5, -2, 61)
        alpha, resistance = 0.82, 20.0
        q = 1.0 / (resistance * (2.0 * np.pi * 0.001) ** alpha)
        support = characteristic_support(
            frequencies, "R0-p(R1,CPE0)", [5.0, resistance, q, alpha]
        )
        self.assertFalse(support["CPE0_0"]["supported"])
        self.assertFalse(support["R1"]["supported"])

    def test_characteristic_support_detects_measured_wo(self):
        support = characteristic_support(
            np.logspace(5, -2, 61), "L0-R0-p(R1,CPE0)-Wo0",
            [2e-6, 5.0, 20.0, 2e-4, 0.84, 12.0, 0.16],
        )
        self.assertTrue(support["Wo0_0"]["supported"])
        self.assertTrue(support["Wo0_1"]["supported"])

    def test_bias_aware_status_downgrades_unsupported_narrow_interval(self):
        self.assertEqual(
            bias_aware_status(
                "identified", window_stable=True, characteristic_supported=False
            ),
            "weak",
        )
        self.assertEqual(
            bias_aware_status(
                "identified", window_stable=True, characteristic_supported=True
            ),
            "identified",
        )


if __name__ == "__main__":
    unittest.main()

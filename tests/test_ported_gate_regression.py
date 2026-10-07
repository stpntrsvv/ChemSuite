import unittest

from chem_suite.modules.eis.legacy.eis_gate_regression import DIFFUSION_GATE_CIRCUITS


class GateRegressionTests(unittest.TestCase):
    def test_regression_candidates_include_competing_families(self):
        self.assertEqual(DIFFUSION_GATE_CIRCUITS[0], "L0-R0-p(R1,CPE0)")
        self.assertEqual(len(DIFFUSION_GATE_CIRCUITS), 4)
        self.assertTrue(all("L0-" in circuit for circuit in DIFFUSION_GATE_CIRCUITS))


if __name__ == "__main__":
    unittest.main()

import unittest

import numpy as np

from chem_suite.modules.eis.legacy.eis_drt import drt_peak_stability, fit_drt, select_regularization


class DrtTests(unittest.TestCase):
    def test_recovers_single_rc_relaxation(self):
        frequencies = np.logspace(4, -2, 61)
        tau = 0.1
        z = 2.0 + 5.0 / (1.0 + 1j * 2 * np.pi * frequencies * tau)
        result = fit_drt(frequencies, z, tau_points=101, regularization=0.01)
        self.assertTrue(result["success"])
        self.assertTrue(result["peaks"])
        strongest = max(result["peaks"], key=lambda item: item["gamma_ohm"])
        self.assertLess(abs(np.log10(strongest["tau_seconds"] / tau)), 0.25)

    def test_rejects_empty_regularization_grid(self):
        with self.assertRaisesRegex(ValueError, "must be non-negative"):
            select_regularization(np.array([1.0, 2.0]), np.array([1-1j, 2-1j]), [])

    def test_stability_handles_no_reference_peaks(self):
        result = drt_peak_stability(np.array([1.0]), np.array([1-1j]), {"peaks": []}, [0.1])
        self.assertEqual(result["conditions"], [])

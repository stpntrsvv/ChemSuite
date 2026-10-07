import unittest
from types import SimpleNamespace

import numpy as np

from chem_suite.modules.eis.legacy.eis_rational import (
    PassiveRationalModel,
    RationalFitMetrics,
    fit_from_ecm_result,
    fit_passive_rational,
)
from tests.support_synthetic import simulate_spectrum


class PassiveRationalTests(unittest.TestCase):
    def test_model_is_stable_passive_and_matches_its_formula(self):
        model = PassiveRationalModel(
            relaxation_rates=np.array([10.0, 1_000.0]),
            residues=np.array([200.0, 3_000.0]),
            direct=2.5,
            derivative=4e-6,
            frequency_min_hz=0.1,
            frequency_max_hz=10_000.0,
            metrics=RationalFitMetrics(0.0, 0.0, 0.0),
            source_circuit="synthetic",
        )
        frequencies = np.logspace(-1, 4, 30)
        s = 1j * 2.0 * np.pi * frequencies
        expected = (
            2.5
            + 4e-6 * s
            + 200.0 / (s + 10.0)
            + 3_000.0 / (s + 1_000.0)
        )

        np.testing.assert_allclose(model.evaluate(frequencies), expected)
        self.assertTrue(model.stable)
        self.assertTrue(model.passive)
        self.assertTrue(np.all(model.poles < 0))

    def test_foster_mapping_preserves_each_relaxation_term(self):
        model = PassiveRationalModel(
            relaxation_rates=np.array([20.0]),
            residues=np.array([100.0]),
            direct=1.0,
            derivative=0.0,
            frequency_min_hz=0.1,
            frequency_max_hz=100.0,
            metrics=RationalFitMetrics(0.0, 0.0, 0.0),
        )

        section = model.foster_sections()[0]

        self.assertAlmostEqual(section.resistance, 5.0)
        self.assertAlmostEqual(section.capacitance, 0.01)
        self.assertAlmostEqual(
            section.resistance * section.capacitance,
            1.0 / section.relaxation_rate,
        )

    def test_fit_approximates_a_cpe_ecm_with_nonnegative_coefficients(self):
        frequencies = np.logspace(-2, 5, 140)
        _, target = simulate_spectrum(
            "R0-p(R1,CPE0)",
            [4.0, 35.0, 2.5e-4, 0.82],
            frequencies,
        )

        model = fit_passive_rational(
            frequencies,
            target,
            order=24,
            source_circuit="R0-p(R1,CPE0)",
        )

        self.assertTrue(model.stable)
        self.assertTrue(model.passive)
        self.assertLess(model.metrics.mean_relative_error_percent, 1.0)
        self.assertLess(model.metrics.max_relative_error_percent, 5.0)

    def test_invalid_frequency_band_is_rejected(self):
        with self.assertRaises(ValueError):
            fit_passive_rational([1.0, 0.0, 10.0, 100.0], np.ones(4, dtype=complex))

    def test_subnormal_residue_is_not_exported_as_infinite_capacitance(self):
        model = PassiveRationalModel(
            relaxation_rates=np.array([10.0]),
            residues=np.array([np.nextafter(0.0, 1.0)]),
            direct=1.0,
            derivative=0.0,
            frequency_min_hz=0.1,
            frequency_max_hz=100.0,
            metrics=RationalFitMetrics(0.0, 0.0, 0.0),
        )

        self.assertEqual(model.foster_sections(), ())

    def test_negligible_finite_section_is_pruned_relative_to_declared_band(self):
        model = PassiveRationalModel(
            relaxation_rates=np.array([10.0, 100.0]),
            residues=np.array([100.0, 1e-20]),
            direct=1.0,
            derivative=0.0,
            frequency_min_hz=0.1,
            frequency_max_hz=100.0,
            metrics=RationalFitMetrics(0.0, 0.0, 0.0),
        )

        sections = model.foster_sections()

        self.assertEqual(len(sections), 1)
        self.assertAlmostEqual(sections[0].residue, 100.0)

    def test_engineering_bridge_uses_ecm_prediction_and_rejects_bad_fit(self):
        frequencies = np.logspace(-2, 4, 80)
        _, target = simulate_spectrum(
            "R0-p(R1,CPE0)",
            [4.0, 35.0, 2.5e-4, 0.82],
            frequencies,
        )

        class Predictor:
            def predict(self, requested):
                np.testing.assert_allclose(requested, frequencies)
                return target

        accepted = SimpleNamespace(
            success=True,
            status="WARN",
            circuit_string="R0-p(R1,CPE0)",
            model=Predictor(),
        )
        rejected = SimpleNamespace(
            success=True,
            status="BAD",
            circuit_string="R0-p(R1,CPE0)",
            model=Predictor(),
        )

        engineering_model = fit_from_ecm_result(accepted, frequencies, order=20)

        self.assertEqual(engineering_model.source_circuit, accepted.circuit_string)
        with self.assertRaises(ValueError):
            fit_from_ecm_result(rejected, frequencies)


if __name__ == "__main__":
    unittest.main()

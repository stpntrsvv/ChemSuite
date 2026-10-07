import unittest
from unittest.mock import Mock, patch

import numpy as np

from chem_suite.modules.eis.legacy.eis_core import FitResult, circuit_family, family_bic_evidence
from chem_suite.modules.eis.legacy.eis_uncertainty import parametric_bootstrap, profile_likelihood, topology_bootstrap


class UncertaintyTests(unittest.TestCase):
    @patch("chem_suite.modules.eis.legacy.eis_uncertainty.fit_circuit")
    def test_parametric_bootstrap_reports_explicit_noise_model(self, fit_circuit_mock):
        model = Mock()
        model.parameters_ = np.array([2.0])
        model.predict.return_value = np.array([2.0 + 0.0j, 2.0 + 0.0j])
        fit_circuit_mock.return_value = FitResult(
            "R0", True, model=model, status="OK"
        )
        result = parametric_bootstrap(
            np.array([1.0, 2.0]), np.array([2.0, 2.0]), "R0",
            noise_fraction=0.01, samples=3, seed=4,
        )
        self.assertEqual(result["method"], "relative_complex_parametric_bootstrap")
        self.assertEqual(result["accepted"], 3)
        self.assertEqual(result["noise_fraction"], 0.01)

    def test_profile_grid_contains_fitted_center_and_interpolates_interval(self):
        frequencies = np.logspace(3, -1, 20)
        parameters = [5.0, 20.0, 1e-3, 0.8]
        from impedance.models.circuits import CustomCircuit
        z = CustomCircuit(
            "R0-p(R1,CPE0)", initial_guess=parameters
        ).predict(frequencies, use_initial=True)
        result = profile_likelihood(
            frequencies, z, "R0-p(R1,CPE0)", "CPE0_1",
            grid_points=11, span_decades=0.2, restarts=1,
        )
        grid = [point["value"] for point in result["points"]]
        self.assertTrue(any(abs(value - result["base"]) < 1e-12 for value in grid))
        self.assertLessEqual(result["ci95_low"], result["base"])
        self.assertGreaterEqual(result["ci95_high"], result["base"])

    def test_profile_rejects_unknown_parameter_before_fitting(self):
        with self.assertRaisesRegex(ValueError, "Unknown parameter"):
            profile_likelihood(np.array([1.0]), np.array([1.0 - 1.0j]), "R0-p(R1,CPE0)", "NOPE")

    def test_topology_bootstrap_requires_competition(self):
        with self.assertRaisesRegex(ValueError, "at least two"):
            topology_bootstrap(np.array([1.0]), np.array([1.0 - 1.0j]), ["R0-p(R1,CPE0)"])

    def test_explicit_family_mapping_and_bic_window(self):
        w = "L0-R0-p(R1,CPE0)-W0"
        wo = "L0-R0-p(R1,CPE0)-Wo0"
        self.assertEqual(circuit_family(w), "inductive_diffusion")
        evidence = family_bic_evidence([
            FitResult(w, True, bic=10.0, status="OK"),
            FitResult(wo, True, bic=11.5, status="OK"),
            FitResult("R0-p(R1,CPE0)", True, bic=20.0, status="OK"),
        ])
        self.assertEqual(evidence["supported_topologies"], [w, wo])
        self.assertEqual(evidence["supported_families"], ["inductive_diffusion"])

    @patch("chem_suite.modules.eis.legacy.eis_uncertainty.fit_circuits")
    def test_same_family_bootstrap_is_not_family_evidence(self, fit_circuits_mock):
        model = Mock()
        model.predict.return_value = np.array([1.0 - 1.0j, 2.0 - 0.5j])
        winner = FitResult(
            "L0-R0-p(R1,CPE0)-W0", True, model=model, bic=0.0, status="OK"
        )
        alternative = FitResult(
            "L0-R0-p(R1,CPE0)-Wo0", True, model=model, bic=1.0, status="OK"
        )
        fit_circuits_mock.return_value = [winner, alternative]
        result = topology_bootstrap(
            np.array([1.0, 2.0]),
            np.array([1.0 - 1.0j, 2.0 - 0.5j]),
            [winner.circuit_string, alternative.circuit_string],
            samples=2,
        )
        self.assertFalse(result["family_competition"])
        self.assertFalse(result["stable_family_recommendation"])
        self.assertIn("conditional", result["family_reason"])
        self.assertEqual([item["family"] for item in result["family_ranking"]],
                         ["inductive_diffusion"])

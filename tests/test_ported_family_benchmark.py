import unittest

from chem_suite.modules.eis.legacy.eis_family_benchmark import (
    calibrated_diffusion_gate,
    summarize_calibrated_gate,
    summarize_rows,
)


class FamilyBenchmarkTests(unittest.TestCase):
    def test_calibrated_gate_recommends_only_positive_family(self):
        supported = calibrated_diffusion_gate({
            "winner_family": "inductive_diffusion",
            "family_fraction": 0.95,
            "diffusion_family_delta_bic": 20.0,
        })
        self.assertEqual(supported["recommended_family"], "inductive_diffusion")
        self.assertIsNone(supported["recommended_topology"])
        refused = calibrated_diffusion_gate({
            "winner_family": "inductive",
            "family_fraction": 1.0,
            "diffusion_family_delta_bic": -20.0,
        })
        self.assertIsNone(refused["recommended_family"])

    def test_summary_counts_false_confident_recommendations(self):
        rows = [
            {
                "success": True,
                "truth_circuit": "A",
                "truth_family": "inductive",
                "best_statistical": "A",
                "truth_in_bic_window": True,
                "winner": "A",
                "winner_family": "inductive",
                "topology_fraction": 0.95,
                "family_fraction": 1.0,
            },
            {
                "success": True,
                "truth_circuit": "B",
                "truth_family": "inductive_diffusion",
                "best_statistical": "A",
                "truth_in_bic_window": False,
                "winner": "A",
                "winner_family": "inductive",
                "topology_fraction": 0.95,
                "family_fraction": 0.95,
            },
        ]
        summary = summarize_rows(rows)
        at_90 = next(item for item in summary["thresholds"] if item["threshold"] == 0.90)
        self.assertEqual(at_90["false_topology_recommendations"], 1)
        self.assertEqual(at_90["false_family_recommendations"], 1)
        self.assertEqual(at_90["false_positive_diffusion_recommendations"], 0)
        self.assertEqual(at_90["false_negative_diffusion_recommendations"], 1)
        self.assertEqual(summary["truth_in_bic_window"], 1)

    def test_calibrated_gate_summary_keeps_zero_event_bound_honest(self):
        rows = [
            {
                "success": True,
                "truth_family": "inductive_diffusion",
                "winner_family": "inductive_diffusion",
                "family_fraction": 0.95,
                "diffusion_family_delta_bic": 20.0,
            },
            {
                "success": True,
                "truth_family": "inductive",
                "winner_family": "inductive",
                "family_fraction": 1.0,
                "diffusion_family_delta_bic": -20.0,
            },
        ]
        summary = summarize_calibrated_gate(rows)
        self.assertEqual(summary["recommendations"], 1)
        self.assertEqual(summary["correct_recommendations"], 1)
        self.assertEqual(summary["false_positive_recommendations"], 0)
        self.assertAlmostEqual(summary["positive_recall"], 1.0)
        self.assertAlmostEqual(
            summary["zero_event_false_positive_rate_upper_95"], 0.95
        )

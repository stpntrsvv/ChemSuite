import unittest

from chem_suite.modules.eis.legacy.eis_gui_decision import format_reliable_decision


class GuiDecisionTests(unittest.TestCase):
    def test_supported_family_keeps_topology_indistinguishable(self):
        view = format_reliable_decision({
            "verdict": "models_indistinguishable",
            "best_statistical": "L0-R0-p(R1,CPE0)-Wo0",
            "recommended_family": "inductive_diffusion",
            "recommended_topology": None,
            "data_validity": "PASS",
            "reason": "family supported",
            "diffusion_gate": {
                "evaluated": True,
                "passed": True,
                "positive_only": True,
                "diffusion_family_delta_bic": 42.0,
                "family_stability_threshold": 0.9,
                "family_delta_bic_threshold": 10.0,
            },
        })
        self.assertEqual(view["status"], "supported")
        self.assertIn("diffusion-family", view["headline"])
        self.assertIn("Recommended topology: indistinguishable", view["details"])
        self.assertIn("positive-only: True", view["details"])

    def test_refusal_shows_reason_and_next_action(self):
        view = format_reliable_decision({
            "verdict": "insufficient_information",
            "data_validity": "FAIL",
            "reason": "Kramers-Kronig validation failed",
            "next_action": "repeat measurement",
        }, language="ru")
        self.assertEqual(view["status"], "refused")
        self.assertIn("не выдана", view["headline"])
        self.assertIn("Kramers-Kronig validation failed", view["details"])
        self.assertIn("repeat measurement", view["details"])

    def test_legacy_or_missing_decision_is_safe(self):
        missing = format_reliable_decision(None)
        self.assertEqual(missing["status"], "not_loaded")
        completed_fit = format_reliable_decision(None, language="ru", fit_completed=True)
        self.assertEqual(completed_fit["status"], "pending")
        self.assertIn("подбор завершён", completed_fit["headline"])
        self.assertIn("не означает", completed_fit["details"])
        legacy = format_reliable_decision({
            "verdict": "recommended",
            "recommended_topology": "R0-p(R1,CPE0)",
        })
        self.assertEqual(legacy["status"], "supported")
        self.assertIn("R0-p(R1,CPE0)", legacy["details"])


if __name__ == "__main__":
    unittest.main()

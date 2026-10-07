import unittest

from chem_suite.modules.eis.legacy.eis_inference import build_inference_decision


def fast(best_status="WARN", kk_status="PASS"):
    return {
        "success": True,
        "best": {"circuit": "A", "status": best_status},
        "kk": {"status": kk_status},
        "model_evidence": {"supported_topologies": ["A", "B"]},
    }


class InferenceDecisionTests(unittest.TestCase):
    def test_recommends_only_stable_topology(self):
        decision = build_inference_decision(
            fast_result=fast(),
            topology={"stable_recommendation": True, "recommendation": "A"},
        )
        self.assertEqual(decision["verdict"], "recommended")
        self.assertEqual(decision["recommended_reliable"], "A")

    def test_drt_can_veto_bootstrap_stable_topology(self):
        decision = build_inference_decision(
            fast_result=fast(),
            topology={"stable_recommendation": True, "recommendation": "A"},
            drt={"stability": {"reference_peaks": [
                {"stable_at_90_percent": False, "reference_frequency_hz": 1.0,
                 "worst_condition_match_fraction": 0.2}
            ]}},
        )
        self.assertEqual(decision["verdict"], "models_indistinguishable")
        self.assertIsNone(decision["recommended_reliable"])

    def test_localized_gap_overrides_unstable_topology(self):
        decision = build_inference_decision(
            fast_result=fast(),
            topology={"stable_recommendation": False, "reason": "unstable"},
            resolution={"measurement_recommendation": {
                "insufficient_data": True,
                "insufficiency_type": "repeatability",
                "message": "repeat 0.01-0.1 Hz",
            }},
        )
        self.assertEqual(decision["verdict"], "insufficient_information")
        self.assertIsNone(decision["recommended_reliable"])
        self.assertEqual(decision["next_action"], "repeat 0.01-0.1 Hz")

    def test_kk_failure_refuses_recommendation(self):
        decision = build_inference_decision(fast_result=fast(kk_status="FAIL"))
        self.assertEqual(decision["verdict"], "insufficient_information")

    def test_fast_only_result_requests_reliable_mode(self):
        decision = build_inference_decision(fast_result=fast())
        self.assertEqual(decision["verdict"], "insufficient_information")
        self.assertEqual(decision["next_action"], "run reliable mode")

    def test_stable_family_can_be_recommended_without_exact_topology(self):
        decision = build_inference_decision(
            fast_result=fast(),
            topology={
                "stable_recommendation": False,
                "stable_family_recommendation": True,
                "family_recommendation": "inductive_diffusion",
            },
        )
        self.assertEqual(decision["recommended_family"], "inductive_diffusion")
        self.assertIsNone(decision["recommended_topology"])
        self.assertEqual(decision["family_status"], "supported")
        self.assertEqual(decision["topology_status"], "models_indistinguishable")

    def test_unstable_family_refuses_both_recommendations(self):
        decision = build_inference_decision(
            fast_result=fast(),
            topology={"stable_recommendation": False, "stable_family_recommendation": False},
        )
        self.assertIsNone(decision["recommended_family"])
        self.assertIsNone(decision["recommended_topology"])
        self.assertEqual(decision["supported_topologies"], ["A", "B"])

    def test_calibrated_diffusion_gate_requires_bic_margin_and_hides_topology(self):
        payload = fast()
        payload["model_evidence"]["diffusion_family_delta_bic"] = 12.0
        topology = {
            "stable_recommendation": True,
            "recommendation": "L0-R0-p(R1,CPE0)-W0",
            "stable_family_recommendation": True,
            "family_recommendation": "inductive_diffusion",
            "candidate_families": ["inductive", "inductive_diffusion"],
        }
        decision = build_inference_decision(fast_result=payload, topology=topology)
        self.assertEqual(decision["recommended_family"], "inductive_diffusion")
        self.assertIsNone(decision["recommended_topology"])
        self.assertTrue(decision["diffusion_gate"]["passed"])

        payload["model_evidence"]["diffusion_family_delta_bic"] = 9.9
        refused = build_inference_decision(fast_result=payload, topology=topology)
        self.assertIsNone(refused["recommended_family"])
        self.assertIsNone(refused["recommended_topology"])
        self.assertFalse(refused["diffusion_gate"]["passed"])

    def test_calibrated_diffusion_gate_does_not_prove_absence(self):
        payload = fast()
        payload["model_evidence"]["diffusion_family_delta_bic"] = -30.0
        decision = build_inference_decision(
            fast_result=payload,
            topology={
                "stable_recommendation": True,
                "recommendation": "L0-R0-p(R1,CPE0)",
                "stable_family_recommendation": True,
                "family_recommendation": "inductive",
                "candidate_families": ["inductive", "inductive_diffusion"],
            },
        )
        self.assertIsNone(decision["recommended_family"])
        self.assertIsNone(decision["recommended_topology"])
        self.assertTrue(decision["diffusion_gate"]["positive_only"])

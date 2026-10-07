import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chem_suite.modules.eis.legacy.eis_inference_batch import inference_summary, run_batch


class InferenceBatchTests(unittest.TestCase):
    def test_flattens_decision_contract(self):
        summary = inference_summary({
            "file": "x.csv",
            "decision": {
                "verdict": "insufficient_information",
                "information_gap": {"insufficiency_type": "repeatability", "problem_region_hz": [0.01, 0.1]},
                "resolved_time_regions": [{"stable": True}, {"stable": False}],
                "diffusion_gate": {
                    "evaluated": True,
                    "passed": True,
                    "positive_only": True,
                    "diffusion_family_delta_bic": 42.5,
                    "family_stability_threshold": 0.9,
                    "family_delta_bic_threshold": 10.0,
                },
            },
        })
        self.assertEqual(summary["information_gap_min_hz"], 0.01)
        self.assertEqual(summary["stable_time_regions"], 1)
        self.assertEqual(summary["unstable_time_regions"], 1)
        self.assertTrue(summary["diffusion_gate_evaluated"])
        self.assertTrue(summary["diffusion_gate_passed"])
        self.assertTrue(summary["diffusion_gate_positive_only"])
        self.assertEqual(summary["diffusion_family_delta_bic"], 42.5)
        self.assertEqual(summary["diffusion_family_stability_threshold"], 0.9)
        self.assertEqual(summary["diffusion_family_delta_bic_threshold"], 10.0)

    def test_old_decision_without_gate_remains_serializable(self):
        summary = inference_summary({
            "file": "legacy.csv",
            "decision": {
                "verdict": "models_indistinguishable",
                "best_statistical": "R0-p(R1,CPE0)",
            },
        })
        self.assertIn("best_statistical", summary)
        self.assertIsNone(summary["diffusion_gate_evaluated"])
        self.assertIsNone(summary["diffusion_family_delta_bic"])

    def test_schema_declares_flattened_gate_fields_as_nullable(self):
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "src" / "chem_suite" / "modules" / "eis" / "legacy" / "schemas"
            / "inference-summary-v1.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        properties = schema["properties"]
        for name in (
            "diffusion_gate_evaluated",
            "diffusion_gate_passed",
            "diffusion_gate_positive_only",
            "diffusion_family_delta_bic",
            "diffusion_family_stability_threshold",
            "diffusion_family_delta_bic_threshold",
        ):
            self.assertIn(name, properties)
            self.assertIn("null", properties[name]["type"])

    @patch("chem_suite.modules.eis.legacy.eis_inference_batch.run_inference")
    def test_jsonl_stream_continues_after_failure(self, mocked):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "a.csv", root / "b.csv"
            first.write_text("x", encoding="utf-8")
            second.write_text("x", encoding="utf-8")
            mocked.side_effect = [
                RuntimeError("broken"),
                {"file": str(second), "decision": {"verdict": "models_indistinguishable"}},
            ]
            output = root / "results.jsonl"
            result = run_batch([str(first), str(second)], output, quiet=True)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(result["written"], 2)
            self.assertEqual(rows[0]["verdict"], "analysis_failed")
            self.assertEqual(rows[1]["verdict"], "models_indistinguishable")

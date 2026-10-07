import json
from pathlib import Path
import tempfile
import unittest

from chem_suite.modules.eis.legacy.eis_spice_benchmark import (
    OrderEvaluation,
    ScenarioBenchmarkResult,
    load_frozen_manifest,
    select_minimal_order,
    summarize_results,
)


def evaluation(order, *, passed):
    return OrderEvaluation(
        order=order,
        stable=True,
        passive=True,
        ecm_mean_error_percent=0.1,
        ecm_rms_error_percent=0.2,
        ecm_max_error_percent=0.5,
        realization_max_error_percent=0.01,
        realization_strategy="legacy_section_cutoff",
        original_sections=order,
        pruned_sections=0,
        sections=order,
        resistance_min_ohm=1.0,
        resistance_max_ohm=10.0,
        resistance_span_decades=1.0,
        capacitance_min_f=1e-6,
        capacitance_max_f=1e-3,
        capacitance_span_decades=3.0,
        direct_resistance_ohm=1.0,
        series_inductance_h=0.0,
        internal_passed=True,
        external_status="validated" if passed else "mismatch",
        external_points=100,
        external_max_error_percent=1e-8 if passed else 1e-3,
        passed=passed,
        refusal_reasons=() if passed else ("ngspice_mismatch",),
    )


class SpiceBenchmarkTests(unittest.TestCase):
    def test_selects_smallest_passing_order_not_first_list_item(self):
        selected = select_minimal_order(
            [evaluation(24, passed=True), evaluation(8, passed=False), evaluation(16, passed=True)]
        )
        self.assertEqual(selected, 16)

    def test_summary_separates_holdout_and_real_ecm(self):
        rows = [
            ScenarioBenchmarkResult(
                "cal", "calibration", "synthetic", "R0", 8, "passed", None,
                (evaluation(8, passed=True),), {},
            ),
            ScenarioBenchmarkResult(
                "real", "holdout", "real_ecm", "R0", None, "refused", "x",
                (evaluation(8, passed=False),), {},
            ),
        ]
        summary = summarize_results(rows)
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["by_split"]["holdout"]["refused"], 1)
        self.assertEqual(summary["by_source"]["real_ecm"]["total"], 1)
        self.assertEqual(summary["selected_order_counts"], {"8": 1, "none": 1})

    def test_manifest_rejects_duplicate_scenario_ids(self):
        payload = {
            "schema_version": 1,
            "name": "duplicate",
            "orders": [8, 16],
            "pole_margin_decades": 1.0,
            "spice_section_relative_tolerance": 0.002,
            "criteria": {
                "ecm_mean_error_percent": 1.0,
                "ecm_max_error_percent": 10.0,
                "realization_max_error_percent": 0.25,
                "ngspice_max_error_percent": 1e-6,
                "max_sections": 32,
                "max_resistance_span_decades": 12.0,
                "max_capacitance_span_decades": 12.0,
            },
            "scenarios": [
                {
                    "id": "same",
                    "split": "calibration",
                    "source_kind": "synthetic",
                    "circuit": "R0-p(R1,C1)",
                    "parameters": [1, 2, 0.1],
                    "frequency_min_hz": 0.1,
                    "frequency_max_hz": 10,
                    "points": 10,
                },
                {
                    "id": "same",
                    "split": "holdout",
                    "source_kind": "synthetic",
                    "circuit": "R0-p(R1,C1)",
                    "parameters": [1, 2, 0.1],
                    "frequency_min_hz": 0.1,
                    "frequency_max_hz": 10,
                    "points": 10,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_frozen_manifest(path)

    def test_repository_manifest_is_balanced_and_frozen(self):
        path = Path(__file__).parents[1] / "src" / "chem_suite" / "modules" / "eis" / "legacy" / "manifests" / "spice_engineering_corpus_v1.json"
        payload, criteria, scenarios = load_frozen_manifest(path)
        calibration = [item for item in scenarios if item.split == "calibration"]
        holdout = [item for item in scenarios if item.split == "holdout"]
        real = [item for item in scenarios if item.source_kind == "real_ecm"]

        self.assertEqual(payload["orders"], [8, 12, 16, 24, 32])
        self.assertEqual(len(calibration), 6)
        self.assertEqual(len(holdout), 12)
        self.assertEqual(len(real), 6)
        self.assertAlmostEqual(payload["spice_section_relative_tolerance"], 0.002)
        self.assertEqual(criteria.realization_max_error_percent, 0.25)

    def test_conditioning_manifest_uses_new_cases_and_global_budget(self):
        path = (
            Path(__file__).parents[1]
            / "src" / "chem_suite" / "modules" / "eis" / "legacy" / "manifests"
            / "spice_conditioning_corpus_v2.json"
        )
        payload, criteria, scenarios = load_frozen_manifest(path)
        calibration = [item for item in scenarios if item.split == "calibration"]
        holdout = [item for item in scenarios if item.split == "holdout"]
        real = [item for item in scenarios if item.source_kind == "real_ecm"]

        self.assertEqual(payload["realization_strategy"], "global_error_budget")
        self.assertEqual(payload["orders"], [12, 16, 24, 32])
        self.assertEqual(len(calibration), 6)
        self.assertEqual(len(holdout), 16)
        self.assertEqual(len(real), 6)
        self.assertEqual(criteria.ngspice_max_error_percent, 1e-6)


if __name__ == "__main__":
    unittest.main()

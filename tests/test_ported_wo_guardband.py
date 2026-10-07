import unittest

from chem_suite.modules.eis.legacy.eis_wo_guardband import (
    CANDIDATE_GUARDS_DECADES,
    candidate_passes_criteria,
    summarize_guardband_rows,
    upper_edge_distance_decades,
    validate_holdout_summary,
    wo_guardband_scenarios,
)


class WoGuardbandTests(unittest.TestCase):
    def test_upper_edge_distance_sign_and_scale(self):
        self.assertAlmostEqual(upper_edge_distance_decades(1.0, 10.0), 1.0)
        self.assertAlmostEqual(upper_edge_distance_decades(10.0, 10.0), 0.0)
        self.assertAlmostEqual(upper_edge_distance_decades(100.0, 10.0), -1.0)

    def test_calibration_and_holdout_are_balanced_and_independent(self):
        calibration = wo_guardband_scenarios("calibration")
        holdout = wo_guardband_scenarios("holdout")
        self.assertEqual(len(calibration), 2 * 3 * 8 * 3 * 3)
        self.assertEqual(len(holdout), len(calibration))
        self.assertTrue(
            {row["seed"] for row in calibration}.isdisjoint(
                {row["seed"] for row in holdout}
            )
        )
        self.assertTrue(
            {row["grid_name"] for row in calibration}.isdisjoint(
                {row["grid_name"] for row in holdout}
            )
        )

    def test_summary_selects_smallest_candidate_that_passes(self):
        rows = []
        for index in range(120):
            truth_distance = 0.5 if index < 60 else 1.3
            rows.append({
                "success": True,
                "truth_upper_distance_decades": truth_distance,
                "guard_passes": {
                    "0.4": index != 0,
                    "0.6": index >= 60,
                    "0.85": index >= 60,
                    "1.1": index >= 60,
                },
                "maximum_truth_fold_error": 1.1,
            })
        summary = summarize_guardband_rows(rows)
        self.assertFalse(summary["candidates"]["0.4"]["passes_criteria"])
        self.assertTrue(summary["candidates"]["0.6"]["passes_criteria"])
        self.assertEqual(summary["selected_guard_decades"], 0.6)

    def test_holdout_validates_preselected_guard_without_reselection(self):
        metrics = {
            "eligible": 60,
            "ineligible": 60,
            "false_passes": 0,
            "retention": 0.95,
            "accurate_retention": 0.95,
        }
        self.assertTrue(candidate_passes_criteria(metrics, completed_fraction=1.0))
        summary = {
            "completed_fraction": 1.0,
            "candidates": {
                key: dict(metrics) for key in map(
                    lambda value: f"{value:g}", CANDIDATE_GUARDS_DECADES
                )
            },
        }
        validation = validate_holdout_summary(summary, 0.6)
        self.assertTrue(validation["passed"])
        self.assertEqual(validation["selected_guard_decades"], 0.6)


if __name__ == "__main__":
    unittest.main()

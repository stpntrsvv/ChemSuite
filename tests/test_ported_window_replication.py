import unittest

from chem_suite.modules.eis.legacy.eis_window_replication import (
    POSITION_ORDER,
    characteristic_frequencies,
    replication_scenarios,
    summarize_replication_rows,
)


class WindowReplicationTests(unittest.TestCase):
    def test_characteristic_positions_cover_both_edges(self):
        frequencies = characteristic_frequencies(0.01, 10.0)
        self.assertEqual(tuple(frequencies), POSITION_ORDER)
        self.assertLess(frequencies["below"], 0.01)
        self.assertEqual(frequencies["low_edge"], 0.01)
        self.assertGreater(frequencies["low_inside"], 0.01)
        self.assertLess(frequencies["high_inside"], 10.0)
        self.assertEqual(frequencies["high_edge"], 10.0)
        self.assertGreater(frequencies["above"], 10.0)

    def test_scenario_matrix_is_balanced(self):
        scenarios = replication_scenarios(
            seeds=(1, 2), noise_fractions=(0.005, 0.02)
        )
        self.assertEqual(len(scenarios), 2 * 7 * 2 * 2)
        self.assertEqual({row["process_kind"] for row in scenarios}, {"cpe", "wo"})
        self.assertEqual(
            sum(not row["expected_supported"] for row in scenarios),
            2 * 2 * 2 * 2,
        )

    def test_summary_separates_false_passes_and_retention(self):
        common = {
            "success": True,
            "process_kind": "cpe",
            "noise_fraction": 0.01,
            "target_diagnostics": [{
                "gate_pass": True,
                "estimate_fold_error": 1.1,
            }],
        }
        rows = [
            {
                **common,
                "characteristic_position": "below",
                "expected_supported": False,
                "gate_pass": False,
            },
            {
                **common,
                "characteristic_position": "inside",
                "expected_supported": True,
                "gate_pass": True,
            },
        ]
        summary = summarize_replication_rows(rows)
        metrics = summary["decision_metrics"]
        self.assertEqual(metrics["outside_false_pass_rate"], 0.0)
        self.assertGreater(metrics["outside_false_pass_upper_95"], 0.0)
        self.assertEqual(metrics["supported_retention"], 1.0)
        self.assertEqual(metrics["strict_interior_retention"], 1.0)
        self.assertIsNone(summary["groups"]["cpe"]["overall"]["expected_supported"])

    def test_bad_fit_counts_as_conservative_gate_refusal(self):
        rows = [{
            "success": True,
            "process_kind": "wo",
            "characteristic_position": "above",
            "expected_supported": False,
            "noise_fraction": 0.02,
            "fit_status": "BAD",
            "gate_pass": False,
            "gate_refusal": "base_fit_bad",
            "target_diagnostics": [],
        }]
        summary = summarize_replication_rows(rows)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["decision_metrics"]["outside_completed"], 1)


if __name__ == "__main__":
    unittest.main()

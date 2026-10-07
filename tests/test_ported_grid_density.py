import unittest

from chem_suite.modules.eis.legacy.eis_grid_density import (
    POINT_COUNTS,
    TRIM_RULES,
    density_passes_criteria,
    density_scenarios,
    stability_by_trim_rule,
    summarize_density_rows,
)


class GridDensityTests(unittest.TestCase):
    def test_final_map_is_balanced(self):
        scenarios = density_scenarios()
        self.assertEqual(len(scenarios), 7 * 3 * 3 * 3 * 5)
        self.assertEqual({row["points"] for row in scenarios}, set(POINT_COUNTS))

    def test_trim_rules_use_declared_variant_subsets(self):
        base = [5.0, 20.0, 0.1]
        stability = {
            "variants": [
                {"window": "drop_low_0.1", "accepted": True, "parameters": base},
                {"window": "drop_high_0.1", "accepted": True, "parameters": base},
                {"window": "drop_low_0.2", "accepted": True, "parameters": base},
                {
                    "window": "drop_high_0.2",
                    "accepted": True,
                    "parameters": [5.0, 40.0, 0.1],
                },
            ]
        }
        rules = stability_by_trim_rule(stability, base)
        self.assertTrue(rules["trim_10"])
        self.assertFalse(rules["trim_20"])
        self.assertFalse(rules["combined"])

    def test_density_criteria_require_every_noise_stratum(self):
        metrics = {
            "completed": 135,
            "retention": 0.95,
            "accurate_retention": 0.95,
            "by_noise": {
                "0.005": {"retention": 1.0},
                "0.01": {"retention": 0.95},
                "0.02": {"retention": 0.89},
            },
        }
        self.assertFalse(density_passes_criteria(metrics, requested=135))

    def test_monotone_threshold_requires_all_denser_strata(self):
        rows = []
        for points in POINT_COUNTS:
            passes = points >= 61 and points != 81
            for index in range(135):
                rows.append({
                    "success": True,
                    "points": points,
                    "noise_fraction": (0.005, 0.01, 0.02)[index % 3],
                    "rule_passes": {
                        rule: passes for rule in TRIM_RULES
                    },
                    "maximum_truth_fold_error": 1.1,
                })
        summary = summarize_density_rows(rows)
        self.assertEqual(summary["minimum_monotone_point_count"], 101)


if __name__ == "__main__":
    unittest.main()

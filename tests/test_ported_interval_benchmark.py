import unittest

from chem_suite.modules.eis.legacy.eis_interval_benchmark import parameter_status, summarize_interval_rows


class IntervalBenchmarkTests(unittest.TestCase):
    def test_edge_or_missing_interval_is_unbounded(self):
        common = dict(estimate=5.0, low=4.0, high=6.0, bound_low=0.0, bound_high=10.0)
        self.assertEqual(parameter_status(**common, interval_hits_edge=True), "unbounded")
        self.assertEqual(
            parameter_status(
                estimate=5.0, low=None, high=None, bound_low=0.0, bound_high=10.0
            ),
            "unbounded",
        )

    def test_status_distinguishes_narrow_and_wide_intervals(self):
        self.assertEqual(
            parameter_status(
                estimate=5.0, low=4.0, high=6.0, bound_low=0.0, bound_high=100.0
            ),
            "identified",
        )
        self.assertEqual(
            parameter_status(
                estimate=1.0, low=0.1, high=3.0, bound_low=0.0, bound_high=10.0
            ),
            "weak",
        )

    def test_summary_measures_truth_coverage(self):
        summary = summarize_interval_rows([{
            "success": True,
            "intervals": [
                {"method": "covariance", "covers_truth": True, "parameter_status": "identified"},
                {"method": "covariance", "covers_truth": False, "parameter_status": "weak"},
            ],
        }])
        self.assertEqual(summary["methods"]["covariance"]["coverage"], 0.5)
        self.assertEqual(summary["methods"]["covariance"]["statuses"]["identified"], 1)

    def test_summary_is_stratified_without_changing_overall_metrics(self):
        rows = [{
            "success": True, "stratum": "observable",
            "target_parameters": ["x"],
            "intervals": [{
                "method": "parametric_bootstrap", "parameter": "x", "covers_truth": True,
                "parameter_status": "identified",
            }],
        }, {
            "success": True, "stratum": "weak",
            "target_parameters": ["x"],
            "intervals": [{
                "method": "parametric_bootstrap", "parameter": "x", "covers_truth": False,
                "parameter_status": "weak",
            }],
        }]
        summary = summarize_interval_rows(rows)
        self.assertEqual(summary["methods"]["parametric_bootstrap"]["coverage"], 0.5)
        self.assertEqual(
            summary["strata"]["observable"]["methods"]["parametric_bootstrap"]["coverage"],
            1.0,
        )
        self.assertEqual(summary["strata"]["weak"]["requested"], 1)


if __name__ == "__main__":
    unittest.main()

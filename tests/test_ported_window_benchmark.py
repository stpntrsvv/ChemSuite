import unittest

from chem_suite.modules.eis.legacy.eis_window_benchmark import summarize_window_rows


class WindowBenchmarkTests(unittest.TestCase):
    def test_summary_measures_identified_retention(self):
        summary = summarize_window_rows([{
            "success": True, "stratum": "weak", "target_parameters": ["x", "y"],
            "intervals": [
                {
                    "method": "bootstrap", "parameter": "x",
                    "parameter_status": "identified", "bias_aware_status": "weak",
                    "covers_truth": False,
                },
                {
                    "method": "bootstrap", "parameter": "y",
                    "parameter_status": "identified", "bias_aware_status": "identified",
                    "covers_truth": True,
                },
            ],
        }])
        method = summary["strata"]["weak"]["methods"]["bootstrap"]
        self.assertEqual(method["identified_before"], 2)
        self.assertEqual(method["identified_after"], 1)
        self.assertEqual(method["identified_coverage_after"], 1.0)


if __name__ == "__main__":
    unittest.main()

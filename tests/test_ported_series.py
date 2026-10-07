import unittest

from chem_suite.modules.eis.legacy.eis_series import analyze_result_series, infer_series_metadata


def result(file_name, circuit, value, status="OK", alternatives=None):
    alternatives = alternatives or []
    fits = [
        {"circuit": circuit, "status": status, "success": True, "bic": 0.0,
         "parameters": [{"name": "R0", "value": value}]}
    ] + alternatives
    return {
        "file_name": file_name,
        "best": {
            "circuit": circuit,
            "status": status,
            "parameters": [{"name": "R0", "value": value}],
        },
        "kk": {"status": "PASS"},
        "fits": fits,
    }


class SeriesAnalysisTests(unittest.TestCase):
    def test_infers_lg_and_21700_metadata(self):
        self.assertEqual(infer_series_metadata("08e_x_SOC-50eis.csv")["soc"], 50.0)
        self.assertEqual(infer_series_metadata("08i_x_SOC99eis.csv")["direction"], "charge")
        self.assertEqual(infer_series_metadata("ID07.csv")["cell_id"], "ID07")

    def test_series_reports_topology_stability_and_parameter_trend(self):
        rows = [
            result("08e_x_SOC-10eis.csv", "A", 1.0),
            result("08e_x_SOC-20eis.csv", "A", 2.0),
            result("08e_x_SOC-30eis.csv", "A", 3.0),
            result("08e_x_SOC-40eis.csv", "B", 9.0, status="WARN"),
        ]
        report = analyze_result_series(rows)["series"][0]

        self.assertEqual(report["dominant_topology"], "A")
        self.assertEqual(report["dominant_topology_fraction"], 0.75)
        trajectory = report["dominant_topology_parameter_trajectories"][0]
        self.assertAlmostEqual(trajectory["spearman_soc_rho"], 1.0)

    def test_pools_bic_evidence_and_smooths_common_topology(self):
        rows = []
        for soc, value in [(10, 1.0), (20, 1.4), (30, 2.1), (40, 2.8)]:
            rows.append(result(
                f"08e_x_SOC-{soc}eis.csv", "A", value,
                alternatives=[{"circuit": "B", "status": "OK", "success": True, "bic": 4.0,
                               "parameters": [{"name": "R0", "value": value * 2}]}],
            ))
        report = analyze_result_series(rows)["series"][0]
        self.assertEqual(report["pooled_topology"], "A")
        curve = report["pooled_parameter_trajectories"][0]
        self.assertEqual(curve["method"], "cubic_smoothing_spline")
        self.assertEqual(curve["scale"], "log")
        self.assertEqual(len(curve["curve"]), 4)


if __name__ == "__main__":
    unittest.main()

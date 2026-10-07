import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from chem_suite.modules.eis.legacy.eis_core import FitResult, KramersKronigResult
from chem_suite.modules.eis.legacy.eis_pipeline import AnalysisResult
from chem_suite.modules.eis.legacy.eis_rational import RationalFitMetrics
from chem_suite.modules.eis.legacy.eis_spice import NgspiceRoundTripResult
from chem_suite.modules.eis.legacy.eis_spice_export import (
    SpiceExportPolicy,
    SpiceExportRefused,
    export_spice_package,
)


class _PredictiveModel:
    parameters_ = np.array([2.0, 3.0, 4e-4])
    conf_ = np.array([0.1, 0.2, 1e-5])

    def predict(self, frequencies):
        s = 1j * 2.0 * np.pi * np.asarray(frequencies, dtype=float)
        return 2.0 + 3.0 / (1.0 + s * 3.0 * 4e-4)


def analysis_result(source: Path, *, status: str = "OK", kk_status: str = "PASS"):
    fit = FitResult(
        circuit_string="R0-p(R1,C1)",
        success=True,
        model=_PredictiveModel(),
        mean_fit_error=0.2,
        max_param_error=5.0,
        rss_weighted=0.1,
        aic=-20.0,
        bic=-18.0,
        n_params=3,
        status=status,
    )
    return AnalysisResult(
        file_path=str(source),
        success=True,
        source_format="fixture",
        selected_channel="Z",
        point_count=80,
        kk=KramersKronigResult(
            success=kk_status in {"PASS", "WARN"},
            status=kk_status,
            rmse_percent=0.4,
            max_error_percent=1.2,
            mu=0.8,
        ),
        fits=[fit],
        best=fit,
        stage="complete",
    )


def external_result(status="validated"):
    metrics = RationalFitMetrics(1e-9, 2e-9, 3e-9) if status != "runtime_missing" else None
    return NgspiceRoundTripResult(
        status=status,
        executable="ngspice",
        simulator_version="46",
        points=200 if metrics else 0,
        metrics=metrics,
        return_code=0 if metrics else None,
        message="ok" if metrics else "missing",
    )


TEST_POLICY = SpiceExportPolicy(
    orders=(12,),
    ecm_mean_error_percent=100.0,
    ecm_max_error_percent=100.0,
)


class SpicePackageTests(unittest.TestCase):
    @patch("chem_suite.modules.eis.legacy.eis_spice_export.run_ngspice_round_trip")
    def test_validated_package_is_published_with_passport(self, round_trip):
        round_trip.return_value = external_result()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.csv"
            source.write_text("fixture", encoding="utf-8")
            target = root / "spice-package"

            result = export_spice_package(
                analysis_result(source),
                np.logspace(-1, 4, 80),
                target,
                source_file=source,
                policy=TEST_POLICY,
            )

            self.assertEqual(result.selected_order, 12)
            self.assertTrue((target / "model.lib").is_file())
            passport = json.loads((target / "passport.json").read_text(encoding="utf-8"))
            self.assertEqual(passport["status"], "validated")
            self.assertNotIn("Infinity", (target / "passport.json").read_text(encoding="utf-8"))
            self.assertEqual(passport["engineering_model"]["selected_order"], 12)
            self.assertEqual(
                passport["engineering_model"]["selected_attempt"]["external_validation"]["version"],
                "46",
            )
            self.assertEqual(len(passport["source"]["sha256"]), 64)

    @patch("chem_suite.modules.eis.legacy.eis_spice_export.run_ngspice_round_trip")
    def test_missing_runtime_refuses_without_creating_package(self, round_trip):
        round_trip.return_value = external_result("runtime_missing")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.csv"
            source.write_text("fixture", encoding="utf-8")
            target = root / "spice-package"

            with self.assertRaises(SpiceExportRefused):
                export_spice_package(
                    analysis_result(source),
                    np.logspace(-1, 4, 80),
                    target,
                    source_file=source,
                    policy=TEST_POLICY,
                )

            self.assertFalse(target.exists())

    @patch("chem_suite.modules.eis.legacy.eis_spice_export.run_ngspice_round_trip")
    def test_bad_scientific_model_is_rejected_before_external_run(self, round_trip):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.csv"
            source.write_text("fixture", encoding="utf-8")

            with self.assertRaises(SpiceExportRefused):
                export_spice_package(
                    analysis_result(source, status="BAD"),
                    np.logspace(-1, 4, 80),
                    Path(temp_dir) / "package",
                    source_file=source,
                    policy=TEST_POLICY,
                )

            round_trip.assert_not_called()

    @patch("chem_suite.modules.eis.legacy.eis_spice_export.run_ngspice_round_trip")
    def test_existing_package_is_never_overwritten(self, round_trip):
        round_trip.return_value = external_result()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.csv"
            source.write_text("fixture", encoding="utf-8")
            target = root / "package"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                export_spice_package(
                    analysis_result(source),
                    np.logspace(-1, 4, 80),
                    target,
                    source_file=source,
                    policy=TEST_POLICY,
                )

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            round_trip.assert_not_called()


if __name__ == "__main__":
    unittest.main()

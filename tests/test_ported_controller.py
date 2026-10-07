import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from chem_suite.modules.eis.legacy.eis_controller import (
    ControllerExportPolicy,
    ControllerExportRefused,
    build_discrete_controller_model,
    export_controller_package,
    generate_float32_header,
    generate_float32_source,
    generate_q31_header,
    generate_q31_source,
    q31_from_normalized,
)
from chem_suite.modules.eis.legacy.eis_core import FitResult, KramersKronigResult
from chem_suite.modules.eis.legacy.eis_pipeline import AnalysisResult
from chem_suite.modules.eis.legacy.eis_rational import FosterSection


class _PredictiveModel:
    parameters_ = np.array([2.0, 3.0, 4e-4])
    conf_ = np.array([0.1, 0.2, 1e-5])

    def predict(self, frequencies):
        s = 1j * 2.0 * np.pi * np.asarray(frequencies, dtype=float)
        return 2.0 + 3.0 / (1.0 + s * 3.0 * 4e-4)


def analysis_result(source: Path, *, status: str = "OK") -> AnalysisResult:
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
            success=True,
            status="PASS",
            rmse_percent=0.4,
            max_error_percent=1.2,
            mu=0.8,
        ),
        fits=[fit],
        best=fit,
        stage="complete",
    )


def discrete_model():
    section = FosterSection(
        resistance=3.0,
        capacitance=4e-4,
        relaxation_rate=1.0 / (3.0 * 4e-4),
        residue=1.0 / 4e-4,
    )
    return build_discrete_controller_model(
        direct_resistance_ohm=2.0,
        sections=[section],
        sample_period_s=1e-4,
        current_full_scale_a=10.0,
        frequency_min_hz=0.1,
        frequency_max_hz=100.0,
    )


TEST_POLICY = ControllerExportPolicy(
    orders=(12,),
    ecm_mean_error_percent=100.0,
    ecm_max_error_percent=100.0,
    controller_realization_max_error_percent=100.0,
    discrete_max_error_percent=20.0,
    q31_max_error_percent=1.0,
)


class ControllerModelTests(unittest.TestCase):
    def test_float32_and_q31_step_responses_agree(self):
        model = discrete_model()
        normalized = np.r_[
            np.zeros(10),
            np.full(100, 0.5),
            np.full(100, -0.25),
        ]
        float_voltage = model.simulate_float32(
            normalized * model.current_full_scale_a
        ).astype(float)
        q31_voltage = (
            model.simulate_q31(q31_from_normalized(normalized)).astype(float)
            / (1 << 31)
            * model.voltage_full_scale_v
        )

        np.testing.assert_allclose(q31_voltage, float_voltage, atol=2e-5, rtol=2e-6)
        self.assertLessEqual(int(np.sum(model.output_gains_q31)), (1 << 31) - 1)

    def test_generated_sources_compile_as_strict_c99(self):
        compiler = shutil.which("gcc")
        if compiler is None:
            self.skipTest("gcc is not available")
        model = discrete_model()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = {
                "eis_model_f32.h": generate_float32_header(model),
                "eis_model_f32.c": generate_float32_source(model),
                "eis_model_q31.h": generate_q31_header(model),
                "eis_model_q31.c": generate_q31_source(model),
            }
            for name, content in sources.items():
                (root / name).write_text(content, encoding="ascii")
            for name in ("eis_model_f32.c", "eis_model_q31.c"):
                completed = subprocess.run(
                    [
                        compiler,
                        "-std=c99",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-c",
                        str(root / name),
                        "-o",
                        str(root / f"{name}.o"),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            inputs = np.array(
                [0, 536870912, 536870912, -1073741824, 2147483647, -2147483648],
                dtype=np.int64,
            )
            input_literals = ", ".join(str(int(value)) for value in inputs)
            (root / "q31_harness.c").write_text(
                f"""#include "eis_model_q31.h"
#include <inttypes.h>
#include <stdio.h>

int main(void) {{
    static const int32_t input[] = {{{input_literals}}};
    eis_model_q31_t model;
    uint32_t k;
    eis_model_q31_reset(&model);
    for (k = 0u; k < sizeof(input) / sizeof(input[0]); ++k) {{
        printf("%" PRId32 "\\n", eis_model_q31_step(&model, input[k]));
    }}
    return 0;
}}
""",
                encoding="ascii",
            )
            executable = root / "q31_harness.exe"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=c99",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(root / "eis_model_q31.c"),
                    str(root / "q31_harness.c"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            c_output = subprocess.run(
                [str(executable)],
                capture_output=True,
                text=True,
                check=True,
            )
            actual = np.asarray(
                [int(line) for line in c_output.stdout.splitlines()],
                dtype=np.int64,
            )
            np.testing.assert_array_equal(actual, model.simulate_q31(inputs))

    def test_q31_source_has_no_floating_point_operations(self):
        source = generate_q31_source(discrete_model())

        self.assertNotIn("float", source)
        self.assertIn("int64_t", source)
        self.assertIn("int32_t eis_model_q31_step", source)


class ControllerPackageTests(unittest.TestCase):
    def test_package_contains_both_implementations_vectors_and_passport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.csv"
            source.write_text("fixture", encoding="utf-8")
            target = root / "controller-package"

            result = export_controller_package(
                analysis_result(source),
                np.logspace(-1, 4, 80),
                target,
                sample_period_s=1e-5,
                current_full_scale_a=10.0,
                source_file=source,
                policy=TEST_POLICY,
            )

            self.assertEqual(result.selected_order, 12)
            for name in (
                "eis_model_f32.h",
                "eis_model_f32.c",
                "eis_model_q31.h",
                "eis_model_q31.c",
                "reference_vectors.csv",
                "passport.json",
            ):
                self.assertTrue((target / name).is_file())
            passport_text = (target / "passport.json").read_text(encoding="utf-8")
            passport = json.loads(passport_text)
            self.assertEqual(passport["status"], "validated")
            self.assertEqual(passport["controller_model"]["q31_format"], "signed Q1.31")
            self.assertNotIn("Infinity", passport_text)
            self.assertEqual(len(passport["source"]["sha256"]), 64)

    def test_existing_package_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.csv"
            source.write_text("fixture", encoding="utf-8")
            target = root / "controller-package"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                export_controller_package(
                    analysis_result(source),
                    np.logspace(-1, 4, 80),
                    target,
                    sample_period_s=1e-5,
                    current_full_scale_a=10.0,
                    source_file=source,
                    policy=TEST_POLICY,
                )

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_bad_scientific_model_is_refused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.csv"
            source.write_text("fixture", encoding="utf-8")

            with self.assertRaises(ControllerExportRefused):
                export_controller_package(
                    analysis_result(source, status="BAD"),
                    np.logspace(-1, 4, 80),
                    Path(temp_dir) / "controller-package",
                    sample_period_s=1e-5,
                    current_full_scale_a=10.0,
                    source_file=source,
                    policy=TEST_POLICY,
                )


if __name__ == "__main__":
    unittest.main()

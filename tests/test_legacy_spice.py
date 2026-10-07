import unittest

import numpy as np

from chem_suite.modules.eis.legacy.eis_rational import PassiveRationalModel, RationalFitMetrics
from chem_suite.modules.eis.legacy.eis_spice import (
    build_ngspice_validation_deck,
    condition_foster_realization,
    detect_ngspice,
    export_foster_subcircuit,
    parse_ngspice_ac_output,
    run_ngspice_round_trip,
    validate_foster_round_trip,
)


def example_model():
    return PassiveRationalModel(
        relaxation_rates=np.array([10.0, 1_000.0]),
        residues=np.array([200.0, 3_000.0]),
        direct=2.5,
        derivative=4e-6,
        frequency_min_hz=0.1,
        frequency_max_hz=10_000.0,
        metrics=RationalFitMetrics(0.2, 0.3, 0.8),
        source_circuit="L0-R0-p(R1,CPE0)",
    )


class SpiceExportTests(unittest.TestCase):
    def test_foster_component_round_trip_is_numerically_exact(self):
        model = example_model()
        metrics = validate_foster_round_trip(model, np.logspace(-1, 4, 80))

        self.assertLess(metrics.max_relative_error_percent, 1e-10)

    def test_subcircuit_uses_portable_rcl_elements(self):
        netlist = export_foster_subcircuit(example_model(), subcircuit_name="CELL_Z")

        self.assertIn(".subckt CELL_Z p n", netlist)
        self.assertIn("R_DIRECT p x1 2.5", netlist)
        self.assertIn("L_SERIES x1 x2 4e-06", netlist)
        self.assertIn("R_F1 x2 x3 20", netlist)
        self.assertIn("C_F1 x2 x3 0.005", netlist)
        self.assertIn("R_F2 x3 n 3", netlist)
        self.assertIn("C_F2 x3 n 0.000333333333333", netlist)
        self.assertTrue(netlist.endswith(".ends CELL_Z\n"))

    def test_global_conditioning_respects_complete_network_error_budget(self):
        model = PassiveRationalModel(
            relaxation_rates=np.array([1e-5, 10.0, 1_000.0]),
            residues=np.array([1e-3, 200.0, 3_000.0]),
            direct=2.5,
            derivative=0.0,
            frequency_min_hz=0.1,
            frequency_max_hz=10_000.0,
            metrics=RationalFitMetrics(0.2, 0.3, 0.8),
        )

        result = condition_foster_realization(model, max_error_percent=0.25)

        self.assertEqual(result.original_sections, 3)
        self.assertEqual(result.pruned_sections, 1)
        self.assertLessEqual(result.metrics.max_relative_error_percent, 0.25)
        self.assertNotIn(1e-5, [section.relaxation_rate for section in result.sections])

    def test_export_can_use_explicit_conditioned_sections(self):
        conditioned = condition_foster_realization(
            example_model(),
            max_error_percent=0.0,
        )
        netlist = export_foster_subcircuit(
            example_model(),
            sections=conditioned.sections,
        )

        self.assertIn("R_F1", netlist)
        self.assertIn("R_F2", netlist)

    def test_ngspice_deck_instantiates_exported_subcircuit(self):
        deck = build_ngspice_validation_deck(
            "cell model.lib",
            example_model(),
            subcircuit_name="CELL_Z",
        )

        self.assertIn('.include "cell model.lib"', deck)
        self.assertIn("I_TEST 0 in AC 1", deck)
        self.assertIn("X_DUT in 0 CELL_Z", deck)
        self.assertIn("set numdgt=15", deck)
        self.assertIn("print frequency vr(in) vi(in)", deck)

    def test_missing_explicit_runtime_is_reported_not_hidden(self):
        status = detect_ngspice("Z:/definitely-missing/ngspice.exe")

        self.assertEqual(status.status, "runtime_missing")
        self.assertIsNone(status.executable)

    def test_ngspice_text_output_parser_handles_paginated_rows(self):
        output = """
Index   frequency       vr(in)          vi(in)
0       1.000000e-02    4.2e+01         -1.1e+02
\f
Index   frequency       vr(in)          vi(in)
1       1.100000e-02    4.1e+01         -1.0e+02
"""

        frequencies, impedance = parse_ngspice_ac_output(output)

        np.testing.assert_allclose(frequencies, [0.01, 0.011])
        np.testing.assert_allclose(impedance, [42.0 - 110.0j, 41.0 - 100.0j])

    def test_round_trip_api_fails_closed_without_runtime(self):
        result = run_ngspice_round_trip(
            example_model(),
            executable="Z:/definitely-missing/ngspice.exe",
        )

        self.assertEqual(result.status, "runtime_missing")
        self.assertIsNone(result.metrics)


if __name__ == "__main__":
    unittest.main()

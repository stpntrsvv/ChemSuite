import json
import tempfile
from pathlib import Path
import unittest

import numpy as np

from chem_suite.modules.eis.legacy.eis_diffusion_map import (
    controlled_parameters,
    generate_diverse_negative_controls,
    generate_observability_corpus,
)


class DiffusionMapTests(unittest.TestCase):
    def test_diverse_negative_controls_cover_declared_axes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = generate_diverse_negative_controls(
                temp_dir,
                samples=6,
                seed=3,
                point_counts=(12, 16),
                frequency_bands=((1e-2, 1e4),),
                noise_fractions=(0.0, 0.01),
                outlier_fractions=(0.0,),
            )
            rows = [
                json.loads(line)
                for line in manifest.read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["points"] for row in rows}, {12, 16})
        self.assertEqual({row["noise_fraction"] for row in rows}, {0.0, 0.01})
        self.assertTrue(all(row["circuit"] == "L0-R0-p(R1,CPE0)" for row in rows))

    def test_controlled_strength_matches_requested_rms_fraction(self):
        frequencies = np.logspace(5, -2, 61)
        for kind in ("W", "Wo", "Ws"):
            parameters, achieved = controlled_parameters(
                kind,
                frequencies,
                signal_fraction=0.05,
                characteristic_frequency_hz=1.0,
            )
            self.assertGreater(parameters[-2] if kind != "W" else parameters[-1], 0)
            self.assertAlmostEqual(achieved, 0.05, places=10)

    def test_manifest_records_observability_axes(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = generate_observability_corpus(
                directory,
                kinds=("Wo",),
                signal_fractions=(0.05,),
                characteristic_positions=("below", "inside"),
                noise_fractions=(0.01,),
                replicates=1,
                points=20,
            )
            lines = Path(manifest).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue((Path(directory) / "spectra").is_dir())

import tempfile
from pathlib import Path
import unittest

import numpy as np

from chem_suite.modules.eis.legacy.eis_io import load_eis_file
from chem_suite.modules.eis.legacy.eis_synthetic import generate_corpus, simulate_spectrum


class SyntheticDataTests(unittest.TestCase):
    def test_noise_free_simulation_is_deterministic(self):
        frequencies = np.logspace(4, -2, 20)
        first_clean, first = simulate_spectrum("R0-p(R1,C1)", [5.0, 20.0, 1e-4], frequencies)
        second_clean, second = simulate_spectrum("R0-p(R1,C1)", [5.0, 20.0, 1e-4], frequencies)
        np.testing.assert_allclose(first, first_clean)
        np.testing.assert_allclose(second, first)
        self.assertTrue(np.isfinite(first.real).all())

    def test_generated_corpus_is_loadable_and_has_truth_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = generate_corpus(
                temp_dir,
                circuits=("R0-p(R1,C1)",),
                samples_per_circuit=2,
                noise_fraction=0.0,
                seed=7,
                points=12,
            )
            files = sorted((Path(temp_dir) / "spectra").glob("*.csv"))
            rows = manifest.read_text(encoding="utf-8").splitlines()
            dataset = load_eis_file(str(files[0]))

        self.assertEqual(len(files), 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(dataset.frequencies), 12)


if __name__ == "__main__":
    unittest.main()

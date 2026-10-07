import csv
import tempfile
import unittest
from pathlib import Path

from chem_suite.modules.eis.legacy.eis_joint import cross_validate_smoothness, load_manifest


class JointManifestTests(unittest.TestCase):
    def test_manifest_is_required_and_soc_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a.csv", "b.csv", "c.csv"):
                (root / name).touch()
            with (root / "series.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["file", "soc"])
                writer.writeheader()
                writer.writerows([{"file": "a.csv", "soc": 30}, {"file": "b.csv", "soc": 10},
                                  {"file": "c.csv", "soc": 20}])
            rows = load_manifest(root / "series.csv")
            self.assertEqual([row["soc"] for row in rows], [10.0, 20.0, 30.0])
            self.assertTrue(Path(rows[0]["file"]).is_absolute())

    def test_rejects_series_without_three_soc_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.csv").touch()
            (root / "series.csv").write_text("file,soc\na.csv,10\na.csv,10\na.csv,20\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "three distinct"):
                load_manifest(root / "series.csv")

    def test_cross_validation_requires_five_spectra(self):
        rows = [{"file": "unused", "soc": float(index)} for index in range(4)]
        with self.assertRaisesRegex(ValueError, "five spectra"):
            cross_validate_smoothness(rows, "R0-p(R1,CPE0)", [0, 1])

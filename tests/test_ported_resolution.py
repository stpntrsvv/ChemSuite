import unittest

from chem_suite.modules.eis.legacy.eis_resolution import _has_peak_near


class ResolutionTests(unittest.TestCase):
    def test_peak_matching_uses_log_frequency_distance(self):
        peaks = [{"frequency_hz": 0.02}]
        self.assertTrue(_has_peak_near(peaks, 0.01, tolerance_decades=0.5))
        self.assertFalse(_has_peak_near(peaks, 1.0, tolerance_decades=0.5))

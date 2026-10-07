import unittest

from chem_suite.modules.eis.legacy.eis_interval_corpus import corpus_scenarios


class IntervalCorpusTests(unittest.TestCase):
    def test_corpus_freezes_four_balanced_strata(self):
        scenarios = corpus_scenarios(replicates=3)
        self.assertEqual(len(scenarios), 12)
        self.assertEqual(
            {item["stratum"] for item in scenarios},
            {"cpe_observable", "cpe_weak", "wo_observable", "wo_weak"},
        )
        self.assertEqual(sum(item["profile_representative"] for item in scenarios), 4)


if __name__ == "__main__":
    unittest.main()

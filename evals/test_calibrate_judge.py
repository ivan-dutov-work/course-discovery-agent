from __future__ import annotations

import unittest

from evals.calibrate_judge import score

PASS = dict.fromkeys(("captured", "polarity", "scope", "no_invention", "no_loss"), "pass")


class ScoreTests(unittest.TestCase):
    def test_failure_is_the_positive_class(self):
        labels = {"a": ["scope"], "b": ["scope"], "c": []}
        verdicts = {
            "a": {**PASS, "scope": "fail"},
            "b": {**PASS, "scope": "pass"},
            "c": {**PASS, "scope": "fail"},
        }
        row = score(labels, verdicts)["scope"]
        self.assertEqual((row["tpr"].hits, row["tpr"].total), (1, 2))
        self.assertEqual((row["tnr"].hits, row["tnr"].total), (0, 1))

    def test_unknown_counts_as_a_miss_on_both_sides(self):
        labels = {"a": ["scope"], "b": []}
        verdicts = {"a": {**PASS, "scope": "unknown"}, "b": {**PASS, "scope": "unknown"}}
        row = score(labels, verdicts)["scope"]
        self.assertEqual(row["tpr"].hits, 0)
        self.assertEqual(row["tnr"].hits, 0)
        self.assertEqual(row["unknown"], 2)


if __name__ == "__main__":
    unittest.main()

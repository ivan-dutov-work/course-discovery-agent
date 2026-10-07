from __future__ import annotations

import unittest

from evals.calibrate_thread_selector import check_no_leak, decide, fit_threshold, rates


def _s(follows: float | None, signal: float = 0.0) -> dict:
    return {"continue": follows, "signal": signal}


def _pair(item_id: str, label: str, split: str = "train") -> dict:
    return {"id": item_id, "label": label, "split": split}


class RateTests(unittest.TestCase):
    def test_both_classes_get_tpr_and_tnr(self):
        pairs = [_pair("a", "continue"), _pair("b", "continue"), _pair("c", "new_topic"), _pair("d", "new_topic")]
        scores = {"a": _s(0.9), "b": _s(0.5), "c": _s(0.8), "d": _s(0.1)}
        result = rates(pairs, scores, 0.7)
        self.assertEqual((result["continue"]["tpr"].hits, result["continue"]["tpr"].total), (1, 2))
        self.assertEqual((result["continue"]["tnr"].hits, result["continue"]["tnr"].total), (1, 2))
        self.assertEqual((result["new_topic"]["tpr"].hits, result["new_topic"]["tpr"].total), (1, 2))
        self.assertEqual((result["new_topic"]["tnr"].hits, result["new_topic"]["tnr"].total), (1, 2))

    def test_missing_score_is_a_new_topic(self):
        self.assertEqual(decide(None, 0.7), "new_topic")
        self.assertEqual(decide(_s(None, None), 0.7), "new_topic")
        self.assertEqual(decide(_s(0.69), 0.7), "new_topic")
        self.assertEqual(decide(_s(0.7), 0.7), "continue")

    def test_high_signal_score_wins_over_the_continue_score(self):
        self.assertEqual(decide(_s(0.9, 0.95), 0.7), "no_signal")
        self.assertEqual(decide(_s(0.9, 0.5), 0.7), "continue")

    def test_all_three_classes_are_reported(self):
        pairs = [_pair("a", "continue"), _pair("b", "new_topic"), _pair("c", "no_signal")]
        scores = {"a": _s(0.9), "b": _s(0.1), "c": _s(0.6, 0.9)}
        result = rates(pairs, scores, 0.7)
        self.assertEqual(set(result), {"continue", "new_topic", "no_signal"})
        for row in result.values():
            self.assertEqual((row["tpr"].hits, row["tpr"].total), (1, 1))


class LeakTests(unittest.TestCase):
    def test_a_test_id_in_the_tuning_inputs_fails(self):
        pairs = [_pair("a", "continue"), _pair("t", "continue", "test")]
        check_no_leak(["a"], pairs)
        with self.assertRaises(SystemExit):
            check_no_leak(["a", "t"], pairs)

    def test_fit_refuses_test_pairs(self):
        with self.assertRaises(SystemExit):
            fit_threshold([_pair("t", "continue", "test")], {"t": _s(0.9)})

    def test_fit_picks_a_threshold_that_separates_the_classes(self):
        pairs = [_pair("a", "continue"), _pair("b", "continue"), _pair("c", "new_topic"), _pair("d", "new_topic")]
        scores = {"a": _s(0.95), "b": _s(0.85), "c": _s(0.45), "d": _s(0.2)}
        threshold = fit_threshold(pairs, scores)
        self.assertTrue(0.45 < threshold <= 0.85)


if __name__ == "__main__":
    unittest.main()

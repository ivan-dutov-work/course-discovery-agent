from __future__ import annotations

import os
import unittest

from tests.judge import judge_notes

LIVE = bool(os.getenv("OPENROUTER_API_KEY")) and os.getenv("LIVE_LLM_TESTS") == "1"

HANDS_ON = "hands-on for Python, theory is fine for math"
OLD = "prefers short lectures [durable]"

LABELLED = [
    ("good", HANDS_ON, [OLD], [OLD, "prefers hands-on courses for Python [topic:python]"], {}),
    (
        "polarity_flipped",
        HANDS_ON,
        [OLD],
        [OLD, "dislikes hands-on courses for Python [topic:python]"],
        {"polarity": "fail"},
    ),
    (
        "topic_stored_as_durable",
        HANDS_ON,
        [OLD],
        [OLD, "prefers hands-on courses for Python [durable]"],
        {"scope": "fail"},
    ),
    (
        "invented_preference",
        HANDS_ON,
        [OLD],
        [OLD, "prefers hands-on courses for Python [topic:python]", "wants certificates [durable]"],
        {"no_invention": "fail"},
    ),
    (
        "lost_old_note",
        HANDS_ON,
        [OLD],
        ["prefers hands-on courses for Python [topic:python]"],
        {"no_loss": "fail"},
    ),
    ("fact_missing", HANDS_ON, [OLD], [OLD], {"captured": "fail"}),
]


@unittest.skipUnless(LIVE, "set OPENROUTER_API_KEY and LIVE_LLM_TESTS=1 to run live LLM tests")
class JudgeLabelledLiveTests(unittest.TestCase):
    def test_judge_agrees_with_the_hand_labels(self):
        for name, feedback, before, after, expected in LABELLED:
            with self.subTest(name):
                result = judge_notes(feedback, before, after)
                for criterion, verdict in expected.items():
                    self.assertEqual(result.verdicts[criterion], verdict, result.reasons[criterion])
                if not expected:
                    self.assertTrue(result.passed, result.failing())


if __name__ == "__main__":
    unittest.main()

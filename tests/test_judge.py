from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from course_discovery.app.llm import JUDGE_MODEL
from tests.judge import (
    CRITERIA,
    JUDGE_SYSTEM_PROMPT,
    CriterionVerdict,
    JudgeVerdict,
    build_judge,
    judge_notes,
)


def verdict(**overrides: str) -> JudgeVerdict:
    fields = {name: CriterionVerdict(verdict=overrides.get(name, "pass"), reason=f"{name} ok") for name in CRITERIA}
    return JudgeVerdict(**fields)


class ScriptedJudge:
    def __init__(self, *verdicts: JudgeVerdict):
        self.verdicts = list(verdicts)
        self.seen: list[list] = []

    def invoke(self, messages):
        self.seen.append(messages)
        return self.verdicts[len(self.seen) - 1]


class JudgeTests(unittest.TestCase):
    def test_three_calls_and_majority_per_criterion(self):
        llm = ScriptedJudge(verdict(), verdict(polarity="fail"), verdict(polarity="fail", scope="fail"))
        result = judge_notes("feedback", [], ["note"], llm=llm)

        self.assertEqual(len(llm.seen), 3)
        self.assertEqual(result.verdicts["polarity"], "fail")
        self.assertEqual(result.verdicts["scope"], "pass")
        self.assertFalse(result.passed)
        self.assertEqual(list(result.failing()), ["polarity"])
        self.assertEqual(len(result.failing()["polarity"]), 3)

    def test_no_majority_is_unknown_and_does_not_pass(self):
        llm = ScriptedJudge(verdict(), verdict(captured="fail"), verdict(captured="unknown"))
        result = judge_notes("feedback", [], [], llm=llm)

        self.assertEqual(result.verdicts["captured"], "unknown")
        self.assertFalse(result.passed)

    def test_all_pass(self):
        result = judge_notes("feedback", [], [], llm=ScriptedJudge(verdict(), verdict(), verdict()))
        self.assertTrue(result.passed)
        self.assertEqual(result.failing(), {})

    def test_prompt_carries_the_free_text_and_nothing_else(self):
        llm = ScriptedJudge(verdict(), verdict(), verdict())
        judge_notes("hands-on please", ["old [durable]"], ["old [durable]", "new [topic:python]"], llm=llm)

        system, human = llm.seen[0]
        self.assertEqual(system.content, JUDGE_SYSTEM_PROMPT)
        self.assertEqual(
            json.loads(human.content),
            {
                "feedback": "hands-on please",
                "notes_before": ["old [durable]"],
                "notes_after": ["old [durable]", "new [topic:python]"],
            },
        )

    def test_calls_can_be_lowered(self):
        llm = ScriptedJudge(verdict())
        judge_notes("feedback", [], [], llm=llm, calls=1)
        self.assertEqual(len(llm.seen), 1)

    def test_built_on_the_judge_model_without_a_fallback_list(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            params = build_judge().first._default_params

        self.assertEqual(params["model"], JUDGE_MODEL)
        self.assertEqual(params["models"], [JUDGE_MODEL])
        self.assertEqual(params["temperature"], 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import unittest
from collections import defaultdict

from tests.judge import judge_notes
from tests.memory_cases import CASES, MemoryCase
from tests.test_memory_e2e import MemoryCaseScenario, install_fake_profiles

LIVE = bool(os.getenv("OPENROUTER_API_KEY")) and os.getenv("LIVE_LLM_TESTS") == "1"
TRIALS = int(os.getenv("LIVE_TRIALS", "3"))
FAILURES: dict[str, int] = defaultdict(int)


def _notes(memory) -> list[str]:
    return [f"{note.text} [{note.scope}]" for note in memory.notes]


@unittest.skipUnless(LIVE, "set OPENROUTER_API_KEY and LIVE_LLM_TESTS=1 to run live LLM tests")
class MemoryE2ELive(MemoryCaseScenario, unittest.IsolatedAsyncioTestCase):
    scripted = False

    def setUp(self):
        self.profiles = install_fake_profiles(self)
        self.addCleanup(self.report)
        self.currently: MemoryCase | None = None

    def user(self, label: str) -> str:
        return f"{label}-{os.urandom(3).hex()}"

    def profile(self, user_id):
        return self.profiles.load(user_id)

    def seed(self, user_id, patch_):
        self.profiles.save(user_id, patch_)

    def recorded_texts(self, user_id):
        return [c["feedback_text"] for c in self.profiles.feedback_calls if c["user_id"] == user_id]

    def use_curator(self, model):
        raise AssertionError("the live layer uses the real curator model")

    def check_notes(self, case, before, after, feedback):
        super().check_notes(case, before, after, feedback)
        result = judge_notes(feedback, _notes(before), _notes(after))
        self.assertTrue(result.passed, result.failing())

    def report(self):
        if FAILURES:
            print("live memory e2e failures by case:", dict(FAILURES), f"of {TRIALS} trials")


def _live_test(case: MemoryCase):
    async def test(self):
        for trial in range(TRIALS):
            with self.subTest(case=case.id, trial=trial):
                try:
                    await self.check_case(case)
                except AssertionError:
                    FAILURES[case.id] += 1
                    raise

    return test


for _case in CASES:
    setattr(MemoryE2ELive, f"test_{_case.id}", _live_test(_case))


if __name__ == "__main__":
    unittest.main()

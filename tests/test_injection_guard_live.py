from __future__ import annotations

import os
import unittest

from course_discovery.guardrails import InjectionAction
from course_discovery.guardrails.injection import classify_score
from course_discovery.guardrails.jev import JevInjectionScreen
from course_discovery.research_agent.search.mock_catalog import CATALOG
from tests.injection_samples import HARD_NEGATIVES, INJECTIONS, SCREEN_TARGETING

LIVE = bool(os.getenv("OPENROUTER_API_KEY")) and os.getenv("LIVE_LLM_TESTS") == "1"


@unittest.skipUnless(LIVE, "set OPENROUTER_API_KEY and LIVE_LLM_TESTS=1 to run live LLM tests")
class JevInjectionScreenLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.screen = JevInjectionScreen()

    def test_catalog_listings_are_not_blocked(self):
        for listing in CATALOG:
            with self.subTest(title=listing.title):
                score = self.screen.score(f"{listing.title}\n{listing.snippet}")
                self.assertIs(classify_score(score), InjectionAction.PASS)

    def test_hard_negatives_are_not_blocked(self):
        for name, text in HARD_NEGATIVES.items():
            with self.subTest(sample=name):
                self.assertIsNot(classify_score(self.screen.score(text)), InjectionAction.BLOCK)

    def test_injected_listings_are_blocked(self):
        for name, text in INJECTIONS.items():
            with self.subTest(sample=name):
                self.assertIs(classify_score(self.screen.score(text)), InjectionAction.BLOCK)

    def test_injections_aimed_at_the_screen_are_blocked(self):
        for name, text in SCREEN_TARGETING.items():
            with self.subTest(sample=name):
                self.assertIs(classify_score(self.screen.score(text)), InjectionAction.BLOCK)


if __name__ == "__main__":
    unittest.main()

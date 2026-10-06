from __future__ import annotations

import os
import time
import unittest

from langchain_core.callbacks import BaseCallbackHandler

from course_discovery.app.llm import TAGGER_MODEL, build_llm
from course_discovery.research_agent.search.mock_catalog import CATALOG
from course_discovery.research_agent.tagging.tagger import CourseForTagging, tag_course

LIVE = bool(os.getenv("OPENROUTER_API_KEY")) and os.getenv("LIVE_LLM_TESTS") == "1"


class UsageRecorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def on_llm_end(self, response, **kwargs) -> None:
        message = getattr(response.generations[0][0], "message", None)
        usage = getattr(message, "usage_metadata", None) or {}
        served = (getattr(message, "response_metadata", None) or {}).get("model_name")
        self.calls.append(
            {
                "served": served,
                "input": usage.get("input_tokens") or 0,
                "cached": (usage.get("input_token_details") or {}).get("cache_read") or 0,
            }
        )


@unittest.skipUnless(LIVE, "set OPENROUTER_API_KEY and LIVE_LLM_TESTS=1 to run live LLM tests")
class TaggingCacheLiveTests(unittest.TestCase):
    def test_prefix_is_cached_across_courses(self) -> None:
        llm = build_llm("tagger", model=TAGGER_MODEL, fallback_models=[])
        recorder = UsageRecorder()
        results = []
        for listing in CATALOG:
            course = CourseForTagging(listing.title, listing.snippet, tuple(listing.modules))
            started = time.perf_counter()
            results.append(tag_course(llm, course, config={"callbacks": [recorder]}))
            print(f"{listing.title[:40]!r} {time.perf_counter() - started:.1f}s", flush=True)

        report = [(c["served"], c["input"], c["cached"]) for c in recorder.calls]
        print("tagger usage (served, input, cached):", report)

        self.assertEqual(len(recorder.calls), len(CATALOG))
        self.assertTrue(all(c["served"].startswith(TAGGER_MODEL) for c in recorder.calls), report)
        warm = recorder.calls[1:]
        hits = [c for c in warm if c["cached"] > 0]
        self.assertGreaterEqual(len(hits), len(warm) - 1, report)
        self.assertTrue(all(c["cached"] >= 0.6 * c["input"] for c in hits), report)
        self.assertTrue(any(tags.tags for tagged in results for tags in tagged))


if __name__ == "__main__":
    unittest.main()

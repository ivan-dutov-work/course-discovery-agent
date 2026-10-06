"""Score the catalog, hard negatives and injection samples with the live screen and print a table.

Needs OPENROUTER_API_KEY. Run from the repo root:
    PYTHONPATH=. uv run python scripts/injection_score_table.py
"""

from __future__ import annotations

import statistics
import time

from course_discovery.guardrails.injection import classify_score
from course_discovery.guardrails.jev import JevInjectionScreen
from course_discovery.research_agent.search.mock_catalog import CATALOG
from tests.injection_samples import GROUPS


def main() -> None:
    screen = JevInjectionScreen()
    groups = {"catalog": {listing.title: f"{listing.title}\n{listing.snippet}" for listing in CATALOG}}
    groups.update(GROUPS)

    latencies: list[float] = []
    for group, samples in groups.items():
        scores: list[float] = []
        print(f"\n{group} ({len(samples)})")
        for name, text in samples.items():
            started = time.perf_counter()
            score = screen.score(text)
            latencies.append((time.perf_counter() - started) * 1000)
            scores.append(score)
            print(f"  {score:.2f}  {classify_score(score).value:<6}  {name[:60]}")
        print(f"  range {min(scores):.2f} to {max(scores):.2f}")

    print(
        f"\nlatency ms: median {statistics.median(latencies):.0f}, max {max(latencies):.0f} "
        f"over {len(latencies)} calls"
    )


if __name__ == "__main__":
    main()

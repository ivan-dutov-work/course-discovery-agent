"""Score the scripted rows with the live Decisions API and print score, class and latency.

Needs OPENROUTER_API_KEY. Run from the repo root:
    PYTHONPATH=. uv run python scripts/thread_selector_probe.py
"""

from __future__ import annotations

import os
import statistics
import time

from course_discovery.conversation import JevThreadSelector
from tests.thread_selector_rows import ROWS


def main() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        print("not run: OPENROUTER_API_KEY is not set")
        return
    selector = JevThreadSelector()
    latencies: list[float] = []
    for row in ROWS:
        if row.parked is None or not row.message.strip():
            continue
        started = time.perf_counter()
        result = selector.select(row.message, row.parked)
        latencies.append((time.perf_counter() - started) * 1000)
        score = "none" if result.score is None else f"{result.score:.2f}"
        print(f"  {score:>5}  {result.decision.value:<10}  want {row.expected.value:<10}  {row.name}")
    print(f"\nlatency ms: median {statistics.median(latencies):.0f}, max {max(latencies):.0f} over {len(latencies)} calls")


if __name__ == "__main__":
    main()

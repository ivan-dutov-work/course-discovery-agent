from __future__ import annotations

import json
import unittest

from course_discovery.domain.contract import STATE_SCHEMA_VERSION
from tests.contract_snapshot import SNAPSHOT, current

HOW_TO_FIX = (
    "The checkpoint contract changed. Bump STATE_SCHEMA_VERSION, run "
    "`uv run python -m tests.contract_snapshot`, add a fixture for the new version under "
    "tests/fixtures/checkpoints/ and record the change in specs/DECISIONS.md."
)


class StateContractTests(unittest.TestCase):
    def test_snapshot_version_matches_the_constant(self):
        recorded = json.loads(SNAPSHOT.read_text())
        self.assertEqual(recorded["version"], STATE_SCHEMA_VERSION, HOW_TO_FIX)

    def test_compiled_graphs_match_the_snapshot(self):
        recorded = json.loads(SNAPSHOT.read_text())
        self.assertEqual(recorded, current(), HOW_TO_FIX)


if __name__ == "__main__":
    unittest.main()

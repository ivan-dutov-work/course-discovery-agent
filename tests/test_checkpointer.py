from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from course_discovery.domain import models as domain_models
from course_discovery.domain.models import (
    DeliveryStatus,
    ResearchPlan,
    ResearchRunMetrics,
    RoutingAction,
    SearchFilters,
)
from course_discovery.persistence.checkpointer import (
    build_serde,
    msgpack_allowlist,
    open_checkpointer,
)


class SerdeTests(unittest.TestCase):
    def test_allowlist_covers_models_and_enums(self):
        allowed = {name for _, name in msgpack_allowlist()}
        self.assertLessEqual(
            {"SearchFilters", "ResearchRunMetrics", "RoutingAction", "DeliveryStatus"}, allowed
        )

    def test_round_trip_restores_types_with_no_warnings(self):
        serde = build_serde()
        values = [
            SearchFilters(topic="python"),
            ResearchRunMetrics(),
            RoutingAction.PUBLISH,
            DeliveryStatus.QUEUED,
        ]
        with self.assertNoLogs("langgraph.checkpoint.serde.jsonplus", "WARNING"):
            for value in values:
                restored = serde.loads_typed(serde.dumps_typed(value))
                self.assertIs(type(restored), type(value))
                self.assertEqual(restored, value)

    def test_plan_checkpointed_with_the_removed_cache_query_field_still_loads(self):
        legacy = type(
            "ResearchPlan",
            (ResearchPlan,),
            {"__module__": domain_models.__name__, "__annotations__": {"cache_query": str}},
        )
        serde = build_serde()
        payload = serde.dumps_typed(legacy(topic="python", cache_query="python"))
        self.assertIn(b"cache_query", payload[1])

        with self.assertNoLogs("langgraph.checkpoint.serde.jsonplus", "WARNING"):
            restored = serde.loads_typed(payload)

        self.assertIs(type(restored), ResearchPlan)
        self.assertEqual(restored.topic, "python")
        self.assertNotIn("cache_query", restored.model_dump())


class OpenCheckpointerTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_memory_saver_without_database_url(self):
        with patch.dict(os.environ, {}, clear=True):
            async with open_checkpointer() as saver:
                self.assertIsInstance(saver, MemorySaver)

    async def test_uses_postgres_saver_when_database_url_is_set(self):
        url = os.getenv("TEST_DATABASE_URL")
        if not url:
            self.skipTest("TEST_DATABASE_URL not set")
        async with open_checkpointer(url) as saver:
            self.assertIsInstance(saver, AsyncPostgresSaver)


if __name__ == "__main__":
    unittest.main()

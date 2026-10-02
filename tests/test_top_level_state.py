from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from course_discovery.app import cli
from course_discovery.app.cli import _initial_state
from course_discovery.app.gateway import _fallback_parse_filters
from course_discovery.domain.models import DeliveryStatus, RoutingAction, SearchFilters
from course_discovery.domain.state import AgentState, ResearchState
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.workflows.outer_graph import build_graph
from course_discovery.workflows.research_graph import build_research_graph

QUERY = "Find free Python courses with certificate for beginners"

REMOVED_CHANNELS = {
    "run_id",
    "iteration_count",
    "max_iterations",
    "max_research_iterations",
    "cache_hits",
    "tavily_calls",
}
TOP_LEVEL_CHANNELS = {
    "user_id",
    "user_query",
    "search_filters",
    "manager_feedback",
    "feedback_history",
    "routing_decision",
    "rewrite_instructions",
    "valid_courses",
    "digest",
    "publish_status",
    "discard_reason",
    "memory_update",
}


def _config(thread_id: str, **budgets) -> dict:
    return {"configurable": {"thread_id": thread_id, **budgets}, "recursion_limit": 60}


class TopLevelStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        self.store = InMemoryOutboxStore()
        set_gateway(InlineGateway(self.store, OutboxWorker(self.store, {"publish_digest": lambda _: None})))
        self.graph = build_graph()

    async def _top_level(self, inputs, config) -> list[str]:
        visited: list[str] = []
        async for chunk in self.graph.astream(inputs, config, stream_mode="updates"):
            visited.extend(name for name in chunk if not name.startswith("__"))
        return visited

    async def _values(self, config) -> dict:
        return (await self.graph.aget_state(config)).values

    async def _review(self, config, feedback: str) -> list[str]:
        await self.graph.aupdate_state(config, {"manager_feedback": feedback})
        return await self._top_level(None, config)

    async def _is_paused(self, config) -> bool:
        return (await self.graph.aget_state(config)).next == ("await_human_review",)

    def test_removed_channels_are_gone_and_the_top_level_is_exactly_the_declared_set(self):
        self.assertFalse(REMOVED_CHANNELS & set(AgentState.__annotations__))
        self.assertFalse(REMOVED_CHANNELS & set(_initial_state(QUERY)))
        self.assertLessEqual(TOP_LEVEL_CHANNELS, set(AgentState.__annotations__))
        self.assertEqual(len(AgentState.__annotations__), 28)
        self.assertEqual(set(AgentState.__annotations__) - set(ResearchState.__annotations__), {"feedback_history"})

    async def test_publish_path_visits_every_node_and_every_channel_has_its_value(self):
        config = _config("tls-publish")

        first = await self._top_level(_initial_state(QUERY), config)
        paused = await self._values(config)

        self.assertEqual(first, ["parse_user_request", "course_research"])
        self.assertTrue(await self._is_paused(config))
        self.assertEqual(paused["user_id"], "cli-user")
        self.assertEqual(paused["user_query"], QUERY)
        self.assertIn("python", paused["search_filters"].topic.lower())
        self.assertIsNone(paused["routing_decision"])
        self.assertIsNone(paused["manager_feedback"])
        self.assertEqual(paused["feedback_history"], [])
        self.assertTrue(paused["valid_courses"])
        self.assertTrue(paused["digest"])
        self.assertIsNone(paused["publish_status"])
        self.assertIsNone(paused["discard_reason"])

        second = await self._review(config, "approve")
        final = await self._values(config)

        self.assertEqual(
            second,
            [
                "await_human_review",
                "interpret_review_feedback",
                "send_approved_courses",
                "record_review_outcome",
                "curate_user_memory",
            ],
        )
        self.assertEqual((await self.graph.aget_state(config)).next, ())
        self.assertEqual(final["routing_decision"], RoutingAction.PUBLISH)
        self.assertEqual(final["feedback_history"], ["approve"])
        self.assertIsNone(final["manager_feedback"])
        self.assertIsNone(final["rewrite_instructions"])
        self.assertEqual(final["publish_status"], DeliveryStatus.DELIVERED)
        self.assertIsNone(final["discard_reason"])
        self.assertEqual(final["memory_update"], "skipped:no_feedback")
        stored = self.store.get("publish:tls-publish")
        self.assertEqual(stored.effect.payload["run_id"], "tls-publish")
        self.assertEqual(stored.effect.payload["digest"], final["digest"])
        self.assertEqual(
            stored.effect.payload["course_urls"], [course.url for course in final["valid_courses"]]
        )

    async def test_rewrite_then_approve_keeps_one_history_entry_per_round(self):
        config = _config("tls-rewrite")
        await self._top_level(_initial_state(QUERY), config)

        visited = await self._review(config, "rewrite: shorter digest please")
        mid = await self._values(config)

        self.assertEqual(visited[:2], ["await_human_review", "interpret_review_feedback"])
        self.assertIn("course_research", visited)
        self.assertTrue(await self._is_paused(config))
        self.assertEqual(mid["routing_decision"], RoutingAction.REWRITE)
        self.assertEqual(mid["rewrite_instructions"], "rewrite: shorter digest please")
        self.assertEqual(mid["feedback_history"], ["rewrite: shorter digest please"])
        self.assertIsNone(mid["manager_feedback"])

        await self._review(config, "approve")
        final = await self._values(config)

        self.assertEqual(final["feedback_history"], ["rewrite: shorter digest please", "approve"])
        self.assertIsNone(final["rewrite_instructions"])
        self.assertEqual(final["publish_status"], DeliveryStatus.DELIVERED)
        self.assertEqual(final["memory_update"], "skipped:no_llm")

    async def test_augment_reenters_research_and_clears_the_inbox(self):
        config = _config("tls-augment")
        await self._top_level(_initial_state(QUERY), config)

        visited = []
        await self.graph.aupdate_state(config, {"manager_feedback": "augment: more courses"})
        async for _, chunk in self.graph.astream(None, config, stream_mode="updates", subgraphs=True):
            visited.extend(name for name in chunk if not name.startswith("__"))
        values = await self._values(config)

        self.assertEqual(
            visited[:3], ["await_human_review", "interpret_review_feedback", "plan_gap_search"]
        )
        self.assertEqual(values["routing_decision"], RoutingAction.AUGMENT)
        self.assertIsNone(values["manager_feedback"])
        self.assertEqual(values["feedback_history"], ["augment: more courses"])

    async def test_reset_reparses_from_the_last_history_entry_and_consumes_the_signal(self):
        config = _config("tls-reset")
        await self._top_level(_initial_state(QUERY), config)
        parsed: list[str] = []

        def record(query):
            parsed.append(query)
            return _fallback_parse_filters(query)

        with patch("course_discovery.app.gateway._parse_filters", side_effect=record):
            visited = await self._review(config, "reset: advanced courses")
        values = await self._values(config)

        self.assertEqual(
            visited[:3], ["await_human_review", "interpret_review_feedback", "parse_user_request"]
        )
        self.assertTrue(parsed[0].startswith(QUERY))
        self.assertTrue(parsed[0].endswith("Reset overrides: reset: advanced courses"))
        self.assertIsNone(values["routing_decision"])
        self.assertIsNone(values["manager_feedback"])
        self.assertEqual(values["feedback_history"], ["reset: advanced courses"])
        self.assertTrue(await self._is_paused(config))

    async def test_discard_by_feedback_ends_through_the_outcome_record_without_publishing(self):
        config = _config("tls-discard")
        await self._top_level(_initial_state(QUERY), config)

        visited = await self._review(config, "discard")
        final = await self._values(config)

        self.assertEqual(
            visited,
            [
                "await_human_review",
                "interpret_review_feedback",
                "discard_run",
                "record_review_outcome",
                "curate_user_memory",
            ],
        )
        self.assertEqual(final["routing_decision"], RoutingAction.DISCARD)
        self.assertEqual(final["discard_reason"], "Discarded by manager")
        self.assertIsNone(final["publish_status"])
        self.assertIsNone(final["manager_feedback"])
        self.assertIsNone(self.store.get("publish:tls-discard"))

    async def test_gateway_failure_signals_through_discard_reason_alone(self):
        config = _config("tls-gateway")
        state = _initial_state(QUERY)
        del state["user_query"]

        visited = await self._top_level(state, config)
        final = await self._values(config)

        self.assertEqual(
            visited, ["parse_user_request", "discard_run", "record_review_outcome", "curate_user_memory"]
        )
        self.assertIn("Gateway failed (KeyError)", final["discard_reason"])
        self.assertIsNone(final["routing_decision"])
        self.assertIsNone(final["error"])
        self.assertIsNone(final["publish_status"])
        self.assertEqual((await self.graph.aget_state(config)).next, ())

    async def test_review_budget_comes_from_config_and_counts_history(self):
        config = _config("tls-budget", max_review_rounds=1)
        await self._top_level(_initial_state(QUERY), config)

        await self._review(config, "rewrite: one")
        self.assertTrue(await self._is_paused(config))
        visited = await self._review(config, "rewrite: two")
        final = await self._values(config)

        self.assertEqual(visited[:3], ["await_human_review", "interpret_review_feedback", "discard_run"])
        self.assertEqual(final["discard_reason"], "max_iterations reached")
        self.assertEqual(final["routing_decision"], RoutingAction.DISCARD)
        self.assertEqual(final["feedback_history"], ["rewrite: one", "rewrite: two"])
        self.assertEqual((await self.graph.aget_state(config)).next, ())

    async def test_approval_in_the_last_allowed_round_still_publishes(self):
        config = _config("tls-last-round", max_review_rounds=1)
        await self._top_level(_initial_state(QUERY), config)

        await self._review(config, "approve")
        final = await self._values(config)

        self.assertEqual(final["publish_status"], DeliveryStatus.DELIVERED)
        self.assertIsNone(final["discard_reason"])

    async def test_gateway_failure_during_reset_discards_the_run(self):
        config = _config("tls-reset-fail")
        await self._top_level(_initial_state(QUERY), config)

        with patch("course_discovery.app.gateway._parse_filters", side_effect=ValueError("bad")):
            visited = await self._review(config, "reset: something else")
        final = await self._values(config)

        self.assertEqual(
            visited,
            [
                "await_human_review",
                "interpret_review_feedback",
                "parse_user_request",
                "discard_run",
                "record_review_outcome",
                "curate_user_memory",
            ],
        )
        self.assertIn("Gateway failed (ValueError)", final["discard_reason"])
        self.assertIsNone(final["publish_status"])
        self.assertEqual((await self.graph.aget_state(config)).next, ())

    async def test_budget_is_supplied_per_invocation_and_not_persisted_in_the_checkpoint(self):
        await self._top_level(_initial_state(QUERY), _config("tls-resume", max_review_rounds=1))
        await self._review(_config("tls-resume", max_review_rounds=1), "rewrite: one")

        await self._review(_config("tls-resume"), "rewrite: two")

        self.assertTrue(await self._is_paused(_config("tls-resume")))
        self.assertEqual((await self._values(_config("tls-resume")))["routing_decision"], RoutingAction.REWRITE)

    async def test_research_iteration_budget_comes_from_config(self):
        research = build_research_graph()

        async def replans(thread: str, **budgets) -> int:
            state = _initial_state(QUERY)
            state["search_filters"] = SearchFilters(
                topic="python", max_price=0, level="beginner", content_languages=["fr"]
            )
            count = 0
            async for chunk in research.astream(state, _config(thread, **budgets), stream_mode="updates"):
                count += "plan_gap_search" in chunk
            return count

        self.assertEqual(await replans("tls-research-default"), 2)
        self.assertEqual(await replans("tls-research-one", max_research_iterations=1), 1)
        self.assertEqual(await replans("tls-research-zero", max_research_iterations=0), 0)


class CliTerminationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))

    async def test_gateway_failure_ends_the_cli_without_asking_for_feedback(self):
        prompts: list[str] = []

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            return ""

        with (
            patch("builtins.input", fake_input),
            patch("course_discovery.app.gateway._parse_filters", side_effect=ValueError("bad")),
        ):
            await cli._run(build_graph())

        self.assertEqual(len(prompts), 1)

    async def test_discard_ends_the_cli_after_one_feedback_prompt(self):
        answers = iter(["", "discard"])
        with patch("builtins.input", lambda prompt="": next(answers)):
            await cli._run(build_graph())
        self.assertEqual(list(answers), [])

    async def test_approve_ends_the_cli_after_one_feedback_prompt(self):
        answers = iter(["", "approve"])
        with patch("builtins.input", lambda prompt="": next(answers)):
            await cli._run(build_graph())
        self.assertEqual(list(answers), [])


if __name__ == "__main__":
    unittest.main()

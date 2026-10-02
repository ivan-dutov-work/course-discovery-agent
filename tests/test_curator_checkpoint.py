from __future__ import annotations

import base64
import os
import unittest
import uuid
from unittest.mock import patch

from course_discovery.app.cli import _initial_state
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.persistence.encryption import KEYS_ENV
from course_discovery.workflows.outer_graph import build_graph
from tests.curator_stubs import FakeProfiles, ScriptedModel, call, install_curator, reply


def curator(messages, _turn):
    tools = sum(1 for m in messages if m.type == "tool")
    if tools == 0:
        return reply(call("read_profile"))
    if tools == 1:
        patch_ = {"add": {"avoided_providers": ["udemy"]}}
        return reply(call("propose_patch", scope="durable", reason="stated", patch=patch_))
    return reply(call("finish", reason="done"))


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class CuratorCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_curator_channels_survive_the_encrypted_postgres_checkpointer(self):
        env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
                KEYS_ENV: "k1:" + base64.b64encode(os.urandom(32)).decode(),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))
        profiles = FakeProfiles()
        install_curator(self, ScriptedModel(curator), profiles)

        thread_id = f"curator-{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}
        async with open_checkpointer() as saver:
            graph = build_graph(checkpointer=saver)
            await graph.ainvoke({**_initial_state("python courses"), "user_id": "ckpt-user"}, config)
            await graph.aupdate_state(config, {"manager_feedback": "discard: I'm done with udemy"})
            await graph.ainvoke(None, config)
            state = await graph.aget_state(config)
            await saver.adelete_thread(thread_id)

        self.assertEqual(state.values["memory_update"], "committed")
        self.assertEqual(profiles.memories["ckpt-user"].avoided_providers, ["udemy"])
        self.assertEqual(state.values["feedback_history"], ["discard: I'm done with udemy"])

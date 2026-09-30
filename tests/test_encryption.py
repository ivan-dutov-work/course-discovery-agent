from __future__ import annotations

import base64
import inspect
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from psycopg.rows import dict_row

from course_discovery.domain.models import RoutingAction, SearchFilters
from course_discovery.persistence.checkpointer import build_serde, open_checkpointer
from course_discovery.persistence.encryption import (
    INHERITED,
    INLINE_PREFIX,
    KEYS_ENV,
    REQUIRED_ENV,
    AesGcmCipher,
    EncryptionConfigError,
    SealingSaver,
    keys_from_env,
    parse_keys,
)


def make_key() -> bytes:
    return os.urandom(32)


def spec(*pairs: tuple[str, bytes]) -> str:
    return ",".join(f"{key_id}:{base64.b64encode(key).decode()}" for key_id, key in pairs)


class KeyParsingTests(unittest.TestCase):
    def test_parses_ordered_keys(self):
        k1, k2 = make_key(), make_key()
        self.assertEqual(parse_keys(spec(("new", k1), ("old", k2))), [("new", k1), ("old", k2)])

    def test_rejects_wrong_length(self):
        with self.assertRaises(EncryptionConfigError):
            parse_keys("k1:" + base64.b64encode(b"short").decode())

    def test_rejects_missing_id_and_duplicates(self):
        with self.assertRaises(EncryptionConfigError):
            parse_keys(base64.b64encode(make_key()).decode())
        key = make_key()
        with self.assertRaises(EncryptionConfigError):
            parse_keys(spec(("k1", key), ("k1", key)))

    def test_unset_is_allowed_unless_required(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(keys_from_env(), [])
        with patch.dict(os.environ, {REQUIRED_ENV: "true"}, clear=True):
            with self.assertRaises(EncryptionConfigError):
                keys_from_env()


class CipherTests(unittest.TestCase):
    def test_round_trip_and_random_nonce(self):
        cipher = AesGcmCipher([("k1", make_key())])
        name, blob = cipher.encrypt(b"secret")
        self.assertNotIn(b"secret", blob)
        self.assertEqual(cipher.decrypt(name, blob), b"secret")
        self.assertNotEqual(blob, cipher.encrypt(b"secret")[1])

    def test_rotation_decrypts_old_ciphertext_and_encrypts_with_new_key(self):
        old_key, new_key = make_key(), make_key()
        old_name, old_blob = AesGcmCipher([("k1", old_key)]).encrypt(b"secret")
        rotated = AesGcmCipher([("k2", new_key), ("k1", old_key)])
        self.assertEqual(rotated.decrypt(old_name, old_blob), b"secret")
        self.assertEqual(rotated.encrypt(b"x")[0], "aesgcm.k2")

    def test_unknown_key_id_and_tampering_fail(self):
        name, blob = AesGcmCipher([("k1", make_key())]).encrypt(b"secret")
        with self.assertRaises(EncryptionConfigError):
            AesGcmCipher([("k2", make_key())]).decrypt(name, blob)
        same = AesGcmCipher([("k1", make_key())])
        with self.assertRaises(Exception):
            same.decrypt(name, blob)

    def test_text_sealing_round_trip(self):
        cipher = AesGcmCipher([("k1", make_key())])
        sealed = cipher.seal_text("user-1")
        self.assertNotIn("user-1", sealed)
        self.assertEqual(cipher.open_text(sealed), "user-1")


class SerdeTests(unittest.TestCase):
    def test_serde_encrypts_and_keeps_type_allowlist(self):
        serde = build_serde(AesGcmCipher([("k1", make_key())]))
        self.assertIsInstance(serde, EncryptedSerializer)
        for value in (SearchFilters(topic="python"), RoutingAction.PUBLISH):
            typ, blob = serde.dumps_typed(value)
            self.assertIn("+aesgcm.k1", typ)
            self.assertNotIn(b"python", blob)
            self.assertEqual(serde.loads_typed((typ, blob)), value)

    def test_serde_reads_legacy_plaintext(self):
        plain = build_serde(None)
        encrypted = build_serde(AesGcmCipher([("k1", make_key())]))
        self.assertEqual(encrypted.loads_typed(plain.dumps_typed(SearchFilters(topic="x"))).topic, "x")


class SealingSaverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.inner = InMemorySaver()
        self.saver = SealingSaver(self.inner, AesGcmCipher([("k1", make_key())]))
        self.config = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"user_id": "user-secret-123", "iteration": 2}
        checkpoint["channel_versions"] = {"user_id": "1", "iteration": "1"}
        self.checkpoint = checkpoint

    async def _put(self):
        await self.saver.aput(
            self.config, self.checkpoint, {"source": "input", "step": 0}, self.checkpoint["channel_versions"]
        )

    async def test_inner_saver_only_ever_sees_sealed_strings(self):
        await self._put()
        raw = (await self.inner.aget_tuple(self.config)).checkpoint["channel_values"]
        self.assertTrue(raw["user_id"].startswith(INLINE_PREFIX))
        self.assertNotIn("user-secret-123", raw["user_id"])
        self.assertEqual(raw["iteration"], 2)

    async def test_reads_return_plaintext(self):
        await self._put()
        latest = await self.saver.aget_tuple(self.config)
        listed = [item async for item in self.saver.alist(self.config)]
        checkpoint = await self.saver.aget(self.config)
        for values in (
            latest.checkpoint["channel_values"],
            listed[0].checkpoint["channel_values"],
            checkpoint["channel_values"],
        ):
            self.assertEqual(values["user_id"], "user-secret-123")

    async def test_missing_checkpoint_reads_as_none(self):
        self.assertIsNone(await self.saver.aget_tuple(self.config))

    async def test_delete_thread_reaches_the_inner_saver(self):
        await self._put()
        await self.saver.adelete_thread("t1")
        self.assertIsNone(await self.inner.aget_tuple(self.config))

    def test_every_public_saver_method_is_transformed_or_delegated(self):
        public = {
            name
            for name, _ in inspect.getmembers(BaseCheckpointSaver, inspect.isfunction)
            if not name.startswith("_")
        }
        self.assertEqual({n for n in public - INHERITED if n not in SealingSaver.__dict__}, set())


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class PostgresEncryptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.thread_id = f"enc-{uuid.uuid4()}"
        self.config = {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": ""}}
        self.env = patch.dict(os.environ, {KEYS_ENV: spec(("k1", make_key()))})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _checkpoint(self):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {
            "user_id": "user-secret-123",
            "user_query": "python for <PERSON>",
            "iteration": 2,
            "filters": SearchFilters(topic="python"),
        }
        versions = {name: "1" for name in checkpoint["channel_values"]}
        checkpoint["channel_versions"] = versions
        return checkpoint, versions

    def _plaintext_rows(self, needle: str) -> dict[str, int]:
        predicates = {
            "checkpoints.checkpoint": ("checkpoints", "strpos(checkpoint::text, %s) > 0"),
            "checkpoints.metadata": ("checkpoints", "strpos(metadata::text, %s) > 0"),
            "checkpoint_blobs.blob": (
                "checkpoint_blobs",
                "position(convert_to(%s, 'UTF8') in blob) > 0",
            ),
            "checkpoint_writes.blob": (
                "checkpoint_writes",
                "position(convert_to(%s, 'UTF8') in blob) > 0",
            ),
        }
        with psycopg.connect(self.url, row_factory=dict_row) as conn:
            return {
                label: conn.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE thread_id = %s AND {predicate}",
                    (self.thread_id, needle),
                ).fetchone()["n"]
                for label, (table, predicate) in predicates.items()
            }

    async def test_no_plaintext_at_rest_and_values_round_trip(self):
        checkpoint, versions = self._checkpoint()
        async with open_checkpointer(self.url) as saver:
            await saver.aput(self.config, checkpoint, {"source": "input", "step": 0}, versions)
            await saver.aput_writes(
                {"configurable": {**self.config["configurable"], "checkpoint_id": checkpoint["id"]}},
                [("user_id", "user-secret-123")],
                "task-1",
            )
            restored = await saver.aget_tuple(self.config)
            try:
                values = restored.checkpoint["channel_values"]
                self.assertEqual(values["user_id"], "user-secret-123")
                self.assertEqual(values["user_query"], "python for <PERSON>")
                self.assertEqual(values["iteration"], 2)
                self.assertEqual(values["filters"], SearchFilters(topic="python"))
                for needle in ("user-secret-123", "python for"):
                    self.assertEqual(sum(self._plaintext_rows(needle).values()), 0, needle)
            finally:
                await saver.adelete_thread(self.thread_id)

    async def test_stock_saver_with_encrypted_serde_leaves_inline_strings_plaintext(self):
        checkpoint, versions = self._checkpoint()
        cipher = AesGcmCipher([("k1", make_key())])
        async with AsyncPostgresSaver.from_conn_string(
            self.url, serde=build_serde(cipher)
        ) as saver:
            await saver.aput(self.config, checkpoint, {"source": "input", "step": 0}, versions)
            try:
                inline = self._plaintext_rows("user-secret-123")
                self.assertEqual(inline["checkpoints.checkpoint"], 1)
                self.assertEqual(inline["checkpoint_blobs.blob"], 0)
            finally:
                await saver.adelete_thread(self.thread_id)

    async def test_legacy_plaintext_checkpoints_stay_readable_after_enabling_encryption(self):
        checkpoint, versions = self._checkpoint()
        async with AsyncPostgresSaver.from_conn_string(self.url, serde=build_serde(None)) as plain:
            await plain.aput(self.config, checkpoint, {"source": "input", "step": 0}, versions)
        async with open_checkpointer(self.url) as saver:
            try:
                values = (await saver.aget_tuple(self.config)).checkpoint["channel_values"]
                self.assertEqual(values["user_id"], "user-secret-123")
                self.assertEqual(values["filters"], SearchFilters(topic="python"))
            finally:
                await saver.adelete_thread(self.thread_id)


if __name__ == "__main__":
    unittest.main()

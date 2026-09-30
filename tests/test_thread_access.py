from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

import psycopg

from course_discovery.privacy import ThreadAccessError, authorize_thread, register_thread


class NoRegistryTests(unittest.TestCase):
    def test_without_database_the_check_is_skipped(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            authorize_thread("anyone", "any-thread")


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class ThreadAccessTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        tag = uuid.uuid4().hex[:8]
        self.owner, self.other = f"owner-{tag}", f"other-{tag}"
        self.thread = f"t-{uuid.uuid4()}"
        self.env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        self.env.start()
        register_thread(self.owner, self.thread)

    def tearDown(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("DELETE FROM run_threads WHERE thread_id = %s", (self.thread,))
        self.env.stop()

    def test_owner_is_allowed(self):
        authorize_thread(self.owner, self.thread)

    def test_other_user_is_refused(self):
        with self.assertRaises(ThreadAccessError):
            authorize_thread(self.other, self.thread)

    def test_unknown_thread_is_refused_like_a_foreign_one(self):
        with self.assertRaises(ThreadAccessError) as unknown:
            authorize_thread(self.owner, f"missing-{uuid.uuid4()}")
        with self.assertRaises(ThreadAccessError) as foreign:
            authorize_thread(self.other, self.thread)
        self.assertEqual(str(unknown.exception), str(foreign.exception))

    def test_missing_caller_is_refused(self):
        with self.assertRaises(ThreadAccessError):
            authorize_thread(None, self.thread)

    def test_registering_a_taken_thread_does_not_transfer_it(self):
        register_thread(self.other, self.thread)
        authorize_thread(self.owner, self.thread)
        with self.assertRaises(ThreadAccessError):
            authorize_thread(self.other, self.thread)

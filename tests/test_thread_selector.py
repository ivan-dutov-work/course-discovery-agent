from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx

from course_discovery.conversation import (
    Decision,
    JevThreadSelector,
    ParkedThread,
    StubThreadSelector,
    select_for_user,
)
from course_discovery.conversation import selector as selector_module
from course_discovery.domain.models import SearchFilters
from course_discovery.guardrails import set_redactor
from tests.thread_selector_rows import DAY, ROWS


def _answer(value: float) -> httpx.Response:
    return httpx.Response(200, json={"answers": {selector_module.QUESTION: {"noul": value}}})


def _signal(value: float) -> httpx.Response:
    return httpx.Response(200, json={"answers": {selector_module.SIGNAL_QUESTION: {"noul": value}}})


def _selector(handler, signal=0.0, seen=None) -> JevThreadSelector:
    def route(request: httpx.Request) -> httpx.Response:
        if selector_module.SIGNAL_QUESTION in json.loads(request.content)["questions"]:
            if seen is not None:
                seen.append(request)
            return signal(request) if callable(signal) else _signal(signal)
        return handler(request)

    return JevThreadSelector("test-key", client=httpx.Client(transport=httpx.MockTransport(route)))


class FakeRedactor:
    def redact(self, text: str) -> str:
        return text.replace("anna.k@example.com", "<EMAIL_ADDRESS>").replace(
            "+380 67 123 45 67", "<PHONE_NUMBER>"
        )


class ScriptedTableTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_redactor, None)
        set_redactor(FakeRedactor())

    def test_every_row_gets_the_expected_class(self):
        self.assertGreaterEqual(len(ROWS), 12)
        for row in ROWS:
            with self.subTest(row.name), mock.patch.object(
                selector_module, "record_degradation"
            ) as degradation:
                scores = {row.message: row.stub_score}
                result = _selector(
                    lambda request: _answer(_stub_score(request, scores)), row.signal_score
                ).select(row.message, row.parked)
                self.assertEqual(result.decision, row.expected)
                if row.degraded:
                    degradation.assert_called_once_with("thread_selector", row.degraded)
                else:
                    degradation.assert_not_called()

    def test_nothing_parked_or_blank_message_makes_no_request(self):
        calls = []
        selector = _selector(lambda request: calls.append(request) or _answer(0.99))
        for row in (r for r in ROWS if r.name in {"nothing parked", "blank message"}):
            self.assertEqual(selector.select(row.message, row.parked).decision, Decision.NEW_TOPIC)
        self.assertEqual(calls, [])

    def test_request_carries_topic_filters_and_age(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _answer(0.9)

        parked = ParkedThread("python", {"level": "beginner"}, 2 * DAY)
        _selector(handler).select("cheaper", parked)
        state = seen["body"]["state"]
        self.assertEqual(seen["body"]["questions"][selector_module.QUESTION]["type"], "noul")
        self.assertIn("topic: python", state["parked_thread"])
        self.assertIn("level: beginner", state["parked_thread"])
        self.assertIn("2 days ago", state["parked_thread"])
        self.assertEqual(state["new_message"], "cheaper")

    def test_acknowledgement_is_no_signal_and_skips_the_continue_question(self):
        continues = []
        result = _selector(
            lambda request: continues.append(request) or _answer(0.9), signal=0.95
        ).select("thanks!", ParkedThread("python", {}, 60.0))
        self.assertEqual(result.decision, Decision.NO_SIGNAL)
        self.assertEqual(continues, [])

    def test_middle_signal_score_falls_through_to_the_continue_question(self):
        result = _selector(lambda request: _answer(0.9), signal=0.5).select(
            "cheaper", ParkedThread("python", {}, 60.0)
        )
        self.assertEqual(result.decision, Decision.CONTINUE)

    def test_age_alone_does_not_force_a_new_topic(self):
        parked = ParkedThread("python", {}, 30 * DAY)
        result = _selector(lambda request: _answer(0.95)).select("cheaper", parked)
        self.assertEqual(result.decision, Decision.CONTINUE)

    def test_parked_thread_view_keeps_only_non_default_filters(self):
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        view = ParkedThread.from_filters(
            SearchFilters(topic="python", level="beginner"), now - timedelta(hours=5), now
        )
        self.assertEqual(view.topic, "python")
        self.assertEqual(view.filters, {"level": "beginner"})
        self.assertEqual(view.age_seconds, 5 * 3600)


def _stub_score(request: httpx.Request, scores: dict[str, float]) -> float:
    return scores[json.loads(request.content)["state"]["new_message"]]


class FaultInjectionTests(unittest.TestCase):
    PARKED = ParkedThread("python", {}, 600.0)

    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    CASES = {
        "http 500": (lambda request: httpx.Response(500), "selector_failed"),
        "timeout": (_timeout, "selector_failed"),
        "malformed json": (lambda request: httpx.Response(200, text="not json"), "selector_failed"),
        "missing answer": (lambda request: httpx.Response(200, json={"answers": {}}), "selector_failed"),
        "out of range": (lambda request: _answer(1.5), "selector_failed"),
        "middle score": (lambda request: _answer(0.5), "middle_score"),
    }

    def test_every_failure_is_a_new_topic_and_counted(self):
        for name, (handler, reason) in self.CASES.items():
            with self.subTest(name), mock.patch.object(
                selector_module, "record_degradation"
            ) as degradation:
                result = _selector(handler).select("cheaper", self.PARKED)
                self.assertEqual(result.decision, Decision.NEW_TOPIC)
                self.assertEqual(result.reason, reason)
                degradation.assert_called_once_with("thread_selector", reason)

    def test_signal_question_failure_is_a_counted_new_topic(self):
        for name, handler in {
            "http 500": lambda request: httpx.Response(500),
            "malformed": lambda request: httpx.Response(200, text="not json"),
            "out of range": lambda request: _signal(1.5),
        }.items():
            with self.subTest(name), mock.patch.object(
                selector_module, "record_degradation"
            ) as degradation:
                result = _selector(lambda request: _answer(0.99), signal=handler).select(
                    "thanks", self.PARKED
                )
                self.assertEqual(result.decision, Decision.NEW_TOPIC)
                degradation.assert_called_once_with("thread_selector", "selector_failed")

    def test_missing_key_is_a_counted_new_topic(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
            selector_module, "record_degradation"
        ) as degradation:
            result = JevThreadSelector(client=httpx.Client()).select("cheaper", self.PARKED)
        self.assertEqual(result.decision, Decision.NEW_TOPIC)
        degradation.assert_called_once_with("thread_selector", "selector_failed")

    def test_redaction_failure_sends_nothing_and_is_a_new_topic(self):
        class Broken:
            def redact(self, text: str) -> str:
                raise RuntimeError("model missing")

        self.addCleanup(set_redactor, None)
        set_redactor(Broken())
        calls = []
        with mock.patch.object(selector_module, "record_degradation"):
            result = _selector(lambda request: calls.append(request) or _answer(0.99)).select(
                "cheaper", self.PARKED
            )
        self.assertEqual(result.decision, Decision.NEW_TOPIC)
        self.assertEqual(calls, [])


class PrivacyAndOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_redactor, None)
        set_redactor(FakeRedactor())

    def test_email_and_phone_never_leave_the_process(self):
        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content.decode())
            return _answer(0.9)

        signals = []
        _selector(handler, seen=signals).select(
            "mail anna.k@example.com or call +380 67 123 45 67 about cheaper ones",
            ParkedThread("python", {}, 60.0),
        )
        self.assertEqual((len(bodies), len(signals)), (1, 1))
        for value in ("anna.k@example.com", "380 67 123 45 67"):
            for body in (bodies[0], signals[0].content.decode()):
                self.assertNotIn(value, body)

    def test_real_redactor_strips_email_and_phone(self):
        set_redactor(None)
        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content.decode())
            return _answer(0.9)

        _selector(handler).select(
            "mail anna.k@example.com or call +380 67 123 45 67 about cheaper ones",
            ParkedThread("python", {}, 60.0),
        )
        for value in ("anna.k@example.com", "123 45 67"):
            self.assertNotIn(value, bodies[0])

    def test_another_users_thread_never_reaches_the_request(self):
        class Lookup:
            threads = {
                "user-a": ParkedThread("secret-medical-topic", {"max_price": 1.0}, 60.0),
                "user-b": ParkedThread("python", {}, 60.0),
            }
            asked: list[str] = []

            def latest_parked(self, user_id: str):
                self.asked.append(user_id)
                return self.threads.get(user_id)

        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content.decode())
            return _answer(0.9)

        lookup = Lookup()
        select_for_user(_selector(handler), lookup, "user-b", "cheaper")
        self.assertEqual(lookup.asked, ["user-b"])
        self.assertNotIn("secret-medical-topic", bodies[0])
        self.assertIn("topic: python", bodies[0])

    def test_user_without_a_parked_thread_gets_a_new_topic_and_no_request(self):
        class Empty:
            def latest_parked(self, user_id: str):
                return None

        stub = StubThreadSelector(Decision.CONTINUE)
        self.assertEqual(
            select_for_user(stub, Empty(), "user-b", "cheaper").decision, Decision.CONTINUE
        )
        self.assertEqual(stub.seen, [("cheaper", None)])
        calls = []
        result = select_for_user(
            _selector(lambda request: calls.append(1) or _answer(0.9)), Empty(), "user-b", "cheaper"
        )
        self.assertEqual((result.decision, calls), (Decision.NEW_TOPIC, []))


if __name__ == "__main__":
    unittest.main()

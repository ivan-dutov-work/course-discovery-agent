from __future__ import annotations

from datetime import datetime, timedelta, timezone

from course_discovery.domain.models import DeliveryStatus
from course_discovery.effects.gateway import InlineGateway, OutboxGateway
from course_discovery.effects.models import Effect, PermanentEffectError, RecordStatus
from course_discovery.effects.worker import OutboxWorker


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Recorder:
    def __init__(self, *failures: Exception) -> None:
        self.failures = list(failures)
        self.delivered: list[str] = []

    def __call__(self, effect: Effect) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.delivered.append(effect.key)


def effect(key: str = "publish:run-1", kind: str = "publish_digest") -> Effect:
    return Effect(key=key, kind=kind, payload={"digest": "d", "n": 1})


class OutboxContract:
    """Behaviour every OutboxStore must satisfy; subclasses provide make_store()."""

    max_attempts = 3

    def make_store(self):
        raise NotImplementedError

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = self.make_store()

    def worker(self, handler, kind: str = "publish_digest", **kwargs) -> OutboxWorker:
        return OutboxWorker(
            self.store,
            {kind: handler},
            max_attempts=self.max_attempts,
            backoff=lambda attempts: 10.0 * attempts,
            lease_seconds=30.0,
            clock=self.clock,
            **kwargs,
        )

    def test_enqueue_is_idempotent_on_key(self):
        first = self.store.enqueue(effect(), self.clock())
        self.store.claim(self.clock(), 10, 30.0)
        second = self.store.enqueue(effect(), self.clock())

        self.assertEqual(first.status, RecordStatus.QUEUED)
        self.assertEqual(second.status, RecordStatus.IN_PROGRESS)
        self.assertEqual(second.attempts, 1)

    def test_payload_round_trips(self):
        self.store.enqueue(effect(), self.clock())
        self.assertEqual(self.store.get("publish:run-1").effect, effect())

    def test_delivers_on_first_attempt(self):
        handler = Recorder()
        self.store.enqueue(effect(), self.clock())

        stats = self.worker(handler).run_once()

        self.assertEqual(stats.delivered, 1)
        self.assertEqual(handler.delivered, ["publish:run-1"])
        self.assertEqual(self.store.get("publish:run-1").status, RecordStatus.DELIVERED)

    def test_delivered_record_is_never_reclaimed(self):
        handler = Recorder()
        self.store.enqueue(effect(), self.clock())
        worker = self.worker(handler)
        worker.run_once()
        self.clock.advance(3600)

        worker.run_once()

        self.assertEqual(handler.delivered, ["publish:run-1"])

    def test_transient_failure_is_retried_after_backoff_only(self):
        handler = Recorder(ConnectionError("down"))
        self.store.enqueue(effect(), self.clock())
        worker = self.worker(handler)

        self.assertEqual(worker.run_once().retried, 1)
        record = self.store.get("publish:run-1")
        self.assertEqual(record.status, RecordStatus.QUEUED)
        self.assertIn("down", record.last_error)

        self.clock.advance(9)
        self.assertEqual(worker.run_once().delivered, 0)
        self.clock.advance(1)
        self.assertEqual(worker.run_once().delivered, 1)
        self.assertEqual(self.store.get("publish:run-1").attempts, 2)

    def test_backoff_grows_with_attempts(self):
        handler = Recorder(ConnectionError("a"), ConnectionError("b"))
        self.store.enqueue(effect(), self.clock())
        worker = self.worker(handler)

        worker.run_once()
        self.clock.advance(10)
        worker.run_once()
        self.clock.advance(19)
        self.assertEqual(worker.run_once().delivered, 0)
        self.clock.advance(1)
        self.assertEqual(worker.run_once().delivered, 1)

    def test_dead_letters_after_max_attempts(self):
        handler = Recorder(*(ConnectionError("down") for _ in range(10)))
        self.store.enqueue(effect(), self.clock())
        worker = self.worker(handler)

        outcomes = []
        for _ in range(self.max_attempts):
            outcomes.append(worker.run_once())
            self.clock.advance(3600)

        self.assertEqual([o.retried for o in outcomes], [1, 1, 0])
        self.assertEqual(outcomes[-1].dead, 1)
        record = self.store.get("publish:run-1")
        self.assertEqual(record.status, RecordStatus.DEAD)
        self.assertEqual(record.attempts, self.max_attempts)
        self.assertIn("down", record.last_error)

        self.assertEqual(worker.run_once().dead, 0)

    def test_permanent_error_dead_letters_immediately(self):
        handler = Recorder(PermanentEffectError("bad payload"))
        self.store.enqueue(effect(), self.clock())

        stats = self.worker(handler).run_once()

        self.assertEqual(stats.dead, 1)
        record = self.store.get("publish:run-1")
        self.assertEqual(record.status, RecordStatus.DEAD)
        self.assertEqual(record.attempts, 1)
        self.assertEqual(record.last_error, "bad payload")

    def test_unknown_kind_dead_letters(self):
        self.store.enqueue(effect(kind="send_email"), self.clock())

        stats = self.worker(Recorder()).run_once()

        self.assertEqual(stats.dead, 1)
        self.assertIn("send_email", self.store.get("publish:run-1").last_error)

    def test_leased_record_is_invisible_until_lease_expires(self):
        self.store.enqueue(effect(), self.clock())
        first = self.store.claim(self.clock(), 10, 30.0)
        self.assertEqual(len(first), 1)

        self.clock.advance(29)
        self.assertEqual(self.store.claim(self.clock(), 10, 30.0), [])

        self.clock.advance(1)
        reclaimed = self.store.claim(self.clock(), 10, 30.0)
        self.assertEqual([r.attempts for r in reclaimed], [2])

    def test_crash_mid_delivery_is_recovered_by_another_worker(self):
        self.store.enqueue(effect(), self.clock())
        self.store.claim(self.clock(), 10, 30.0)
        self.clock.advance(31)
        handler = Recorder()

        stats = self.worker(handler).run_once()

        self.assertEqual(stats.delivered, 1)
        self.assertEqual(handler.delivered, ["publish:run-1"])

    def test_repeated_crashes_on_one_record_end_in_dead_letter(self):
        self.store.enqueue(effect(), self.clock())
        for _ in range(self.max_attempts):
            self.assertEqual(len(self.store.claim(self.clock(), 10, 30.0)), 1)
            self.clock.advance(31)

        stats = self.worker(Recorder()).run_once()

        self.assertEqual(stats.dead, 1)
        self.assertEqual(self.store.get("publish:run-1").status, RecordStatus.DEAD)

    def test_concurrent_claims_never_overlap(self):
        for index in range(4):
            self.store.enqueue(effect(f"k{index}"), self.clock())

        a = self.store.claim(self.clock(), 3, 30.0)
        b = self.store.claim(self.clock(), 3, 30.0)

        keys_a = {r.effect.key for r in a}
        keys_b = {r.effect.key for r in b}
        self.assertEqual(len(a), 3)
        self.assertEqual(len(b), 1)
        self.assertFalse(keys_a & keys_b)

    def test_claim_respects_next_attempt_time(self):
        self.store.enqueue(effect(), self.clock())
        self.store.claim(self.clock(), 10, 30.0)
        self.store.release("publish:run-1", "boom", self.clock() + timedelta(seconds=60))

        self.assertEqual(self.store.claim(self.clock(), 10, 30.0), [])
        self.clock.advance(60)
        self.assertEqual(len(self.store.claim(self.clock(), 10, 30.0)), 1)

    def test_list_dead_returns_only_dead_records(self):
        self.store.enqueue(effect("a"), self.clock())
        self.store.enqueue(effect("b"), self.clock())
        self.store.claim(self.clock(), 10, 30.0)
        self.store.mark_dead("a", "boom")
        self.store.mark_delivered("b", self.clock())

        dead = self.store.list_dead(10)

        self.assertEqual([r.effect.key for r in dead], ["a"])
        self.assertEqual(dead[0].last_error, "boom")

    def test_requeue_resets_a_dead_record_and_it_is_redelivered(self):
        handler = Recorder(PermanentEffectError("bad"))
        self.store.enqueue(effect(), self.clock())
        worker = self.worker(handler)
        worker.run_once()

        self.assertTrue(self.store.requeue("publish:run-1", self.clock()))
        record = self.store.get("publish:run-1")
        self.assertEqual((record.status, record.attempts), (RecordStatus.QUEUED, 0))

        self.assertEqual(worker.run_once().delivered, 1)

    def test_requeue_refuses_non_dead_records(self):
        self.store.enqueue(effect(), self.clock())

        self.assertFalse(self.store.requeue("publish:run-1", self.clock()))
        self.assertFalse(self.store.requeue("missing", self.clock()))

    def test_prune_deletes_only_old_delivered_records(self):
        for key in ("old", "new", "dead", "queued"):
            self.store.enqueue(effect(key), self.clock())
        self.store.claim(self.clock(), 3, 30.0)
        self.store.mark_delivered("old", self.clock())
        self.clock.advance(86400 * 10)
        self.store.mark_delivered("new", self.clock())
        self.store.mark_dead("dead", "x")

        removed = self.store.prune_delivered(self.clock() - timedelta(days=5))

        self.assertEqual(removed, 1)
        self.assertIsNone(self.store.get("old"))
        for key in ("new", "dead", "queued"):
            self.assertIsNotNone(self.store.get(key))

    def test_outbox_gateway_queues_without_delivering(self):
        handler = Recorder()
        gateway = OutboxGateway(self.store, self.clock)

        status = gateway.submit(effect())

        self.assertEqual(status, DeliveryStatus.QUEUED)
        self.assertEqual(handler.delivered, [])
        self.assertEqual(gateway.status("publish:run-1"), DeliveryStatus.QUEUED)
        self.assertIsNone(gateway.status("missing"))

    def test_outbox_gateway_resubmit_after_delivery_reports_delivered(self):
        gateway = OutboxGateway(self.store, self.clock)
        gateway.submit(effect())
        self.worker(Recorder()).run_once()

        self.assertEqual(gateway.submit(effect()), DeliveryStatus.DELIVERED)

    def test_inline_gateway_delivers_during_submit(self):
        handler = Recorder()
        gateway = InlineGateway(self.store, self.worker(handler))

        self.assertEqual(gateway.submit(effect()), DeliveryStatus.DELIVERED)
        self.assertEqual(handler.delivered, ["publish:run-1"])

    def test_inline_gateway_replayed_submit_delivers_once(self):
        handler = Recorder()
        gateway = InlineGateway(self.store, self.worker(handler))

        gateway.submit(effect())
        status = gateway.submit(effect())

        self.assertEqual(status, DeliveryStatus.DELIVERED)
        self.assertEqual(handler.delivered, ["publish:run-1"])

    def test_inline_gateway_failure_leaves_effect_queued_for_a_worker(self):
        handler = Recorder(ConnectionError("down"))
        gateway = InlineGateway(self.store, self.worker(handler))

        self.assertEqual(gateway.submit(effect()), DeliveryStatus.QUEUED)

        self.clock.advance(10)
        self.worker(handler).run_once()
        self.assertEqual(gateway.status("publish:run-1"), DeliveryStatus.DELIVERED)

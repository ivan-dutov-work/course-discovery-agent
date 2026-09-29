from __future__ import annotations

from course_discovery.effects.models import Effect, PermanentEffectError
from course_discovery.effects.worker import Handler

PUBLISH_DIGEST = "publish_digest"


def deliver_digest(effect: Effect) -> None:
    digest = effect.payload.get("digest")
    if not digest:
        raise PermanentEffectError("publish_digest payload has no digest")
    print(f"\n=== PUBLISH (stdout stub, idempotency-key={effect.key}) ===")
    print(digest)
    print("=== END PUBLISH ===\n")


def default_handlers() -> dict[str, Handler]:
    return {PUBLISH_DIGEST: deliver_digest}

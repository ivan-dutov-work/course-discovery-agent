# Notes: scale and scope (§11–§12)

Evidence for drafting. Load when working on these sections.

## Cross-worker coordination (§11)

All three patterns are architectural descriptions; no code implements them.

- **Circuit breaking across workers (§11.1).** Store as shared state for circuit state; a
  two-tier check (queue boundary and subgraph entry); half-open probes; composition with
  `RetryPolicy`. Also the reason for choosing vendor-neutral tracing (state and prompts must not
  leave for a third party).
- **Outbox leasing at scale (§11.2).** `FOR UPDATE SKIP LOCKED`, lease expiry, claim-time attempt
  counting. The SQL pattern is the one the existing worker uses
  (`course_discovery/effects/worker.py`); describe it architecturally rather than pointing at code.
- **Concurrent resume (§11.3).** An application-level advisory lock before `ainvoke`. The risk is
  double work (LLM calls, synthesis), not corruption, because keys are deterministic (§6.1).

## Scope and verification (§12)

- State once, plainly: this is not a production case study. See `DECISIONS.md`.
- "Production would swap this one class" is an architectural claim, not a tested migration.
- Strongest evidence in the repo: the SIGKILL child-process test. `async` durability is
  deliberately untested for a hard kill.
- Strict-mode msgpack behaviour for a type outside the state schema is untested.
- Not verified: the OpenRouter fallback path live, OpenRouter retention controls, `Command` or
  dynamic `interrupt()` inside a subgraph, `@task` inside a subgraph mechanism.

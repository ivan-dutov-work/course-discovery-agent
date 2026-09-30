# Notes: observability and compliance (§9–§10)

Evidence for drafting. Load when working on these sections. Observed on langgraph 1.1.2
unless marked as documentation.

## Observability (§9)

- **Tracing (§9.1).** `observability/tracing.py`: OpenTelemetry SDK plus OpenInference LangChain
  and psycopg instrumentors, OTLP/HTTP to Jaeger through the `tracing` compose profile.
  Observed: every node is a span; the `Send` workers are siblings under `course_research`;
  `interrupt_before` splits a run into two traces (`run.start`, `run.resume`) sharing
  `course.run_id`. `traceparent` is stored in the outbox payload, so `effect.deliver` in the
  worker joins the trace of the `effect.submit` that queued it. Content is redacted by default
  (`OTEL_CAPTURE_CONTENT`); exporter failure is fail-open and tested; log lines carry
  `trace_id` and `span_id`. Verified live against Jaeger: LLM spans show model and token counts,
  inputs and outputs `__REDACTED__`. Spans flush at each run segment end and batch every 1 s, so
  a SIGKILL loses at most the last second plus spans still open.
- **Why not LangSmith OTel.** Documentation, verified: `LANGSMITH_OTEL_ENABLED=true` plus
  standard `OTEL_EXPORTER_OTLP_*` gives end-to-end OTel and can route to Datadog, Grafana or
  Jaeger. Not chosen here because prompts and state would leave for a third party.
- **Metrics (§9.3).** `observability/metrics.py`: cache hit and miss, run duration by outcome,
  run and review outcomes, degraded paths by component and reason, search calls, LLM calls,
  errors, fallbacks, latency and tokens on the GenAI conventions, outbox outcomes and backlog
  gauges. Opt-in with `OTEL_METRICS_EXPORTER`. `tavily.*` log events carry `query_len`, not the query.
- **`stream_mode` (§9.2).** Documentation, verified: `values`, `updates`, `messages`, `custom`,
  `debug`, plus `checkpoints` and `tasks`. A list yields `(mode, chunk)` tuples; a string yields
  bare chunks. It is how a client gets partial progress without polling. The CLI uses
  `["updates", "custom"]`.

## Data protection (§10.1)

- LangGraph has no compliance tooling; frame this as a layer-of-abstraction point. Whatever
  PII flows through `AgentState` is persisted at every superstep, with no built-in TTL or
  redaction. `adelete_thread(thread_id)` erases checkpoints, but `thread_id` is opaque and,
  with encryption, the user is unreadable in the blob, so the app owns the user-to-thread
  index (`run_threads`, `privacy/erasure.py`). `Store` has `TTLConfig`; the checkpointer does not.
- The compliance lever sits below the graph, at the model-provider boundary (ZDR, DPAs,
  region), invisible to LangGraph.
- **Redaction placement matters more than the library.** Input is checkpointed before any node
  runs, so redacting inside the parse node leaves raw PII in every checkpoint
  (`CheckpointPlacementTests`: 17 of 17 checkpoints leaked versus 0). Redaction happens at CLI
  ingress, the parse node, `record_feedback`, and log and error masking. Presidio with
  `en_core_web_sm`, score threshold 0.4 because the phone recognizer scores 0.4; instructor
  names are redacted.
- **Encryption at rest.** `EncryptedSerializer` alone is not enough: `AsyncPostgresSaver.aput`
  inlines `str`, `int`, `float`, `bool` and `None` channel values as plaintext JSON in
  `checkpoints.checkpoint`. `test_stock_saver_with_encrypted_serde_leaves_inline_strings_plaintext`
  pins this. `SealingSaver` wraps any `BaseCheckpointSaver` through its public methods. Metadata
  JSONB stays plaintext, as do `outbox.payload` and the application tables. `prune_checkpoints`
  reads `run_threads.last_activity_at` (migration 006), so it only sees registered threads.
  Alternative not built: per-user keys (crypto-shredding).
- **Declared data-flow check** (`domain/pii.py`, `privacy/flow.py`, `privacy/flow_specs.py`,
  `tests/test_flow_rules.py`). `Pii` marker as `Annotated` metadata on channels; per-node
  `flow(...)` declaring reads, writes, sinks, redacts, declassifies; labels propagate over shared
  channels. Rules: a sink must accept the labels reaching it; stores receiving subject data
  must be in `USER_DATA_SOURCES`; every compiled-graph node must be declared. Verified with
  mutation tests per rule, a run-based test that nodes write only declared channels, and canary
  tests scanning checkpoint history and the outbox payload. The canary caught a wrong
  declassification (search results echo the query); the coverage rule caught an undeclared node
  (`end_research_on_error`). Limits, stated in the article: channel granularity, reads declared
  not observed, only the approve path driven, sinks declared not observed, edge order ignored,
  canary finds verbatim copies only. Not built: reducer-based redaction (unverified whether the
  initial input passes through a reducer), selective sealing, AST-derived reads and writes,
  boundary-observed sinks.

## Versioning (§10.2)

- **State schema.** The checkpointer serializes the state directly, so a changed shape can
  break old checkpoints. Msgpack allowlist, verified with `langgraph-checkpoint` 4.2.0: a durable
  saver logs `Deserializing unregistered type ... This will be blocked in a future version` once
  per Pydantic model in state (6 here), once per type per process, so assert it in a fresh
  process. Fixes, each giving 0 warnings and correct state: `JsonPlusSerializer(
  allowed_msgpack_modules=[...])` passed as `serde=`, or `LANGGRAPH_STRICT_MSGPACK=true`, which
  derives an allowlist from the state schema at compile time (`langgraph/_internal/_serde.py`).
  Not tested: what strict mode blocks for a type outside the schema. This is also a
  deserialization-safety control. An allowlist of `BaseModel` subclasses is not enough: enum
  fields (`RoutingAction`, `DeliveryStatus`) log `Blocked deserialization`; the allowlist in
  `persistence/checkpointer.py` now includes `Enum` subclasses and `tests/test_checkpointer.py`
  round-trips a model and both enums. The effect of a blocked enum on the restored value was not inspected.
- **Graph structure.** Adding, removing or renaming a node between deploys can break in-flight
  threads even when `AgentState` is unchanged. Node names are checkpoint interrupt targets; the
  rename recorded in `ARCHITECTURE.md` means a run paused at the old `review_gate` should be
  treated as unable to resume (not verified). Name the two risks as independent.

## Access control (§10.4)

- `thread_id` is a bearer capability, and `update_state` on `manager_feedback` can open the publish
  gate. `privacy/registry.py:authorize_thread` checks the `run_threads` owner before `update_state`
  in the CLI; unknown and foreign threads raise the same `ThreadAccessError`; re-registering a
  taken id keeps the owner (`tests/test_thread_access.py`, Postgres). Not covered: a second entry
  point that skips the guard, and no-`DATABASE_URL` mode, where there is no registry.

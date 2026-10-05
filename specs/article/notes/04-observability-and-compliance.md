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
  (a no-op error terminal, since removed). Limits, stated in the article: channel granularity, reads declared
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

## Prompt-injection screen (§8.5, §10.3)

- **What is wired.** `guardrails/injection.py` is a port (`InjectionScreen.score(text) -> float`) with
  a switch (`INJECTION_GUARD`); `guardrails/jev.py` implements it with `typesafe/jev-1.13` on
  OpenRouter. `rank_and_summarize_courses` screens each course's title, description and evidence
  quotes before the model sees them. Score at or above 0.70: the model is not called and the line
  carries a withheld note. 0.35 to 0.70: the model gets structured fields only, no description or
  evidence. Below 0.35: unchanged. Any screen error counts as the middle band, not as a pass
  (`tests/test_injection_guard.py`, stubbed HTTP and a fake model).
- **How Jev is reached (observed 2026-10-05).** `typesafe/jev-1.13` is absent from the public
  `/api/v1/models` list but live on `/api/v1/models/typesafe/jev-1.13/endpoints`: output modality
  `decisions`, 32k context, provider TypeSafe, $0.042 per million input tokens. It is called on
  `POST https://openrouter.ai/api/alpha/decisions`, not on chat completions (`typesafe/jev-router` is
  the chat-completions product and returns text). A `noul` question returns a probability in
  `answers.<name>.noul`. The endpoint path says `alpha`; treat the contract as unstable.
- **Live measurement (2026-10-05, 28 samples, one run, `typesafe/jev-1.13-20260917`).** The 15
  mock-catalog listings scored 0.02 to 0.03. Three hand-written hard negatives scored 0.24
  (hype copy), 0.44 (a real-sounding "ignore the optional readings" line) and 0.12 (a course about
  defending against injection). Ten injections, from blunt to buried inside a real description,
  scored 0.97 to 0.99. Seven more aimed at the screen itself (claims to be benign, addresses the
  classifier, fakes the JSON answer, polite request, German, instruction in the title, 28k characters
  with one sentence in the middle) scored 0.95 to 0.99. Median latency 292 ms, slowest 1331 ms.
  The 0.44 negative lands in the review band, so one of 18 benign samples was withheld.
- **Reproducing it.** The samples are reconstructed from the original run's descriptions, not the original strings. They live in `tests/injection_samples.py` (10 injections, 7 aimed at the screen, 3 hard negatives); `PYTHONPATH=. uv run python scripts/injection_score_table.py` prints the score table and latency. Reconstructed run (2026-10-05, 35 calls): catalog 0.02 to 0.03; hard negatives 0.07 (hype), 0.31 (optional readings), 0.06 (defending against injection), so none reached the review band here; injections 0.97 to 0.99; screen-targeting 0.97 to 0.99; median 300 ms, max 1426 ms. The live suite passes (4 tests).
- **How far to trust that.** The samples were written by the author and are easy next to the
  adversarial cases TypeSafe's own limitations page warns about ("text that argues for its own
  classification can move the answer"); 28 samples fit no threshold. The 0.35 and 0.70 bands come
  from an independent benchmark (`jev-sec-bench`, 662 messages from a public corpus that may have
  leaked into training, one German news assistant) and are not fitted here. That benchmark also
  found recall fell from 95.1% to 74.9% without a description of the deployment, which is why
  `jev.py` sends one in the state.
- **Not exercised.** An adaptive attacker who iterates against the screen, a non-English corpus
  beyond one sentence, the 12,000-character chunk boundary live (stub only), and the live suite's
  one unexplained error in the first of about eight runs (error text not captured; not reproduced in
  six reruns). The live tests need `LIVE_LLM_TESTS=1` and a key (`tests/test_injection_guard_live.py`).
- **What it does not cover.** Only the synthesis prompt. `parse_user_request` and the memory curator
  read no web text. The tagger (uncommitted) does and is not screened. Reviewer feedback and
  reader notes are user text, not web text, and are out of scope.

## Feedback to memory, end to end (P6)

- **Provider names are redacted as people (observed 2026-10-05).** `redact_pii` through Presidio
  (`presidio-analyzer` 2.2.364, spaCy `en_core_web_sm` 3.8.0) turns capitalised `Udemy` and
  `Coursera` into `<PERSON>`: `"I'm done with Udemy"` becomes `"I'm done with <PERSON>"`, and `"I prefer
  Coursera courses"` becomes `"I prefer <PERSON> courses"`. `udemy` in lower case, `edX`, `Udacity`,
  `Pluralsight` and `freeCodeCamp` pass through. Checked by calling `redact_pii` on twelve feedback
  strings, one run. The curator reads the redacted `feedback_history`, so a live model cannot
  see which provider the user named in cases 1, 2, 7 and 8 of `FEEDBACK.md` when it is capitalised.
  The stubbed layer does not see this, because the scripted curator ignores the text; the case
  table pins the redacted history instead (`tests/memory_cases.py`, field `history`). Consistent with
  the instructor-name trade-off in `DECISIONS.md`, but that entry did not expect provider names.
- **Layer 2 is exact and passes on both stores.** Eleven cases (1, 2, 3, 7, 8, 9, 10, 11, 12, 13,
  14) through the whole outer graph, with a scripted curator and, for the cases that need a route,
  a stubbed router, on the fake store and on Postgres: 26 tests, 6 s on Postgres, 2 s without.
  Each asserts the stored profile, the tool trace, the route, and run two against a baseline run
  by a user with no profile.
- **The judge is built and its stubbed tests pass; it has never been called against the real model.**
  `tests/judge.py`: `google/gemini-3.1-flash-lite` through `build_llm("judge")` with an empty
  fallback list, three calls, majority per criterion, a tie is `unknown` and does not pass. The
  live suites (`tests/test_memory_e2e_live.py`, `tests/test_judge_live.py`) need
  `LIVE_LLM_TESTS=1` and a key; the session that wrote them had none. **Which cases the live
  model fails, and how often, is therefore not measured**, and so are the judge's agreement with
  six hand-labelled outputs and the model versions served. The redaction finding predicts that
  cases 1, 2, 7 and 8 fail live until provider names stop being redacted; that is a prediction.

## CI rehearsal (E1 milestone 2)

- **Method (2026-10-05).** A fresh `pgvector/pgvector:pg17` container with no mounted migrations, a fresh
  virtual environment from `uv sync --locked`, `scripts/apply_migrations.py`, then
  `python -m unittest discover tests` and `pytest evals -n 4`, as `ci.yml` does. Not run on GitHub Actions:
  action versions (`actions/checkout@v4`, `astral-sh/setup-uv@v6`) and the service-container health options
  are unverified until the first run.
- **The spaCy model installs from its URL.** `en_core_web_sm-3.8.0` is a URL dependency in
  `pyproject.toml`, hashed in `uv.lock`; the release URL answered 200 and `uv sync --locked` installed it.
- **A fresh database found a test-order bug.** `tests/test_checkpoint_compatibility.py` inserted fixture rows
  before anything created LangGraph's checkpoint tables; they existed locally only because a later test calls
  `setup()` and the local volume is long-lived. Three subtests failed with `relation "checkpoints" does not
  exist` on the first run against an empty database. Fixed by loading the fixture after `open_checkpointer`.
  Result afterwards: 481 tests, 24 skipped, about 21 s; 42 evals, about 1.4 s.
- **The two guards are covered by their scenarios.** Replacing the owner check in `authorize_thread` with
  `False` fails `graph-wrong-owner-cannot-resume` on three graders; the same for the version check and
  `graph-stale-thread-refused`. Both reverted.


# Status: what is implemented

Ledger of capabilities that exist and are tested. One line each: what it is, where it
lives, which article section it feeds. No instructions and no history; the evidence
behind an item is in `specs/article/notes/`, and the graph itself is in `ARCHITECTURE.md`.

Integration tests need `docker compose up -d` and
`TEST_DATABASE_URL=postgresql://course:course@localhost:55432/course_discovery`.

## Graph and state

- Bounded workflow with four LLM nodes through `build_llm()`: `app/llm.py` (§1, §8.2).
- Reducers on parallel-written channels (`tavily_results`, `completed_queries`,
  `research_notes`); `extracted_candidates` is a plain list (§2.2; open question in BACKLOG).
- Top-level state slimmed from 34 to 28 channels: run id is the `thread_id`, review and research
  budgets are `configurable` keys, the review round is `len(feedback_history)`, counters live in
  `metrics` only, the gateway failure signals through `discard_reason`: `domain/run_config.py`,
  `tests/test_top_level_state.py` (§2.1).
- Plan-driven `Send` fan-out: `workflows/research_graph.py` (§3.2).
- Only `await_human_review` remains as a no-op anchor; the research entry is a conditional
  entry point: `workflows/`, `tests/test_graph_topology.py` (§3.1, §4.1).
- Research graph mounted as a subgraph of the outer graph (§3.3).
- Static `interrupt_before=["await_human_review"]` with five review routes (§4.1, §4.3).
- `recursion_limit` set in `app/cli.py` (§4.4).
- `stream_mode=["updates", "custom"]` in `app/cli.py` (§9.2).

## Retrieval

- `Embedder` port with a local lexical `HashingEmbedder` default and an `OpenRouterEmbedder` selected by `EMBEDDER=openrouter` (1536 dimensions, batched, transient errors propagate for `RetryPolicy`, other failures raise `EmbeddingError`); embeddings stored with each cached course: `research_agent/embeddings/`, `tests/test_embeddings.py`, `tests/test_openrouter_embedder.py` (stubbed HTTP; the live test skips without a key). The calibration script takes the embedder from `EMBEDDER`; it was run against `openai/text-embedding-3-small`, which set a per-embedder floor and relative cutoff (`topic_floor` 0.12 / 0.22, `relative_cutoff` none / 0.70) and confirmed the blend weights; facet words are stripped from the topic and the stored course text, a blank topic never reaches the embedder, and `embeddings backfill --all` re-embeds existing rows.
- Topic-aware cache lookup (similarity floor plus blended score), same ranking on the seed path: `research_agent/cache/`, `tests/test_cache_topic.py`, `scripts/calibrate_topic_floor.py`.

## Durability and effects

- Postgres checkpointer through `open_checkpointer()`, `MemorySaver` without `DATABASE_URL`;
  msgpack allowlist covers models and enums: `persistence/checkpointer.py`,
  `tests/test_checkpointer.py` (§5.1, §10.2).
- Durability modes `sync`, `async`, `exit` tested against Postgres; SIGKILL resume test:
  `tests/test_integration_kill.py` (§5.2).
- Replay-safe writes: `migrations/002`, `004`; `ON CONFLICT` on evidence and events (§6.1).
- Read and write paths fail closed: DB helpers re-raise after logging (§6.1).
- Outbox behind an `EffectGateway` port, worker with leases, backoff and dead-letter,
  inline and outbox adapters: `course_discovery/effects/`, `migrations/003`,
  `tests/outbox_contract.py` (§6.2, §6.4).
- Publish key derived from `run_id`; `publish_status` is `queued | delivered | dead` (§6.2).

## Resilience

- `RetryPolicy` on read-only and idempotent nodes, retrying transient errors only:
  `resilience.py`, `tests/test_retries.py` (§7.1).
- Client-level timeouts: LLM request, search `wait_for`, Postgres connect and statement (§7.2).
- OpenRouter as the single LLM gateway with a server-side fallback list: `app/llm.py`,
  `tests/test_llm.py` (§8.2).
- In-process rate limiter on the router LLM (§8.3).
- Graceful degradation paths: no key means deterministic parse and summary; no database
  means seed cache; search failure writes a limitation note (§6.1).

## User memory

- Writable profile: `save_user_memory` (one transaction, row lock, merge, redacted free text), `MemoryPatch`, `MemoryNote`; `save_user_memory` also writes `profile_embedding` in the same transaction: `research_agent/memory/repository.py`, `tests/test_user_profile.py`. Only the curator's `commit` calls it.
- Profile consumers: stored budget and certificate defaults in `parse_user_request`; preferred provider, level, language boost and scoped notes in `rank_and_summarize_courses`: `research_agent/memory/defaults.py`, `research_agent/synthesis/nodes.py`.
- `feedback_history` accumulates every review round (redacted); DISCARD now goes through `record_review_outcome`; the research subgraph runs on `ResearchState`, which omits the channel: `domain/state.py`, `tests/test_memory_e2e.py` (feedback in run one changes run two, in-memory and Postgres).

- Profile similarity as a ranking tie-break: the cache query (and the seed path) returns a scalar `CourseCandidate.profile_similarity`, never the vector; `_rank_courses` uses it in 0.1 buckets after explicit preferences and price, before rating. Web candidates have none: `research_agent/memory/profile_vector.py`, `research_agent/cache/repository.py`, `tests/test_profile_and_duration.py` (Postgres cases skip without `TEST_DATABASE_URL`, not run in the session that wrote them).
- Course duration: `CourseCandidate.duration_hours` from listing metadata or snippet text (`extraction/nodes.py`), stored in `courses.duration_hours` (migration 009). `preferred_course_length` is `short` (up to 10 h), `medium` (up to 40 h) or `long`, validated in `MemoryPatch`, writable by the curator, and counted as one match in `_preference_score`: `tests/test_profile_and_duration.py`, `tests/test_memory_e2e.py` (case 5).

- Memory curator subgraph `curate_user_memory` after `record_review_outcome`: bounded tool loop (`read_profile`, `read_run_events`, `propose_patch`, `finish`), 4-step cap, fail-closed commit, idempotent per `run_id` through `memory_updates` (migration 008, erasable); uses `input_schema`/`output_schema`: `memory_curator/`, `tests/test_memory_curator.py` (cases 1, 2, 3, 7, 10 to 18 with a scripted model, plus Postgres), `tests/test_curator_checkpoint.py` (private channels through the encrypted Postgres checkpointer). `tests/test_memory_e2e.py` now runs the real curator between run one and run two.
- Not built yet: case 6 of `FEEDBACK.md` (no consumer for career goals), cases 4 and 8 need a live model and the judge (P6).

## Observability

- OpenTelemetry traces and metrics, vendor-neutral, content redacted by default:
  `observability/`, `tests/test_observability.py` (§9.1, §9.3).

## Privacy and access

- PII redaction before state, LLM, feedback and logs: `pii_redaction/`, `guardrails/`,
  `tests/test_pii_redaction.py`, `tests/test_guardrails.py` (§10.1).
- Checkpoint encryption at rest (`EncryptedSerializer` plus `SealingSaver`):
  `persistence/encryption.py`, `tests/test_encryption.py` (§10.1).
- `run_threads` registry, per-user erasure, checkpoint pruning: `privacy/`,
  `tests/test_erasure.py` (§10.1).
- Thread ownership check before resume: `privacy/registry.py`, `tests/test_thread_access.py` (§10.4).
- Prompt-injection screen on the synthesis prompt: `InjectionScreen` port, `typesafe/jev-1.13` adapter over OpenRouter's Decisions API, three bands (pass, structured fields only, model skipped), screen errors withhold free text, `INJECTION_GUARD` switch: `guardrails/injection.py`, `guardrails/jev.py`, `tests/test_injection_guard.py`; live suite `tests/test_injection_guard_live.py` (samples in `tests/injection_samples.py`, score table via `scripts/injection_score_table.py`) needs `LIVE_LLM_TESTS=1`. Thresholds are the independent benchmark's, not fitted (§8.5, §10.3).
- Shared-cache writes wait for approval: `save_verified_courses` stages valid web courses in `pending_courses` (migration 010), `promote_approved_courses` upserts the approved ones into `courses`, `drop_pending_courses` clears a discarded run; uncertain courses are not persisted, existing rows untouched: `research_agent/cache/`, `tests/test_pending_courses.py`, `tests/test_integration_postgres.py`.
- User-text injection probes (live, `LIVE_LLM_TESTS=1`): five payloads through the gateway, router and curator tool loop; outputs stay in schema, no system-prompt leak, proposals stay inside `WRITABLE_FIELDS`: `tests/test_user_text_injection_live.py`. Passed once (2026-10-05).
- Reducer echo fixed: fan-in reducers only on `ResearchState`, parent channels plain; REWRITE and AUGMENT rounds no longer re-add `completed_queries`, `research_notes` or `tavily_results`: `domain/state.py`, `tests/test_state_hygiene.py`.
- Research subgraph behind `input_schema`/`output_schema` (four keys in; `valid_courses`, `digest`, `metrics` out), stateful via `checkpointer=True`; outer `AgentState` is 13 channels; `start_research_pass` guards resume: `domain/state.py`, `workflows/`, `tests/test_top_level_state.py`, `tests/test_graph_topology.py`, `tests/test_nested_checkpoints_postgres.py` (§2.3).
- Declared PII data-flow check: `domain/pii.py`, `privacy/flow.py`, `privacy/flow_specs.py`,
  `tests/test_flow_rules.py` (§10.1).

## Not in the code

Each of these is covered in the article as prose only, and the reason is in `DECISIONS.md`:
`CachePolicy`, node-level `timeout=`, `Command` routing,
dynamic `interrupt()`, `durability=` set explicitly, circuit
breaker, cross-worker coordination.

# Status: what is implemented

Ledger of capabilities that exist and are tested. One line each: what it is, where it
lives, which article section it feeds. No instructions and no history; the evidence
behind an item is in `specs/article/notes/`, and the graph itself is in `ARCHITECTURE.md`.

Integration tests need `docker compose up -d` and
`TEST_DATABASE_URL=postgresql://course:course@localhost:55432/course_discovery`.

## Graph and state

- Bounded workflow with four LLM nodes through `build_llm()`: `app/llm.py` (§1, §8.2).
- Reducers on parallel-written channels (`tavily_results`, `completed_queries`,
  `research_notes`, `tavily_calls`); `extracted_candidates` is a plain list (§2.2; open question in BACKLOG).
- Plan-driven `Send` fan-out: `workflows/research_graph.py` (§3.2).
- Only `await_human_review` remains as a no-op anchor; the research entry is a conditional
  entry point: `workflows/`, `tests/test_graph_topology.py` (§3.1, §4.1).
- Research graph mounted as a subgraph of the outer graph (§3.3).
- Static `interrupt_before=["await_human_review"]` with five review routes (§4.1, §4.3).
- `recursion_limit` set in `app/cli.py` (§4.4).
- `stream_mode=["updates", "custom"]` in `app/cli.py` (§9.2).

## Retrieval

- `Embedder` port with a local lexical `HashingEmbedder`, embeddings stored with each cached course: `research_agent/embeddings/`, `tests/test_embeddings.py`.
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

- Writable profile: `save_user_memory` (one transaction, row lock, merge, redacted free text), `MemoryPatch`, `MemoryNote`; `preferred_course_length` stored, no consumer yet: `research_agent/memory/repository.py`, `tests/test_user_profile.py`. Only the curator's `commit` calls it.
- Profile consumers: stored budget and certificate defaults in `parse_user_request`; preferred provider, level, language boost and scoped notes in `rank_and_summarize_courses`: `research_agent/memory/defaults.py`, `research_agent/synthesis/nodes.py`.
- `feedback_history` accumulates every review round (redacted); DISCARD now goes through `record_review_outcome`; the research subgraph runs on `ResearchState`, which omits the channel: `domain/state.py`, `tests/test_memory_e2e.py` (feedback in run one changes run two, in-memory and Postgres).

- Memory curator subgraph `curate_user_memory` after `record_review_outcome`: bounded tool loop (`read_profile`, `read_run_events`, `propose_patch`, `finish`), 4-step cap, fail-closed commit, idempotent per `run_id` through `memory_updates` (migration 008, erasable); uses `input_schema`/`output_schema`: `memory_curator/`, `tests/test_memory_curator.py` (cases 1, 2, 3, 7, 10 to 18 with a scripted model, plus Postgres), `tests/test_curator_checkpoint.py` (private channels through the encrypted Postgres checkpointer). `tests/test_memory_e2e.py` now runs the real curator between run one and run two.
- Not built yet: cases 5 and 6 of `FEEDBACK.md` (no consumer for course length or career goals), cases 4 and 8 need a live model and the judge (P6).

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
- Declared PII data-flow check: `domain/pii.py`, `privacy/flow.py`, `privacy/flow_specs.py`,
  `tests/test_flow_rules.py` (§10.1).

## Not in the code

Each of these is covered in the article as prose only, and the reason is in `DECISIONS.md`:
`CachePolicy`, node-level `timeout=`, `Command` routing,
dynamic `interrupt()`, `durability=` set explicitly, prompt-injection check, circuit
breaker, cross-worker coordination.

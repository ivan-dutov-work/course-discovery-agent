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
- Top-level state slimmed from 34 to 15 channels (counted 2026-10-06 from `AgentState`; `ResearchState` has 23, nine shared with the outer state and fourteen private): run id is the `thread_id`, review and research
  budgets are `configurable` keys, the review round is `len(feedback_history)`, counters live in
  `metrics` only, the gateway failure signals through `discard_reason`: `domain/run_config.py`,
  `tests/test_top_level_state.py` (§2.1).
- State contract is versioned: `STATE_SCHEMA_VERSION` stamped in `run_threads` (migration 011), `authorize_thread` refuses a thread from another version with `IncompatibleThreadError`, the CLI exits non-zero; channel and node names snapshotted, one stored checkpoint per version marked `resumes` or `refused`: `domain/contract.py`, `privacy/registry.py`, `tests/test_state_contract.py`, `tests/test_checkpoint_compatibility.py`, `tests/fixtures/checkpoints/`, `scripts/dump_checkpoint_fixture.py` (§10.2).
- Multi-round review loop against Postgres, local only (skips without `TEST_DATABASE_URL`): augment, rewrite, reset, approve with a fresh saver and graph per segment, encryption on, registry guard on; plus the discard and wrong-owner and wrong-version paths: `tests/test_e2e_review_loops_postgres.py` (§2.3, §4).
- Docs drift cleared: unused `telegram_gate_node` alias removed, known gaps renumbered 1 to 6, channel counts (outer 15, subgraph 23) computed from the state classes on 2026-10-06 and quoted in `ARCHITECTURE.md` and above, stray bullets moved out of `Not in the code`.
- Router classification failure writes the exception type into `discard_reason`, not its text: `review/router.py`, `tests/test_user_profile.py`. `langgraph` is pinned to the tested `>=1.1.2,<1.2`.
- Plan-driven `Send` fan-out: `workflows/research_graph.py` (§3.2).
- `verify_course_claims` returns `Command(update=..., goto="plan_gap_search" | "save_verified_courses")` in place of a conditional edge; `enough_valid` stays the pure decision: `research_agent/validation/nodes.py`, `tests/test_research_nodes.py`, `tests/test_graph_topology.py`; resume after an injected crash on the `Command` node and on its target, fresh Postgres saver per segment: `tests/test_command_resume_postgres.py` (§3.4).
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
- CLI passes `durability="sync"` on its one `astream` call (first run and resume); the library
  default is `async` (langgraph 1.1.2). A SIGKILL through the CLI path resumes without re-running the
  validator under `sync`, and re-ran it in 3 of 3 trials under the default: `app/cli.py`,
  `tests/test_cli_durability.py`, `tests/kill_child_cli.py` (§5.2).
- Replay-safe writes: `migrations/002`, `004`; `ON CONFLICT` on evidence and events (§6.1).
- Read and write paths fail closed: DB helpers re-raise after logging (§6.1).
- Outbox behind an `EffectGateway` port, worker with leases, backoff and dead-letter,
  inline and outbox adapters: `course_discovery/effects/`, `migrations/003`,
  `tests/outbox_contract.py` (§6.2, §6.4).
- Publish key derived from `run_id`; `publish_status` is `queued | delivered | dead` (§6.2).
- Tagging job: `courses.content_hash` (generated), `tagged_content_hash`, `tagging_outcome` (migration 013); `schedule_tagging` submits one `tag_course` effect per course keyed `tag:{url_hash}:{content_hash}`; `jobs/tagging_graph.py` runs once per effect (hash check, injection screen, keyword tagger, compare-and-set write); `default_handlers` registers it when `DATABASE_URL` is set; `python -m course_discovery.jobs tag-schedule`. On Postgres, 200 courses with two concurrent schedulers and three workers give 200 tagger calls; a worker SIGKILLed after 60 calls leaves every course tagged with at most one re-run; changed text gives exactly one new call; an injected description never reaches the tagger; dropping the hash from the key or the screen fails its test. Not built: a model tagger, re-embedding after tagging, the digest job: `jobs/`, `tests/test_tagging_job_postgres.py`, `scripts/send_vs_runs.py`.
- Telegram surface: `ChatTransport` port with a fake and a Bot API adapter, webhook intake (`X-Telegram-Bot-Api-Secret-Token` check, redaction before storage, `update_id` dedupe), `chat_inbox` and a per-user lease so one run is active per user and pending messages merge into one run, replies through the graph's chat effects, chat tables in the erasure sources (migration 012). Checked on Postgres against a fake Bot API process: five concurrent identical updates give one row and one run, four racing workers never overlap, a SIGKILL after the digest was submitted gives one send after the lease expires; removing the lease fails the race tests. Not built: choosing between continuing a thread and starting a new one (every batch is a new thread), a `setWebhook` helper: `chat/`, `tests/test_chat_surface_postgres.py`, `scripts/chat_smoke.py`.
- Chat review mode: `chat_mode` configurable key, `send_review_digest` (digest effect per round, one feedback prompt per thread), `review/close.py:close_thread` (implicit acceptance, idempotent, owner and schema checks), `PUBLISH` in chat mode skips publish and promotion, curator reads `routing_decision`; schema v4 with a fixture and v3 flipped to `refused`. In-process crash after the effect submit does not resend (not a SIGKILL), concurrent close writes one event set: `review/`, `tests/test_chat_review_mode_postgres.py`.

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
- End-to-end feedback tests (P6): eleven cases of `FEEDBACK.md` (1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14) as a table through the whole outer graph with a scripted curator and a stubbed router, on the fake store and Postgres, asserting the profile, the tool trace, the route and run two; case 4 and the live layer (real models, repeated trials, judge for free text) are written and skip without `OPENROUTER_API_KEY` and `LIVE_LLM_TESTS=1`, never run live: `tests/memory_cases.py`, `tests/test_memory_e2e.py`, `tests/test_memory_e2e_live.py`. Judge (`google/gemini-3.1-flash-lite`, five criteria, three calls, majority) with stubbed tests and a labelled live check: `tests/judge.py`, `tests/test_judge.py`, `tests/test_judge_live.py`.
- Not built yet: case 6 of `FEEDBACK.md` (no consumer for career goals).
- Note hardening: `propose` refuses notes over 120 characters, with control characters, or with a `topic:` scope outside a short slug; `apply_patch` keeps the newest 30 notes; the curator prompt stores only course-learning preferences; live case 19 (off-topic fact plus an instruction) wrote nothing in 3 of 3 trials: `memory_curator/tools.py`, `research_agent/memory/repository.py`, `app/prompts.py`, `tests/test_memory_curator.py`, `tests/test_user_profile.py`, `tests/memory_cases.py`.

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
- Abandoned staging rows are pruned by `python -m course_discovery.effects prune` (default 30 days): a row goes only if it is older than the cutoff and its `run_threads` row shows no activity since, so a run still being reviewed keeps its staging: `research_agent/cache/repository.py:prune_staged_courses`, `tests/test_prune_staging_postgres.py` (skips without `TEST_DATABASE_URL`).
- User-text injection probes (live, `LIVE_LLM_TESTS=1`): five payloads through the gateway, router and curator tool loop; outputs stay in schema, no system-prompt leak, proposals stay inside `WRITABLE_FIELDS`: `tests/test_user_text_injection_live.py`. Passed once (2026-10-05).
- Reducer echo fixed: fan-in reducers only on `ResearchState`, parent channels plain; REWRITE and AUGMENT rounds no longer re-add `completed_queries`, `research_notes` or `tavily_results`: `domain/state.py`, `tests/test_state_hygiene.py`.
- Research subgraph behind `input_schema`/`output_schema` (five keys in; `valid_courses`, `digest`, `metrics`, `discard_reason`, `research_pass` out), stateful via `checkpointer=True`; outer `AgentState` is 15 channels (13 plus `research_pass` and `research_retries`); the subgraph schema has 23, nine shared with the outer state and fourteen private; `start_research_pass` stamps a pass counter, and a stale result goes through `retry_research_pass` once, then raises `StaleResearchResultError` (schema v3); `begin_pass` resets private state on a fresh pass; planning failures route to `discard_run`: `domain/state.py`, `workflows/`, `tests/test_top_level_state.py`, `tests/test_graph_topology.py`, `tests/test_nested_checkpoints_postgres.py` (§2.3).
- Declared PII data-flow check: `domain/pii.py`, `privacy/flow.py`, `privacy/flow_specs.py`,
  `tests/test_flow_rules.py` (§10.1).

## Evals

- Eval harness skeleton (E1 milestone 1): YAML cases per level, grader registry, `run.py`, pytest parametrization. 33 L1a rows (validator evidence rule, dedup, planner, replan route) and 7 L3 scenarios (approve, discard, rewrite, augment, reset, multi-round, parked at gate) on `MemorySaver`, no key, no network. Mutating the evidence rule fails 10 rows: `evals/`, `uv run python -m evals.run`, `uv run pytest evals -n 4`.
- E1 milestone 2: two Postgres-backed L3 scenarios (`requires: [postgres]`, skipped without `TEST_DATABASE_URL`): a wrong owner and a thread stored under another schema version are refused and leave the parked thread untouched; skipping either check in `privacy/registry.py` fails its case. `.github/workflows/ci.yml` runs the unittest suite and the evals on every push and pull request against a pgvector service container, migrations applied by `scripts/apply_migrations.py`. Rehearsed locally on a fresh pgvector container with a fresh venv (`uv sync --locked`, 481 tests, 42 evals); the workflow has not run on GitHub: `evals/runners.py`, `evals/graders/l3.py`, `evals/cases/l3/scenarios.yaml`, `.github/workflows/ci.yml`.
- E1 milestone 3, partly: `evals/labels/judge_notes.yaml` (32 generated outputs, labelled by the owner with `evals/labels/label_ui.py`), `python -m evals.calibrate_judge` (TPR and TNR per criterion, failure positive; on the owner's labels `captured` 0.93 / 0.67, `scope` 0.57 / 1.00, `no_invention` 0.91 / 0.86, `no_loss` 0.75 / 1.00, `polarity` 1.00 / 1.00, one labeller, 32 items, so run to run noise is large; judge prompt revised once against these labels, so they are no longer a held-out measure), and the first live memory run. Three harness bugs in `tests/test_memory_e2e_live.py` fixed; 7 of 12 cases fail live at one trial, 5 from provider names redacted as `<PERSON>`. Judge numbers are against generator labels, not yet the owner's; L1b live for router and synthesizer not built: `evals/calibrate_judge.py`, `evals/labels/`, `notes/04`.

## Article

- Article: §2.1, §3.1, §13 and the appendix drafted in `DRAFT.md`. Only §0 and §1 remain, and are best written last.
- Article freshness pass: `DRAFT.md` snippets and claims checked against the code (research-graph state type and node names, outbox claim SQL, `ServedModelLogger` call, per-schema `user_memory`, topology-change test no longer listed as untested).

## Not in the code

Each of these is covered in the article as prose only, and the reason is in `DECISIONS.md`:
`CachePolicy`, node-level `timeout=`, `Command` for the gateway failure,
dynamic `interrupt()`, circuit
breaker, cross-worker coordination.

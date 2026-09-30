# CLAUDE.md — Course Discovery Agent

## What This Project Is

A personalized, cache-first course research agent controlled by LangGraph, built as the running example for a Halo Lab engineering article. The article's subject is **using LangGraph to implement complex AI agents in production** — state contracts, reducers, plan-driven `Send` fan-out, subgraphs, `interrupt_before` human review, checkpointing/durability, Store vs. checkpointer memory, retry/cache policies, and provider-fallback composition. The course-discovery domain (memory + cache + planning + search + extraction + evidence validation + replanning) is the vehicle for demonstrating these patterns, not the point of the article.

**Search is mocked, deliberately.** `course_discovery/research_agent/search/tavily_client.py` serves results from a static in-repo catalog (`mock_catalog.py`) instead of calling a real search API. Course listing pages are mostly JS/PHP-rendered and poorly indexed, so real web search would introduce operational noise (rate limits, flaky results, API keys) that has nothing to do with the article's subject. The code is written so a real provider (Tavily, Serper, Brave) could replace `TavilyClient` behind the same `search()` signature without touching graph or node code — call this out in the article as "production would swap this one class," not as a gap.

## Source of Truth

One document per question. `specs/` holds live documents; `archive/` holds superseded ones and is never read for context.

| Question | Document |
|---|---|
| What is the graph, who owns each decision, what are the known gaps? | `specs/ARCHITECTURE.md` |
| What is implemented and tested? | `specs/STATUS.md` |
| What was settled and must not be reopened? | `specs/DECISIONS.md` |
| What is the next work? | `specs/BACKLOG.md` |
| What must the user-memory update handle, and how is each case tested? | `specs/FEEDBACK.md` |
| What will the article say, section by section, within which word budget? | `specs/article/OUTLINE.md` |
| What has been drafted? | `specs/article/DRAFT.md` |
| What was verified about LangGraph for a section? | `specs/article/notes/` (five files by theme) |

Loading rules. Always: this file, `ARCHITECTURE.md`, `STATUS.md`, `DECISIONS.md`. When implementing: the one backlog item (and `FEEDBACK.md` for P4 to P6). When drafting: its outline section, the matching notes file, and the `course-article-style` skill. Never: `archive/`. When specs and `archive/PLAN.md` conflict, the specs win.

Before implementing, check the item and the code it touches against `ARCHITECTURE.md`, `STATUS.md` and `DECISIONS.md`. Stop and ask if the item contradicts a `DECISIONS.md` entry, or if the docs and the code disagree about something the item depends on. Inconsistencies elsewhere: note them, keep going, and report them at the end. Do not audit the whole repo; that is a separate review task.

Keeping the docs current. Any change to behavior, not only a backlog item, triggers these updates. Read the target file first so you extend it rather than duplicate or contradict it.

| If you… | Update |
|---|---|
| add or remove a node or edge, or change who decides (rules vs LLM), or open or close a known gap | `ARCHITECTURE.md` |
| add or change a node or state channel | `privacy/flow_specs.py` |
| verify a LangGraph behavior or measure something | the matching `notes/` file, with the library version and how it was checked |
| choose between real alternatives, or rule something out on purpose (a future reader would otherwise reopen it) | `DECISIONS.md`: append under the fitting heading, what and why |
| change behavior that a drafted section describes | that section in `DRAFT.md` |
| finish anything | one line in `STATUS.md` |

A finding that is both evidence and a choice goes in both: the evidence in notes, the choice in `DECISIONS.md` pointing at it. Notes files by theme: `01-state-and-control-flow`, `02-durability-replay-effects`, `03-resilience-and-providers`, `04-observability-and-compliance`, `05-scale-and-scope`.

Definition of done for a backlog item: code and tests pass; the table above applied; the item deleted from `BACKLOG.md`; and, if the item has an outline section, that section drafted within its word budget (infra-only items with no section skip this).

## Architecture

The graph, node responsibilities, decision ownership (rules vs LLM) and known gaps live only in `specs/ARCHITECTURE.md`. Read it before implementing anything, to understand the system the change lands in. It is deliberately not duplicated here.

## Key Directories

```
course_discovery/
  workflows/        outer_graph.py, research_graph.py
  domain/           AgentState, Pydantic models (state contract)
  research_agent/   memory, cache, planner, search, extraction, validator, synthesizer
  review/           review gate, feedback router, publish/discard nodes
  persistence/      Postgres adapter, checkpointer factory + msgpack allowlist, AES-GCM checkpoint encryption
  privacy/          thread registry (`run_threads`) and per-user erasure (`python -m course_discovery.privacy erase`)
                    plus a declared PII data-flow check (`flow.py`, `flow_specs.py`; `Pii` marker in `domain/pii.py`). Adding a node or state channel means updating `flow_specs.py`; `tests/test_flow_rules.py` fails otherwise
  effects/          EffectGateway port, outbox stores, worker (publish goes through here)
  guardrails/       app-side PII port: `redact_pii`, `set_redactor`, fail-closed wrapper, `PII_GUARDRAIL` switch
pii_redaction/      standalone package (no `course_discovery` imports): `Redactor` protocol, `PresidioRedactor`
  resilience.py     transient-error classification, RetryPolicy, timeout settings
  observability/    structured logging
  app/              CLI, prompts, gateway
migrations/         SQL schema (Postgres + pgvector)
specs/              live docs (architecture, status, decisions, backlog); article/ holds outline, draft, notes
archive/            superseded docs, never read for context
```

## Environment

```powershell
$env:OPENROUTER_API_KEY   # DeepSeek V4.1 Flash (Gemini 2.5 Flash Lite fallback) for gateway/synthesis/router
$env:DATABASE_URL     # Postgres+pgvector (in-memory seed cache if absent)
```

No search API key is required — `search_web_for_courses` reads from the mock catalog.

## Run

```bash
uv sync
uv run python main.py
```

Optional local Postgres (pgvector) and the integration tests:

```bash
docker compose up -d
export TEST_DATABASE_URL=postgresql://course:course@localhost:55432/course_discovery
uv run python -m unittest discover tests
```

Without `TEST_DATABASE_URL` the integration tests skip. `docker-compose.yml` applies `migrations/` on a fresh volume only; apply a new migration by hand to an existing one (`007_course_embedding_index.sql` is the latest; rows written before it keep `NULL` embeddings until `python -m course_discovery.research_agent.embeddings backfill`).

Tracing is off until an exporter is configured. `docker compose --profile tracing up -d jaeger`, then `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run python main.py` and open http://localhost:16686 (`OTEL_TRACES_EXPORTER=console` prints spans instead, `OTEL_SDK_DISABLED=true` turns it off). Prompt and state content is redacted from spans unless `OTEL_CAPTURE_CONTENT=true`. Exporter failures never fail a run. Metrics (cache lookups by hit/miss, run duration per segment and outcome, run and review outcomes, review wait, degraded/fail-closed paths by component and reason, search calls, LLM calls/errors/fallbacks/latency/tokens on the GenAI conventions, transient errors seen by `RetryPolicy`, outbox outcomes with dead-letter reason, delivery latency, backlog/dead-letter/oldest-age gauges) are off until `OTEL_METRICS_EXPORTER=otlp` or `console`; Jaeger does not ingest metrics, so point OTLP at a collector. Spans are flushed at the end of each run segment and batched every 1s (`OTEL_BSP_SCHEDULE_DELAY`), so a SIGKILL loses at most the last second of spans plus any span still open. Metrics are force-flushed at the same points but otherwise export every 60s (`OTEL_METRIC_EXPORT_INTERVAL`), so a SIGKILL loses the counters recorded since the last segment end. Log previews of queries, feedback and titles are `null` unless `OTEL_CAPTURE_CONTENT=true`; `SERVICE_VERSION` and `DEPLOYMENT_ENVIRONMENT` set the matching resource attributes.

With `DATABASE_URL` set, the CLI checkpoints to Postgres (`open_checkpointer`). `EFFECT_GATEWAY=inline` (default) delivers the publish effect during `send_approved_courses`; `EFFECT_GATEWAY=outbox` only queues it, and `python -m course_discovery.effects` runs the delivery worker.

## LLM

All LLM nodes go through `course_discovery/app/llm.py:build_llm()`, which returns a `ChatOpenRouter` (`langchain-openrouter`) with `temperature=0` and structured output via Pydantic. Primary model is `deepseek/deepseek-v4.1-flash`; OpenRouter's server-side `models` priority array falls back to `google/gemini-2.5-flash-lite`. Do not build chat models anywhere else, and do not add providers without updating both code and article.

## Key Constraints

- **Never auto-publish.** `interrupt_before=["await_human_review"]` is always compiled in.
- **Loop budget.** `max_research_iterations` caps the replanning loop (default: 2–3).
- **Evidence over claims.** Treat missing evidence as `uncertain`, not `valid`.
- **Cache first.** The search worker is only dispatched for gaps, freshness checks, or new topics.
- **PII never persists raw.** User queries and review feedback go through `guardrails.redact_pii` (backed by `pii_redaction.PresidioRedactor`, local spaCy `en_core_web_sm`, no network; to move it out of process, add another `Redactor` implementation, e.g. an HTTP client, and return it from `guardrails/pii.py:_default_redactor`) before entering `AgentState`, the gateway LLM call, `record_feedback`, and log previews/error messages. Redaction failure raises `PiiGuardrailError`. `PII_GUARDRAIL=off` disables it for local debugging only. Person names in queries (e.g. an instructor) are redacted too.
- **Encryption at rest.** With `CHECKPOINT_ENCRYPTION_KEYS` set (`<id>:<base64 32-byte key>[,<older id>:<key>...]`; first key encrypts, all decrypt, so rotation means prepending a key), the checkpointer seals serde blobs and writes with AES-GCM via `EncryptedSerializer`, and `SealingSaver` (a wrapper over any `BaseCheckpointSaver`, public API only) also seals string channel values, which the Postgres saver otherwise inlines as plaintext in `checkpoints.checkpoint`. `CHECKPOINT_ENCRYPTION_REQUIRED=true` refuses to start without keys. Plaintext rows written before enabling stay readable. Not covered: `checkpoints.metadata`, the `outbox.payload` JSONB, and the application tables.
- **Erasure goes through `run_threads`.** Encrypted checkpoints cannot be searched by user, so every run registers `(thread_id, user_id)` at start (migrations `005_run_threads.sql` and `006_run_threads_activity.sql`, apply by hand to an existing volume). `prune --checkpoints` uses the same table (`last_activity_at`, bumped on every resume). `authorize_thread` (same table) refuses a resume or `update_state` from a user who does not own the thread, and is skipped without `DATABASE_URL`. `erase --user-id X` is a dry run; `--execute` deletes checkpoints via `adelete_thread` (never reads them, so it works without the key), then every table in `privacy/sources.py:USER_DATA_SOURCES` in one transaction, and exits 1 if anything remains. A new table must be added there or to `NOT_USER_DATA`; `tests/test_erasure.py` fails otherwise. Deleting a `users` row alone leaves `recommendation_events` behind (`ON DELETE SET NULL`).
- **Fail closed.** Gateway parsing, DB access, and search failures all fail closed with structured error state — no silent fallback to hallucination. DB writers re-raise; never swallow a write failure into a log line.
- **No direct side effects in nodes.** External effects go through `EffectGateway.submit` with a key derived from `run_id`, never generated at execution time. Add `RetryPolicy` only to nodes that are read-only or idempotent, and let transient errors propagate so it can fire.

## Article Framing

> LangGraph is the control shell for a bounded, personalized research agent — but the article's actual subject is the LangGraph mechanics themselves: reducers, `Send` fan-out, subgraphs, `interrupt_before`, checkpointer durability, `Store` vs. thread-scoped memory, node-level retry/cache policies, and Runnable-level provider fallback. The course-discovery domain gives every pattern something concrete and narratively coherent to attach to.

The article is NOT a LangGraph basics tutorial, and it is NOT a course-search product spec. Search is intentionally mocked so the domain never becomes the story. LangGraph features are introduced where they illustrate a production-relevant principle, with a small real snippet from this codebase — not a fully productized feature.

## Writing Style

When drafting or editing article prose, load the `course-article-style` skill
(in `.claude/skills/course-article-style/SKILL.md`) for the voice and
architectural-level conventions used throughout this repo's specs.

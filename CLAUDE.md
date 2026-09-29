# CLAUDE.md — Course Discovery Agent

## What This Project Is

A personalized, cache-first course research agent controlled by LangGraph, built as the running example for a Halo Lab engineering article. The article's subject is **using LangGraph to implement complex AI agents in production** — state contracts, reducers, plan-driven `Send` fan-out, subgraphs, `interrupt_before` human review, checkpointing/durability, Store vs. checkpointer memory, retry/cache policies, and provider-fallback composition. The course-discovery domain (memory + cache + planning + search + extraction + evidence validation + replanning) is the vehicle for demonstrating these patterns, not the point of the article.

**Search is mocked, deliberately.** `course_discovery/research_agent/search/tavily_client.py` serves results from a static in-repo catalog (`mock_catalog.py`) instead of calling a real search API. Course listing pages are mostly JS/PHP-rendered and poorly indexed, so real web search would introduce operational noise (rate limits, flaky results, API keys) that has nothing to do with the article's subject. The code is written so a real provider (Tavily, Serper, Brave) could replace `TavilyClient` behind the same `search()` signature without touching graph or node code — call this out in the article as "production would swap this one class," not as a gap.

## Source of Truth

`specs/` is the authoritative source for:

- `ARTICLE_OUTLINE.md` — article sections, narrative structure, word counts
- `ARTICLE_TODO.md` — running checklist of production LangGraph principles (retry, cache
  policy, durability, Store vs. checkpointer, OTel, provider fallback, API brittleness)
  to fold into the article; check before drafting any new section
- `IMPLEMENTATION_PLAN.md` — implementation steps and milestones
- `PROPOSED_METRICS.md` — quality, efficiency, and personalization metrics
- `PROPOSED_TESTING.md` — test scope and coverage expectations
- `FUTURE_IDEAS.md` — deferred decisions and open questions
- `ARTICLE.md` — full article draft (work in progress)
- `FINAL_VERSION.md` — published/near-final draft when it exists

`PLAN.md` in the root is the original architecture design document. It was the input that shaped the current implementation. When specs and PLAN.md conflict, the specs win — they have been written after PLAN.md and reflect decisions made during implementation.

## Architecture (Current Implementation)

```
gateway
  -> research_agent subgraph
  -> [interrupt_before: review_gate]
  -> router
      -> PUBLISH -> publish_node -> user_memory_update -> END
      -> REWRITE -> research_agent subgraph
      -> AUGMENT -> research_agent subgraph
      -> RESET   -> gateway
      -> DISCARD -> discard_node -> END
```

Research subgraph (the complex agent):

```
research_entry
  -> user_memory_lookup        (Postgres or in-memory seed)
  -> course_cache_lookup       (Postgres + pgvector, or seed cache)
  -> research_planner          (LLM: decides queries, cache-first vs. web)
  -> tavily_search_worker(s)   (via Send, parallel, only for gaps)
  -> candidate_extractor       (LLM: structured candidates from Tavily results)
  -> aggregate + dedup
  -> evidence_validator        (LLM: checks each candidate vs. filters + memory)
  -> replanner                 (if too few valid; bounded by max_research_iterations)
  -> course_cache_upsert       (persist valid candidates)
  -> synthesizer               (LLM: ranked, evidence-backed digest)
```

## Key Directories

```
course_discovery/
  workflows/        outer_graph.py, research_graph.py
  domain/           AgentState, Pydantic models (state contract)
  research_agent/   memory, cache, planner, search, extraction, validator, synthesizer
  review/           review_gate, router
  persistence/      Postgres adapter, checkpointer factory + msgpack allowlist
  effects/          EffectGateway port, outbox stores, worker (publish goes through here)
  resilience.py     transient-error classification, RetryPolicy, timeout settings
  observability/    structured logging
  app/              CLI, prompts, gateway
migrations/         SQL schema (Postgres + pgvector)
specs/              article drafts, implementation plan, metrics, testing
```

## Environment

```powershell
$env:OPENROUTER_API_KEY   # DeepSeek V4.1 Flash (Gemini 2.5 Flash Lite fallback) for gateway/synthesis/router
$env:DATABASE_URL     # Postgres+pgvector (in-memory seed cache if absent)
```

No search API key is required — `tavily_search_worker` reads from the mock catalog.

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

Without `TEST_DATABASE_URL` the integration tests skip. `docker-compose.yml` applies `migrations/` on a fresh volume only; apply a new migration by hand to an existing one.

Tracing is off until an exporter is configured. `docker compose --profile tracing up -d jaeger`, then `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run python main.py` and open http://localhost:16686 (`OTEL_TRACES_EXPORTER=console` prints spans instead, `OTEL_SDK_DISABLED=true` turns it off). Prompt and state content is redacted from spans unless `OTEL_CAPTURE_CONTENT=true`. Exporter failures never fail a run. Metrics (cache lookups/hits, run duration per segment, LLM calls/fallbacks, outbox outcomes with dead-letter reason) are off until `OTEL_METRICS_EXPORTER=otlp` or `console`; Jaeger does not ingest metrics, so point OTLP at a collector. Spans are flushed at the end of each run segment and batched every 1s (`OTEL_BSP_SCHEDULE_DELAY`), so a SIGKILL loses at most the last second of spans plus any span still open.

With `DATABASE_URL` set, the CLI checkpoints to Postgres (`open_checkpointer`). `EFFECT_GATEWAY=inline` (default) delivers the publish effect during `publish_node`; `EFFECT_GATEWAY=outbox` only queues it, and `python -m course_discovery.effects` runs the delivery worker.

## LLM

All LLM nodes go through `course_discovery/app/llm.py:build_llm()`, which returns a `ChatOpenRouter` (`langchain-openrouter`) with `temperature=0` and structured output via Pydantic. Primary model is `deepseek/deepseek-v4.1-flash`; OpenRouter's server-side `models` priority array falls back to `google/gemini-2.5-flash-lite`. Do not build chat models anywhere else, and do not add providers without updating both code and article.

## Key Constraints

- **Never auto-publish.** `interrupt_before=["review_gate"]` is always compiled in.
- **Loop budget.** `max_research_iterations` caps the replanning loop (default: 2–3).
- **Evidence over claims.** Treat missing evidence as `uncertain`, not `valid`.
- **Cache first.** The search worker is only dispatched for gaps, freshness checks, or new topics.
- **Fail closed.** Gateway parsing, DB access, and search failures all fail closed with structured error state — no silent fallback to hallucination. DB writers re-raise; never swallow a write failure into a log line.
- **No direct side effects in nodes.** External effects go through `EffectGateway.submit` with a key derived from `run_id`, never generated at execution time. Add `RetryPolicy` only to nodes that are read-only or idempotent, and let transient errors propagate so it can fire.

## Article Framing

> LangGraph is the control shell for a bounded, personalized research agent — but the article's actual subject is the LangGraph mechanics themselves: reducers, `Send` fan-out, subgraphs, `interrupt_before`, checkpointer durability, `Store` vs. thread-scoped memory, node-level retry/cache policies, and Runnable-level provider fallback. The course-discovery domain gives every pattern something concrete and narratively coherent to attach to.

The article is NOT a LangGraph basics tutorial, and it is NOT a course-search product spec. Search is intentionally mocked so the domain never becomes the story. LangGraph features are introduced where they illustrate a production-relevant principle, with a small real snippet from this codebase — not a fully productized feature.

## Writing Style

When drafting or editing article prose, load the `course-article-style` skill
(in `.claude/skills/course-article-style/SKILL.md`) for the voice and
architectural-level conventions used throughout this repo's specs.

# Course Discovery Agent

Built as part of the engineering work at [Halo Lab](https://www.halolab.us).

This repository implements a personalized, cache-first course research agent controlled by LangGraph.

The agent:

- parses a learner query into structured filters;
- loads durable user preferences;
- checks a shared course cache before web search;
- plans Tavily searches only for missing or stale evidence;
- extracts course candidates with evidence;
- deduplicates and validates candidates;
- replans when too few valid courses exist;
- synthesizes an evidence-backed digest;
- interrupts before publication for human review;
- records useful courses and feedback through the persistence layer.

## Architecture

```text
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

The research subgraph owns the complex agent loop:

```text
research_entry
  -> user_memory_lookup
  -> course_cache_lookup
  -> research_planner
  -> tavily_search_worker(s) via Send when needed
  -> candidate_extractor
  -> aggregate
  -> dedup
  -> evidence_validator
  -> replanner when validation is insufficient
  -> course_cache_upsert
  -> synthesizer
```

The graph uses in-memory checkpointing for the local demo. The durable course cache and user memory are designed for Postgres plus pgvector; see `migrations/001_postgres_pgvector.sql`.

## Environment

Optional keys:

```powershell
$env:GOOGLE_API_KEY="your-key"   # structured LLM parsing/synthesis/router
$env:TAVILY_API_KEY="your-key"   # web search
$env:DATABASE_URL="postgresql://..." # durable memory/cache
```

If `GOOGLE_API_KEY` is absent, the gateway, synthesis, and router use deterministic fallbacks. If `TAVILY_API_KEY` is absent, Tavily workers record research notes and the digest reports the limitation. If `DATABASE_URL` is absent, the cache lookup uses a small local seed cache.

## Run

```bash
uv sync
uv run python main.py
```

At review time the CLI prints the digest plus cache/search/validation counts. Feedback commands:

- `approve` or `publish`
- `rewrite: ...`
- `augment: ...`
- `reset: ...`
- `discard`

## Roadmap

This repo delivers **Milestone 1** of a larger planned system. The full milestone breakdown lives in [`specs/IMPLEMENTATION_PLAN.md`](specs/IMPLEMENTATION_PLAN.md).

| # | Milestone | Status |
|---|-----------|--------|
| M1 | **Article-Ready Core** — LangGraph patterns, mock workers, CLI human-in-the-loop | ✅ complete |
| M2 | Telegram Integration — real bot, live status, inline approval buttons | planned |
| M3 | Real Discovery Workers — API-backed and crawler-based workers with `SearchFilters` | planned |
| M4 | Embedding Dedup + Postgres — pgvector, LangGraph Postgres checkpointer | planned |
| M5 | Production Hardening — scheduling, multilingual support, monitoring dashboards | planned |

See [`MAYBE_LATER.md`](MAYBE_LATER.md) for the current backlog of future ideas.

## Key Files

- `course_discovery/workflows/outer_graph.py`: review/publish control shell.
- `course_discovery/workflows/research_graph.py`: the complex research-agent subgraph.
- `course_discovery/domain/`: state and Pydantic contracts.
- `course_discovery/research_agent/`: memory, cache, planning, search, extraction, validation, and synthesis capabilities.
- `course_discovery/review/`: human gate and feedback router.
- `course_discovery/persistence/`: Postgres connection adapter.
- `course_discovery/observability/`: logging helpers.
- `course_discovery/app/`: CLI, prompts, and gateway parsing.
- `ARTICLE.md`: article-style explanation of the implementation.
- `PLAN.md`: original implementation plan.
- `specs/`: full implementation plan, article drafts, proposed metrics and testing approach.

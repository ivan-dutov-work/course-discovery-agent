# Course Discovery Agent

Built as part of the engineering work at [Halo Lab](https://www.halolab.us).

This repository implements a personalized, cache-first course research agent controlled by LangGraph.

The agent:

- parses a learner query into structured filters;
- loads durable user preferences;
- checks a shared course cache before web search;
- plans searches only for missing or stale evidence (search results are served from a mock catalog — see below);
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

## Search is mocked

`tavily_search_worker` reads from a small static catalog (`course_discovery/research_agent/search/mock_catalog.py`) instead of calling a real search API. This project demonstrates LangGraph patterns, not course-search product quality — real course listing pages are mostly JS/PHP-rendered and poorly indexed, so a real search integration adds operational noise unrelated to the article's subject. `TavilyClient.search()` keeps the shape a real provider (Tavily, Serper, Brave) would have, so swapping it in production is a one-class change; no graph or node code depends on it being mocked.

## Environment

Optional keys:

```powershell
$env:OPENROUTER_API_KEY="..."   # structured LLM parsing/synthesis/router
$env:DATABASE_URL="postgresql://..." # durable memory/cache
```

If `OPENROUTER_API_KEY` is absent, the gateway, synthesis, and router use deterministic fallbacks. If `DATABASE_URL` is absent, the cache lookup uses a small local seed cache. No search API key is needed — search results are served from a mock catalog (see below).

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

The current implementation covers the full complex research agent loop (memory, cache,
planning, search, extraction, validation, replanning, synthesis) with search mocked.
The full implementation order lives in [`specs/IMPLEMENTATION_PLAN.md`](specs/IMPLEMENTATION_PLAN.md).

Deferred work and future ideas live in [`specs/FUTURE_IDEAS.md`](specs/FUTURE_IDEAS.md).

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

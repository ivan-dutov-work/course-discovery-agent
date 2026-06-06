# CLAUDE.md — Course Discovery Agent

## What This Project Is

A personalized, cache-first course research agent controlled by LangGraph, built for a Halo Lab engineering article. The article's subject is the **complex research agent** (memory + cache + planning + Tavily search + extraction + evidence validation + replanning). The surrounding LangGraph graph (outer workflow, review gate, router) is the control shell — secondary to the article's argument.

## Source of Truth

`specs/` is the authoritative source for:

- `ARTICLE_OUTLINE.md` — article sections, narrative structure, word counts
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
  persistence/      Postgres adapter
  observability/    structured logging
  app/              CLI, prompts, gateway
migrations/         SQL schema (Postgres + pgvector)
specs/              article drafts, implementation plan, metrics, testing
```

## Environment

```powershell
$env:GOOGLE_API_KEY   # Gemini 2.0 Flash for gateway/synthesis/router
$env:TAVILY_API_KEY   # web search (graceful degradation if absent)
$env:DATABASE_URL     # Postgres+pgvector (in-memory seed cache if absent)
```

## Run

```bash
uv sync
uv run python main.py
```

## LLM

All LLM nodes use Gemini 2.0 Flash (`langchain-google-genai`) with `temperature=0` and structured output via Pydantic. Do not introduce other providers without updating both code and article.

## Key Constraints

- **Never auto-publish.** `interrupt_before=["review_gate"]` is always compiled in.
- **Loop budget.** `max_research_iterations` caps the replanning loop (default: 2–3).
- **Evidence over claims.** Treat missing evidence as `uncertain`, not `valid`.
- **Cache first.** Tavily is only called for gaps, freshness checks, or new topics.
- **Fail closed.** Gateway parsing, DB access, and Tavily failures all fail closed with structured error state — no silent fallback to hallucination.

## Article Framing

> LangGraph controls a bounded, personalized research agent. The complex agent lives in the research subgraph; the surrounding graph provides persistence, interrupts, routing, and review controls.

The article is NOT about LangGraph basics. It is about what makes this agent **complex**: memory, cache-first strategy, evidence extraction, validation, replanning, and personalized synthesis. LangGraph features are introduced where they serve this story.

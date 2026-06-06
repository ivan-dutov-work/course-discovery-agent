# Course Discovery Agent — Implementation Plan

## Project Goal

Build a personalized, cache-first course research agent controlled by LangGraph.

The agent understands a user's learning goals, retrieves long-term memory, reuses a
shared course cache, searches the web only for gaps, validates evidence, adapts its
strategy, and produces personalized course recommendations. The surrounding LangGraph
workflow provides persistence, interrupts, routing, and review controls — it is the
control shell, not the subject.

---

## Architecture Summary

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

Research subgraph:

```
research_entry
  -> user_memory_lookup
  -> course_cache_lookup
  -> research_planner
  -> tavily_search_worker(s)  [via Send, parallel, only for gaps]
  -> candidate_extractor
  -> aggregate + dedup
  -> evidence_validator
  -> enough_valid? [conditional]
      -> replanner  [if too few valid; bounded by max_research_iterations]
      -> synthesizer
  -> course_cache_upsert
```

---

## Implementation Order

These steps reflect the order in which the system was designed to be built.
Each step is a meaningful unit of work with a clear deliverable.

### Step 1 — Inspect current project structure

Review existing node implementations, model definitions, and graph wiring.
Identify what exists, what is stubbed, and what needs to be created.

### Step 2 — Postgres connection and migrations

Add database connection configuration. Write the initial SQL migration:
`users`, `user_preferences`, `courses`, `course_evidence`,
`recommendation_events`, `research_runs` tables.

Environment: `DATABASE_URL`. Graceful fallback to in-memory seed cache when absent.

### Step 3 — pgvector support and embedding generation

Enable the `pgvector` extension. Add `course_embedding vector` to `courses` and
`profile_embedding vector` to `user_preferences`. Add an embedding utility that
generates vectors from course descriptions and user preference summaries using
the configured embedding model.

### Step 4 — Data-access layer

Implement repository functions for:

- `user_memory_lookup`: load `UserMemory` from `user_preferences` for a given user.
- `course_cache_lookup`: structured filter + pgvector similarity query against
  `courses`.
- `course_cache_upsert`: insert or update course record, evidence, and embedding.
- `recommendation_events`: record accepted/rejected recommendations.
- `research_runs`: write run-level observability metrics.

### Step 5 — Pydantic models

Add or update models:

- `UserMemory`
- `ResearchPlan`
- `TavilySearchResult`
- `EvidenceItem`
- `CourseCandidate` (with `source`, `evidence`, `confidence`)
- `CandidateValidation`
- `ResearchRunMetrics`

### Step 6 — Extend `AgentState`

Add new fields to the state contract:

```
user_id, user_memory, cache_candidates, research_plan,
tavily_results (reducer), extracted_candidates (reducer),
valid_courses, rejected_courses, uncertain_courses,
validation_results, research_iteration, max_research_iterations,
completed_queries, research_notes (reducer),
cache_hits, tavily_calls, metrics
```

Remove or replace fixed worker keys (`worker_a_courses`, etc.) with
source-oriented buffers.

### Step 7 — `user_memory_lookup` node

Load `UserMemory` from Postgres. Fall back to default (empty) memory when database
is absent or user has no history. Write compact summary to state — do not inject
unbounded raw history into LLM prompts.

### Step 8 — `course_cache_lookup` node

Query validated courses using structured filters plus pgvector semantic similarity.
Exclude courses in the user's completed or rejected lists, and from avoided providers.
Mark stale candidates that need a Tavily freshness check. Increment `cache_hits`.

### Step 9 — Tavily client wrapper

Implement `TavilyClient` with async `search(query, max_results)`. Validate API key
at startup. Normalize result payloads into `TavilySearchResult`. Log errors and empty
result sets into state — do not let Tavily failures collapse the run.

### Step 10 — Plan-driven Tavily fan-out

Replace fixed mock workers with plan-driven fan-out using `Send`. The `dispatch_tavily_workers`
function reads `research_plan.search_queries` and emits one `Send` per query. Workers
run in parallel. Results accumulate via list reducer. Increment `tavily_calls` per call.

### Step 11 — `candidate_extractor` node

LLM node. Convert Tavily result snippets into structured `CourseCandidate` objects
with `EvidenceItem` lists. Mark unknown fields explicitly. Do not hallucinate metadata
not present in the source.

### Step 12 — `evidence_validator` node

LLM node. For each candidate, produce a `CandidateValidation` against `SearchFilters`
and `UserMemory`. Classify: `valid`, `rejected`, or `uncertain`. Populate `reasons`
and `missing_evidence`. Treat missing evidence as `uncertain`, never as `valid`.
Write to `valid_courses`, `rejected_courses`, `uncertain_courses`.

### Step 13 — `enough_valid?` conditional routing

Check `len(valid_courses) >= research_plan.min_valid_candidates`:

- Yes → `synthesizer`
- No, `research_iteration < max_research_iterations` → `replanner`
- No, budget exhausted → `synthesizer` with limitation note in `research_notes`

### Step 14 — `replanner` node and loop budget

LLM node. Inspect rejection reasons and missing evidence from current iteration.
Generate new cache and/or Tavily queries. Append to `completed_queries` to avoid
repetition. Increment `research_iteration`. Return a `Command` that updates state
and routes to `course_cache_lookup` or `tavily_search_workers` as appropriate.

### Step 15 — `course_cache_upsert` node

Upsert valid (and useful uncertain) courses into Postgres. Store evidence, validation
status, confidence, `last_seen_at`, and embedding. Increment `use_count` for
recommended courses.

### Step 16 — `user_memory_update` node

Convert publish/discard feedback into durable preferences. Record accepted and
rejected recommendations. Avoid over-learning from one interaction. Keep
user-editable memory separate from derived observations.

### Step 17 — Rename `telegram_gate` to `review_gate`

Update all references. The node is the interrupt boundary before any external
publication, regardless of the downstream channel.

### Step 18 — Router semantics update

- `PUBLISH`: record recommendation events, route to `user_memory_update`.
- `REWRITE`: rerun synthesis on existing validated candidates (no new search).
- `AUGMENT`: trigger replanning and additional search.
- `RESET`: reparse constraints and restart research from `gateway`.
- `DISCARD`: terminate, optionally record negative feedback.

### Step 19 — Metrics logging

At the end of each research run, write a `research_runs` record with:
`queries_run`, `cache_hits`, `tavily_calls`, `valid_count`, `rejected_count`,
`uncertain_count`, `replan_count`, `unsupported_claim_count`, `latency_ms`,
`token_count`.

### Step 20 — CLI output update

Print at review time: digest, cache hit count, Tavily call count, validation summary
(valid / rejected / uncertain), and any limitation notes from `research_notes`.

### Step 21 — Tests

Add focused tests for:

- Cache lookup with structured filters and pgvector.
- Memory lookup and fallback behavior.
- Research planner output structure.
- Fan-out routing: correct number of `Send` objects for given plan.
- Validation routing: valid / rejected / uncertain classification.
- Replanning loop: `research_iteration` increments correctly and stops at limit.
- `enough_valid?` edge routing under all three conditions.
- Memory update: accepted/rejected courses recorded correctly.

### Step 22 — Rewrite `ARTICLE.md`

Rewrite to match the current implementation following `specs/ARTICLE_OUTLINE.md`.

---

## Minimal Viable Version

If time is constrained, implement only:

1. Postgres course cache schema.
2. User preference memory schema.
3. Cache-first course lookup.
4. Tavily search workers for cache misses.
5. Candidate extraction.
6. Evidence validation.
7. Replanning when too few valid candidates exist.
8. Course cache upsert.
9. Basic feedback-to-memory update.

That is sufficient to make the system a defensible personalized complex research
agent while keeping the surrounding LangGraph workflow secondary.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph |
| LLM calls | LangChain + Gemini 2.0 Flash (`langchain-google-genai`) |
| Schema validation | Pydantic v2 |
| Web search | Tavily API |
| Checkpointing (demo) | LangGraph `MemorySaver` |
| Checkpointing (production) | LangGraph `AsyncPostgresSaver` |
| Durable memory / cache | PostgreSQL + pgvector |
| Embedding generation | Configurable; pgvector stores vectors |
| Fuzzy dedup | `rapidfuzz` (URL normalization + title match) |
| Migrations | SQL files in `migrations/` |
| Package management | `uv` |

---

## Deferred / Out of Scope

- Telegram bot integration (future surface, does not change the agent).
- Scheduling and cron triggers (future operational layer).
- Multi-API discovery workers (Google, Udemy, Reddit) — Tavily covers discovery.
- WebSocket dashboard and Prometheus metrics export.
- Multilingual LLM quality benchmarking.
- Playwright headless browser for JS-heavy sites.

See `specs/FUTURE_IDEAS.md` for the full backlog.

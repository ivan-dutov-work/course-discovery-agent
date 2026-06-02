# LangGraph as the Control Shell for a Personalized Course Research Agent

## TL;DR

This project implements a bounded course-discovery agent, not a generic agentic workflow demo. The agent parses a learner's request, looks up durable user preferences, checks a shared course cache first, searches Tavily only for gaps, extracts structured candidates with evidence, validates each candidate against hard constraints, replans when evidence is insufficient, and synthesizes a personalized recommendation digest.

LangGraph is the control shell. It gives the agent inspectable state, dynamic fan-out, conditional routing, checkpointed interruption, and a mandatory human review boundary before publication.

## The Problem

Course recommendations fail when they are generic, unverifiable, or repeatedly researched from scratch. A learner may ask for "free beginner Python courses with certificates", but a plain chat flow can drift into paid courses, stale links, unsupported certificate claims, or already rejected providers.

The system treats those risks as engineering constraints:

- hard requirements are parsed into `SearchFilters`;
- long-term preferences are represented as `UserMemory`;
- known validated courses are reused from a Postgres/pgvector cache;
- Tavily search is reserved for cache gaps and freshness checks;
- candidate facts must be backed by `EvidenceItem` records;
- missing evidence produces `uncertain`, not `valid`;
- replanning is bounded by `max_research_iterations`.

## Agent Scope

The complex agent lives in the research loop:

```text
research_agent subgraph:
research_entry
  -> user_memory_lookup
  -> course_cache_lookup
  -> research_planner
  -> dynamic Tavily search when needed
  -> candidate_extractor
  -> aggregate
  -> dedup
  -> evidence_validator
  -> replanner when too few valid courses exist
  -> course_cache_upsert
  -> synthesizer
```

The surrounding graph is supporting infrastructure:

```text
outer workflow:
gateway
  -> research_agent subgraph
  -> interrupt_before: review_gate
  -> router
  -> publish / rewrite / augment / reset / discard
```

This distinction matters. LangGraph does not make recommendations by itself; it makes the agent bounded, resumable, observable, and safe to put behind a human review step.

## Persistence

The durable memory and cache layer is designed for Postgres plus pgvector. The migration in `migrations/001_postgres_pgvector.sql` creates:

- `users`
- `user_preferences`
- `courses`
- `course_evidence`
- `recommendation_events`
- `research_runs`

Course data is relational and queryable: provider, level, language, price, certificate availability, validation status, freshness, and recommendation history all benefit from structured filters. pgvector adds semantic retrieval over course descriptions and preference profiles without losing SQL constraints.

The local demo still runs without `DATABASE_URL`. In that mode, `course_discovery/research_agent/cache/seed_data.py` provides a tiny seed cache so the graph can exercise cache-first behavior offline. With Postgres configured, repository modules under `course_discovery/research_agent/*/repository.py` read and write the durable tables through `course_discovery/persistence/postgres.py`.

## Dynamic Search

`research_planner` decides whether cache results are enough. If not, it emits a variable number of Tavily queries. The graph turns those into parallel workers with `Send`:

```text
research_planner
  -> Send("tavily_search_worker", query_1)
  -> Send("tavily_search_worker", query_2)
  -> ...
```

State fields populated by parallel workers use reducers, including `tavily_results`, `completed_queries`, `research_notes`, and `tavily_calls`. This prevents concurrent fan-in updates from overwriting each other.

If `TAVILY_API_KEY` is missing or a search fails, the worker records a research note. The agent can still synthesize validated cache results and state the limitation instead of inventing web evidence.

## Evidence Validation

Extraction is deliberately conservative. Tavily results become `CourseCandidate` objects with unknown fields set to `None`, not guessed values. Evidence snippets are preserved as `EvidenceItem` records.

`evidence_validator` checks each candidate against:

- parsed query filters,
- avoided providers,
- completed or rejected course URLs,
- price requirements,
- certificate requirements,
- level and language constraints,
- rating constraints when known.

Candidates are split into `valid_courses`, `rejected_courses`, and `uncertain_courses`. The synthesizer ranks only valid courses. If too few valid candidates exist and loop budget remains, `replanner` generates new gap-focused queries from validation failures and missing evidence.

## Human Review

The graph compiles with:

```python
interrupt_before=["review_gate"]
```

The CLI prints the digest and writes review feedback into checkpointed state. The router supports:

- `PUBLISH`: publish stub and record recommendation feedback;
- `REWRITE`: rerun synthesis over the same validated candidates;
- `AUGMENT`: replan/search for additional evidence;
- `RESET`: reparse constraints and restart research;
- `DISCARD`: terminate.

The review gate is intentionally secondary. It protects publication, while the agentic complexity stays in personalized research, validation, and replanning.

## Metrics

The run state carries `ResearchRunMetrics`:

- cache hits;
- queries run;
- Tavily calls;
- valid, rejected, and uncertain counts;
- replan count;
- unsupported claim count;
- latency and token placeholders for production instrumentation.

The CLI also prints cache hits, Tavily calls, and validation counts at review time. These metrics make cache-first runs comparable with web-search-heavy runs.

## Reliability Controls

The implementation keeps autonomy bounded:

- `max_research_iterations` defaults to 2;
- Tavily queries are capped by the planner;
- completed queries are tracked to avoid loops;
- missing evidence is `uncertain`;
- gateway, Tavily, and router failures fail closed or become visible limitations;
- publication remains human-gated;
- deterministic fallbacks allow local tests without model or search credentials.

## Known Limitations

This is a minimal viable implementation of the plan. The Postgres schema is present, but semantic pgvector retrieval and embedding generation are not yet implemented. Tavily extraction is snippet-based, so source quality still matters. The local seed cache is intentionally small. The memory update path records explicit feedback but does not yet infer rich preference changes from repeated behavior.

The important shift is complete: the project no longer demonstrates a mocked worker pipeline. It now demonstrates a personalized, cache-first, evidence-validating course research agent controlled by LangGraph.

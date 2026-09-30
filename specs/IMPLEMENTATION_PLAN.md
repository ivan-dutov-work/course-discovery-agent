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

### Step 9 — Search client (mocked)

Implement `TavilyClient` with async `search(query, max_results)`, serving results from
a static in-repo catalog (`mock_catalog.py`) instead of a real API. Normalize results
into `TavilySearchResult`. The article's subject is LangGraph's control-flow patterns,
not search quality — a real provider (Tavily, Serper, Brave) would replace this class
behind the same signature in production, with no graph or node changes.

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
| LLM calls | LangChain + OpenRouter (`langchain-openrouter`): DeepSeek V4.1 Flash, Gemini 2.5 Flash Lite fallback |
| Schema validation | Pydantic v2 |
| Web search | Mocked (static catalog, `TavilyClient`-shaped for a real provider swap) |
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
- Multi-API discovery workers (Google, Udemy, Reddit) — a single search worker covers discovery.
- Swapping the mocked `TavilyClient` for a real search API — deliberately out of scope; the article is about LangGraph patterns, not search integration.
- WebSocket dashboard and Prometheus metrics export.
- Multilingual LLM quality benchmarking.
- Playwright headless browser for JS-heavy sites.

See `specs/FUTURE_IDEAS.md` for the full backlog.

---

## Phase 2 — Article Alignment Changes

Phase 1 (steps 1-22) built the domain agent. Phase 2 does not add domain
features — it wires in the production LangGraph mechanics that
`specs/ARTICLE_OUTLINE.md` (sections 2-11) needs a real, small, in-repo snippet
for. Every step here is scoped to be the minimum change that makes a concept
*true of this codebase*, not a staged demonstration. Where no real, honest
change was possible, the section stays prose-only in the article (see
`ARTICLE_TODO.md`'s "explicitly ruled out" list) and there is no step for it
below.

**Before starting: pin down the exact LangGraph API.** `pyproject.toml` pins
`langgraph>=0.6.0` only. `RetryPolicy`, `CachePolicy`, `InMemoryCache`,
`input_schema`/`output_schema` on `StateGraph`, `timeout=` on `add_node`, and
the `Command`/`interrupt()` resume signature have all changed import paths or
parameter names across LangGraph minor versions. Check the installed version
(`uv pip show langgraph`) and its actual `langgraph.types`/`langgraph.cache.*`
exports before writing any of steps 27-34 — don't trust prior knowledge of the
API here.

**Known documentation/implementation mismatch (not fixed in this phase):**
`research_planner_node`, `replanner_node`, `candidate_extractor_node`, and
`evidence_validator_node` are plain deterministic Python — no LLM call, no
`with_structured_output`. Only `gateway_node`, `router_node`, and
`synthesizer_node` (per course, inside `_highlight_with_retry`) call
`ChatOpenRouter`. `CLAUDE.md`'s architecture section and
the pre-restructure `ARTICLE_OUTLINE.md` §5 both described all four as LLM nodes. This
phase does not convert them to real LLM calls (out of scope — a heuristic
extractor/validator is a legitimate design, not a bug) but every step below is
placed against the *actual* LLM nodes, not the documented ones. `CLAUDE.md`'s
architecture section should eventually be corrected to say "rule-based" for
these four nodes — tracked as a follow-up, not part of this phase.

### Step 23 — Fix the `extracted_candidates` reducer (real bug, doubles as §2.2)

**File:** `course_discovery/domain/state.py`

`extracted_candidates: list[CourseCandidate]` has no reducer, but
`candidate_extractor_node` runs once per parallel `Send` branch
(`research_graph.py`'s `tavily_search_worker → candidate_extractor` edge) and
returns `{"extracted_candidates": candidates}` from each branch. With more
than one planned query this should raise `InvalidUpdateError` on concurrent
writes to the same key. Change to
`Annotated[list[CourseCandidate], operator.add]`, matching `tavily_results`.
This is the article's reducer example precisely because it's a real fix, not
a staged one — add a regression test (Step 36) asserting a 2+ query plan
doesn't raise.

### Step 24 — `input_schema`/`output_schema` on the outer graph (§2.3)

**File:** `course_discovery/workflows/outer_graph.py` (new `TypedDict`s can
live here or in `domain/state.py`)

Add:

```python
class WorkflowInput(TypedDict):
    user_query: str
    user_id: str | None

class WorkflowOutput(TypedDict):
    digest: str | None
    published: bool
    routing_decision: RoutingAction | None
```

`StateGraph(AgentState, input_schema=WorkflowInput, output_schema=WorkflowOutput)`
(confirm exact kwarg names per the version check above). Only the outer graph
gets this — the research subgraph keeps `AgentState` as both input and output,
since it has no narrower public contract worth carving out.

### Step 25 — Explicit `durability=` on invoke (§5.2)

**File:** `course_discovery/app/cli.py`

Add `durability="async"` to both `graph.ainvoke(...)` calls in `main()`. One
line each, no behavior change from the current default — the point is making
the choice explicit and named, not changing it.

### Step 26 — Explicit `recursion_limit` (§4.4)

**File:** `course_discovery/app/cli.py`

Add `recursion_limit` to the `config` dict alongside `thread_id`, e.g.
`{"configurable": {"thread_id": run_id}, "recursion_limit": 50}`. Comment
should name the contrast: this is a generic structural backstop, independent
of the domain-level `max_iterations`/`max_research_iterations` budgets already
in `AgentState`.

### Step 27 — `timeout=` on the search worker node (§7.2)

**File:** `course_discovery/workflows/research_graph.py`

`builder.add_node("tavily_search_worker", tavily_search_worker_node, timeout=10)`.
*(Correction: langgraph 1.1.2's `add_node` has no `timeout=` parameter. The timeout
was implemented in the clients instead: `asyncio.wait_for` around search, plus LLM
request and Postgres connect/statement timeouts. See `ARTICLE_TODO.md`.)*
The one real I/O-bound call in the graph — the only node where this is
honestly motivated.

### Step 28 — `RetryPolicy` + fault injection on the search worker (§7.1, §7.4 tie-in)

**Files:** `course_discovery/research_agent/search/tavily_client.py`,
`course_discovery/research_agent/search/nodes.py`,
`course_discovery/workflows/research_graph.py`

- `tavily_client.py`: add `class TransientSearchError(Exception)`. Add an
  env-gated fault injector — `TAVILY_MOCK_FORCE_FAILURES` (int, default 0): a
  module-level counter that raises `TransientSearchError` for the first N
  calls to `search()` before succeeding. Disabled by default, so normal runs
  are unaffected; it exists only so `RetryPolicy` has something real to catch
  locally, since the mock catalog otherwise never fails.
- `search/nodes.py`: in `tavily_search_worker_node`, split the except clause —
  `except TransientSearchError: raise` (let `RetryPolicy` handle it),
  `except Exception:` keeps today's graceful-degradation behavior (write to
  `research_notes`). This is the concrete "retry = call failed, graceful
  degradation = call permanently unavailable" split from `ARTICLE_TODO.md`.
- `research_graph.py`: add `retry_policy=RetryPolicy(max_attempts=3)` to the
  `tavily_search_worker` node registration.

### Step 29 — `CachePolicy` on `course_cache_lookup` (§7.3)

**File:** `course_discovery/workflows/research_graph.py`

`course_cache_lookup_node` is deterministic on `(search_filters, user_memory)`
and gets re-invoked with unchanged inputs whenever `REWRITE` re-enters the
subgraph from `research_entry`. Add
`cache_policy=CachePolicy(ttl=...)` to its node registration, and
`builder.compile(cache=InMemoryCache())` on `build_research_graph()`'s return.
Framing: this is a request-level cache *in front of* the domain cache — it
skips re-querying Postgres/pgvector entirely on an exact-input repeat, which
is a different, narrower guarantee than the domain cache's semantic-similarity
matching. (Originally scoped for `research_planner`, but that node isn't an
LLM call — see the mismatch note above — so the latency story is a DB
round-trip saved, not an LLM call saved. Still a real, honest example.)

### Step 30 — `Command` in `replanner_node` (§3.4)

**Files:** `course_discovery/research_agent/planning/nodes.py`,
`course_discovery/workflows/research_graph.py`

Change `replanner_node`'s return from a plain dict to
`Command(update={...}, goto=[Send("tavily_search_worker", {...}) for q in plan.search_queries] or "aggregate")`,
folding what `_dispatch_search_queries` currently does for the replanner path
into the node itself. Remove `"replanner"` as a source of
`add_conditional_edges(..., _dispatch_search_queries, ...)` in
`research_graph.py`, since `Command.goto` now owns that routing. Leave
`research_planner`'s path on the existing conditional-edge pattern unchanged,
so the article can show both side by side — same routing decision, two
different LangGraph mechanisms.

### Step 31 — Dynamic `interrupt()` for zero-valid-after-budget-exhausted (§4.2)

**Files:** `course_discovery/research_agent/validation/nodes.py`,
`course_discovery/workflows/research_graph.py`, `course_discovery/app/cli.py`

This is the largest single change in this phase — new node, new edge, new CLI
resume path.

- `validation/nodes.py`: add `low_confidence_check_node`. Reached when
  `enough_valid()` would otherwise fall through to `course_cache_upsert` with
  zero valid courses and the iteration budget exhausted. Calls
  `answer = interrupt({"reason": "no_valid_candidates", "rejected_count": ..., "uncertain_count": ...})`
  and returns a routing outcome based on the human's answer (`"broaden"` →
  back to `replanner` with relaxed constraints noted in `research_notes`,
  anything else → proceed to `course_cache_upsert`/`synthesizer` as today).
- `research_graph.py`: `enough_valid()` gets a third branch
  (`"low_confidence_check"`) for the exhausted-and-zero-valid case, distinct
  from today's silent "exhausted → `course_cache_upsert` with a limitation
  note" path. **Decide explicitly which of these two behaviors replaces the
  other** — don't leave both, or the exhausted-budget case becomes
  non-deterministic between two different outcomes.
- `cli.py`: the current resume loop only knows how to resume the static
  `review_gate` interrupt (`update_state` + `ainvoke(None, config)`). A
  dynamic `interrupt()` elsewhere needs `graph.get_state(config).next` to
  detect *which* node is paused, and resumes via
  `graph.ainvoke(Command(resume=answer), config)` — a different resume call
  than the static gate uses. This must not create any path that reaches
  `publish_node` without passing through `review_gate` — verify the graph
  topology still forces that after this node is inserted.

### Step 32 — OpenRouter as the single LLM gateway (§8.1, §8.2)

**Files:** `pyproject.toml`, new `course_discovery/app/llm.py`, `app/gateway.py`,
`review/router.py`, `research_agent/synthesis/nodes.py`, `CLAUDE.md`, `README.md`,
`.env.example`, new `tests/test_llm.py`

- Replace `langchain-google-genai` with `langchain-openrouter`. Gemini-direct is
  removed entirely, not kept as a branch.
- `app/llm.py`: `build_llm(node)` returns
  `ChatOpenRouter(model="deepseek/deepseek-v4.1-flash", temperature=0, model_kwargs={"models": [...]})`
  with `google/gemini-2.5-flash-lite` as the fallback. `ChatOpenRouter` has no
  first-class `models` field, so the priority array goes through `model_kwargs`.
  OpenRouter does the cross-provider fallback server-side, so LangChain code
  still only talks to one chat model instance — no `.with_fallbacks()`.
- `llm_enabled()` (`OPENROUTER_API_KEY` present) replaces every
  `os.getenv("OPENROUTER_API_KEY")` check; the deterministic no-LLM paths are
  unchanged.
- `CLAUDE.md`/`README.md`/`.env.example` updated to `OPENROUTER_API_KEY`.
- `ServedModelLogger` (callback attached in `build_llm`) logs the response
  `model_name` per call and warns when it differs from the primary, since the
  server-side fallback is otherwise invisible to the caller.
- `tests/test_llm.py` asserts the outgoing request carries the priority list
  with the primary first, and that a missing key fails closed.

### Step 33 — Rate limiter on `router_node`'s LLM (§8.3)

**File:** `course_discovery/review/router.py`

Pass a module-level `InMemoryRateLimiter(requests_per_second=2, max_bucket_size=4)`
to `build_llm("router", rate_limiter=...)`; the limiter is module-level so it is
shared across calls. Chosen
over gateway/synthesizer because `router_node` is the node most likely to be
called repeatedly within a single run — once per `PUBLISH`/`REWRITE`/
`AUGMENT`/`RESET`/`DISCARD` review round-trip — so it's the most honest
"shared quota under repeated calls" example.

### Step 34 — Hand-rolled guardrail around `synthesizer` (§8.5)

**Files:** new `course_discovery/research_agent/synthesis/guardrails.py`,
`course_discovery/research_agent/synthesis/nodes.py`, new
`tests/test_guardrails.py`

Only `synthesizer` both consumes content that traces back to untrusted
external search results (`course.description`/`evidence`, built from mock
Tavily snippets) *and* produces user-facing output — the one real
injection-surface + leak-surface node, per the mismatch note above. PII redaction
superseded this regex approach and uses Presidio (`guardrails/pii.py`); the
prompt-injection phrase list below is still unimplemented:

- `guardrails.py`: `contains_prompt_injection(text: str) -> bool` (a short
  canned phrase list — "ignore previous instructions", "disregard the
  above", "system:") and `contains_pii(text: str) -> bool` (regex, same style
  as the existing `_SECRET_PATTERNS` in `observability/logging.py`).
- `synthesis/nodes.py`: in `_highlight_with_retry`, check the injected payload
  text before building the prompt; check the returned highlight before it's
  used in the digest. On a hit, log and fall back to the existing
  non-LLM templated summary (the same fallback path already used when
  `OPENROUTER_API_KEY` is absent) rather than raising.
- `tests/test_guardrails.py`: assert both functions actually fire on a canned
  prompt-injection string and a canned PII pattern — the "verified, not just
  wired in" bar from `ARTICLE_TODO.md`.

### Step 35 — OpenTelemetry via LangSmith (§9.1, no code)

**Files:** `README.md`, `CLAUDE.md` Environment section

Document `LANGSMITH_TRACING=true`, `LANGSMITH_OTEL_ENABLED=true`,
`LANGSMITH_API_KEY`, and optionally `OTEL_EXPORTER_OTLP_ENDPOINT`/
`OTEL_EXPORTER_OTLP_HEADERS` as optional env vars. No source change — that's
the substance of the section, not a gap in it.

### Step 36 — `stream_mode` in the CLI (§9.2)

**File:** `course_discovery/app/cli.py`

Replace the initial `await graph.ainvoke(...)` with
`async for mode, chunk in graph.astream(initial_state, config, stream_mode=["updates"]): ...`,
printing the completed node name as each update lands. After the stream ends
(graph either hit `review_gate` or finished), call `graph.get_state(config)`
to get the values snapshot the existing interrupt/resume logic already reads
— keep that logic untouched, this step only changes how progress is surfaced
before the first pause.

### Step 37 — Tests

Add to the existing test scope (`specs/PROPOSED_TESTING.md`):

- Regression test for Step 23: a 2+ query research plan through the fan-out
  does not raise `InvalidUpdateError`.
- `test_guardrails.py` from Step 34.
- `RetryPolicy` test using `TAVILY_MOCK_FORCE_FAILURES` from Step 28: worker
  succeeds on the Nth attempt, `research_notes` stays empty (no false
  degradation note written for a retried-then-succeeded call).
- Dynamic `interrupt()` test from Step 31: a run with zero valid courses and
  exhausted budget pauses at `low_confidence_check`, not at `review_gate`
  directly, and `Command(resume=...)` reaches `synthesizer` correctly.

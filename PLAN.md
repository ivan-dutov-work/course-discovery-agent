# Plan: Personalized Complex Course Research Agent

## Objective

Build and describe a complex course-discovery agent, not a generic agentic workflow.

The article should focus on the agent that can understand a user's learning goals, consult long-term memory, reuse a shared course cache, search the web when needed, validate evidence, adapt its strategy, and produce personalized course recommendations. The broader LangGraph workflow should be presented only as the control shell that makes the agent bounded, inspectable, resumable, and safe to publish.

## Scope Boundary

### In Scope: The Complex Agent

These parts demonstrate agentic complexity and should be the article's center:

- `user_memory_lookup`: retrieves durable user preferences and history.
- `course_cache_lookup`: searches known validated courses before spending web-search budget.
- `research_planner`: plans search/cache strategy from the user query, memory, and cache gaps.
- `tavily_search_workers`: uses Tavily for external discovery when cache is insufficient or stale.
- `candidate_extractor`: extracts structured course candidates and evidence from search/page results.
- `aggregate` / `dedup`: merges candidates from cache and web search.
- `evidence_validator`: checks whether each candidate satisfies the user's constraints.
- `enough_valid?`: decides whether to synthesize, replan, or stop with limitations.
- `replanner`: adapts search strategy based on validation failures and missing evidence.
- `synthesizer`: ranks validated candidates using user preferences and evidence.
- `course_cache_upsert`: persists validated courses, evidence, freshness, and usage signals.
- `user_memory_update`: learns from explicit feedback and accepted/rejected recommendations.

### Supporting Infrastructure Only

These pieces are useful, but should not become the article's main topic:

- `gateway`: query parsing and initial normalization.
- `review_gate`: human approval boundary before publication or external action.
- `router`: publish, rewrite, augment, reset, discard.
- CLI output and demo plumbing.
- Telegram/publication integration.

The framing should be:

> LangGraph controls a bounded, personalized research agent. The complex agent lives in the research loop; the surrounding graph provides persistence, interrupts, routing, and review controls.

## Target Agent Behavior

The agent should not search the web blindly for every request. It should follow this order:

1. Parse the user's request into constraints.
2. Retrieve long-term user memory.
3. Query the shared course cache.
4. Decide whether cached courses are enough, stale, or missing key evidence.
5. Use Tavily only for gaps, freshness checks, or new topics.
6. Extract course candidates with evidence.
7. Validate candidates against the query and user preferences.
8. Replan if too few valid courses exist and loop budget remains.
9. Synthesize a personalized, evidence-backed recommendation list.
10. Persist useful discoveries and feedback.

## Proposed Graph

```text
user query
   |
   v
[gateway]
   |
   v
[user_memory_lookup]
   |
   v
[course_cache_lookup]
   |
   v
[research_planner]
   |
   v
[need_web_search?]
   | no
   v
[aggregate_cached_candidates]
   |
   v
[dedup]
   |
   v
[evidence_validator]
   |
   v
[enough_valid?]
   | yes
   v
[synthesizer]
   |
   v
[interrupt_before: review_gate]
   |
   v
[router]
   | publish / rewrite / augment / reset / discard
   v
terminal or loop

if need_web_search? = yes:
   [tavily_search_workers]
      |
      v
   [candidate_extractor]
      |
      v
   [aggregate]

if enough_valid? = no and loop budget remains:
   [replanner] -> [course_cache_lookup] and/or [tavily_search_workers]

after validated courses:
   [course_cache_upsert]

after review/user feedback:
   [user_memory_update]
```

## Persistence Architecture

Use Postgres + pgvector as the durable memory and shared course-cache layer.

### Why Postgres

Course recommendation data is highly queryable:

- provider
- topic
- level
- language
- price
- certificate availability
- freshness
- validation status
- user feedback
- recommendation history

These fit relational filtering better than a purely document-oriented store. Postgres also supports JSONB for flexible evidence payloads and pgvector for semantic search over course descriptions, evidence summaries, and user preference profiles.

### Why pgvector

pgvector lets the agent retrieve semantically similar courses and preferences without giving up structured filtering.

Example retrieval:

```text
Find courses semantically close to "practical beginner Python for data analysis",
but only where:
- is_free = true
- has_certificate = true
- validation_status = 'valid'
- language = 'English'
- provider not in user's avoided providers
```

This is stronger than using a vector store alone because the agent can combine fuzzy retrieval with strict constraints.

## Data Model

### `users`

Stores user identity and coarse profile metadata.

Suggested fields:

```sql
id
display_name
created_at
updated_at
```

### `user_preferences`

Stores durable personalization signals.

Suggested fields:

```sql
user_id
preferred_providers
avoided_providers
preferred_languages
budget_preference
certificate_importance
preferred_level
preferred_course_length
learning_style_notes
career_goals
raw_memory_json
profile_embedding vector
updated_at
```

### `courses`

Stores canonical course records.

Suggested fields:

```sql
id
canonical_url
title
provider
description
topics
level
language
price_text
is_free
has_certificate
rating
published_or_updated
last_seen_at
validation_status
validation_confidence
use_count
course_embedding vector
created_at
updated_at
```

### `course_evidence`

Stores evidence separately so validation is auditable.

Suggested fields:

```sql
id
course_id
source_url
quote_or_summary
supports
observed_at
source_type
confidence
```

### `recommendation_events`

Stores what the agent recommended and how the user responded.

Suggested fields:

```sql
id
user_id
course_id
query
rank
recommendation_reason
accepted
rejected
feedback_text
created_at
```

### `research_runs`

Stores run-level observability and evaluation data.

Suggested fields:

```sql
id
user_id
thread_id
query
parsed_filters_json
research_plan_json
queries_run
cache_hits
tavily_calls
valid_count
rejected_count
uncertain_count
replan_count
unsupported_claim_count
latency_ms
token_count
created_at
```

## Agent State Contract

Add:

```python
user_id: str | None
user_memory: UserMemory | None
cache_candidates: list[CourseCandidate]
research_plan: ResearchPlan | None
tavily_results: list[TavilySearchResult]
extracted_candidates: list[CourseCandidate]
valid_courses: list[CourseCandidate]
rejected_courses: list[CourseCandidate]
uncertain_courses: list[CourseCandidate]
validation_results: list[CandidateValidation]
research_iteration: int
max_research_iterations: int
completed_queries: list[str]
research_notes: list[str]
cache_hits: int
tavily_calls: int
metrics: ResearchRunMetrics
```

Remove or replace:

```python
worker_a_courses
worker_b_courses
worker_c_courses
```

Fixed worker keys should be replaced with query/source-oriented buffers. The number of workers should come from the `ResearchPlan`.

## Core Models

### `UserMemory`

```python
class UserMemory(BaseModel):
    preferred_providers: list[str] = []
    avoided_providers: list[str] = []
    preferred_languages: list[str] = []
    budget_preference: str | None = None
    certificate_importance: Literal["required", "preferred", "irrelevant"] | None = None
    preferred_level: str | None = None
    learning_style_notes: str | None = None
    career_goals: list[str] = []
    completed_course_urls: list[str] = []
    rejected_course_urls: list[str] = []
```

### `ResearchPlan`

```python
class ResearchPlan(BaseModel):
    topic: str
    constraints: list[str]
    cache_query: str
    search_queries: list[str]
    target_sources: list[str]
    exclude_patterns: list[str]
    min_valid_candidates: int
    use_cache_first: bool
    freshness_required: bool
    rationale: str
```

### `CourseCandidate`

```python
class CourseCandidate(BaseModel):
    title: str
    provider: str | None
    url: str
    description: str | None = None
    price: str | None
    is_free: bool | None
    has_certificate: bool | None
    level: str | None
    language: str | None
    rating: float | None
    published_or_updated: str | None
    source: Literal["cache", "tavily", "manual"]
    evidence: list[EvidenceItem]
    confidence: float
```

### `EvidenceItem`

```python
class EvidenceItem(BaseModel):
    source_url: str
    quote_or_summary: str
    supports: list[str]
```

### `CandidateValidation`

```python
class CandidateValidation(BaseModel):
    url: str
    status: Literal["valid", "rejected", "uncertain"]
    reasons: list[str]
    missing_evidence: list[str]
```

## New and Changed Nodes

### 1. `gateway`

Keep the current role:

- Parse the user query into `SearchFilters`.
- Normalize topic, level, price, certificate requirement, language, rating, provider preference, and recency.
- Fail closed when parsing fails.

### 2. `user_memory_lookup`

New node.

Responsibilities:

- Load durable preferences from Postgres.
- Load completed/rejected/recently recommended courses.
- Summarize memory into a compact `UserMemory` object for downstream prompts.
- Avoid injecting unbounded raw history into the LLM context.

### 3. `course_cache_lookup`

New node.

Responsibilities:

- Query Postgres for validated courses using structured filters.
- Use pgvector for semantic topic/course similarity.
- Exclude completed, rejected, recently over-recommended, or disliked-provider courses.
- Mark stale candidates that require Tavily refresh.

### 4. `research_planner`

Updated node.

Responsibilities:

- Decide whether cache results are enough.
- Generate Tavily queries only for missing or stale evidence.
- Produce provider-specific, certificate-focused, free-course, recent-course, and general web queries as needed.
- Respect user memory, but do not silently relax hard constraints.

### 5. `tavily_search_workers`

Replace fixed mock workers with Tavily-backed search workers.

Responsibilities:

- Run planned queries in parallel using LangGraph `Send`.
- Store source URL, title, snippet, score, and Tavily metadata.
- Track errors, timeouts, and empty result sets.
- Increment `tavily_calls`.

### 6. `candidate_extractor`

New node.

Responsibilities:

- Convert Tavily results into structured course candidates.
- Preserve source URLs and evidence snippets.
- Mark unknown fields explicitly instead of guessing.
- Avoid presenting aggregator/blog pages as course pages unless evidence supports the course facts.

### 7. `aggregate` / `dedup`

Updated nodes.

Responsibilities:

- Merge cache and Tavily candidates.
- Canonicalize URLs.
- Prefer fresher evidence and higher-confidence validated records.
- Keep provenance for every candidate.

### 8. `evidence_validator`

New node.

Responsibilities:

- Check each candidate against `SearchFilters` and `UserMemory`.
- Reject candidates with missing evidence for critical constraints.
- Separate valid, rejected, and uncertain candidates.
- Produce structured reasons and missing-evidence fields.

### 9. `enough_valid?`

New conditional routing function.

Responsibilities:

- Continue to synthesis if enough valid candidates exist.
- Route to replanning if count is too low and loop budget remains.
- Route to synthesis with a limitation note if loop budget is exhausted.

### 10. `replanner`

New node.

Responsibilities:

- Inspect rejection reasons and missing evidence.
- Generate new cache queries and/or Tavily queries.
- Avoid repeating failed queries.
- Tighten or broaden constraints only when allowed.

Example triggers:

- Too few free courses found.
- Certificate evidence missing.
- Cache results are stale.
- Results are mostly blogs or aggregators instead of course pages.
- URLs duplicate already rejected candidates.

### 11. `synthesizer`

Updated node.

Responsibilities:

- Rank valid candidates using query constraints, evidence quality, and user memory.
- Explain why each course fits this user.
- Mention limitations when valid count is below target.
- Avoid unsupported claims.

### 12. `course_cache_upsert`

New node.

Responsibilities:

- Upsert valid and useful uncertain courses into Postgres.
- Store evidence, validation status, confidence, freshness, and embeddings.
- Increment use counts for recommended courses.

### 13. `review_gate`

Rename `telegram_gate` to `review_gate` unless Telegram is actually implemented.

Responsibilities:

- Serve as the interrupt boundary before external publication.
- Keep publication human-gated.

### 14. `router`

Keep feedback actions, but update semantics:

- `PUBLISH`: mark as published and record recommendation events.
- `REWRITE`: rerun synthesis with the same validated candidates.
- `AUGMENT`: trigger replanning/search for additional candidates.
- `RESET`: reparse constraints and restart research.
- `DISCARD`: terminate and optionally record negative feedback.

### 15. `user_memory_update`

New node.

Responsibilities:

- Convert explicit feedback into durable preferences.
- Record accepted/rejected recommendations.
- Avoid over-learning from one interaction.
- Keep user-editable memory separate from derived observations.

## Tavily Integration

Environment:

```powershell
$env:TAVILY_API_KEY="your-key"
```

Wrapper responsibilities:

- Validate API key presence.
- Search by query.
- Normalize Tavily result payloads into internal `TavilySearchResult`.
- Capture errors in state and logs.
- Avoid publishing raw Tavily payloads directly into synthesis prompts.

Suggested interface:

```python
class TavilyClient:
    async def search(self, query: str, *, max_results: int = 5) -> list[TavilySearchResult]:
        ...
```

## LangGraph Topics To Cover

Cover these only where they improve the complex-agent story.

### Dynamic Fan-Out With `Send`

Use `Send` for plan-driven Tavily search workers.

Impact:

- The planner can emit a variable number of queries.
- Searches run in parallel.
- The article can measure query count, latency, and result quality.

### Reducers For Concurrent State Updates

Use reducers for fields populated by parallel workers, such as Tavily results, extracted candidates, and research notes.

Impact:

- Prevents concurrent state overwrite problems.
- Makes parallel search deterministic and inspectable.

### Conditional Routing

Use conditional edges for:

- `need_web_search?`
- `enough_valid?`
- router actions after review

Impact:

- Shows bounded autonomy.
- Makes replanning measurable instead of implicit.

### `Command` For Update-And-Route Nodes

Use `Command` where a node must update state and choose the next destination, especially router/replanner paths.

Impact:

- Keeps control flow explicit.
- Avoids scattering routing decisions across unrelated functions.

### Checkpointing And Durable Execution

Use a durable checkpointer for real runs. Memory-only checkpointing is fine for tests and demos.

Impact:

- Resume interrupted research runs.
- Inspect state before/after human review.
- Recover from tool or model failures.
- Support time-travel debugging.

### Interrupts For Human Review

Use `interrupt` / `interrupt_before` at `review_gate`.

Impact:

- Publication remains human-gated.
- The agent can be autonomous in research but not in external publication.

### Long-Term Memory Store

Use Postgres + pgvector as the cross-thread memory/cache layer.

Impact:

- Personalized recommendations across sessions.
- Fewer duplicate Tavily searches.
- Better repeat-user experience.
- Reuse of validated course evidence.

### Timeouts, Retries, And Error Handling

Apply node-level controls around Tavily and LLM extraction/validation.

Impact:

- Tavily failures do not collapse the whole agent.
- Failed searches can become replanning signals.
- The agent can return limitations instead of fabricated certainty.

### Observability And Evaluation

Use LangSmith or structured run logs to trace LLM calls, tool calls, state transitions, and validation decisions.

Impact:

- Measure token cost, latency, Tavily calls, replan count, validation failures, and unsupported claims.
- Compare cache-first runs against web-search-heavy runs.

### Subgraphs

Optional, but useful for scope clarity.

Recommended structure:

```text
outer workflow:
   gateway -> research_agent_subgraph -> review_gate -> router

research_agent_subgraph:
   memory -> cache -> planner -> search/extract/validate/replan -> synthesize
```

Impact:

- Keeps the article focused on the complex agent.
- Shows how a complex agent can be embedded in a larger workflow without making the workflow the topic.

## Measurable Impact

The article should include before/after or run-level metrics.

### Quality Metrics

- `constraint_satisfaction_rate`
- `evidence_coverage_rate`
- `unsupported_claim_count`
- `valid_candidate_count`
- `rejected_candidate_count`
- `uncertain_candidate_count`
- `replan_success_rate`

### Efficiency Metrics

- `cache_hit_rate`
- `tavily_calls_per_query`
- `latency_ms`
- `token_count`
- `duplicate_candidate_rate`

### Personalization Metrics

- accepted recommendations
- rejected recommendations
- avoided-provider violations
- repeated recommendation rate
- completed-course exclusion rate

## Reliability Controls

- Keep `max_research_iterations` low, probably 2 or 3.
- Keep Tavily `max_results` per query bounded.
- Track `completed_queries` to avoid loops.
- Require evidence for critical constraints.
- Treat missing evidence as `uncertain`, not valid.
- Use cache freshness windows.
- Fail closed when gateway parsing, database access, or Tavily access fails.
- Use structured outputs for planning, extraction, validation, and routing.
- Log every cache lookup, query, validation decision, rejection reason, and replanning trigger.
- Keep user memory compact and editable.
- Do not let one feedback event permanently dominate personalization.

## Article Rewrite Implications

The article should change from:

> LangGraph orchestrates a mocked course-discovery pipeline.

To:

> LangGraph controls a personalized, bounded course research agent that uses long-term memory, a shared Postgres/pgvector course cache, Tavily search, structured extraction, evidence validation, and replanning to produce evidence-backed course recommendations.

Sections to rewrite:

- TL;DR: introduce personalized complex course research agent.
- Problem Statement: focus on unverifiable, generic recommendations and repeated research.
- Agent Scope: distinguish complex agent from surrounding workflow.
- Personalization: explain user memory and feedback learning.
- Shared Course Cache: explain Postgres + pgvector and cache-first behavior.
- Graph section: update topology.
- Parallel Execution: replace mock workers with dynamic Tavily query fan-out via `Send`.
- State Management: cover reducers, durable checkpoints, and long-term store.
- LLM Nodes: add planning, extraction, validation, replanning, memory update.
- Human Review: keep secondary; rename `telegram_gate` to `review_gate`.
- Evaluation: add measurable quality, efficiency, and personalization metrics.
- Known Limitations: mention Tavily dependence, source quality, stale cache, memory privacy, evidence uncertainty, and bounded research depth.

## Implementation Order

1. Inspect current project structure and node/model files.
2. Add Postgres connection/config and migrations.
3. Add pgvector support and embedding generation for courses/user profiles.
4. Add data-access layer for user memory, course cache, evidence, recommendation events, and research runs.
5. Add or update Pydantic models for memory, research plan, Tavily results, evidence, validation, and metrics.
6. Extend `AgentState`.
7. Add `user_memory_lookup`.
8. Add `course_cache_lookup`.
9. Add Tavily client wrapper and environment validation.
10. Replace fixed mock worker fan-out with plan-driven Tavily query fan-out.
11. Add candidate extraction node.
12. Add evidence validation node.
13. Add enough-valid conditional routing.
14. Add replanner node and loop budget.
15. Add `course_cache_upsert`.
16. Add `user_memory_update`.
17. Rename `telegram_gate` to `review_gate`.
18. Update router semantics for `AUGMENT`, `PUBLISH`, and feedback capture.
19. Add metrics logging for research runs.
20. Update CLI output to show validation notes, cache hits, and evidence-backed digest.
21. Add focused tests for cache lookup, memory lookup, planning, fan-out routing, validation routing, replanning loop limits, and memory updates.
22. Rewrite `ARTICLE.md` to match the new implementation.

## Minimal Viable Version

If time is limited, implement only:

1. Postgres course cache schema.
2. User preference memory schema.
3. Cache-first course lookup.
4. Tavily search workers for cache misses.
5. Candidate extraction.
6. Evidence validation.
7. Replanning when too few valid candidates exist.
8. Course cache upsert.
9. Basic feedback-to-memory update.

That is enough to make the system a defensible personalized complex research agent while keeping the surrounding workflow secondary.


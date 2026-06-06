# Article Outline: "Using LangGraph to Build a Personalized Course Research Agent"

## 0. TL;DR (~100 words)

Most "AI agents" are LLM calls with a for-loop around them. They hallucinate results,
ignore constraints, and repeat the same searches every time. In this article we build
something different: a personalized course research agent that remembers what you
already know, checks a shared course cache before touching the web, validates every
candidate against your actual constraints, and replans when the results are
insufficient. LangGraph provides the control shell that keeps it bounded, inspectable,
resumable, and safe to publish. The agent does the real work.

---

## 1. The Problem: Why Naive LLM Wrappers Fail (~400 words)

**1.1 Three failure modes that matter**

- **Hallucinated content.** Ask a bare LLM for "free Python courses with a
  certificate" and it confidently invents plausible-looking URLs and titles. Users
  hit dead links and trust erodes fast. This is not a model defect in isolation —
  it is a system design defect. If unconstrained generation can become published
  content, hallucinations are expected behavior.

- **Constraint drift.** "Max price: $0" becomes "$29 course, look how affordable!"
  by paragraph three. Models optimize for helpful continuation, not strict policy
  adherence across a multi-step run. The only reliable fix is structural enforcement.

- **Repeated, unverifiable research.** Every run starts from scratch. There is no
  memory of what you already recommended, what you already validated, or what
  courses the user has already completed. Cost and latency scale with queries, not
  with value.

**1.2 What a real research agent needs**

- A cache that stores validated courses and reuses them before spending web-search
  budget.
- Long-term memory of user preferences and history.
- Evidence attached to every claim, so the agent can reject candidates that fail
  constraint checks instead of guessing.
- Replanning when the first pass is insufficient — without looping forever.
- A human review boundary before anything is published.

---

## 2. LangGraph in the Context of This Agent (~400 words)

This is not a LangGraph tutorial. We introduce only the features that serve the
complex-agent story.

**2.1 What LangGraph provides here**

- A typed state contract (`AgentState`) that flows through every node.
- Explicit edges and conditional routing — control flow is in the graph, not in
  prompts.
- `Send` API for plan-driven parallel fan-out.
- Reducers for safely merging concurrent state updates from parallel search workers.
- `interrupt_before` for mandatory human review before publication.
- Checkpointing so the run can resume from the same point after a crash or restart.
- Subgraphs that keep the complex agent encapsulated and the outer workflow thin.

**2.2 What LangGraph does not replace**

LangGraph is the control shell. The interesting behavior — memory retrieval,
cache-first strategy, evidence extraction, validation, replanning, personalized
synthesis — lives entirely inside the research subgraph. The outer graph provides
persistence, routing, and review controls.

**Table: LangGraph features used and where**

| Feature | Where used |
|---|---|
| `StateGraph` + `TypedDict` | Entire graph |
| `add_conditional_edges` | `need_web_search?`, `enough_valid?`, router |
| `Send` API (fan-out) | Plan-driven Tavily search workers |
| Reducers | Tavily results, extracted candidates, research notes |
| `interrupt_before` | `review_gate` |
| `MemorySaver` / Postgres checkpointer | Full graph |
| Subgraph compilation | Research agent encapsulation |
| `Command` | Router and replanner update-and-route |

---

## 3. The Agent We're Building (~300 words)

Introduce the full system at a high level.

- **Input:** free-form user query ("find me free Python courses with a certificate")
- **What makes it complex:** user memory, shared course cache, research planning,
  parallel Tavily search, candidate extraction, evidence validation, replanning,
  personalized synthesis
- **Key constraint:** nothing is published without explicit human approval

Include the architecture diagram:

```
User query
    │
    ▼
[gateway]  — parse filters, normalize
    │
    ▼
[research_agent subgraph]
    │   user_memory_lookup
    │   course_cache_lookup
    │   research_planner
    │   tavily_search_worker(s)  ← via Send, only for gaps
    │   candidate_extractor
    │   aggregate + dedup
    │   evidence_validator
    │   replanner  ← if too few valid, bounded by max_research_iterations
    │   course_cache_upsert
    │   synthesizer
    │
    ▼
[interrupt_before: review_gate]  — human reads digest
    │
    ▼
[router]
    ├─ PUBLISH  → publish_node → user_memory_update → END
    ├─ REWRITE  → research_agent subgraph
    ├─ AUGMENT  → research_agent subgraph
    ├─ RESET    → gateway
    └─ DISCARD  → discard_node → END
```

---

## 4. Designing the State Contract (~500 words)

**The state is the contract between every node. Nodes never talk to each other
directly — they read and write state.**

Show the full `AgentState` and explain each field's role:

```python
class AgentState(TypedDict):
    # outer workflow
    user_query: str
    search_filters: SearchFilters | None
    digest: str | None
    manager_feedback: str | None
    routing_decision: str | None

    # research agent
    user_id: str | None
    user_memory: UserMemory | None
    cache_candidates: list[CourseCandidate]
    research_plan: ResearchPlan | None
    tavily_results: Annotated[list[TavilySearchResult], add_list]
    extracted_candidates: Annotated[list[CourseCandidate], add_list]
    valid_courses: list[CourseCandidate]
    rejected_courses: list[CourseCandidate]
    uncertain_courses: list[CourseCandidate]
    validation_results: list[CandidateValidation]
    research_iteration: int
    max_research_iterations: int
    completed_queries: list[str]
    research_notes: Annotated[list[str], add_list]
    cache_hits: int
    tavily_calls: int
    metrics: ResearchRunMetrics
```

Explain: fields populated by parallel workers use `Annotated` reducers to prevent
concurrent overwrite. This is how `Send`-based fan-out produces deterministic,
mergeable state.

Introduce the core Pydantic models: `UserMemory`, `ResearchPlan`, `CourseCandidate`,
`EvidenceItem`, `CandidateValidation`.

---

## 5. The Research Agent: Node by Node (~1,200 words)

Walk through each node in the research subgraph in execution order.

**5.1 `user_memory_lookup`**

What it does: loads durable preferences from Postgres — preferred providers, avoided
providers, budget, certificate importance, completed and rejected courses. Compact
`UserMemory` summary injected into downstream prompts. Graceful fallback to seed
memory when Postgres is absent.

Why it matters: the agent cannot personalize without remembering who the user is.
Without memory, every run treats the user as a stranger.

**5.2 `course_cache_lookup`**

What it does: queries Postgres with structured filters (level, language, price,
certificate) combined with pgvector semantic similarity over course embeddings.
Excludes completed, rejected, and recently over-recommended courses. Marks stale
candidates that need a freshness check.

Why it matters: cache-first behavior reduces Tavily calls. If the cache already holds
five valid Python beginner courses with certificates, there is no reason to search.

Show the hybrid query: structured filter + vector similarity in one SQL expression.

**5.3 `research_planner`**

What it does: LLM node. Evaluates cache results against the query and memory. Decides
whether Tavily is needed, and if so, generates targeted queries — provider-specific,
certificate-focused, free-course, recency-filtered. Sets `use_cache_first`,
`freshness_required`, and `min_valid_candidates`. Does not silently relax hard
constraints.

Why it matters: this is the agent's judgment layer. It makes the difference between
"search blindly" and "search only for what is actually missing."

**5.4 `tavily_search_workers` — dynamic fan-out via `Send`**

What it does: for each planned query, a `Send` dispatches a worker node with the
query payload. Workers run in parallel. Each stores URL, title, snippet, score, and
error state. Results accumulate via reducers.

Why it matters: the number of workers comes from the plan, not the code. Three
queries fan out to three parallel workers; one query runs one worker. This is the
right use of `Send` — variable, plan-driven parallelism.

Show the fan-out pattern:

```python
def dispatch_tavily_workers(state: AgentState) -> list[Send]:
    return [
        Send("tavily_search_worker", {"query": q, ...})
        for q in state["research_plan"].search_queries
    ]
```

**5.5 `candidate_extractor`**

What it does: LLM node. Converts Tavily snippets into structured `CourseCandidate`
objects with `EvidenceItem` lists. Marks unknown fields explicitly. Does not
hallucinate missing metadata.

**5.6 `aggregate` + `dedup`**

What it does: merges cache and Tavily candidates. Canonicalizes URLs. Prefers fresher
evidence and higher-confidence records. Retains provenance (source: `"cache"` or
`"tavily"`) for every candidate.

**5.7 `evidence_validator`**

What it does: LLM node. Checks each candidate against `SearchFilters` and
`UserMemory`. Produces `CandidateValidation` for each: `valid`, `rejected`, or
`uncertain`, with structured reasons and `missing_evidence` fields. Missing evidence
is never treated as valid — it is uncertain.

Why it matters: this is the anti-hallucination layer. If a candidate lacks evidence
for a critical constraint (e.g., certificate), it cannot be marked valid regardless
of how plausible the title sounds.

**5.8 `enough_valid?` — conditional routing**

Routing logic:
- Enough valid candidates → `synthesizer`
- Too few, loop budget remains → `replanner`
- Loop budget exhausted → `synthesizer` with limitation note

Show the conditional edge wiring and the budget check.

**5.9 `replanner`**

What it does: LLM node. Inspects rejection reasons and missing evidence from the
previous iteration. Generates new queries (cache or Tavily). Avoids repeating
`completed_queries`. Returns a `Command` that updates state and routes back to
`course_cache_lookup` or `tavily_search_workers` as appropriate.

Example triggers: too few free courses found; certificate evidence missing; results
dominated by blog aggregators; stale cache entries.

**5.10 `course_cache_upsert`**

What it does: upserts valid (and useful uncertain) courses into Postgres. Stores
evidence, validation status, confidence, freshness timestamp, embedding vector.
Increments `use_count` for recommended courses.

**5.11 `synthesizer`**

What it does: LLM node. Ranks valid candidates using query constraints, evidence
quality, and user memory. Explains why each course fits this specific user. Notes
limitations when valid count is below target. Avoids unsupported claims.

---

## 6. The Control Shell: Outer Graph (~400 words)

This section is deliberately short. The outer graph is not the article's subject —
it is what keeps the agent safe to deploy.

**6.1 `review_gate` — mandatory human interrupt**

`interrupt_before=["review_gate"]` pauses the graph before any publication side
effect. The digest is printed; the human reads it and types feedback. The graph
resumes by writing feedback into state and calling `invoke` with the same
`thread_id`.

Why this is not optional: one bad autonomous publish erodes trust instantly. The
interrupt is the boundary between the agent's autonomy and the world.

**6.2 Router — five feedback paths**

| Decision | Effect |
|---|---|
| PUBLISH | Records recommendation events, runs `user_memory_update`, ends |
| REWRITE | Reruns synthesis on existing validated candidates |
| AUGMENT | Triggers replanning and additional search |
| RESET | Reparses constraints and restarts research |
| DISCARD | Terminates, optionally records negative feedback |

**6.3 `user_memory_update`**

Converts explicit publish/discard feedback into durable preferences. Avoids
over-learning from a single interaction. Keeps user-editable memory separate from
derived observations.

**6.4 Checkpointing**

`MemorySaver` for local demo; Postgres checkpointer for production. Switching is one
import change. Every node completion writes a checkpoint: the run can resume after a
crash, and state can be inspected at any point using `graph.get_state(config)`.

---

## 7. Personalization: Memory and the Cache (~500 words)

**7.1 Why long-term memory changes everything**

Without memory, every run is the same. With memory, the agent knows: you prefer
Coursera, you have already completed the CS50 Python course, you rejected paid
courses last time. The synthesizer can explain why each recommendation fits you
specifically.

**7.2 The Postgres + pgvector data model**

Five tables: `users`, `user_preferences`, `courses`, `course_evidence`,
`recommendation_events`, `research_runs`.

The `courses` table stores a `course_embedding` vector. The `user_preferences` table
stores a `profile_embedding` vector. Both are populated at upsert time by the
embedding function.

Explain the hybrid retrieval pattern: structured filter + vector similarity in a
single query. Show an example:

```sql
SELECT * FROM courses
WHERE is_free = true
  AND has_certificate = true
  AND validation_status = 'valid'
  AND language = 'English'
  AND provider != ALL($avoided_providers)
ORDER BY course_embedding <-> $query_embedding
LIMIT 10;
```

This is stronger than a pure vector store because hard constraints are enforced
before semantic ranking.

**7.3 Cache freshness**

Courses have a `last_seen_at` timestamp. The planner can request freshness checks
for candidates that are old enough to be stale. Tavily is called only for those
specific candidates, not for the entire topic.

---

## 8. Observability and Evaluation (~400 words)

**8.1 What the agent logs**

Every cache lookup, Tavily call, validation decision, rejection reason, and
replanning trigger is logged. The `research_runs` table stores run-level metrics.

**8.2 Quality metrics**

- `constraint_satisfaction_rate`: % of recommended courses that satisfy all hard
  constraints.
- `evidence_coverage_rate`: % of constraint fields covered by at least one evidence
  item.
- `unsupported_claim_count`: claims in the digest not backed by evidence.
- `valid_candidate_count`, `rejected_candidate_count`, `uncertain_candidate_count`.
- `replan_success_rate`: % of replanning iterations that produced additional valid
  candidates.

**8.3 Efficiency metrics**

- `cache_hit_rate`: % of recommended courses served from cache without Tavily.
- `tavily_calls_per_query`: search budget consumed per run.
- `latency_ms`, `token_count`.
- `duplicate_candidate_rate`: % of raw candidates deduped.

**8.4 Personalization metrics**

- Accepted recommendations vs. rejected.
- Avoided-provider violations (should be zero).
- Repeated recommendation rate (courses recommended to the same user in multiple
  runs).
- Completed-course exclusion rate (courses in the user's history correctly excluded).

---

## 9. Known Limitations (~200 words)

- **Tavily dependence.** External search quality is bounded by Tavily's index.
  Niche topics or paywalled content may return few useful results.
- **Source quality.** Blog aggregators and review pages can surface as Tavily
  results. The extractor distinguishes course pages from non-course pages, but
  imperfectly.
- **Stale cache.** Cached courses can become outdated. Cache freshness windows
  mitigate this but do not eliminate it.
- **Bounded research depth.** `max_research_iterations` keeps the agent from
  looping forever. It also means the agent may return fewer results than requested
  when the topic is narrow or evidence is hard to find.
- **Memory privacy.** Storing user preferences and recommendation history in Postgres
  introduces data retention obligations. The memory schema should be reviewed against
  applicable data protection requirements before production deployment.
- **Evidence uncertainty.** "Uncertain" candidates are not invalid — they are
  candidates for which the agent could not confirm a constraint. The synthesizer
  excludes them from recommendations but logs them for human review.

---

## 10. What's Next (~150 words)

The current implementation is the complete complex agent. Useful next steps:

- **Postgres checkpointer** for durable multi-session resumability (swap
  `MemorySaver` for `AsyncPostgresSaver` — one line change).
- **LangSmith tracing** for full LLM call observability across nodes.
- **Telegram or webhook review interface** to replace the CLI HIL stub.
- **Multilingual support**: Ukrainian-language query parsing and digest generation;
  multilingual embedding model benchmarking.
- **Scheduling**: cron-triggered research runs with configurable query cadence.
- **Dashboard**: WebSocket stream of node-level progress events for non-CLI
  environments.

None of these change the research agent's architecture — they add surfaces and
infrastructure around it.

**Link to the repository.**

---

## Appendix: Running the Demo (~100 words)

```bash
git clone https://github.com/your-org/course-discovery-agent
cd course-discovery-agent
# optional: set GOOGLE_API_KEY, TAVILY_API_KEY, DATABASE_URL in .env
uv sync
uv run python main.py
```

The CLI prints the digest after synthesis, shows cache hit count, Tavily call count,
and validation summary, then prompts for review. Type `approve`, `rewrite: ...`,
`augment: ...`, `reset: ...`, or `discard`.

---

## Estimated Word Count

| Section | ~Words |
|---|---|
| 0. TL;DR | 100 |
| 1. Problem | 400 |
| 2. LangGraph context | 400 |
| 3. Agent overview | 300 |
| 4. State contract | 500 |
| 5. Research agent nodes | 1,200 |
| 6. Control shell | 400 |
| 7. Personalization + cache | 500 |
| 8. Observability | 400 |
| 9. Known limitations | 200 |
| 10. What's next | 150 |
| Appendix | 100 |
| **Total** | **~4,650** |

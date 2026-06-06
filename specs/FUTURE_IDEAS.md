# Course Discovery Agent — Future Ideas and Deferred Decisions

Items that are recognized as valuable but deferred from the current architecture
scope. Revisit as the system matures.

---

## 1. Tavily Cost and Rate-Limit Management

Tavily is the agent's only web search provider. At scale (many users, many topics,
frequent replanning), Tavily call count drives operating cost.

**Current controls already in place:**

- Cache-first behavior: Tavily is only called for gaps.
- `max_results` per query is bounded.
- `completed_queries` prevents repeat searches within a run.
- Replanning is bounded by `max_research_iterations`.

**Potential improvements:**

- Per-user or per-topic Tavily call budget tracked in `research_runs`.
- Alerting when daily Tavily usage exceeds a configurable threshold.
- Fallback to cache-only synthesis with a limitation note when budget is exhausted.
- Evaluate whether a secondary search provider (e.g., Brave Search API) reduces
  Tavily dependence for general queries.

---

## 2. pgvector Similarity Threshold Calibration

The course cache lookup uses cosine similarity over `course_embedding` to retrieve
semantically relevant courses. The similarity threshold that balances recall vs.
noise has not been formally calibrated.

**Suggested experiment:**

- Collect ≥100 (query, course) pairs from production runs.
- Manually label each pair: relevant or irrelevant.
- Sweep cosine similarity threshold from 0.70 to 0.95 in 0.05 increments.
- Compute precision and recall at each threshold. Identify the threshold that
  maximizes F1.
- Calibrate separately for: general topic queries, provider-specific queries,
  certificate-focused queries.

**Current approach:** threshold is set conservatively to avoid noise. Update this
document when calibration data is available.

---

## 3. Memory Privacy and Data Retention

The agent stores user preferences, completed course history, and recommendation
events in Postgres. This creates data retention obligations that should be reviewed
before production deployment.

**Questions to resolve:**

- What is the data retention period for `recommendation_events` and
  `user_preferences`?
- How does a user request deletion of their memory?
- Should `profile_embedding` vectors be stored separately from preference text
  to allow deletion of source data while keeping anonymized signals?
- If operating under GDPR, Ukrainian data protection law, or similar: is a
  privacy notice required? Is there a lawful basis for processing?

**Action:** conduct a data protection review before production launch. The schema
already separates `user_preferences` from `recommendation_events`, which makes
targeted deletion easier to implement.

---

## 4. Evidence Quality for Aggregator and Blog Pages

Tavily results frequently include blog posts, "best courses" aggregators, and
review pages alongside direct course landing pages. The `candidate_extractor` node
attempts to distinguish course pages from non-course pages, but this is imperfect.

**Symptoms of the problem:**

- A blog post titled "10 Best Free Python Courses" surfaces as a Tavily result.
- The extractor pulls 10 course candidates from it, each with low-quality evidence
  (scraped blog text rather than first-party course page content).
- These candidates pass extraction but often fail validation due to missing evidence
  for critical fields.

**Potential improvements:**

- Domain heuristics: prefer results from known course providers (Coursera, edX,
  Udemy, YouTube, Khan Academy, etc.) and penalize aggregator domains.
- Evidence source classification: tag each `EvidenceItem` with `source_type`
  (`course_page`, `aggregator`, `review`, `social`). Weight validation confidence
  by source type.
- Post-extraction URL check: for candidates extracted from non-course pages, fetch
  the candidate URL directly and re-extract evidence from the course page itself.
  This adds latency but improves evidence quality significantly.

---

## 5. Multilingual LLM Quality

When generating digests or parsing queries in languages other than English, LLM
output quality varies. Gemini 2.0 Flash supports multiple languages but has not
been benchmarked for educational domain tasks in non-English contexts.

**Suggested research (not blocking current launch):**

- Benchmark Gemini 2.0 Flash on 50 sample course summaries in Ukrainian.
- Evaluate: grammatical correctness, formal register, domain terminology handling.
- Compare against Claude 3.5 Sonnet and a locally hosted Ukrainian-tuned model if
  available.
- Choose provider based on benchmark results and document the quality bar as a
  regression test.

**Status:** not required for initial launch while review is CLI-based and all content
is English. Revisit when non-English digest generation is added.

---

## 6. Scheduling and Cron Triggers

The current system is user-triggered via CLI. A scheduled mode would allow automatic
research runs on a cadence (daily, weekly) without manual invocation.

**Design sketch:**

- `APScheduler` or a cron job fires the outer graph on a configurable interval.
- Synthetic user message with the last-used `search_filters` is written into state.
- Run proceeds normally through the research subgraph.
- Review gate still interrupts before publication — scheduling does not bypass
  human review.
- Multi-run serialization: if a run is pending review, the next scheduled run is
  queued rather than started.

---

## 7. Telegram or Webhook Review Interface

The current review gate uses a CLI prompt. A Telegram bot or webhook interface
would allow review from a mobile device or integrate into existing team workflows.

**Design notes:**

- The `interrupt_before=["review_gate"]` mechanism does not change — only the I/O
  surface changes.
- `graph.update_state(config, feedback)` + `graph.invoke(None, config)` is the
  resume pattern regardless of transport.
- Feedback commands (approve, rewrite, augment, reset, discard) map directly to
  the existing router actions.

**Not a current priority** because the CLI demo is sufficient for the article and
the review mechanism is transport-agnostic.

---

## 8. LangGraph Postgres Checkpointer (Production Resumability)

The current implementation uses `MemorySaver` (in-process, lost on restart). Switching
to `AsyncPostgresSaver` makes runs resumable across process restarts and supports
time-travel debugging.

**What the switch requires:**

- One import change: replace `MemorySaver()` with `AsyncPostgresSaver(conn)`.
- The LangGraph checkpoint schema is managed by the library — run migrations once.
- The same `thread_id` resumes the run from the last checkpoint node.

**Current status:** deferred because `MemorySaver` is sufficient for local demo and
article purposes. The architecture is designed for this switch from the start.

---

## 9. LangSmith Tracing

LangSmith provides full observability over LLM calls, tool calls, and state
transitions across all nodes. Adding it requires:

- Set `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY`.
- All LangChain and LangGraph calls are automatically traced.

**Value:** compare cache-first runs against Tavily-heavy runs, trace token cost per
node, debug validation failures with full prompt/response logs.

**Current status:** structured logging to `research_runs` covers run-level metrics.
LangSmith adds per-node and per-LLM-call granularity.

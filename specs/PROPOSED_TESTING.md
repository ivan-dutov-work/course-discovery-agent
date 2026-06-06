# Course Discovery Agent — Testing Protocol

This document defines the testing approach for the Course Discovery Agent:
unit and integration tests for the research agent's core behaviors, and
evaluation protocols for measuring quality and personalization at scale.

---

## 1. Unit and Integration Tests

These tests are automated and should run as part of CI. They cover the
behaviors that are hardest to verify by inspection alone.

**1.1 Cache lookup**

- Structured filter query returns only courses matching all hard constraints
  (level, language, price, certificate, validation status).
- pgvector similarity ranking: semantically closer courses rank higher.
- Excluded courses (completed, rejected, avoided provider) do not appear in results.
- Stale courses are marked correctly based on `last_seen_at` and the freshness
  threshold.
- Graceful fallback: when `DATABASE_URL` is absent, returns seed cache candidates.

**1.2 Memory lookup**

- `UserMemory` loaded correctly from `user_preferences` for a known user.
- Default empty memory returned for unknown user IDs.
- Graceful fallback when database is absent.

**1.3 Research planner output structure**

- Returned `ResearchPlan` is a valid Pydantic model.
- `search_queries` is non-empty when cache candidates are insufficient.
- `use_cache_first = True` when cache hits meet `min_valid_candidates`.
- `search_queries` does not repeat entries in `completed_queries`.

**1.4 Fan-out routing**

- `dispatch_tavily_workers` returns exactly one `Send` per query in
  `research_plan.search_queries`.
- With zero queries in the plan, returns an empty list (no Tavily calls fired).
- `tavily_calls` increments by one per completed worker.

**1.5 Validation routing**

- Candidate with full evidence for all constrained fields → `valid`.
- Candidate with contradicting evidence (e.g., `is_free = false` when filter
  requires free) → `rejected`.
- Candidate missing evidence for a critical field → `uncertain`, not `valid`.
- `valid_courses`, `rejected_courses`, `uncertain_courses` are mutually exclusive
  after validation.

**1.6 Replanning loop limits**

- `research_iteration` increments on each replanning cycle.
- Loop terminates when `research_iteration >= max_research_iterations`.
- At termination, routes to `synthesizer` (not back to `replanner`).
- `completed_queries` contains all queries from all iterations — replanner does not
  generate duplicates.

**1.7 `enough_valid?` edge routing**

- `len(valid_courses) >= min_valid_candidates` → routes to `synthesizer`.
- Below threshold, budget remaining → routes to `replanner`.
- Below threshold, budget exhausted → routes to `synthesizer`; limitation note
  appears in `research_notes`.

**1.8 Memory update**

- PUBLISH action: accepted courses recorded in `recommendation_events`.
- DISCARD action: run terminated; negative feedback optionally recorded.
- Avoided provider in feedback: added to `user_memory.avoided_providers` in next run.
- Previously completed course URL: added to `completed_course_urls`.

**1.9 Dedup**

- Duplicate URLs (after normalization) produce exactly one candidate.
- Near-duplicate titles (rapidfuzz threshold) produce exactly one candidate.
- Cache and Tavily candidates for the same course are merged, preferring the record
  with higher confidence.

---

## 2. Evaluation Protocol

These experiments are run manually or semi-automatically against the live system
to measure quality at scale.

### 2.1 Baseline Comparisons

The agent's output is measured against two baselines.

**Baseline A — General-purpose LLM (ChatGPT / Perplexity)**

- Feed the identical user query to ChatGPT-4o and Perplexity.
- Record all courses mentioned in the response.
- For each course: verify existence (live URL), price accuracy, certificate accuracy.
- Comparison metrics: constraint satisfaction rate, hallucination rate (% of courses
  that don't exist or don't match claims), URL liveness.
- This baseline demonstrates the core failure mode the agent is designed to prevent.

**Baseline B — Manual human search**

- A human researcher searches for courses on the same topic using Google, Coursera,
  YouTube, and Class Central. Time-boxed to 30 minutes.
- Record every relevant course found with metadata.
- Comparison metrics: constraint satisfaction rate, time-to-results (agent latency
  vs. 30 minutes), and evidence coverage (human notes vs. agent evidence items).

### 2.2 Test Topic Set

20 topics spanning five domains and three difficulty levels, each as a realistic
user query.

| # | Domain | Level | Topic Query |
|---|---|---|---|
| 1 | Programming | Beginner | "Free Python basics course with certificate" |
| 2 | Programming | Intermediate | "API development with FastAPI, free or cheap" |
| 3 | Programming | Advanced | "Advanced system design courses, free certificate" |
| 4 | Programming | Beginner | "Introduction to web development, HTML/CSS/JS" |
| 5 | Data Science | Beginner | "Free data analysis course for beginners" |
| 6 | Data Science | Intermediate | "Machine learning with scikit-learn, certificate" |
| 7 | Data Science | Advanced | "Deep learning specialization, free audit" |
| 8 | Data Science | Beginner | "SQL for data analysis, free course" |
| 9 | Design | Beginner | "Free UX design fundamentals course" |
| 10 | Design | Intermediate | "Figma advanced prototyping course" |
| 11 | Design | Beginner | "Graphic design basics, free with certificate" |
| 12 | Business | Beginner | "Free project management course, certificate" |
| 13 | Business | Intermediate | "Digital marketing analytics course" |
| 14 | Business | Beginner | "Introduction to entrepreneurship, free" |
| 15 | Business | Advanced | "Advanced financial modeling courses" |
| 16 | Language | Beginner | "Free Spanish for beginners course" |
| 17 | Language | Intermediate | "Business English communication course" |
| 18 | Cybersecurity | Beginner | "Free cybersecurity fundamentals, certificate" |
| 19 | Cloud/DevOps | Intermediate | "AWS or Azure cloud certification prep, free" |
| 20 | Cloud/DevOps | Advanced | "Kubernetes advanced deployment courses" |

### 2.3 Per-Topic Procedure

For each topic:

1. Run the agent with the topic query. Record the digest, run metrics from
   `research_runs`, and the full state snapshot.
2. Run Baseline A (ChatGPT + Perplexity). Record verbatim responses.
3. Run Baseline B (human search, 30 min). Record all courses found.
4. Two independent reviewers evaluate each course in the agent digest:
   - **Constraint satisfied?** Does it actually match all filters?
   - **Exists?** Is the URL live and pointing to a real course?
   - **Evidence accurate?** Do the evidence items correctly describe the course?
5. Compute all metrics from `specs/PROPOSED_METRICS.md` for the agent run.
   Compute constraint satisfaction and hallucination rate for each baseline.

### 2.4 Reviewer Protocol

- Two reviewers independently label the same data.
- Inter-rater reliability measured via Cohen's kappa (κ). Target: κ ≥ 0.80.
- Disagreements resolved by discussion; third reviewer breaks unresolved ties.

---

## 3. Longitudinal Cache and Memory Test

Validates that cache reuse and memory exclusion work correctly across repeated runs.

**Protocol:**

- Select 5 topics from the test set (one per domain).
- Run the agent on each topic once per week for 4 consecutive weeks.
- Use the same user ID across runs so memory and recommendation history accumulate.

**Expected behavior:**

- Week 1: Full cache miss; most candidates come from Tavily. Cache is populated
  after the run.
- Week 2–4: Cache hit rate should increase significantly for stable topics.
  `tavily_calls_per_query` should decrease. Previously recommended courses should
  not reappear unless explicitly augmenting.

**Metrics to track:**

- `cache_hit_rate` per run (should increase week over week for stable topics).
- `tavily_calls` per run (should decrease).
- `repeated_recommendation_rate` (should approach 0 after week 1).
- `completed_course_exclusion_rate` (should remain 100%).

---

## 4. Reproducibility Controls

- **LLM temperature:** 0 for all LLM nodes (planner, extractor, validator,
  replanner, synthesizer).
- **Prompt logging:** every LLM call logs the full prompt and response, keyed by
  `thread_id` and node name.
- **Model version pinning:** record exact model identifier at experiment start.
  Do not change mid-batch.
- **Tavily result caching:** for unit and integration tests, cache Tavily responses
  by query to ensure deterministic test inputs.
- **Checkpoint snapshots:** full state snapshots after each node are stored as JSON.
  Any single node can be replayed from its input snapshot.
- **Random seed:** any operation with randomness (e.g., tie-breaking in ranking)
  uses a fixed seed logged per run.

---

## 5. Determinism Framing

The system is described as "structurally deterministic." This means:

- **Deterministic:** graph topology (node order, edges, interrupt boundaries),
  Pydantic validation rules, URL normalization, dedup logic, conditional routing
  edges, cache filter queries.
- **Controlled stochastic:** LLM nodes (planner, extractor, validator, replanner,
  synthesizer). Temperature=0 reduces variance but does not guarantee identical
  outputs across API calls.

Research claim: "The pipeline enforces deterministic control flow with bounded
stochastic text generation at defined points, each governed by structured output
constraints and human review."

---

## 6. Reporting Template

Each formal evaluation batch produces a report with:

1. **Summary table:** all metrics from `PROPOSED_METRICS.md` aggregated across the
   test topic set, compared against baselines.
2. **Per-topic breakdown:** constraint satisfaction rate, evidence coverage rate, and
   latency for each topic, for each system (agent + baselines).
3. **Cache efficiency:** cache hit rate and Tavily call count per run across the
   longitudinal test.
4. **Personalization check:** avoided-provider violation rate, repeated recommendation
   rate, completed-course exclusion rate.
5. **Qualitative notes:** reviewer observations on failure patterns (common rejection
   reasons, aggregator page false positives, evidence gaps).
6. **Reproducibility artifact:** link to full log archive (prompts, responses, Tavily
   caches, state snapshots) for the batch.

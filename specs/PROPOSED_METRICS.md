# Course Discovery Agent — Evaluation Metrics

This document defines the metrics used to evaluate the agent's performance.
Metrics are organized into three categories: quality, efficiency, and personalization.
Each metric includes its definition, measurement method, and target threshold
where applicable.

---

## Quality Metrics

**1. Constraint Satisfaction Rate**

- **Definition:** Percentage of courses in the final digest that satisfy all hard
  constraints from `SearchFilters` (price, certificate, language, level, rating).
- **Measurement:** Automated from validation results. Count of `valid_courses` whose
  `CandidateValidation` shows no constraint violations, divided by total recommended.
- **Target:** 100%. Any constraint violation in a published recommendation is a
  failure.

**2. Evidence Coverage Rate**

- **Definition:** Percentage of constraint fields in each `CourseCandidate` that are
  covered by at least one `EvidenceItem`.
- **Measurement:** For each valid course: count fields with at least one supporting
  evidence item divided by total constrained fields. Average across the digest.
- **Target:** ≥80%. Lower coverage means the agent is accepting claims it has not
  verified.

**3. Unsupported Claim Count**

- **Definition:** Number of factual claims in the synthesized digest that are not
  backed by any evidence item in the corresponding `CourseCandidate`.
- **Measurement:** LLM-assisted audit or structured synthesis output — the synthesizer
  is prompted to cite evidence for each claim. Log count of uncited claims per run.
- **Target:** 0 per run. Non-zero triggers a rewrite cycle.

**4. Valid Candidate Count**

- **Definition:** Number of candidates classified `valid` by the evidence validator
  before synthesis.
- **Measurement:** `len(valid_courses)` from state at synthesis time.
- **Tracking:** Per-run. Useful for diagnosing poor recall from cache or search.

**5. Rejected Candidate Count**

- **Definition:** Number of candidates classified `rejected` by the evidence
  validator.
- **Measurement:** `len(rejected_courses)` from state.
- **Tracking:** Per-run. High rejection rates indicate poor search quality or
  constraint-retrieval mismatch.

**6. Uncertain Candidate Count**

- **Definition:** Number of candidates classified `uncertain` — not invalid, but
  lacking sufficient evidence for a critical constraint.
- **Measurement:** `len(uncertain_courses)` from state.
- **Note:** Uncertain candidates are not included in recommendations but are logged
  for human review. They may be promoted to valid on a subsequent Tavily call.

**7. Replan Success Rate**

- **Definition:** Percentage of replanning iterations that produced at least one
  additional valid candidate.
- **Measurement:** For each run with `replan_count > 0`: count iterations after which
  `valid_candidate_count` increased. Divide by `replan_count`.
- **Target:** ≥50%. Below this, the replanner is generating redundant queries.

---

## Efficiency Metrics

**8. Cache Hit Rate**

- **Definition:** Percentage of courses in the final digest that were served from the
  course cache without a Tavily search.
- **Measurement:** Count of `valid_courses` with `source = "cache"` divided by total
  `valid_courses`.
- **Target:** As high as possible for repeat topics. A run on a well-covered topic
  should serve most recommendations from cache. Low cache hit rate on repeat queries
  signals a cache population or freshness problem.

**9. Tavily Calls Per Query**

- **Definition:** Number of Tavily API calls made per research run.
- **Measurement:** `tavily_calls` from state at run end.
- **Tracking:** Per-run. High values indicate the cache is not being used effectively
  or replanning is generating too many new queries.

**10. Latency**

- **Definition:** Wall-clock time from research entry to synthesizer output.
  Excludes human review wait time.
- **Measurement:** Timestamp at `research_entry` start and `synthesizer` end.
  Breakdown tracked per node.
- **Target:** <90 seconds for a typical run (cache-first, 1–2 Tavily calls).

**11. Token Count**

- **Definition:** Total LLM tokens consumed per run across all LLM nodes (planner,
  extractor, validator, replanner, synthesizer).
- **Measurement:** Accumulated from LLM call metadata in each node.
- **Tracking:** Per-run. High token counts relative to valid candidate count indicate
  inefficient prompting or excessive candidate extraction.

**12. Duplicate Candidate Rate**

- **Definition:** Percentage of raw candidates (before dedup) that are removed by
  the dedup step.
- **Measurement:** `1 - len(after_dedup) / len(before_dedup)`.
- **Tracking:** Per-run. High rates are expected when cache and Tavily both surface
  well-known courses. Very low rates may indicate insufficient cache coverage.

---

## Personalization Metrics

**13. Accepted Recommendation Rate**

- **Definition:** Percentage of recommendations that the user explicitly accepts
  (PUBLISH action) vs. total recommendations shown.
- **Measurement:** From `recommendation_events`: count `accepted = true` divided by
  total events for the user.
- **Tracking:** Per-user over time. Low acceptance rate signals poor personalization.

**14. Rejected Recommendation Rate**

- **Definition:** Percentage of recommendations explicitly rejected by the user.
- **Measurement:** From `recommendation_events`: count `rejected = true` divided by
  total events.
- **Tracking:** Per-user. Rising rejection rate suggests memory is not capturing
  preferences accurately.

**15. Avoided-Provider Violation Rate**

- **Definition:** Percentage of recommended courses whose provider is in the user's
  `avoided_providers` list.
- **Measurement:** Check each `valid_course.provider` against `user_memory.avoided_providers`.
- **Target:** 0%. Any violation is a memory or validation failure.

**16. Repeated Recommendation Rate**

- **Definition:** Percentage of recommended courses that have already been recommended
  to the same user in a previous run.
- **Measurement:** Cross-reference digest courses against `recommendation_events` for
  the user. Count duplicates divided by total recommendations.
- **Target:** <10%. The agent should not repeatedly surface the same courses unless
  explicitly augmenting.

**17. Completed-Course Exclusion Rate**

- **Definition:** Percentage of a user's completed courses that are correctly excluded
  from recommendations.
- **Measurement:** For runs where user has `completed_course_urls`, verify none appear
  in `valid_courses`. Rate = correctly excluded / total completed courses checked.
- **Target:** 100%. Recommending a course the user already completed is a memory
  failure.

---

## Metric Collection Infrastructure

**Automated (every run):**

Metrics 1–12 and 15–17 can be computed from structured run state and database
records. They are written to `research_runs` at the end of each research subgraph
execution.

**Semi-automated (requires human signal):**

Metrics 13–14 (acceptance and rejection) require the user to complete the review
cycle (PUBLISH or DISCARD) and optionally provide feedback text. Recorded in
`recommendation_events` at the `user_memory_update` step.

**Recommended reporting cadence:**

After every 20 production runs, or weekly during active development — whichever
comes first. Per-run metrics are always available from the `research_runs` table.

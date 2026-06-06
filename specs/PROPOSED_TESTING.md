**Course Discovery Agent --- Experiment Design & Testing Protocol**

This document defines the experimental framework for evaluating the
Course Discovery Agent as a research contribution. It covers baseline
comparisons, test methodology, reproducibility controls, and specific
validation experiments.

---

**1. Baseline Comparisons**

The agent's performance is measured against three baselines to establish
that it provides meaningful improvement over existing alternatives.

**Baseline A --- Manual Human Search**

- A human researcher searches for courses on the same topic using
  Google, YouTube, Udemy, Coursera, and Class Central.
- Time-boxed: 30 minutes per topic (realistic user effort).
- Researcher records every relevant course found, with metadata (title,
  URL, price, certificate, platform).
- Comparison metrics: Discovery Precision, Discovery Recall, and
  wall-clock time vs. agent's End-to-End Latency.

**Baseline B --- General-Purpose LLM (ChatGPT / Perplexity)**

- Feed the identical user query (e.g., "Find free beginner Python
  courses with certificates") to ChatGPT-4o and Perplexity.
- Record all courses mentioned in the response.
- Verify each course: does it exist? Is the URL valid? Is the price
  correct? Does it actually offer a certificate?
- Comparison metrics: Discovery Precision (after verifying existence),
  hallucination rate (% of courses that don't exist), and URL Liveness
  Rate.

**Baseline C --- Class Central Aggregator**

- Search Class Central for the same topic + filters.
- Record the first 15 results (matching the agent's digest cap).
- Comparison metrics: Discovery Precision, coverage overlap with the
  agent, and recency of results.

---

**2. Test Topic Set**

20 topics spanning five domains and three difficulty levels. Each topic
is a realistic user query:

| #   | Domain        | Level        | Topic Query                                        |
| --- | ------------- | ------------ | -------------------------------------------------- |
| 1   | Programming   | Beginner     | "Free Python basics course with certificate"       |
| 2   | Programming   | Intermediate | "API development with FastAPI, free or cheap"      |
| 3   | Programming   | Advanced     | "Advanced system design courses, free certificate" |
| 4   | Programming   | Beginner     | "Introduction to web development, HTML/CSS/JS"     |
| 5   | Data Science  | Beginner     | "Free data analysis course for beginners"          |
| 6   | Data Science  | Intermediate | "Machine learning with scikit-learn, certificate"  |
| 7   | Data Science  | Advanced     | "Deep learning specialization, free audit"         |
| 8   | Data Science  | Beginner     | "SQL for data analysis, free course"               |
| 9   | Design        | Beginner     | "Free UX design fundamentals course"               |
| 10  | Design        | Intermediate | "Figma advanced prototyping course"                |
| 11  | Design        | Beginner     | "Graphic design basics, free with certificate"     |
| 12  | Business      | Beginner     | "Free project management course, certificate"      |
| 13  | Business      | Intermediate | "Digital marketing analytics course"               |
| 14  | Business      | Beginner     | "Introduction to entrepreneurship, free"           |
| 15  | Business      | Advanced     | "Advanced financial modeling courses"              |
| 16  | Language      | Beginner     | "Free Spanish for beginners course"                |
| 17  | Language      | Intermediate | "Business English communication course"            |
| 18  | Cybersecurity | Beginner     | "Free cybersecurity fundamentals, certificate"     |
| 19  | Cloud/DevOps  | Intermediate | "AWS or Azure cloud certification prep, free"      |
| 20  | Cloud/DevOps  | Advanced     | "Kubernetes advanced deployment courses"           |

---

**3. Experiment Protocol**

**3.1 Per-Topic Procedure**

For each of the 20 topics:

1. Run the Course Discovery Agent with the topic query as input. Record
   the full digest, run_id, wall-clock time, and all intermediate state
   snapshots.

2. Run Baseline A (human search, 30 min). Record all courses found.

3. Run Baseline B (ChatGPT + Perplexity). Record responses verbatim.

4. Run Baseline C (Class Central search). Record first 15 results.

5. Compile a **union ground-truth set**: all unique courses found across
   the agent + all three baselines. This is the recall denominator.

6. Two independent reviewers score each course in the ground-truth set:
   - **Relevant?** (yes/no) --- Does the course match the topic, level,
     and price constraints?
   - **Exists?** (yes/no) --- Is the URL live and pointing to a real
     course?
   - **Certificate accurate?** (yes/no) --- Does the certificate claim
     match reality?

7. Compute all metrics from PROPOSED_METRICS.md for the agent run.
   Compute Discovery Precision and Recall for each baseline.

**3.2 Reviewer Protocol**

- Two reviewers independently label the same data.
- Inter-rater reliability measured via **Cohen's kappa** (κ).
- Target: κ ≥ 0.80 (substantial agreement).
- Disagreements resolved by discussion; if unresolved, a third reviewer
  breaks the tie.

**3.3 Sample Size Justification**

20 topics × 4 systems (agent + 3 baselines) = 80 evaluation runs. With
≤15 courses per digest, this yields up to 1,200 individual course
evaluations --- sufficient for statistically meaningful
precision/recall estimates with confidence intervals.

---

**4. Reproducibility Controls**

To ensure that experiments can be replicated and results are credible:

- **LLM temperature:** Set to 0 for all LLM calls (Gateway parser,
  Synthesizer summaries, Feedback Router classifier).

- **Prompt logging:** Every LLM call logs the full prompt and full
  response to a structured log file, keyed by run_id and node name.

- **Model version pinning:** Record exact model identifiers (e.g.,
  "gpt-4o-2024-08-06") at experiment start. Do not change mid-batch.

- **Embedding model pinning:** Record text-embedding-3-small version.
  Embeddings are deterministic for the same input and model version.

- **API response caching:** For test runs, cache raw API responses
  (Google, YouTube, Udemy, Coursera, Reddit) so that re-running the
  same test topic produces identical worker output. Cache is keyed by
  query + filters + timestamp window.

- **Random seed:** Any operation involving randomness (e.g., tie-
  breaking in ranking) must use a fixed seed logged per run.

- **Checkpoint snapshots:** Full state snapshots after each node are
  stored as JSON. Any single node can be replayed from its input
  snapshot.

---

**5. Determinism Framing**

The architecture document describes the agent as "structurally
deterministic." For research clarity, this must be precisely defined:

- **Deterministic components:** The graph topology (node order, edges,
  interrupt boundaries), Pydantic validation rules, URL normalization,
  fuzzy dedup logic, ranking algorithm, digest formatting, and all
  conditional routing edges.

- **Stochastic components:** LLM-powered nodes (Gateway parser,
  Synthesizer summarizer, Feedback Router classifier). These are
  controlled but not deterministic.

- **Control strategy:** temperature=0 reduces variance but does not
  guarantee identical outputs across API calls. The experiment logs
  every LLM input/output pair to enable post-hoc analysis of output
  variation.

- **Research claim:** "The pipeline enforces deterministic control flow
  with bounded stochastic text generation at three defined points,
  each governed by structured output constraints and human review."

---

**6. Dedup Threshold Tuning Experiment**

A dedicated experiment to calibrate the cosine similarity threshold
(currently 0.92) for Layer 2 dedup.

**6.1 Dataset Construction**

- Collect ≥100 course pairs from production runs.
- Manually label each pair as **duplicate** (same course, possibly
  different URLs/platforms) or **distinct** (different courses).
- Aim for ~50/50 split between duplicate and distinct pairs.

**6.2 Sweep Protocol**

- Compute cosine similarity for each pair using
  text-embedding-3-small.
- Sweep threshold from 0.85 to 0.95 in increments of 0.01.
- At each threshold, compute:
  - **Precision:** % of pairs flagged as duplicate that actually are.
  - **Recall:** % of actual duplicates that are correctly flagged.
  - **F1 score.**

**6.3 Reporting**

- Plot precision-recall curve across thresholds.
- Identify the threshold that maximizes F1.
- If the optimal threshold differs significantly from 0.92, update the
  architecture doc.

---

**7. Longitudinal Cross-Run Test**

Validates that the cross-run dedup and from_date mechanics work
correctly over time.

**7.1 Protocol**

- Select 5 topics from the test set (one per domain).
- Run the agent on each topic once per week for 4 consecutive weeks.
- Each run uses the default from_date (last_search_date from the
  previous run).

**7.2 Expected Behavior**

- Week 1: Full discovery, all courses are new.
- Week 2-4: Only genuinely new or changed courses should appear.
  Previously surfaced courses should be deduped by Layer 2.

**7.3 Metrics**

- **New course rate:** % of digest courses that are genuinely new each
  week. Should decrease over time for stable topics.
- **False re-surface rate:** % of digest courses that were already in a
  previous week's digest and have not meaningfully changed. Should be
  0%.
- **Metadata change detection:** Count of correctly re-surfaced courses
  with \[PRICE DROP\], \[RATING CHANGE\], or \[UPDATED\] tags. Verify
  each tag against actual platform data.

---

**8. Reporting Template**

Each formal evaluation batch produces a report with:

1. **Summary table:** All 10 metrics from PROPOSED_METRICS.md,
   aggregated across the test topic set.
2. **Per-topic breakdown:** Precision, Recall, and Latency for each
   topic, for each system (agent + baselines).
3. **Statistical tests:** Paired t-test or Wilcoxon signed-rank test
   comparing agent Precision/Recall vs. each baseline across topics.
   Report p-values and effect sizes.
4. **Dedup analysis:** Threshold sweep results (if run in this batch).
5. **Qualitative notes:** Reviewer observations on failure patterns,
   edge cases, and digest quality.
6. **Reproducibility artifact:** Link to full log archive (prompts,
   responses, API caches, state snapshots) for the batch.

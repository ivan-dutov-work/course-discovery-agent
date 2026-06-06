**Course Discovery Agent --- Evaluation Metrics**

This document defines the quantitative metrics used to evaluate the
Course Discovery Agent's performance. Each metric includes its
definition, measurement method, and target threshold where applicable.

---

**1. Discovery Precision**

- **Definition:** Percentage of courses surfaced in the digest that are
  genuinely relevant to the user's query (topic, level, price
  constraints).

- **Measurement:** PM labels each course in the digest as relevant or
  irrelevant during review. Precision = relevant / total surfaced.

- **Target:** ≥85% across all test topics.

- **Granularity:** Per-run and aggregated across a test batch.

**2. Discovery Recall**

- **Definition:** Percentage of known-good courses for a given topic
  that the agent successfully finds, compared to a baseline.

- **Measurement:** For each test topic, compile a ground-truth set via
  manual search (human researcher spends 30 min per topic using Google,
  platform sites, and Class Central). Recall = agent-found ∩
  ground-truth / ground-truth.

- **Target:** ≥60%. Full recall is unrealistic due to API coverage gaps;
  the goal is significant time savings over manual search.

- **Baseline comparison:** Also compute recall for ChatGPT/Perplexity
  given the same query, and for Class Central search results.

**3. Dedup Accuracy**

- **Definition:** How accurately the Dedup Node distinguishes new
  courses from previously seen ones.

- **Measurement:** Over N runs, track two error types:
  - **False positive (over-dedup):** A genuinely new course was
    incorrectly matched to a known course and dropped. Detected by
    manual review of dropped candidates.
  - **False negative (under-dedup):** A duplicate course was surfaced
    as new. Detected when PM flags a course as "already seen" during
    review.

- **Target:** False positive rate <5%, false negative rate <10%.

- **Note:** This metric directly informs dedup threshold tuning (see
  Section 8 of the architecture doc).

**4. Feedback Router Accuracy**

- **Definition:** Percentage of PM feedback messages correctly
  classified into the intended routing action on the first attempt
  (before the confirmation echo step).

- **Measurement:** Log every router classification alongside PM's
  confirmation or correction. Accuracy = confirmed / total
  classifications.

- **Target:** ≥90%. Below this, consider expanding few-shot examples or
  switching to structured inline buttons.

**5. End-to-End Latency**

- **Definition:** Wall-clock time from run trigger (user message or
  cron fire) to digest delivery at the Telegram Gate.

- **Measurement:** Timestamp difference between run start and the
  Synthesizer's final output message. Excludes PM review wait time.

- **Target:** <120 seconds for a typical run (≤50 raw candidates).

- **Breakdown tracked:** Gateway parse time, Worker A/B/C time
  (parallel, so max of three), Dedup time, Synthesizer time, Telegram
  send time.

**6. PM Effort Score**

- **Definition:** Average number of feedback loops (REWRITE, AUGMENT,
  RESET cycles) per run before the run terminates in PUBLISH or
  DISCARD.

- **Measurement:** From the Run Log: count of routing_decisions per
  run, excluding the terminal PUBLISH/DISCARD.

- **Target:** ≤1.5 average. A score of 0 means the PM approved on
  first review. Lower is better --- indicates higher agent quality.

- **Secondary metric:** DISCARD rate. If >30% of runs are discarded,
  the discovery pipeline needs quality improvement.

**7. Certificate Accuracy**

- **Definition:** Percentage of courses where the `certificate_type`
  classification (none / platform / accredited) matches ground truth.

- **Measurement:** For each test batch, manually verify certificate
  claims on course landing pages. Compare against agent's
  classification.

- **Target:** ≥90%. Misclassifying "platform" as "accredited" is the
  highest-severity error.

**8. URL Liveness Rate**

- **Definition:** Percentage of course URLs in the final digest that
  return a valid HTTP response (2xx) at the time of PUBLISH.

- **Measurement:** Automated --- the Synthesizer's HTTP HEAD check
  already produces this data. Log pass/fail per URL.

- **Target:** 100% at PUBLISH time (dead links should be caught and
  dropped by the Synthesizer). Track the pre-filter liveness rate
  (before dropping) as a data quality indicator.

**9. Digest Acceptance Rate**

- **Definition:** Ratio of runs that end in PUBLISH vs. total completed
  runs (PUBLISH + DISCARD).

- **Measurement:** From the Run Log: count PUBLISHED / (PUBLISHED +
  DISCARDED). Exclude STALLED runs.

- **Target:** ≥70%. Below this threshold, investigate whether the issue
  is poor discovery quality, bad synthesis, or overly strict PM
  standards.

**10. Curriculum Overlap Score**

- **Definition:** Average curriculum_score across all courses in
  published digests, as computed by the RAG Curriculum Verification
  pipeline.

- **Measurement:** Per course: % of syllabus modules that match formal
  academic course outlines. Aggregated as mean across all published
  courses in a test period.

- **Target:** ≥50% average for published courses. Courses below 40% are
  flagged as low-rigor; tracking the average indicates whether the
  agent tends to surface academically grounded content.

- **Dependency:** Requires syllabus PDF availability. Report coverage
  rate (% of courses with available syllabi) alongside the score.

---

**Metric Collection Infrastructure**

All metrics except Discovery Recall and Certificate Accuracy can be
computed automatically from existing system logs (Run Log, Dedup logs,
Telegram message logs). Discovery Recall and Certificate Accuracy
require manual labeling effort and are measured during formal evaluation
batches, not on every production run.

**Recommended reporting cadence:** After every 50 production runs, or
weekly during active development --- whichever comes first.

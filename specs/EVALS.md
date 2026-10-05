# Evals: levels, graders, cases, CI

Plan for the eval harness (backlog E1). It says what is measured at each level, with which
grader, from which cases, and what runs where in CI. Decisions are in `DECISIONS.md`; the
research behind them is in `article/notes/04-observability-and-compliance.md` once E1 lands.

## Terms

- **Task**: one fixed input plus the success criteria. A row in a case file.
- **Trial**: one complete execution of a task from a clean state, then graded. A new
  `thread_id`, an empty `pending_courses`, no shared cache from a previous trial. Repeating a
  task k times gives k trials; they must be independent, or one failure drags the others
  with it and the pass rate lies.
- **Trace**: the record of one trial. For this graph: the node sequence and the state after
  each node, read from `graph.get_state_history(config)`, plus the final state and the
  database and outbox rows the run left behind.
- **Grader**: logic that scores one aspect of a trial or a trace. A grader is code, a string
  check, or an LLM judge.
- **Trace grading**: scoring a trace with graders. It is not the same as LLM-as-judge. Most
  graders here are code that asserts on the trace (node order, `Send` count, evidence present,
  nothing published before the interrupt); the judge is used only for free text.
- **Outcome**: the state the run left in the environment (rows in `courses`, the outbox row,
  the profile), as opposed to what the run said.

## Principles

1. **Failures first.** Each case comes from an observed failure or a named requirement, not
   from a guess about what might go wrong. Past findings in `notes/` and `ARCHITECTURE.md`
   (RESET re-extracting the old topic, the reducer echo, a stale subgraph result on resume,
   the uncleared replan budget) are real failures and seed the first cases.
2. **Seed now, replace later.** The MVP has no traffic, so the first cases are representative
   seeds. Every case carries `source: seed | review | prod`; a seed is replaced or joined by
   real cases once they exist, and the swap is a data change, not a code change.
3. **Deterministic graders first.** A judge is built only for a free-text field that code
   cannot grade, and only for a failure that will be iterated on repeatedly.
4. **Path where the path is code, outcome where the model chooses.** The outer and research
   graphs have a fixed topology, so asserting the node sequence there is a correctness check.
   The curator's tool loop is model-driven, so grade the outcome and invariants only
   (cap, validated patch, nothing written without `finish`).
5. **Binary verdicts.** Pass or fail with a one-line reason; partial credit only where a task
   has independent parts, scored per part. No 1 to 5 scales.
6. **Balanced cases.** Each behavior has cases where it should fire and cases where it should
   not (route PUBLISH versus a question that merely sounds approving; screen a poisoned
   description versus a benign one that quotes an instruction).
7. **Read transcripts.** A score is not trusted until someone has read failed and passed
   traces; a failing case is first checked for a grader bug.
8. **Noise is measured, not guessed.** A live score is reported with an error bar, and a
   change is called a regression only past the minimum detectable effect of a paired
   comparison against the stored baseline.

## Cases

One YAML file per level under `evals/cases/<level>/`. Fields: `id`, `level`, `source`,
`origin` (finding, backlog item or `FEEDBACK.md` case it came from), `input`, `expect`
(structured assertions), `graders` (names from the registry), `tags`, `status`
(`capability` | `regression`). A case that is stable at 100% over three nightly runs
graduates from `capability` to `regression`. A grader bug found while reading transcripts
is fixed in the grader, not by editing the case.

Seed targets (counts are a floor, not a quota):

| Level | Seed cases | Where they come from |
|---|---|---|
| L1a validator, dedup, extractor, ranking, planner | 25 golden rows | mock catalog, `test_research_nodes`, `test_cache_topic`, the evidence rule |
| L1b parser | 12 queries | filters implied, budget and certificate defaults, PII in the query, rule-fallback parity |
| L1b router | 5 routes, 4 phrasings each, plus 4 look-alikes | `RoutingAction`, ambiguous approvals |
| L1b synthesizer | 10 digests | groundedness against the evidence, ranking highlights, withheld free text |
| L1b curator | `FEEDBACK.md` cases 1 to 3, 7, 10 to 18 stubbed; 4 and 8 live | the case list there |
| L2 subgraphs | 8 scenarios | enter at `plan_gap_search` for AUGMENT, budget exhausted, empty cache, search failure |
| L3 graph | 8 scenarios | approve, discard, REWRITE, AUGMENT, RESET, multi-round, wrong owner, stale thread |
| L4 reliability | 10 tasks, k trials each | the L3 scenarios that touch a live model, plus fault and injection probes |

## Levels

Cost falls toward L0. Everything through L3 runs with no key and no network.

### L0 Contract
Topology, channel set, state schema version, PII flow, stored-checkpoint compatibility.
Exists: `test_graph_topology`, `test_state_contract`, `test_checkpoint_compatibility`,
`test_flow_rules`. Nothing to build; it runs in the same CI job.

### L1a Component, deterministic
One node or function against golden rows, no model. Purpose: pin each rule, including
"missing evidence is `uncertain`, never `valid`". Metrics: exact match per row; for cache
retrieval, precision and recall as in `scripts/calibrate_topic_floor.py`. Gate: 100%.

### L1b Component, model nodes
`parse_user_request`, `interpret_review_feedback`, `rank_and_summarize_courses`,
`curate_user_memory`, the tagger. Two modes:
- **Stubbed** (CI): scripted model, asserts schema, fallback and degradation paths.
- **Live** (opt-in): real model. Parser and router are graded by code (structured fields
  exact; router also as a confusion matrix over the five routes). Synthesizer and curator
  free text go to the judge for groundedness, polarity, scope, no invention, no loss.

### L2 Subgraph
The research subgraph and the curator subgraph, entered through their schemas.
Enter mid-graph with `update_state(as_node=...)` and `interrupt_after` to test AUGMENT and
the replan without running the whole spine. Assert the five-key output, the loop budget, and
that the evidence rule holds after a replan.

### L3 Graph scenarios
The full outer graph on a checkpointer, across review rounds. Graders: fixed path
(nothing publishes without the interrupt; the route taken equals the route expected), outcome
(`courses` and `pending_courses` after publish and after discard, outbox row, profile after
the curator), and run two reflects run one. Model stubbed in CI.

### L4 Reliability and adversarial
Whole graph, live model, k trials per task. Metrics: pass rate, pass^k, and flakiness per
task. Also fault injection (primary model down so the fallback fires, search failure, DB
failure), the injection probes that already exist, and kill-and-resume. Closes the
"fallback never triggered live" item under Verify in `BACKLOG.md`.

### L5 Online
Sampled real traces, error analysis, new cases. No production traffic exists, so this level
is a described loop and article prose only (`DECISIONS.md`, "Not a production case study").
The data model already allows it: a case with `source: prod` is a row like any other.

## Graph-level analysis

Every failing L3 or L4 trial is attributed to its first failing node. Walk
`get_state_history` from the earliest snapshot, run each node's graders on its snapshot, and
report the first red node as the cause, since errors compound downstream. Aggregate across
the suite as a node-by-outcome table; the topology is fixed, so the transition matrix is a
plain table. The report lists, per failing case, the node, the grader and the level at which
a smaller reproducing case should be added.

## Judge

- **Model:** `google/gemini-3.1-flash-lite` through `build_llm("judge")`, temperature 0,
  structured Pydantic verdict, short reason, an `unknown` option. $0.25 per million input
  tokens and $1.50 per million output (OpenRouter list, checked 2026-10-05). Output is a
  verdict plus one line, so input dominates the bill. A different family from the primary
  generator (`deepseek/deepseek-v4.1-flash`) avoids self-preference. Its fallback
  `google/gemini-2.5-flash-lite` is Gemini too, so a trial whose generator fell back to it is
  tagged `judge_same_family` and reported separately.
- **No fallback list for the judge**, as for the tagger: a silent model swap changes the
  grades.
- **Rubric:** the five from `FEEDBACK.md` (captured, polarity, scope, no invention, no loss)
  plus `grounded` for digests (each claim is supported by that course's evidence). Each is a
  separate call or a separate field, scored pass or fail. Majority of three calls.
- **Calibration:** the owner labels 30 judged outputs first, then about 100 across the
  failure modes, split train, dev and test. Gate on TPR and TNR on the test split; the numbers
  to beat are set after the first measurement, not before. With one labeller there is no
  inter-annotator agreement; say so, and recalibrate after any judge model change.
- **Not used for:** structured fields (exact assertions), anything an embedding or a rule can
  check, and polarity by embedding similarity (`DECISIONS.md`).

## Statistics

- Deterministic levels run one trial. Live levels run k trials per task, default k = 5 in the
  nightly job.
- Report pass rate with a standard error, and pass^k for `regression` tasks. For a
  comparison against the baseline use paired per-task differences, not two independent rates.
- Compute the minimum detectable effect from the suite size and the observed variance before
  setting a gate. On a suite this small the MDE is large; the report prints it so a one-point
  move is not read as a signal.
- The mock catalog is 14 courses and 13 topics, and hashing retrieval already scores 1.000
  mean average precision on it. L1a and L3 measure plumbing and regressions, not
  generalization. The article says so.

## CI

`.github/workflows/ci.yml` runs the first row below (milestone 2); the other workflows do not exist yet. GitHub Actions (remote is GitHub). `evals/` runs on pytest with
`pytest-xdist` (dev dependencies, parametrized over the case files); `tests/` stays on
unittest. Layout:

```
evals/
  cases/{l1a,l1b,l2,l3,l4}/*.yaml
  graders/            code graders, judge.py, registry
  baselines/*.json    committed scores with error bars
  cassettes/          recorded model replies and judge verdicts
  run.py              --level --trials --replay --live --report
.github/workflows/ci.yml
.github/workflows/evals-nightly.yml
```

| Trigger | What runs | Gate |
|---|---|---|
| Every push and PR; target under 5 minutes; no key, no network | L0, L1a, L1b stubbed, L2, L3 on `MemorySaver` with the hashing embedder; a pgvector service container for the Postgres tests (including the L3 wrong-owner and stale-thread scenarios) | Hard block, 100% |
| PR touching prompts, `app/llm.py`, graders or the catalog (path filter) | L1b replayed from cassettes, judge verdicts cached, re-scored; strict replay, the network is off and a missing cassette fails the job | Block when the paired difference to the baseline is worse than the MDE |
| Nightly, and a `run-live-evals` PR label | L1b live and L4: k = 5 trials, pass^k, judge majority of 3, fault injection, injection probes; hard cost cap of $2 per job | Alert; block only for the label or a model or prompt change |
| Weekly, by a person | Read 10 to 20 traces; judge TPR and TNR on the labelled set; refresh adversarial samples; check for saturation | Process |
| Deploy | contract version check and checkpoint fixtures (`DECISIONS.md` runbook) | Block |

Cassettes are keyed by a hash of model, parameters and the full prompt, so a prompt edit
invalidates exactly the entries it changes and the PR must re-record them. The record step
runs only in the nightly job or locally with `LIVE_LLM_TESTS=1`, never in the PR job.

## Build order

1. `evals/` skeleton: case loader, grader registry, `run.py`, report. Move the existing
   hand-coded L1a and L3 checks onto cases without changing what they assert.
2. L1a golden rows and the L3 scenario table; `ci.yml` with the hard-block job and the
   Postgres service container.
3. The judge (this is P6's `tests/judge.py`, built once and reused), the rubric, the 30-label
   set and the first TPR and TNR measurement; L1b live for router, synthesizer, curator.
4. Cassette record and replay; the path-filtered PR job; baselines and the paired comparison.
5. k-trial runner, pass^k, L4 fault injection (forced fallback), `evals-nightly.yml`.
6. First-failing-node report over `get_state_history`.

## Definition of done for E1

Code and tests pass; the `CLAUDE.md` table applied; `STATUS.md` line; findings in `notes/04`
(judge model, calibration numbers, MDE, the measured fallback behavior); the backlog item
deleted; the article section drafted if it is in the outline (`BACKLOG.md`, "Article drafting").

## Out of scope

Online A/B testing, user feedback pipelines, crowdsourced human studies, and a hosted eval
platform. LangSmith is not adopted: `DECISIONS.md` keeps traces on vendor-neutral
OpenTelemetry so prompts and state stay in-house, and the harness reads checkpoints
directly.

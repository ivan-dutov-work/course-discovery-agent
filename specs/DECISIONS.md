# Decisions: settled, don't re-litigate

Append only. Each entry says what was chosen and why; reopen one only with new evidence.

What belongs here: a choice between real alternatives, or something ruled out on purpose,
that a future reader would otherwise reopen. Evidence for it (versions, measurements,
how it was verified) goes in the matching `article/notes/` file; the entry here states the
choice and points at it. Not for status, plans or anything the code already shows.

Where to write: add a bullet at the end of the section it fits, and create a new `##`
section only if none does.

- **Scope**: what the article claims or declines to claim.
- **Design**: how the graph, nodes and integrations are built, and what was left out.
- **Compliance**: privacy, encryption and erasure boundaries.

Entry format: `- **Choice in bold.** Why, in one to three lines.` Add a trailing
`(notes: 03-resilience-and-providers.md)` when evidence exists. To supersede an entry,
leave it and append a new one that says which it replaces and what changed.

## Scope

- **Not a production case study.** Nothing in the article comes from operating this agent
  under real traffic: no load data, no incident log, no cost-at-scale numbers. Say so once,
  in §12, and don't let "production principles" imply operational validation.
- **"Production would swap this one class"** (mock search to a real provider) is an
  architectural claim, not a tested migration.
- **The article's subject is LangGraph mechanics.** Surrounding infrastructure gets a
  pointer, not a section (rule 12 in the `course-article-style` skill).
- **Model tiering by task difficulty is out.** It is a cost and quality policy, orthogonal to
  wiring control flow. At most a parenthetical: each node calls whatever Runnable it is bound to.
- **The outline follows one spine:** a checkpoint is exact at node boundaries, so production
  correctness is about the seam between nodes.
- **The design target is many users, not one reviewing manager.** Per-run human approval does
  not scale, and the consequential shared state is the course cache, not a single digest. The
  article still teaches LangGraph mechanics; the reframing is prose in §13, and the per-run gate
  is unchanged until backlog N2 is decided.

## Design

- **Workflow, not agent.** Planning, extraction and validation are rules; four nodes use an
  LLM. A model-driven planner is the one change that would move the graph toward an agent
  (`ARCHITECTURE.md`).
- **`RetryPolicy` only on read-only or idempotent nodes,** retrying `is_transient` errors.
  The router and synthesizer keep their own retry; `build_llm` defaults to zero client
  retries so the layers don't multiply.
- **No `CachePolicy`.** This topology doesn't produce same-input reruns, so it is contrasted
  against the domain cache in prose.
- **No node-level `timeout=`.** langgraph 1.1.2 has no such parameter on `add_node`.
  Timeouts wrap the client call.
- **Fallback is delegated to OpenRouter's `models` array,** not `.with_fallbacks()`. The
  argument for a gateway is one bill, one rate-limit surface and spend caps, not fallback.
- **Vendor-neutral OpenTelemetry over `LANGSMITH_OTEL_ENABLED`,** because prompts and state
  would otherwise leave for a third party.
- **PII redaction is an in-process library** (Presidio, `en_core_web_sm`), not an external
  API: no network hop, no new data-sharing surface. Instructor names are redacted as a
  known trade-off.
- **Outbox behind a port, not a queue.** Deterministic submission, at-least-once delivery,
  consumer idempotent. Lease-versus-handler-duration is queue territory and not built.
  The outbox row does not share a transaction with the domain write.
- **`async` durability is only lightly tested under a hard kill:** whether the last background write
  lands is a race. One scenario, three trials (`notes/02`): the validator re-ran after the kill.
  The CLI passes `durability="sync"`.
- **Cross-worker patterns (§11) are architectural descriptions,** with no implementation.
- **Feedback handling is one bounded agentic step; the rest stays a workflow.** Refines
  "Workflow, not agent": planning, extraction and validation stay rules, but turning free-text
  feedback into memory changes depends on the data (read the profile, detect a conflict, decide
  it is not a preference), so it gets a tool loop with a closed tool set, a step cap and a
  fail-closed commit. The model proposes a validated patch and never writes. Spec and cases in
  `FEEDBACK.md`; backlog P5.
- **Memory update is judged by exact assertions plus an LLM judge, never by embeddings.**
  Embeddings score topical closeness, not polarity, so a wrong-sign update passes. Structured
  fields are asserted exactly, free-text fields go to a rubric judge, and run two's behavior is
  the primary check.
- **A learned preference is written only if a named consumer changes the next run.** Today only
  three `UserMemory` fields are read; storing the others without a consumer would pass a judge
  and change nothing.
- **Embeddings sit behind an `Embedder` port with a deterministic local default** for tests and
  CI. It is lexical, not semantic, so the article claims topic-aware retrieval, not semantic
  search. A real provider is another implementation of the same port (1536 dimensions, fixed by
  migration 001).
- **The four no-op anchors are reduced to one.** `start_research`, `prepare_augmented_search`
  and `end_research_on_error` are removed (conditional entry point, direct edges).
  `await_human_review` stays because `interrupt_before` needs a real node to pause in front of.
- **Shared-cache promotion is a cascade: rules, then a cheap typed verifier with confidence,
  then an LLM reviewer, then a human queue,** and an item moves up a tier only when the tier
  below is not confident or the tiers disagree. Promotion needs a higher confidence than
  rejection because a wrong promotion reaches every user. A random audit sample of
  auto-accepted items feeds the human queue and the threshold fit. The verifier sits behind a
  port; JEV (TypeSafe, hosted, early access) is one adapter, and its published cost, latency
  and accuracy figures are vendor claims until measured on our data. Its confidence
  calibration did not transfer between tasks in the paper, so thresholds are fitted on our
  own labels. Backlog N1.
- **Implicit feedback is not built.** Course choices are sparse per user, so the realistic
  methods are decayed counters, an embedding moving average and batched LLM personas;
  collaborative filtering needs cross-user scale this domain will not have soon. Article prose
  in §13 only.
- **Level, price and certificate words are stopwords in the embedder.** They are structured
  filters, and left in the topic they matched unrelated courses: the fallback parser's topic
  `Find free Python certificate beginners` ranked a web-design certification above the Python
  courses. Stopwords are removed after stemming (`beginners` first leaked through).
  (notes: 05-scale-and-scope.md)
- **The topic floor and relative cutoff belong to the embedder.** Hashing: floor 0.12, no relative
  cutoff. `openai/text-embedding-3-small`: floor 0.22, relative cutoff 0.70 (keep courses scoring
  at least 70% of the best remaining match). The hashing floor gave the real model precision 0.22,
  because unrelated topics such as `cooking` score 0.06 to 0.18 against every course. Level, price
  and certificate words are stripped from the topic and the stored course text for every embedder
  (`embeddings/facets.py`); the real model had been matching `free certificate beginners` against
  unrelated courses. On the mock catalog this took the real model from precision 0.68 / recall
  0.89 to 0.84 / 0.86 (stripping the topic did all of it; stripping the course text changed
  nothing measurable here). The relative cutoff adds little at the best floor (F1 0.849 to 0.861)
  but removes the cliff: F1 stays at or above 0.849 for floors 0.19 to 0.24, against 0.61 to 0.82
  without it, so the floor is set mid-plateau. It hurts hashing (F1 0.857 to 0.64), whose scores
  are not compressed, hence per embedder. Blend weights 0.7 / 0.2 / 0.1 stay: they only order
  courses past the floor and mean average precision is flat across the weights tried (real model
  0.920 to 0.924, hashing 1.000). `confidence` and `use_count` have no ground truth in the
  catalog, so that is a robustness check against random values, not a fit. Refit the floor and
  cutoff if the model or the stored text changes, then `embeddings backfill --all`. The article
  keeps "topic-aware", not "semantic": the real model reaches recall 0.86 against 0.75 for
  hashing at precision 0.84 against 1.00, on 13 topics and 14 courses. (notes: 05-scale-and-scope.md)
- **`ResearchPlan.cache_query` is removed.** The cache lookup runs before the plan exists and
  reads `filters.topic`. Checkpoints that still hold the field load, because the model ignores
  extra keys (test in `tests/test_checkpointer.py`). (notes: 01-state-and-control-flow.md)
- **`courses.topics` is a property of the course, never of the query.** The run's topic is
  user-derived text; storing it in the shared cache reaches a table erasure cannot cover (the
  flow-rule check flags it) and lets loosely matched courses pollute later lookups. Topics come
  from the course's own content, by a tagging step that is not built. (notes: 05-scale-and-scope.md)
- **Do not restructure `AgentState` wholesale** (superseded for the research subgraph by the entries below; applies to the rest). It was wide (34 channels, 28 after the top-level pass below) but the width is mostly
  the article's subject: per-stage candidate lists are checkpoint history, and reducers and `Pii`
  markers are per channel, so nesting channels hides both from `flow_specs.py`. Fix the real
  redundancy (duplicate counters, budgets held as state) and try private schemas on new code
  first (the P5 curator). Backlog S1. Done for the research subgraph: see the next entries.
- **The research subgraph has a five-key input and a five-key output, and keeps its state with
  `checkpointer=True`.** AUGMENT re-enters mid-pipeline and needs the previous pass's plan, ledger
  and validation results, which a private channel only keeps when the subgraph is stateful
  (`article/notes/01`). Ruled out: keeping those channels in the outer state (the outer state would
  stay at 28 channels and the boundary would be decoration), and re-running AUGMENT from
  `load_user_profile` (changes what AUGMENT means). The intermediate candidate lists stay channels
  of the subgraph and so stay in its checkpoint history. The counters and the valid, rejected and
  uncertain counts leave through `metrics`, which already held them, so there is no separate
  summary channel.
- **A pass counter, not a no-op anchor, guards the stateful subgraph.** A subgraph with
  `checkpointer=True` resumes from its saved checkpoint, ignoring new input, whenever it is the
  first task of a resumed tick (`langgraph/pregel/_loop.py`, `CONFIG_KEY_RESUMING`). The earlier
  no-op node `start_research_pass` only moved that tick and left a window after it committed.
  Now `start_research_pass` stamps `research_pass = len(feedback_history)` and the subgraph echoes
  it; if the echo differs, `course_research` has returned a previous pass's result and the edge
  re-enters `start_research_pass`, where the flag is spent. Ruled out: making the subgraph
  stateless (AUGMENT needs the plan, ledger and validation results) and putting those channels in
  the outer state. (notes: 01-state-and-control-flow.md, 02-durability-replay-effects.md)
- **`begin_pass` resets the subgraph's private state on a fresh pass.** `routing_decision` is empty
  on a first run and after RESET (the gateway clears it); REWRITE and AUGMENT keep the ledger and
  results on purpose. The reducer channels are cleared with `Overwrite`, since `operator.add`
  cannot subtract. Without it a RESET to a new topic planned no new queries and re-extracted the
  old topic's results, and the replan budget was spent for the rest of the thread.
- **A planning failure leaves the subgraph through `discard_reason`,** an outer channel, so the
  outer graph routes to `discard_run` instead of offering an empty digest for review. The private
  `error` channel is gone.
- **A fan-in reducer is declared on the schema where the branches meet, not on the parent's.**
  `tavily_results`, `completed_queries` and `research_notes` are `operator.add` in `ResearchState`
  and not in `AgentState`. The alternative, a reducer on the parent that is idempotent over the
  echo (prefix test or dedup), was ruled out: it cannot tell an echo from a repeated query, and
  `metrics.tavily_calls` is a count of calls. A REWRITE round therefore adds no queries, because
  the planner skips every `completed_queries` entry. (notes: 02-durability-replay-effects.md)
- **Top-level state holds only what cannot be derived.** The run id is the `thread_id`; the two
  budgets are optional `configurable` keys with in-code defaults; the review round is
  `len(feedback_history)`; `manager_feedback` is an inbox cleared once interpreted;
  `metrics` is the only home of the counters; the gateway failure sets `discard_reason` and
  the edge reads that, not a second `error` flag. `user_id` stays a channel because the `Pii`
  markers and `flow_specs.py` anchor on it. Budgets are not persisted by LangGraph, so a caller
  that resumes with a different config changes them (measured in `tests/test_top_level_state.py`);
  accepted because the CLI is the only caller and always passes the defaults. `Command(goto=...)`
  for the gateway failure stays out (`STATUS.md`, "Not in the code"); the replan decision is the one
  `Command` in the graph (entry below). In-flight runs must be finished
  or discarded before deploying it: a thread paused under the old channel set does not resume
  (`ARCHITECTURE.md`, "Naming"). (notes: 02-durability-replay-effects.md)
- **The shared cache is written after approval, through a staging table, not a status column.**
  `save_verified_courses` writes valid web-sourced courses to `pending_courses` keyed by run;
  `promote_approved_courses` upserts the ones in the approved digest into `courses` and
  `drop_pending_courses` deletes the staging on discard. A status column on `courses` would let a
  re-seen, already-served row be overwritten in place before anyone approved the new text
  (`upsert_courses` replaces title and description on conflict), and a second run could replace a
  pending row another run's reviewer was about to approve. Keying staging by run keeps both
  apart. Only valid courses are promoted; uncertain ones are no longer persisted (the lookup never
  served them). Rows already in `courses` are left alone, not re-screened. This is the interim
  human tier for backlog N1 and does not settle N2; N1's `promotion_status` column is replaced by
  this table, so N1 item 1 is amended.
- **Staging rows are pruned by thread inactivity, not by row age alone.** `prune_staged_courses`
  deletes a `pending_courses` row only when it is older than the cutoff and its run has no
  `run_threads.last_activity_at` since. Age alone would delete the staging of a run a reviewer is
  still working through (`created_at` is not bumped by later rounds), and the approval would then
  promote nothing without an error. A run with no `run_threads` row (already forgotten by
  `prune --checkpoints`) counts as idle. Same cutoff as `prune --checkpoints`, so a pruned thread and its
  staging go together.

- **The curator writes only fields a next-run consumer reads.** `career_goals`,
  `learning_style_notes` and `preferred_course_length` are refused by `propose_patch`; free-text
  preferences go into scoped notes, which the ranking prompt reads. Cases 5 and 6 of
  `FEEDBACK.md` wait for P4c and for a consumer of goals.
- **The curator reads the profile through a tool, not from preloaded context, and cannot
  propose before it has.** A preloaded profile would make `read_profile` dead weight and the loop
  a structured call in disguise; the refusal is what pins case 7's trace. Deviates from the
  `load_context` description that listed `UserMemory` in `FEEDBACK.md`, which is amended.
- **The curator is an isolated subgraph with `input_schema` and `output_schema`.** Its only
  output is the `memory_update` status string. Returning the shared `feedback_history` would
  add it to itself again (reducer echo). (notes: 01-state-and-control-flow.md)
- **Memory-update idempotency is a claim row in the same transaction as the write.** One
  `memory_updates` row per `run_id`, inserted `ON CONFLICT DO NOTHING` under the profile row
  lock; a replay or a `RetryPolicy` retry of `commit` applies once. It doubles as the audit
  trail and is erasable by `user_id`.
- **Curator failures degrade, database failures raise.** An LLM error, cap hit or reply without
  a tool call leaves the profile unchanged and never fails the run; a failed write in `commit`
  re-raises, as every DB writer does. Bare approvals skip the model entirely.
- **A profile vector never enters state.** `profile_embedding` (1536 floats) would land in every
  checkpoint and span; it is computed and applied inside the repository or node, and only the
  derived order reaches state. Backlog P4b.
- **`parse_user_request` reads the profile from the repository, not from state,** to apply the
  stored budget and certificate defaults, because the profile is loaded inside the research
  subgraph, after parsing. Only the two scalar defaults are read, and they reach
  `search_filters`, never the LLM prompt.

- **Profile similarity is a coarse tie-break, and web candidates are not embedded for it.**
  The query returns a scalar (`profile_similarity`), never the vector, and `_rank_courses`
  rounds it to 0.1 and places it after explicit preferences and price but before rating, because
  a continuous score would never tie and would replace rating outright. Embedding web candidates
  on the fly would add one remote call per candidate on the request path for a tie-break.
  Refines the "profile vector never enters state" entry above.
- **`preferred_course_length` is `short | medium | long`, not free text.** Up to 10 hours,
  up to 40 hours, more. Free text such as "2h/week" has no comparison against a total duration,
  and the curator model picks from three words reliably. Weekly effort is not a duration and is
  ignored by the extractor. Supersedes the entry that kept the field unwritable.
- **`OpenRouterEmbedder` sends `dimensions=1536`** so models with adjustable output fit the
  fixed column; the default `openai/text-embedding-3-small` is natively 1536. A transient error
  (transport, 429, 5xx) propagates unwrapped so `RetryPolicy` fires; anything else becomes
  `EmbeddingError`. Vectors from different embedders are not comparable, so switching needs a
  backfill.
- **The injection screen is a port with JEV as one adapter, and it is not a chat model.**
  `typesafe/jev-1.13` is reached on OpenRouter's Decisions API (`/api/alpha/decisions`, `noul`
  question), a different endpoint from chat completions, so it lives in `guardrails/` beside the PII
  port and not behind `build_llm()`. The article's LLM-provider rule (code and article change
  together) is unchanged. JEV is itself steerable by the text it screens (TypeSafe's limitations
  page), so it is one layer, not the defense. (notes: 04-observability-and-compliance.md)
- **A screen failure withholds free text instead of passing it or failing the run.** An error,
  timeout or malformed reply is treated as the review band: the model still gets structured fields,
  never description or evidence. Failing the run would let one provider outage stop every
  digest; passing would turn the outage into a bypass. Counted as a degradation
  (`injection_guard`, `screen_failed`). Differs from PII redaction, which raises, because a missed
  redaction is a leak that cannot be undone and a withheld description is not.
- **Screen thresholds are the independent benchmark's (0.35 review, 0.70 block), not ours.** Our
  28 hand-written samples separate cleanly and fit nothing. Refit on labelled data before
  trusting them. (notes: 04-observability-and-compliance.md)
- **Screen the text per course, not per run.** One call per course keeps a poisoned listing from
  steering the verdict on its neighbours, since questions in one call share one state. Cost is one
  small request per course (about 300 ms median, $0.042 per million input tokens, vendor price).
- **The screen's flow is declared as its own step (`step:screen_course_text`).** The synthesizer
  reads channels that carry subject data, so a sink declared on the node is flagged; the screen
  receives catalog text only, and the check cannot see which read feeds which sink.

- **State contract changes are versioned, and an old thread is refused, not migrated.** The
  checkpoint schema is a contract with every paused thread. Any change to the outer or research
  channels, or to a node name that can hold a pause, bumps `STATE_SCHEMA_VERSION`
  (`domain/contract.py`). Resuming a thread written under another version raises
  `IncompatibleThreadError` in `authorize_thread` (version stored in `run_threads.schema_version`,
  never upgraded in place; NULL is v1). Why refuse: without the guard, an old thread re-runs the
  research pass with real searches, staging writes and a model call, then parks again with a
  different digest (`notes/02`); and the pre-change shape cannot be rebuilt safely, since the
  dropped channels are exactly the ones whose values would have to be reconstructed. A migration is
  worth building only when drain-before-deploy is not possible, and then as an `aupdate_state`
  backfill that has its own fixture. Runbook for a contract change: (1) change the channels or
  nodes; (2) bump the version; (3) `uv run python -m tests.contract_snapshot`; (4) capture a fixture
  for the new version with `scripts/dump_checkpoint_fixture.py` (thread id `fixture-*`) and add it
  to `tests/fixtures/checkpoints/manifest.json`, flipping the previous one to `refused` unless a
  migration exists; (5) drain or discard in-flight runs before deploy. A change that does not touch
  channels or nodes (a reducer, a type, a function body) needs none of this; the snapshot does not
  see it, and `notes/02` lists which of those changes still break a resume. Applied once already:
  the stale-result retry got its own counter (`research_retries`) and node (`retry_research_pass`),
  which bumped the version to 3, so that a result that stays stale raises
  `StaleResearchResultError` after one retry instead of looping until `GraphRecursionError`.
  Raising, not discarding, because a stale echo after a spent resume flag means the guard's
  assumption about LangGraph no longer holds, and that should stop the run visibly.
  (notes: 02-durability-replay-effects.md)

- **The replan decision is a `Command` returned by the validator, and the gateway failure is not.**
  `verify_course_claims` writes the verdicts and picks the next node in one return, so the update
  and the route cannot disagree. `enough_valid` stays a pure function of state and is called on the
  merged update, which keeps the eval rows and the unit tests that exercise the rule. The gateway
  failure stays on `discard_reason` because the failing node sits in the outer graph and the
  route reads a channel that the subgraph's output schema already exports. The return annotation
  `Command[Literal[...]]` is what draws the two edges; without it the graph shows none.
  (notes: 01-state-and-control-flow.md)

- **Evals are layered, and cases start as labelled seeds.** Six levels (contract, deterministic
  component, model-node component, subgraph, graph scenarios, reliability), each owning one
  failure class, with the online level as prose only. Without traffic the first cases are
  representative seeds taken from observed failures and `FEEDBACK.md`, tagged `source: seed`,
  and are swapped for `review` and `prod` cases as they appear; the swap is a data change.
  Ruled out: inventing a large synthetic set up front, and waiting for production data.
  (`EVALS.md`)
- **Path is graded where the topology is code, outcome where the model chooses.** Asserting the
  node sequence of the outer and research graphs is a correctness check, since control flow is
  fixed at compile time; the curator tool loop is graded on outcome and invariants, because
  checking its steps would punish valid alternatives. (`EVALS.md`)
- **Trace grading is not LLM-as-judge.** The trace is the checkpoint history of a trial; graders
  are mostly code over it, and the judge sees free text only. Every failing trial is attributed
  to its first failing node. (`EVALS.md`)
- **The judge is `google/gemini-3.1-flash-lite`, with no fallback list.** A different family from
  the generator (`deepseek-v4.1-flash`) against self-preference; $0.25 per million input tokens
  and $1.50 output, input-dominated because the verdict is a line. Under the one-dollar budget
  on input, not on output; the only model under it on both is `google/gemini-2.5-flash-lite`,
  which is the generator's own fallback, so it would share a family exactly when the fallback
  fires. Trials where the generator fell back to Gemini are tagged and reported apart.
  Recalibrate (TPR and TNR on labelled outputs) after any judge change. (`EVALS.md`)
- **PR gates are deterministic or replayed; live-model evals run nightly.** A live model on every
  PR makes the gate flaky and costs money. Replay from cassettes keyed by a hash of model,
  parameters and prompt; a missing cassette fails the job. LangSmith is not adopted for the
  harness (see the OpenTelemetry entry above).
- **Stored notes are bounded on the write path, not by the model.** `propose` refuses a note over
  120 characters or with line breaks, and a `topic:` scope outside a short slug; `apply_patch`
  keeps the newest 30 notes per user and drops the oldest. Ruled out: putting the limits on
  `MemoryNote` (rows already stored would fail to load), and refusing new notes at the cap (a
  long-time user could never teach the agent again). Relevance to courses is a prompt rule plus
  a live case, not a validator: a rule cannot tell a course preference from any other sentence.
- **Product shape: one continuous chat per person, in Telegram.** A message is classified before
  a thread exists, as continuing the person's latest parked thread or starting a new topic (JEV,
  app layer, redacted input, any error or middle score means a new topic). Refinement inside a
  thread stays on the existing `REWRITE`, `AUGMENT` and `RESET` routes. A wrong "new topic" costs a
  repeated filter; a wrong "continue" applies stale constraints to an unrelated query and writes
  wrong profile notes, so the default is the cheaper error. Ruled out: one thread per person
  forever (checkpoint grows without bound), and one thread per message (no refinement). The
  classifier has no fallback model; its accuracy is unmeasured and the article says so until the
  labelled pairs exist (backlog W3). Built later: backlog W3, W5, W8. Partly settles N2.
- **Acceptance is implicit and review sits at catalogue promotion only.** In chat mode the
  pause at `await_human_review` is where the thread waits for the person's next message. A thread
  left behind (new topic or idle) is closed as accepted with reason `implicit`, and nothing from a
  chat run reaches the shared catalogue without staff review (N1). The per-run digest gate stays
  as the CLI demo and keeps the article's verified claims true. Pending the owner's sign-off on
  the `CLAUDE.md` wording (backlog W4).
- **At most one run is active per user.** Messages that arrive during a run wait in a per-user
  queue and are merged into one request, so a person sending two short messages gets one research
  pass. Ruled out: cancelling the active run on a newer message (searches already paid for are
  wasted) and rejecting the message. Course tagging is one run per course, keyed on URL and
  content hash; the digest is one run per user and period. Batch-wide `Send` over all units is
  the demonstrated wrong shape, not the design (backlog W5, W6).

## Compliance

- **The privacy controls are justified by many users' stored data,** not by a single
  reviewer's admin panel: per-user profiles, checkpoints and events are what erasure,
  ownership checks and encryption protect. Do not argue them away by narrowing the product to
  one manager.
- **A boundary, not a feature.** LangGraph has no compliance tooling. The app owns the
  user-to-thread index (`run_threads`); the model-provider boundary (ZDR, DPAs, region) is
  invisible to the graph.
- **Not built, on purpose:** per-user keys (crypto-shredding), reducer-based redaction,
  selective sealing (seal-everything fails closed), AST-derived reads and writes for the
  data-flow check, boundary-observed sinks.
- **Checkpoints from an older state schema are refused, not migrated.** See "State contract
  changes are versioned" under Design.
- **OpenRouter "PII filtering"** is not cited as a feature: it is unconfirmed and would be a
  different guarantee from data-retention controls. Don't conflate them.

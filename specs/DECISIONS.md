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

- **Workflow, not agent.** Planning, extraction and validation are rules; three nodes use an
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
- **`async` durability is not tested under a hard kill:** whether the last background write
  lands is a race.
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
- **Topic floor 0.12; blend weights 0.7 / 0.2 / 0.1 are not fitted.** The floor is the last one
  before recall drops (0.72 to 0.59) on the mock catalog, and precision is preferred because web
  search fills a miss. The weights only order courses already past the floor. Refit both if the
  embedder or the stored text changes. (notes: 05-scale-and-scope.md)
- **`ResearchPlan.cache_query` is removed.** The cache lookup runs before the plan exists and
  reads `filters.topic`. Checkpoints that still hold the field load, because the model ignores
  extra keys (test in `tests/test_checkpointer.py`). (notes: 01-state-and-control-flow.md)
- **`courses.topics` is a property of the course, never of the query.** The run's topic is
  user-derived text; storing it in the shared cache reaches a table erasure cannot cover (the
  flow-rule check flags it) and lets loosely matched courses pollute later lookups. Topics come
  from the course's own content, by a tagging step that is not built. (notes: 05-scale-and-scope.md)

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
- **OpenRouter "PII filtering"** is not cited as a feature: it is unconfirmed and would be a
  different guarantee from data-retention controls. Don't conflate them.

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

## Compliance

- **A boundary, not a feature.** LangGraph has no compliance tooling. The app owns the
  user-to-thread index (`run_threads`); the model-provider boundary (ZDR, DPAs, region) is
  invisible to the graph.
- **Not built, on purpose:** per-user keys (crypto-shredding), reducer-based redaction,
  selective sealing (seal-everything fails closed), AST-derived reads and writes for the
  data-flow check, boundary-observed sinks.
- **OpenRouter "PII filtering"** is not cited as a feature: it is unconfirmed and would be a
  different guarantee from data-retention controls. Don't conflate them.

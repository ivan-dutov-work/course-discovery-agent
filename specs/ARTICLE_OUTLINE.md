# Article Outline: "LangGraph for Agentic Workflows: Production Patterns"

(Working title — open to revision. Previous working title, "Using LangGraph to
Build a Personalized Course Research Agent," is retired: what this codebase
implements is an agentic *workflow* — a fixed graph topology orchestrating LLM
calls — not an agent in the sense of an LLM dynamically choosing its own next
action. See §1.)

## Framing

This is a concept-first article, not a case study. `course-discovery-agent` is a
demo with search mocked and nothing wired into production — it has no value as a
case study by itself, only as a labeled illustration. Structure follows LangGraph
concepts in the order a production build would need them; code excerpts from this
repo appear *within* each concept as a supporting snippet, never as the spine.
Sections do not map one-to-one onto the repo's node list, and no section is a full
walkthrough of the repo.

**Spine.** A checkpoint gives exactness at node boundaries and nothing inside a
node. Resume re-runs the interrupted node from the top, so the production
question is what happens across that seam: what replays, what must not run
twice, and where non-determinism is allowed to live. §5 (durability) and §6
(effects) carry this; §7 (retries, timeouts, caches) and §8-§9 (brittleness,
providers) are the surrounding machinery.

**Claim labels.** Each mechanism claim in the article is one of: observed here
(a test or experiment in this repo, on the stated langgraph version), read from
source, or from docs. Say which, in the text, wherever it matters. Unverified
items stay out of the article or are flagged as unverified.

---

## Cross-Cutting Concerns Index

A reader scanning for a specific operational concern (not a LangGraph primitive)
can use this table instead of reading linearly. Tags are attached to subsection
headers below; sections 1, 12, and 13 are framing/meta and carry no tag.

| Concern | Where it's covered |
|---|---|
| `[performance]` | §3.2 Send fan-out, §5.2 durability modes, §5.5 subgraph resume cost, §7.3 CachePolicy, §7.2 async nodes, §10.2 stream_mode |
| `[ux]` | §4.3 interrupt UX gaps, §10.2 stream_mode |
| `[durability]` | §4.1 interrupt_before, §5.1-§5.4 checkpoints, durability modes, replay, `@task` |
| `[reliability]` | §6.2 outbox, §7.1 RetryPolicy, §7.2 timeouts, §7.4 circuit breaking, §8.2 rate limiting, §9.1 provider fallback, §11.2 checkpoint/graph versioning |
| `[correctness]` | §2.2 reducers, §5.3 replay determinism, §6.1 idempotent writes, §8.3 schema-drift validation |
| `[cost]` | §5.5 subgraph resume cost, §7.1 retry multiplication, §8.2 rate limiting, §9.2 OpenRouter/LiteLLM |
| `[security]` | §9.3 guardrails, §11.3 security primitives, §11.4 thread access control |
| `[compliance]` | §5.6 Store vs. checkpointer / retention, §11.1 GDPR |
| `[observability]` | §10.1 OpenTelemetry |
| `[maintainability]` | §3.3 subgraphs |
| `[safety]` | §4.4 recursion_limit |

---

## 0. TL;DR (~100 words)

State the terminology fix immediately: this is an agentic workflow, not an agent —
a fixed-topology graph that orchestrates LLM calls with structural guarantees
(state contracts, bounded loops, mandatory human review, checkpointed durability,
effects that survive replay). LangGraph is the control shell; the domain example (course research) is a vehicle
for the mechanics, not the subject.

---

## 1. Agents vs. Agentic Workflows (~350 words)

**1.1 The distinction that matters**

Workflow: predefined code paths orchestrate LLM calls and tools. Agent: an LLM
dynamically directs its own process and tool use, with the framework not deciding
the sequence. This is not a semantic nitpick — it determines which LangGraph
primitives are actually relevant.

**1.2 Why this codebase is a workflow, not an agent**

The graph topology (gateway → research subgraph → interrupt → router →
publish/rewrite/augment/reset/discard) is fixed at compile time. Nodes make LLM
*judgments* (which queries to run, valid/invalid/uncertain) but nothing decides
*which node runs next* except the graph definition and conditional edges written
in advance. This is an orchestrator-workers pattern plus an evaluator-optimizer
loop (the replanner) — named workflow patterns, not agent patterns.

**1.3 Where LangGraph's value lies for each**

For a workflow: `Send` fan-out, reducers, subgraphs — topology machinery. For a
genuine agent (an LLM tool-loop choosing its own next call): checkpointed
durability for an open-ended loop, `interrupt()` for mid-loop human input,
`recursion_limit` as a runaway-loop backstop, `Store` for cross-session memory.
LangGraph is not required to build a bare agent loop — it earns its place when
the loop needs to be durable, interruptible, or bounded, or when one of the
agent's own tools is itself a structured multi-step workflow.

---

## 2. State as the Contract (~400 words)

**2.1 The state is the single channel between nodes**

Nodes never call each other directly — they read and write a shared, typed state
object. Show a small excerpt (3-4 fields), not the full `AgentState` listing —
enough to establish the shape, not to document the domain.

**2.2 Reducers for safe concurrent merges** `[correctness]`

`Annotated[..., add]`-style reducers let parallel `Send` workers write to the same
state key without clobbering each other. Show the concrete merge conflict this
prevents: two search workers appending to `tavily_results` in the same superstep.

**2.3 `input_schema`/`output_schema` separation**

`StateGraph(OverallState, input_schema=InputState, output_schema=OutputState)` —
`invoke()` returns only the output schema's fields, not full internal state. This
is the public-API-vs-internal-contract distinction, worth pairing directly with
2.1.

---

## 3. Control Flow Primitives `[performance]` `[maintainability]` (~450 words)

**3.1 Conditional edges**

Control flow lives in the graph, not in prompt text — routing decisions are
inspectable and testable independent of any single LLM call.

**3.2 Plan-driven `Send` fan-out** `[performance]`

Worker count comes from a runtime plan, not from hardcoded parallel branches.
Show the fan-out snippet. Name the boundary explicitly: `Send` is in-process
fan-out, not a distributed queue — conflating "parallel Send workers" with "a job
queue" is a real mistake if the workload needs cross-service distribution (SQS,
Celery, Temporal).

**3.3 Subgraphs for encapsulation** `[maintainability]`

A compiled subgraph mounted as a single node in an outer graph — the outer graph
doesn't know or care about the subgraph's internal node count.

**3.4 `Command` for update-and-route**

Combining a state update and an edge decision in one return value, used where a
node both mutates state and decides where execution goes next (e.g., a
replanning node).

---

## 4. Human Review as a Durable Boundary `[ux]` `[durability]` `[safety]` (~450 words)

**4.1 `interrupt_before` as a durability-backed boundary** `[durability]`

Contrast with a naive `input()` prompt: the graph checkpoints and can resume
from a fresh saver and graph object, not just "pause the loop." State what is
tested (state read back from Postgres, resume through publish; a SIGKILL crash
test, detailed in §5.2) and what is not (a different machine).

**4.2 Dynamic `interrupt()`**

The runtime counterpart to a compile-time `interrupt_before` — called inside a
node, so review can be conditional (only low-confidence output pauses;
high-confidence output passes straight through). One clear contrast, not a full
comparison. The router's five outcomes (PUBLISH/REWRITE/AUGMENT/RESET/DISCARD)
are the positive example against a binary approve/reject gate.

**4.3 UX Gaps Around the Interrupt Boundary** `[ux]`

A paused thread sits in the checkpointer indefinitely — no built-in TTL or
staleness handling. Notifying a human that a thread is waiting is entirely
app-layer: LangGraph gives `get_state(config)` to poll, not a push mechanism.
One-paragraph sketch, not a protocol design. `update_state()` lets a reviewer
patch state before resuming, not just approve/reject. Nothing stops two callers
resuming the same `thread_id` concurrently — an app-level lock. Cross-reference
§11.4: the same `update_state` call can write the feedback that opens the
publish gate, so who may resume is an access question, not only a UX one.

**4.4 `recursion_limit` as a structural safety net** `[safety]`

A generic, structural backstop *underneath* any domain-level iteration budget
(`max_research_iterations`) — two independent guardrails against runaway loops,
one generic, one domain-aware.

---

## 5. Durability: What a Checkpoint Does and Doesn't Give You `[durability]` `[correctness]` (~800 words)

The section the rest of the article leans on. Everything here is observed on
langgraph 1.1.2 unless labelled otherwise.

**5.1 What is checkpointed, and when** `[durability]`

Checkpoints land at superstep boundaries. Completed nodes are not re-run on
resume; the interrupted node re-runs from the top. Wiring: `open_checkpointer()`
returns `AsyncPostgresSaver` when `DATABASE_URL` is set, `MemorySaver`
otherwise; both share one msgpack allowlist. Subgraph steps are stored under a
`checkpoint_ns` discovered at runtime, and `ainvoke(None, snapshot.config)` on
the parent replays from exactly that node.

**5.2 Durability modes, with a real crash** `[performance]` `[durability]`

`durability="sync" | "async" | "exit"` as a tuning knob. Tested against
Postgres: all three resume a run that failed with an exception, but only `sync`
survives a SIGKILL inside `course_cache_upsert` without re-running
`evidence_validator`; after the same kill `exit` has no checkpoints and the run
restarts from scratch. `async` is deliberately not tested for a hard kill,
because whether the last background write lands is a race. This is the
article's strongest piece of evidence; say how the test works (child process
that kills itself, fresh process resumes).

**5.3 Replay is exact between nodes, best-effort inside one** `[correctness]`

The design rule: keep non-determinism out of effect nodes. A node that calls an
LLM and then performs an effect can re-run after the effect but before its
checkpoint, with different LLM output. Split them so a checkpoint lands between
the two and the effect node derives its payload only from checkpointed state.
Audit of this repo: `publish_node`, `course_cache_upsert_node` and
`user_memory_update_node` build from state, and `run_id` is minted before the
graph starts. Temperature 0 is not a guarantee (provider nondeterminism, model
drift, and a fallback provider can change the answer between a run and its
replay). Check that what the reviewer approved is what gets published.

**5.4 Intra-node memoization: `@task`** `[durability]`

The built-in answer to "inside one node". Works inside a plain `StateGraph`
node; completed tasks short-circuit on resume. Then the limits, each observed:
did not memoize inside a subgraph (the whole research pipeline here is one);
matched by function name and call index, not arguments, so a changed argument
silently returns a stale result; the result write is asynchronous, so a kill
right after an effect runs the effect twice; return types must be in the
msgpack allowlist; scoped per superstep and discarded on old-checkpoint replay.
Conclusion: `@task` narrows the window, it does not close it, and it is not a
substitute for splitting nodes and keying effects (§6). Reproduce the subgraph
result before citing it.

**5.5 Resume cost in a subgraph** `[performance]` `[cost]`

Resuming the outer graph re-runs sibling `Send` workers that already succeeded
(a standalone graph re-runs only the failed one). It is extra search and LLM
spend, measured by call counts. Reducer output is not double-counted. This is
the price of encapsulation (§3.3) and the design alternative is to keep mutating
nodes at the top level.

**5.6 `Store` vs. checkpointer** `[compliance]`

The memory-scope distinction: checkpointer is thread-scoped run state, wired in
via `compile(checkpointer=...)`; `Store` is cross-thread long-term memory
(e.g. user_id-scoped), requiring explicit `store.get`/`store.put`. A
hand-rolled Postgres user-memory table is conceptually what `Store` is for —
name it as "here's the API for what got hand-rolled." One line on retention:
this durability is also a retention liability with no built-in TTL, redaction,
or right-to-erasure primitive. (The compliance lever sits at the model-provider
boundary, invisible to LangGraph.)

---

## 6. Effects: Making Side Effects Survive Replay `[correctness]` `[reliability]` (~700 words)

Checkpointing resumes the graph. It does not make an external call happen once.
Two recovery loops, not to be conflated: the checkpointer resumes the graph; a
worker retries the effect.

**6.1 Idempotent writes under replay** `[correctness]`

A naturally idempotent write (upsert) against one that is not. Deterministic
keys, never generated at execution time. Worked findings: `course_evidence`
was half-idempotent and is now keyed on `(course_id, source_url)` with
`DO UPDATE`, chosen so the key does not depend on volatile snippet text
(trade-off: last write wins, no history); `recommendation_events` carries an
`idempotency_key`; the write that swallowed its own FK failure now re-raises,
so a missing `users` row surfaces instead of silently dropping feedback. Fail
closed, visibly.

**6.2 Outbox behind a port** `[reliability]`

Nodes call `EffectGateway.submit(Effect(key, kind, payload))` and never the
external system; the key derives from `run_id`. Adapters behind one
`OutboxStore` protocol (in-memory, Postgres), an inline gateway for the demo
and an outbox gateway with a separate worker. The worker leases rows, backs off,
counts attempts at claim time so a crash-looping record ends up dead-lettered.
State contract cost: `published: bool` became `publish_status`
(`queued | delivered | dead`). Atomicity limit: the outbox row and the node's
domain write are separate transactions, and neither shares one with the
checkpointer, so this is at-least-once delivery plus idempotent submit, not
consistency. The consumer stays idempotent.

**6.3 More than one effect in a node** `[correctness]`

Not built; reasoned from the gateway code and labelled as such. Each effect has
its own key; a replay re-submits all, existing keys are no-ops, and retry is the
worker's job, not the graph's. Keys must be stable per effect (not list
position); `DO NOTHING` keeps the first payload if a replay recomputes a
different one; there is no ordering between effects.

**6.4 Operating it: what is not built** `[reliability]`

Dead-lettered rows produce a log line and nothing else; a handler that outlives
`locked_until` can be claimed twice; nothing prunes outbox rows or checkpoints;
the delivery handler is a stdout stub and does not forward the key downstream.
Name these as the gap between a demonstrated mechanism and an operated one.

**6.5 When to reach for something else**

Temporal, Restate and DBOS give durable execution at the activity level, which
is what keys plus an outbox approximate by hand. One short paragraph on when the
workflow is mostly long-running side-effect steps, not a comparison.

---

## 7. Node-Level Resilience: Retries, Timeouts, Caches `[reliability]` `[performance]` `[cost]` (~450 words)

**7.1 `RetryPolicy`** `[reliability]` `[cost]`

Passed to `add_node(..., retry_policy=...)`, retrying only transient errors
(`is_transient`: timeouts, connection errors, HTTP 408/425/429/5xx), not
integrity errors or `ValueError`. Contrast sharply with a domain-level
replanning loop: retry means "the call failed"; replanning means "the call
succeeded but the result was insufficient." Findings: a node that catches every
exception makes `RetryPolicy` a no-op, so transient errors must propagate;
client-level retries (the chat client ships its own) multiply with
`RetryPolicy`, so `build_llm` defaults to 0 and only nodes without a policy opt
back in; exhausted retries raise out of the graph and the run stays resumable
from its checkpoint. Retrying a mutation is safe only because `submit` is keyed
(§6).

**7.2 Timeouts and async nodes** `[reliability]` `[performance]`

There is no node-level `timeout=` on `add_node` in langgraph 1.1.2 (verified
against the signature); timeouts belong inside the client: LLM request timeout,
`asyncio.wait_for` around search, Postgres connect and statement timeouts. A
search timeout raises `TimeoutError`, which is transient and therefore
retried. Async `def` nodes give real I/O concurrency — call it out as a
deliberate pattern.

**7.3 `CachePolicy`** `[performance]`

Memoizes a node's output keyed on input, across threads. Contrast framework
cache (identical-input reruns) against the purpose-built domain cache (a
validated-results store, cache-first). Not the same thing as replay
memoization (§5.4). Not implemented in the repo; keep this to a short contrast
unless it gets a working example.

**7.4 Retry vs. circuit breaking** `[reliability]`

`RetryPolicy` does not protect against a sustained outage; retrying a fully down
API burns budget and latency. LangGraph gives per-node retry, not a circuit
breaker: hand-rolled (a failure-count field in state, checked before dispatching
the next `Send`) or delegated to a client library or gateway. Not implemented.

---

## 8. API Brittleness by Design `[reliability]` `[correctness]` (~350 words)

**8.1 Graceful degradation as the baseline**

Lead with worked examples: LLM nodes falling back to deterministic parsing when
no API key is present (no call attempted); the cache falling back to an
in-memory seed when no database is configured; the search worker recording a
visible note instead of collapsing the run. "Fail closed with a visible
limitation." Distinguish this from the read/write paths that re-raise (§6.1):
degrade where a fallback is correct, fail where it would hide data loss.

**8.2 Rate limiting** `[cost]` `[reliability]`

`rate_limiter=` on chat models smooths outbound call rate when several nodes
share a provider quota; in-process only, does not coordinate across workers.

**8.3 Schema drift / structured-output validation** `[correctness]`

Pydantic structured output fails loudly when a provider's output changes shape.
It does not catch well-formed but malicious output (§9.3).

---

## 9. Provider-Level Resilience and Spend Control `[reliability]` `[cost]` `[security]` (~450 words)

**9.1 `.with_fallbacks()` vs. `ModelFallbackMiddleware`** `[reliability]`

`Runnable.with_fallbacks()` (`langchain_core.runnables`, not LangGraph-specific)
tries fallback runnables in order until one succeeds. `ModelFallbackMiddleware`
(`langchain.agents.middleware`) is the newer, native multi-model fallback.
Inside a node, either is "wrap the chain, call it like any other Runnable" — the
graph owns structural control flow; the Runnable layer owns call-level
resilience. `init_chat_model()` does not natively take a fallback list. Tie back
to §5.3: a fallback provider is one more reason replay output can differ.

**9.2 OpenRouter and LiteLLM** `[cost]`

OpenRouter via `langchain-openrouter`; its `models: [...]` priority array
retries on context-length errors, moderation flags, rate limits or downtime,
billing only for the model that served the request. The production value is one
bill, one rate-limit surface and per-key spend caps, not fallback per se. The
fallback path was asserted in the outgoing request payload but never triggered
live; say so. LiteLLM has two shapes: SDK in-process (`ChatLiteLLM`,
`ChatLiteLLMRouter`) vs. Proxy as a standalone gateway service; Proxy is the
common production pattern and spend/cache-token billing accuracy is a known
rough edge.

**9.3 Guardrails and security** `[security]`

Same wrap-the-Runnable shape as fallback: no native prompt-injection or
PII-guardrail primitive in LangChain/LangGraph core. Concrete instantiation: an
in-process library (Presidio or `llm-guard`), no network hop. Two insertion
points: nodes that consume untrusted external content as LLM input (injection
surface) and nodes that produce user-facing output (leak surface). Include an
integration test asserting the guardrail fires on a known case. Not implemented.

---

## 10. Observability `[observability]` `[performance]` `[ux]` (~400 words)

**10.1 OpenTelemetry via LangSmith** `[observability]`

LangSmith has native end-to-end OTel support — `LANGSMITH_OTEL_ENABLED=true`
plus standard OTLP endpoint/headers env vars; traces can route to
Datadog/Grafana/Jaeger over plain OTLP. (OpenInference/Traceloop bridges exist
for LangSmith-independent setups.) Not wired in this repo.

**10.2 `stream_mode`** `[performance]` `[ux]`

Current modes: `"values"`, `"updates"`, `"messages"`, `"custom"`, `"debug"`,
`"checkpoints"`, `"tasks"`. A list yields `(mode, chunk)` tuples; a single string
yields bare chunks. How a client gets partial progress without polling. Note
`"tasks"` and `"updates"` also expose `@task` boundaries (§5.4).

**10.3 Domain metrics as a light example**

Brief and short: cache hit rate, calls-per-run, latency, token count as
instances of the general "what to measure" question.

---

## 11. What LangGraph Doesn't Own `[compliance]` `[security]` (~400 words)

**11.1 GDPR / data retention**

Cross-reference §5.6: the checkpointer and `Store` persist whatever PII flows
through state at every superstep, with no built-in compliance tooling. The
compliance lever sits at the model-provider boundary.

**11.2 Checkpoint and graph versioning** `[reliability]`

Two distinct failure modes: state schema versioning (a changed state shape can
break deserialization of old checkpoints; already hit in small form as the
msgpack allowlist and enum warnings, and it is also a deserialization-safety
control) versus graph structural versioning (changed topology can break
in-flight threads even when state did not change; not tested here).

**11.3 Security/guardrail primitives**

Cross-reference §9.3 rather than re-explain.

**11.4 Access control on `thread_id`** `[security]`

A `thread_id` is a bearer capability. `update_state` on `manager_feedback` can
open the publish gate that `interrupt_before` guards, so whoever can resume can
approve. The check belongs in the application layer (compare the authenticated
caller with the thread's stored `user_id`, or derive the thread id server-side).
Not implemented; the CLI has one user.

---

## 12. Scope, Limitations and Verification (~250 words)

- Not a production case study — no load data, no incident log, no
  cost-at-scale numbers. A demonstration of mechanics using a domain-shaped
  example, evaluated by reading the code and running it locally.
- Search is mocked — a deliberate scope cut. "Production would swap this one
  class" is an architectural claim, not a tested migration — say so.
- Model tiering by task difficulty is out of scope — a cost/quality policy
  decision orthogonal to LangGraph's job. At most a parenthetical.
- **How claims were verified.** One contract suite runs against both outbox
  stores; Postgres integration tests cover replay, parallel workers and
  concurrent submits; a SIGKILL child-process test covers crash recovery per
  durability mode. Findings are labelled observed, read from source, or from
  docs (see Framing). Known untested: `durability="async"` under hard kill, a
  changed graph topology against a persisted thread, strict-mode msgpack
  behaviour for a type outside the schema, and everything under real load.

---

## 13. What's Next (~150 words)

Next steps that add surfaces and infrastructure around the mechanics shown,
none of which change the core patterns: a real delivery handler and
same-transaction outbox write, dead-letter alerting and pruning, circuit
breaking, a push-based review interface replacing the CLI, thread access
control, LangSmith/OTel tracing, scheduling.

**Link to the repository.**

---

## Appendix: Running the Demo (~100 words)

```bash
git clone https://github.com/your-org/course-discovery-agent
cd course-discovery-agent
# optional: set OPENROUTER_API_KEY, DATABASE_URL in .env — no search API key needed, search is mocked
uv sync
uv run python main.py
```

Optional local Postgres for the durable checkpointer, the outbox and the
integration tests: `docker compose up -d`.

The CLI prints the digest after synthesis, shows cache hit count, search call
count, and validation summary, then prompts for review. Type `approve`,
`rewrite: ...`, `augment: ...`, `reset: ...`, or `discard`.

---

## Estimated Word Count

| Section | ~Words |
|---|---|
| 0. TL;DR | 100 |
| 1. Agents vs. Agentic Workflows | 350 |
| 2. State as the Contract | 400 |
| 3. Control Flow Primitives | 450 |
| 4. Human Review as a Durable Boundary | 450 |
| 5. Durability | 800 |
| 6. Effects | 700 |
| 7. Node-Level Resilience | 450 |
| 8. API Brittleness by Design | 350 |
| 9. Provider-Level Resilience | 450 |
| 10. Observability | 400 |
| 11. What LangGraph Doesn't Own | 400 |
| 12. Scope, Limitations and Verification | 250 |
| 13. What's Next | 150 |
| Appendix | 100 |
| **Total** | **~5,800** |

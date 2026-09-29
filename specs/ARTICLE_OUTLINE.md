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

---

## Cross-Cutting Concerns Index

A reader scanning for a specific operational concern (not a LangGraph primitive)
can use this table instead of reading linearly. Tags are attached to subsection
headers below; sections 1, 10, and 11 are framing/meta and carry no tag.

| Concern | Where it's covered |
|---|---|
| `[performance]` | §3.2 Send fan-out, §4.4 durability modes, §5.2 CachePolicy, §5.3 async nodes, §8.2 stream_mode |
| `[ux]` | §4.3 interrupt UX gaps, §8.2 stream_mode |
| `[durability]` | §4.1 interrupt_before, §4.4 durability modes |
| `[reliability]` | §5.1 RetryPolicy, §5.3 timeouts, §6.2 retry vs. circuit breaking, §6.4 rate limiting, §7.1 provider fallback, §9.2 checkpoint/graph versioning |
| `[correctness]` | §2.2 reducers, §6.3 idempotency under replay, §6.5 schema-drift validation |
| `[cost]` | §6.4 rate limiting, §7.2 OpenRouter/LiteLLM |
| `[security]` | §7.3 guardrails, §9.3 security primitives |
| `[compliance]` | §4.5 Store vs. checkpointer / retention, §9.1 GDPR |
| `[observability]` | §8.1 OpenTelemetry |
| `[maintainability]` | §3.3 subgraphs |
| `[safety]` | §4.6 recursion_limit |

---

## 0. TL;DR (~100 words)

State the terminology fix immediately: this is an agentic workflow, not an agent —
a fixed-topology graph that orchestrates LLM calls with structural guarantees
(state contracts, bounded loops, mandatory human review, checkpointed durability).
LangGraph is the control shell; the domain example (course research) is a vehicle
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

## 4. Human-in-the-Loop and Durability `[ux]` `[durability]` `[compliance]` (~600 words)

**4.1 `interrupt_before` as a durability-backed boundary** `[durability]`

Contrast with a naive `input()` prompt: the graph actually checkpoints and can
resume in a new process, after a crash, or from a different machine — not just
"pause the loop."

**4.2 Dynamic `interrupt()`**

The runtime counterpart to a compile-time `interrupt_before` — called inside a
node, so review can be conditional (only low-confidence output pauses;
high-confidence output passes straight through). One clear contrast, not a full
comparison.

**4.3 UX Gaps Around the Interrupt Boundary** `[ux]`

A paused thread sits in the checkpointer indefinitely — no built-in TTL or
staleness handling. Notifying a human that a thread is waiting is entirely
app-layer: LangGraph gives `get_state(config)` to poll, not a push mechanism.
One-paragraph sketch, not a protocol design: the API layer observes the paused
state after invoke/stream returns and pushes a single event over a websocket/SSE
channel; the frontend subscribes per-thread and renders the pending review.
`update_state()` lets a reviewer patch state before resuming, not just
approve/reject. Nothing stops two callers resuming the same `thread_id`
concurrently — an app-level lock, not something the checkpointer arbitrates.

**4.4 Durability modes** `[performance]` `[durability]`

`durability=` on `invoke`/`stream`: `"exit"` (checkpoint only at graph exit,
fastest/least safe), `"async"` (background checkpoint, balanced), `"sync"`
(checkpoint before every step, safest/slowest). A tuning knob — a low-stakes
intermediate step doesn't need `"sync"`; a publish-adjacent step might.

**4.5 `Store` vs. checkpointer** `[compliance]`

The memory-scope distinction: checkpointer is thread-scoped run state, wired in
via `compile(checkpointer=...)`, what makes `interrupt_before` resumable. `Store`
is cross-thread long-term memory (e.g. user_id-scoped), requiring explicit
`store.get`/`store.put`. A hand-rolled Postgres user-memory table is conceptually
what `Store` is for — name it explicitly as "here's the API for what got
hand-rolled." One line on the retention consequence: this durability is also a
retention liability — no built-in TTL, redaction, or right-to-erasure primitive;
deleting a user's data means writing the delete yourself. (The actual compliance
lever — ZDR flags, DPAs, processing region — sits one layer down at the
model-provider boundary, invisible to LangGraph entirely.)

**4.6 `recursion_limit` as a structural safety net** `[safety]`

A generic, structural backstop *underneath* any domain-level iteration budget —
two independent guardrails against runaway loops, one generic, one domain-aware.

---

## 5. Node-Level Resilience `[reliability]` `[performance]` (~400 words)

**5.1 `RetryPolicy`**

Passed to `add_node(..., retry_policy=RetryPolicy(...))`. Handles transient
exceptions (timeout, rate limit) with backoff/jitter. Contrast sharply with a
domain-level replanning loop: retry means "the call failed"; replanning means
"the call succeeded but the result was insufficient." Two different failure
classes, two different mechanisms.

**5.2 `CachePolicy`** `[performance]`

Memoizes a single node's output keyed on input — narrower than a whole-workflow
cache. Contrast framework cache (identical-input reruns) against a
purpose-built domain cache (e.g. a validated-results store keyed on semantic
similarity, not exact input match) — different tools solving different
problems. Also worth naming: `timeout=` and `error_handler=` on `add_node`.

**5.3 Timeouts and async nodes** `[reliability]` `[performance]`

Node-level `timeout=` plus per-call timeouts inside the API client itself — an
HTTP client with no explicit timeout lets a hung request block the whole
fan-out branch. Async `def` nodes give real concurrency for I/O-bound work (LLM
calls, search) versus simulated parallelism — call this out as a deliberate
pattern, not an incidental detail.

---

## 6. API Brittleness by Design `[reliability]` `[correctness]` (~550 words)

**6.1 Graceful degradation as the baseline, not an afterthought**

Lead with a worked example instead of abstract principle: an LLM-backed node
falling back to deterministic parsing when no API key is present (no call
attempted at all, not a failed call); a cache falling back to an in-memory seed
when a database is absent; a search worker catching exceptions and writing a
visible note instead of collapsing the run. "Fail closed with a visible
limitation," not silent hallucination or a crashed graph.

**6.2 Retry + backoff vs. circuit breaking** `[reliability]`

`RetryPolicy` handles transient failures; it does not protect against a
sustained outage — retrying a fully down API just burns budget and latency.
LangGraph gives per-node retry, not a circuit breaker: that's either hand-rolled
(a failure-count field in state, checked before dispatching the next `Send`) or
delegated to a client library/gateway in front of the API.

**6.3 Idempotency under checkpoint replay** `[correctness]`

If a node crashes mid-execution and the graph resumes from the last checkpoint,
does re-running that node do something unsafe (double-charge, double-publish,
duplicate insert)? Contrast a naturally idempotent node (upsert semantics)
against a node with a side effect like "send an email," which needs an explicit
idempotency key derived from run ID + node name.

**6.4 Rate limiting** `[cost]` `[reliability]`

LangChain chat models accept a `rate_limiter=` to smooth outbound call rate —
relevant when multiple nodes in one run share the same provider quota, and
worse under concurrent users.

**6.5 Schema drift / structured-output validation as a brittleness defense** `[correctness]`

Pydantic structured output is itself a defense against a brittle API: if a
provider changes behavior and returns malformed output, validation fails loudly
instead of silently propagating garbage downstream. Ties back to a "fail closed"
framing.

---

## 7. Provider-Level Resilience and Spend Control `[reliability]` `[cost]` `[security]` (~450 words)

**7.1 `.with_fallbacks()` vs. `ModelFallbackMiddleware`** `[reliability]`

`Runnable.with_fallbacks()` (`langchain_core.runnables`, not LangGraph-specific)
tries fallback runnables in order until one succeeds. `ModelFallbackMiddleware`
(`langchain.agents.middleware`) is the newer, native multi-model fallback
mechanism. Inside a LangGraph node, either is just "wrap the chain, call it like
any other Runnable" — the graph owns *structural* control flow (which node runs
next); the Runnable layer underneath already owns *call-level* resilience.
Note: `init_chat_model()` does not natively take a fallback list.

**7.2 OpenRouter and LiteLLM** `[cost]`

OpenRouter: dedicated `langchain-openrouter` package is the modern integration
path; its own `models: [...]` priority array auto-retries on context-length
errors, moderation flags, rate limits, or downtime, billing only for whichever
model served the request. The real production value isn't fallback per se — you
already get that in-process — it's one bill, one rate-limit surface, and
per-key spend caps across providers. LiteLLM has two distinct shapes: (a) SDK,
in-process (`langchain-litellm`'s `ChatLiteLLM`/`ChatLiteLLMRouter`, no separate
service) vs. (b) Proxy, a standalone gateway service exposing an
OpenAI-compatible endpoint, handling routing/key-management/spend-tracking.
Proxy is the common production pattern; spend/cache-token billing accuracy is a
known rough edge worth naming as an operational cost of self-hosting this layer.

**7.3 Guardrails and security** `[security]`

Same wrap-the-Runnable shape as fallback: LangChain/LangGraph core has no native
prompt-injection or PII-guardrail primitive — a guardrail is a function wrapped
around a call, not a graph feature. Concrete instantiation: an in-process
library (Presidio or `llm-guard`), not an external API — no network hop, no new
data-sharing surface. Name the two concrete insertion-point shapes: a node that
consumes untrusted external content as LLM input (the injection surface), and a
node that produces user-facing output (the leak surface). An integration test
asserting the guardrail actually catches a known case (a canned
prompt-injection string or PII pattern) is the difference between "a library is
wired in" and "the mechanism is verified to fire."

---

## 8. Observability `[observability]` `[performance]` `[ux]` (~400 words)

**8.1 OpenTelemetry via LangSmith** `[observability]`

LangSmith has native end-to-end OTel support —
`LANGSMITH_OTEL_ENABLED=true` plus standard OTLP endpoint/headers env vars;
traces can route to Datadog/Grafana/Jaeger over plain OTLP, no bridging library
required. (OpenInference/Traceloop bridges exist for LangSmith-independent
setups.) Real distributed tracing across a whole stack, not just an isolated
agent UI.

**8.2 `stream_mode`** `[performance]` `[ux]`

Current modes: `"values"`, `"updates"`, `"messages"`, `"custom"`, `"debug"`,
`"checkpoints"`, `"tasks"`. A list yields `(mode, chunk)` tuples; a single string
yields bare chunks. How a client gets partial progress without polling.

**8.3 Domain metrics as a light example**

Brief, from the repo, kept short since it's domain-specific: cache hit rate,
calls-per-run, latency, token count as instances of the general "what to
measure" question — not a metrics catalog for this article to own.

---

## 9. What LangGraph Doesn't Own `[compliance]` `[security]` (~300 words)

**9.1 GDPR / data retention**

Cross-reference §4.5 rather than re-explain: the checkpointer and `Store` persist
whatever PII flows through state at every superstep, with no built-in
compliance tooling.

**9.2 Checkpoint and graph versioning** `[reliability]`

Two distinct failure modes, not one bucket: state schema versioning (changing a
state object's shape can break deserialization of old checkpoints — a
LangGraph-specific problem because the checkpointer serializes state directly)
versus graph structural versioning (changing node topology can break in-flight
checkpointed threads between deploys even when the state shape didn't change).

**9.3 Security/guardrail primitives**

Cross-reference §7.3 rather than re-explain.

---

## 10. Scope and Limitations (~200 words)

- Not a production case study — no load data, no incident log, no
  cost-at-scale numbers. A demonstration of mechanics using a domain-shaped
  example, evaluated by reading the code and running it locally, not a report
  on operating it in production.
- Search is mocked — a deliberate scope cut so the article stays about
  LangGraph's control-flow patterns, not search-provider quality. "Production
  would swap this one class" is an architectural claim, not a tested
  migration — say so.
- Model tiering by task difficulty (cheap model for easy nodes, strong model
  for hard ones) is out of scope — a cost/quality policy decision orthogonal
  to LangGraph's actual job of wiring control flow. At most a parenthetical
  where per-node model binding is already discussed, never its own section.

---

## 11. What's Next (~150 words)

Next steps that add surfaces and infrastructure around the mechanics shown, none
of which change the core patterns: Postgres checkpointer for durable
multi-session resumability, LangSmith tracing, a push-based review interface
replacing a CLI HIL stub, scheduling, a dashboard streaming node-level progress.

**Link to the repository.**

---

## Appendix: Running the Demo (~100 words)

```bash
git clone https://github.com/your-org/course-discovery-agent
cd course-discovery-agent
# optional: set GOOGLE_API_KEY, DATABASE_URL in .env — no search API key needed, search is mocked
uv sync
uv run python main.py
```

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
| 4. HITL and Durability | 600 |
| 5. Node-Level Resilience | 400 |
| 6. API Brittleness by Design | 550 |
| 7. Provider-Level Resilience | 450 |
| 8. Observability | 400 |
| 9. What LangGraph Doesn't Own | 300 |
| 10. Scope and Limitations | 200 |
| 11. What's Next | 150 |
| Appendix | 100 |
| **Total** | **~4,450** |

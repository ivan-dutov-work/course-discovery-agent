# LangGraph for Agentic Workflows: Production Patterns

Draft in progress. Structure follows `specs/ARTICLE_OUTLINE.md`. Sections below are
either drafted prose or an explicit placeholder naming what's pending — nothing is
silently missing. Placeholders are marked `[NOT DRAFTED]` so a partial read never
gets mistaken for a finished section.

Drafted so far: §2.2, §3.2, §3.3, §4.1, §4.2, §4.3, §8.2, §9.1, §9.2 (OpenRouter half), §11.1, §11.2, §11.4, §12.
Sections 5-7 were restructured around durability and effects; their placeholders name
the verified material in `specs/ARTICLE_TODO.md`.
Everything else is outline-only — see `specs/ARTICLE_OUTLINE.md` for what each
pending section needs to say.

---

## 0. TL;DR

[NOT DRAFTED] — see outline §0.

---

## 1. Agents vs. Agentic Workflows

[NOT DRAFTED] — see outline §1 (1.1–1.3).

---

## 2. State as the Contract

### 2.1 The state is the single channel between nodes

[NOT DRAFTED] — see outline §2.1.

### 2.2 Reducers for safe concurrent merges

Nodes dispatched in parallel via `Send` don't get their own private state — they
all write into the same `AgentState`, in the same superstep. Without a merge
strategy, two workers writing to the same key would just clobber each other:
whichever write LangGraph applies last wins, and the other worker's results are
silently gone.

`course_discovery/domain/state.py` avoids this with `Annotated[..., operator.add]`
reducers on every field that parallel search workers write to:

```python
tavily_results: Annotated[list[TavilySearchResult], operator.add]
completed_queries: Annotated[list[str], operator.add]
research_notes: Annotated[list[str], operator.add]
tavily_calls: Annotated[int, operator.add]
```

Instead of "last write wins," LangGraph concatenates (or, for the counter, sums)
every worker's contribution into the shared key. Three `tavily_search_worker`
branches running in the same superstep each return their own slice of
`tavily_results`; the graph appends them all rather than keeping only one.

Worth being precise about what *isn't* reduced: `extracted_candidates` is a plain
`list[CourseCandidate]` field, not reducer-backed. It's written once per branch by
`candidate_extractor`, which is a different shape of concurrency — each branch
produces a self-contained value, so there's no cross-branch merge to protect. The
reducer is specifically for state a worker *appends to*, not state a worker
*replaces*. Conflating the two is an easy mistake: not every list-typed field in a
fan-out needs `operator.add`, only the ones actually shared across branches.

### 2.3 `input_schema`/`output_schema` separation

[NOT DRAFTED] — see outline §2.3.

---

## 3. Control Flow Primitives

### 3.1 Conditional edges

[NOT DRAFTED] — see outline §3.1.

### 3.2 Plan-driven `Send` fan-out

Worker count in `research_graph.py` isn't a fixed number of branches — it comes
from whatever the planner decided at runtime:

```python
def _dispatch_search_queries(state: AgentState):
    plan = state.get("research_plan")
    if not plan or not plan.search_queries:
        return "aggregate"
    return [
        Send("tavily_search_worker", {**state, "active_search_query": query})
        for query in plan.search_queries
    ]
```

If `research_planner` decides two queries close the gap, two workers run. If it
decides five are needed, five run. Compare this to a graph with three hardcoded
parallel branches (`worker_a`, `worker_b`, `worker_c`) wired in at compile time —
that topology can't adapt to "this query only needed one more search" or "this
query needs six." The fan-out width is a runtime decision the plan makes, not a
structural constant the graph author bakes in.

The same dispatch function is reused as the conditional-edge target from both
`research_planner` (the first pass) and `replanner` (the bounded retry loop) —
one fan-out mechanism serves both call sites, rather than duplicating the `Send`
logic per caller.

Worth being explicit about the boundary this doesn't cross: `Send` is in-process
fan-out, not a distributed queue. Every worker it dispatches runs inside the same
graph execution, on the same machine, sharing the same Python process's memory —
there's no broker, no independent retry/ack semantics per worker, no way for one
worker to survive if the process crashes mid-fan-out. If the real system needed
cross-service work distribution — search workers running on separate machines,
surviving a coordinator restart, backed by at-least-once delivery — that's a
different infrastructure layer (SQS, Celery, Temporal) that LangGraph does not
replace. Treating "parallel `Send` workers" as equivalent to "a job queue" is a
real mistake: they solve the same-looking problem (do several things at once) at
completely different reliability guarantees.

### 3.3 Subgraphs for encapsulation

`build_research_graph()` compiles the entire research pipeline — planner, search
fan-out, extraction, aggregation, validation, replanning, synthesis — into its own
`StateGraph`, and returns it bare-compiled (no checkpointer or interrupt config at
this level; those are the outer graph's concerns). The outer graph then mounts it
as a single node:

```python
builder.add_node("research_agent", build_research_graph())
```

From the outer graph's perspective, `research_agent` is just another node — one
that happens to internally run a multi-step pipeline instead of a single
function. This pays off directly in the router: REWRITE, AUGMENT, and RESET all
route back into `research_agent` as one edge target, without the outer graph
needing to know (or care) which internal node of the subgraph should resume
work. Adding a node inside the research pipeline — another validation pass,
another extraction step — never touches `outer_graph.py`. The encapsulation
boundary is real, not just a naming convention.

### 3.4 `Command` for update-and-route

[NOT DRAFTED] — see outline §3.4.

---

## 4. Human Review as a Durable Boundary

### 4.1 `interrupt_before` as a durability-backed boundary

```python
graph = builder.compile(
    checkpointer=checkpointer or memory_saver(),
    interrupt_before=["review_gate"],
)
```

Contrast this with a naive `input()` call inside a node: that pauses a Python
stack frame, which only exists as long as the process does. `interrupt_before`
pauses the *graph* — LangGraph checkpoints state before `review_gate` runs and
returns control to the caller. Resuming later is `graph.update_state(...)`
followed by `ainvoke(None, config)`, keyed by `thread_id` — not "the same
process happened to still be running when the human replied."

Which checkpointer backs it is a wiring choice, not a graph change. The CLI
opens one with `open_checkpointer()`: `AsyncPostgresSaver` when `DATABASE_URL`
is set, `MemorySaver` otherwise. Both share the same msgpack allowlist (§11.2).
With Postgres, a fresh saver and a fresh graph object can read the thread back
and resume through publish. That is tested, but in the same OS process, so it
shows the state is in the database and not that a crash is survivable.

Crash survival is tested separately: a child process SIGKILLs itself inside
`course_cache_upsert`, and a fresh process resumes with `ainvoke(None, config)`.
With `durability="sync"` the resumed run does not re-run the already-completed
`evidence_validator`. With `durability="exit"` the thread has no checkpoints at
all after the kill and the run restarts from scratch. `durability="async"` is
not tested for a hard kill, because whether the last background write lands
before the process dies is a race, and a test would be asserting on timing.
The default `build_graph()` still falls back to `MemorySaver`, which has none
of these properties.

### 4.2 Dynamic `interrupt()`

`interrupt_before=["review_gate"]` is a compile-time decision: every run pauses
at that node, unconditionally. LangGraph also has `interrupt()`, called from
inside a node at runtime — which makes the pause conditional on whatever the
node just computed. In this codebase's terms: instead of every run stopping for
review, only runs where `evidence_validator` produced low-confidence or
`uncertain` candidates would call `interrupt()`; a run where everything
validated cleanly could route straight to `publish_node` without a human ever
seeing it.

This repo doesn't use dynamic `interrupt()` — the blanket "never auto-publish"
rule in `CLAUDE.md` is deliberately unconditional, so a static
`interrupt_before` is the right tool for that constraint. The distinction is
worth naming anyway: "should every run stop for review, or only the ones that
need it" is a real design fork, and this repo shows one answer, not the only
one.

### 4.3 UX gaps around the interrupt boundary

The router's five outcomes — PUBLISH, REWRITE, AUGMENT, RESET, DISCARD — are
already a better example than the binary approve/reject gate most HITL
write-ups show, and worth calling out as a deliberate design point rather than
incidental plumbing.

What LangGraph doesn't give you, briefly:

- A paused thread sits in the checkpointer indefinitely. There's no built-in
  TTL or staleness handling for a review nobody ever answers.
- Notifying a human that a thread is waiting is entirely app-layer.
  `graph.get_state(config)` tells you a thread is parked at `review_gate`; it
  doesn't push that fact anywhere. A real deployment needs the API layer to
  observe the paused state after `invoke`/`stream` returns and push one event
  (thread ID, run ID, payload) over a websocket/SSE channel, with the frontend
  subscribing per-thread to render the pending review. That's a one-paragraph
  sketch, not a protocol design.
- Reviewers aren't limited to approve/reject. `graph.update_state(...)` before
  resuming lets a reviewer patch state and continue — correct a filter, adjust
  a candidate — not just gate the run.
- Nothing stops two callers resuming the same `thread_id` concurrently. That's
  an app-level lock to build, not something the checkpointer arbitrates.
- The same `update_state` call can write the feedback that opens the publish
  gate, so who may resume a thread is an access question as well as a UX one
  (§11.4).

### 4.4 `recursion_limit` as a structural safety net

[NOT DRAFTED] — see outline §4.4.

---

## 5. Durability: What a Checkpoint Does and Doesn't Give You

### 5.1 What is checkpointed, and when

[NOT DRAFTED] — see outline §5.1.

### 5.2 Durability modes, with a real crash

[NOT DRAFTED] — see outline §5.2.

### 5.3 Replay is exact between nodes, best-effort inside one

[NOT DRAFTED] — see outline §5.3.

### 5.4 Intra-node memoization: `@task`

[NOT DRAFTED] — see outline §5.4.

### 5.5 Resume cost in a subgraph

[NOT DRAFTED] — see outline §5.5.

### 5.6 `Store` vs. checkpointer

[NOT DRAFTED] — see outline §5.6.

---

## 6. Effects: Making Side Effects Survive Replay

### 6.1 Idempotent writes under replay

[NOT DRAFTED] — see outline §6.1.

### 6.2 Outbox behind a port

[NOT DRAFTED] — see outline §6.2.

### 6.3 More than one effect in a node

[NOT DRAFTED] — see outline §6.3.

### 6.4 Operating it: what is not built

[NOT DRAFTED] — see outline §6.4.

### 6.5 When to reach for something else

[NOT DRAFTED] — see outline §6.5.

---

## 7. Node-Level Resilience: Retries, Timeouts, Caches

### 7.1 `RetryPolicy`

[NOT DRAFTED] — see outline §7.1.

### 7.2 Timeouts and async nodes

[NOT DRAFTED] — see outline §7.2.

### 7.3 `CachePolicy`

[NOT DRAFTED] — see outline §7.3.

### 7.4 Retry vs. circuit breaking

[NOT DRAFTED] — see outline §7.4.

---

## 8. API Brittleness by Design

[NOT DRAFTED] — see outline §8 (8.1, 8.3). Only §8.2 is drafted below.

### 8.2 Rate limiting

Every LLM call in this repo goes through one factory, `build_llm()`, which takes
an optional `rate_limiter=`. LangChain chat models accept any
`BaseRateLimiter`; `router_node` passes a module-level `InMemoryRateLimiter`
(2 requests/second, bucket of 4). The limiter is module-level on purpose: a
limiter built per call would never see the previous call.

The router is the node that gets called repeatedly within one run — once per
review round-trip, across PUBLISH/REWRITE/AUGMENT/RESET/DISCARD — so it's the
honest place to show a shared quota under repeated calls. The 2/4 numbers are
demo values, not tuned ones.

Two boundaries worth stating. `InMemoryRateLimiter` is a token bucket inside one
process: it smooths this process's outbound rate and knows nothing about a
second worker or a second user's run against the same key. Real cross-process
limiting belongs at the gateway or provider account, which is where §9.2 picks
up. And it limits the rate of *starting* calls; it says nothing about whether a
call succeeded, which is the retry question in §7.1.

---

## 9. Provider-Level Resilience and Spend Control

[NOT DRAFTED] — §9.3 (guardrails) and the LiteLLM half of §9.2 are pending; see outline §9.

### 9.1 Where call-level resilience lives

The graph decides which node runs next. What happens when the model behind a
node is down is not a graph concern, and LangGraph doesn't try to make it one:
the node calls a Runnable, and resilience is a property of that Runnable.

There are two in-process ways to build that in. `Runnable.with_fallbacks()`
(`langchain_core.runnables`, not LangGraph-specific) tries a list of runnables
in order. `ModelFallbackMiddleware` (`langchain.agents.middleware`) is the newer
native multi-model version. Either way it's "wrap the chain, call it like any
other Runnable" — no LangGraph glue. One correction worth making explicitly:
`init_chat_model()` does not take a fallback list.

This repo uses neither. It pushes the fallback one layer further out, to the
gateway (§9.2), so the LangChain side still sees exactly one chat model object.

### 9.2 OpenRouter as the single gateway

Every LLM node — gateway, router, synthesizer — is built by
`course_discovery/app/llm.py:build_llm()`, which returns a `ChatOpenRouter`
from the `langchain-openrouter` package. The primary model is
`deepseek/deepseek-v4.1-flash`; the fallback is `google/gemini-2.5-flash-lite`.
The fallback is OpenRouter's own `models: [...]` priority array in the request
body, which retries the next model on rate limits, downtime, context-length
errors, or moderation flags, and bills only the model that actually answered.

`ChatOpenRouter` has no first-class field for that array, so it goes in through
`model_kwargs`:

```python
ChatOpenRouter(
    model=PRIMARY_MODEL,
    temperature=0,
    model_kwargs={"models": [PRIMARY_MODEL, *FALLBACK_MODELS]},
    rate_limiter=rate_limiter,
    callbacks=[ServedModelLogger(node)],
)
```

Two facts from running it, not from docs. An invalid model ID is not a
fallback trigger: OpenRouter rejects the whole request with a 400 before any
routing happens, so the priority array protects against provider failures, not
configuration mistakes. And because the fallback is invisible to the caller,
you have to ask which model answered: the response carries a `model_name`, and
`ServedModelLogger` logs it on every call, at WARNING when it differs from the
primary. Without that, a week of silent fallbacks would look identical to a
week of healthy primary calls.

What was and wasn't verified. A live call to each LLM path returned the primary
model with structured output parsed correctly. The fallback path itself was not
triggered live — forcing a real provider failure on demand isn't practical — so
the test suite asserts only that the priority array is in the outgoing request
and that the logger flags a non-primary model.

The argument for a gateway isn't fallback; `.with_fallbacks()` gives you that
in-process. It's one bill, one rate-limit surface, and per-key spend caps across
providers. The cost is a hop through a third party: every prompt now transits
OpenRouter, which matters for §11.1.

---

## 10. Observability

[NOT DRAFTED] — see outline §10 (10.1–10.3).

---

## 11. What LangGraph Doesn't Own

### 11.1 GDPR / data retention

LangGraph has zero built-in compliance tooling, and that's worth framing as a
layer-of-abstraction point rather than a gap to fill.

The checkpointer and `Store` (see §5.6) are, mechanically, just a database of
user state. Whatever personal data flows through `AgentState` — search queries,
and in this codebase's case the `UserMemory` fields (`preferred_providers`,
`career_goals`, `completed_course_urls`, and so on) — gets persisted at every
superstep the checkpointer writes. There's no built-in TTL, no redaction
primitive, no right-to-erasure call. Deleting a user's data means writing
`DELETE WHERE thread_id = ...` (checkpointer) or `store.delete(...)` (`Store`)
yourself; LangGraph gives you the durability, not the retention policy on top
of it. One line worth stating plainly once §5.6 lands: this durability is also
a retention liability you now own.

The actual compliance lever sits one layer down, at the model-provider
boundary — zero-data-retention flags, DPAs, which region processes the
request. That's invisible to LangGraph entirely; the graph doesn't know or
care what the Runnable underneath a node does with the payload it's handed.
Worth noting as an open question rather than a claim: some providers advertise
PII-filtering features distinct from their data-retention policy (not-logging
vs. actively detecting/stripping PII are different guarantees) — don't conflate
the two without checking the specific provider's current docs.

### 11.2 Checkpoint and graph versioning

Two distinct failure modes worth naming separately, not one bucket:

**State schema versioning.** If `AgentState`'s shape changes — a field renamed,
a type changed — checkpoints persisted under the old shape may not deserialize
under the new one. This is a LangGraph-specific risk, not generic app
versioning, because the checkpointer serializes the state object directly
rather than through a migration-aware layer.

**Graph structural versioning — a separate risk.** Changing node topology
(adding, removing, or renaming a node) between deploys can break an in-flight
checkpointed thread from the old graph shape, even when `AgentState` itself
didn't change — the checkpoint records *where* execution was parked, and that
position may no longer exist in the new graph.

Schema versioning has already bitten here, in a smaller form. With
`langgraph-checkpoint` 4.2.0 a durable saver logs a "Deserializing unregistered
type" warning once per Pydantic model in state, and enum-typed state fields log
a separate "Blocked deserialization" message under an allowlist built from
`BaseModel` subclasses only. The allowlist in `persistence/checkpointer.py` now
covers models and enums, and a test round-trips both through it. What a blocked
enum does to the restored value was not inspected, so treat the allowlist as the
fix and not the explanation. This is also a deserialization-safety control: a
writer to the checkpoint database can otherwise cause arbitrary type
construction on read.

Structural versioning is not tested here. There is no test that persists a
thread under one topology and resumes it under a changed one, so that half is
still "here's what you'd hit," not something this codebase has hit.

### 11.3 Security/guardrail primitives

[NOT DRAFTED] — see outline §13.3 (cross-references §9.3 once drafted).

### 11.4 Access control on `thread_id`

A `thread_id` is a bearer capability. Anyone who can present it can read the
thread with `get_state`, resume it with `ainvoke(None, config)`, or patch it
with `update_state`. Nothing in the checkpointer binds a thread to a user. In
this repo the CLI mints `run_id` with `uuid4()` and uses it as the thread id,
which makes it unguessable, but that is an accident of the CLI and not an
access rule.

The consequence is sharper than "someone can read another user's run".
`update_state` can write `manager_feedback`, and the router turns that into
PUBLISH. Whoever can resume a paused thread can therefore approve the publish
that `interrupt_before` was put there to gate. The gate stops the graph from
publishing on its own; it says nothing about who is allowed to open it.

The check belongs in the application layer, before any `get_state`,
`update_state` or resume call: compare the authenticated caller with the
`user_id` stored in the thread's state, or derive the thread id server-side
from the caller so it cannot be supplied. This repo does neither, because the
CLI has one user. It is a boundary to name, not a feature to bolt on.

---

## 12. Scope, Limitations and Verification

This is not a production case study. Nothing in this article comes from
operating this agent under real traffic — no load data, no incident log, no
cost-at-scale numbers. It's a demonstration of LangGraph mechanics using a
domain-shaped example, evaluated by reading the code and running it locally.
The retry/cache/durability/`Store`/OTel/fallback material in this article describes
what these mechanisms *are* and where they'd attach in this codebase, not a
report on how they behaved under production load.

Search is mocked, on purpose. `tavily_search_worker` reads from a static
in-repo catalog instead of calling a real search API — course listing pages are
mostly JS/PHP-rendered and poorly indexed, so a real provider would add
operational noise (rate limits, flaky results, API keys) that has nothing to do
with the article's actual subject. "Production would swap this one class" is an
architectural claim about the seam (`TavilyClient`'s `search()` signature), not
a tested migration — worth saying plainly rather than implying the swap has
been validated.

How the claims were checked. One contract suite runs against both outbox
stores; Postgres integration tests cover replay, parallel workers and
concurrent submits; a SIGKILL child-process test covers crash recovery for each
durability mode. Where it matters the text says whether a finding was observed
here, read from source, or taken from docs. Known untested: `durability="async"`
under a hard kill, a changed graph topology against a persisted thread, strict
msgpack mode for a type outside the schema, and everything under real load.

Model tiering by task difficulty — a cheap model for easy nodes, a strong model
for hard ones — is explicitly out of scope. It's a cost/quality policy
decision, orthogonal to LangGraph's actual job of wiring control flow. Each
node just calls whatever Runnable it's bound to, so per-node model choice is a
one-line fact, not a feature worth its own section — at most a parenthetical
where per-node model binding is already discussed.

---

## 13. What's Next

[NOT DRAFTED] — see outline §11.

---

## Appendix: Running the Demo

[NOT DRAFTED] — see outline appendix. (The old M1 appendix in
`specs/ARCHIVE_ARTICLE_M1_DRAFT.md` describes a different CLI surface —
`telegram_gate`, `worker_a/b/c` — and should not be reused verbatim; the
current CLI/router action set is PUBLISH/REWRITE/AUGMENT/RESET/DISCARD.)

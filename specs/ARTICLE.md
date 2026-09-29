# LangGraph for Agentic Workflows: Production Patterns

Draft in progress. Structure follows `specs/ARTICLE_OUTLINE.md`. Sections below are
either drafted prose or an explicit placeholder naming what's pending — nothing is
silently missing. Placeholders are marked `[NOT DRAFTED]` so a partial read never
gets mistaken for a finished section.

Drafted so far: §2.2, §3.2, §3.3, §4.1, §4.2, §4.3, §5.1–§5.6, §6.1–§6.5, §7.1–§7.4, §8.2, §9.1, §9.2 (OpenRouter half), §9.3, §11.1, §11.2, §11.4, §12, §13.
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

Nodes dispatched in parallel via `Send` don't get private state. They all write
into the same state object, in the same superstep, and without a merge strategy
two workers writing the same key clobber each other: whichever write LangGraph
applies last wins, and the other worker's results are silently gone.

The merge strategy is declared on the field, as a reducer:

```python
tavily_results: Annotated[list[TavilySearchResult], operator.add]
completed_queries: Annotated[list[str], operator.add]
research_notes: Annotated[list[str], operator.add]
tavily_calls: Annotated[int, operator.add]
```

Instead of "last write wins", LangGraph concatenates every branch's contribution
into the shared key, or sums it for a counter. Three workers in the same
superstep each return their own slice of the results, and the graph appends all
three rather than keeping one.

Worth being precise about what *isn't* reduced. A field written once per branch,
where each branch produces a self-contained value, needs no reducer: there is no
cross-branch merge to protect. A list of extracted candidates produced by a
single downstream node is a plain field. The reducer is for state a worker
*appends to*, not state a worker *replaces*. Conflating the two is an easy
mistake: not every list-typed field in a fan-out needs `operator.add`, only the
ones shared across branches.

### 2.3 `input_schema`/`output_schema` separation

[NOT DRAFTED] — see outline §2.3.

---

## 3. Control Flow Primitives

### 3.1 Conditional edges

[NOT DRAFTED] — see outline §3.1.

### 3.2 Plan-driven `Send` fan-out

Fan-out width shouldn't be a compile-time constant. `Send` lets a conditional
edge return one task per item of a runtime plan, so the number of workers comes
from whatever an earlier node decided:

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

If the plan says two queries close the gap, two workers run; if it says five,
five run. Compare a graph with three hardcoded parallel branches wired in at
compile time: that topology can't adapt to "this query only needed one more
search" or "this one needs six." The fan-out width is a runtime decision the
plan makes, not a structural constant the graph author bakes in.

Because the dispatch is a plain function, it can serve as the conditional-edge
target for every node that needs to fan out. A first planning pass and a bounded
replanning loop share one mechanism instead of duplicating the `Send` logic per
caller.

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

A subgraph is the unit of encapsulation. Compile a multi-step pipeline into its
own `StateGraph`, bare, without a checkpointer or interrupt config, because
those are the parent's concerns, and mount it as one node:

```python
builder.add_node("research_agent", build_research_graph())
```

From the parent's perspective the pipeline is just another node, one that
happens to run many steps internally. This pays off wherever the parent routes:
every outcome that means "do the work again" (a rewrite, an augmentation, a
reset) points at one edge target, and the parent never needs to know which
internal step should resume. Adding a validation pass or an extraction step
inside the pipeline never touches the parent's wiring. The encapsulation
boundary is real, not a naming convention.

What the boundary costs is in §5.5.

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
stack frame, which lives only as long as the process. `interrupt_before` pauses
the *graph*. LangGraph checkpoints state before the named node runs and returns
control to the caller. Resuming is `graph.update_state(...)` followed by
`ainvoke(None, config)`, keyed by `thread_id`, not "the same process happened to
still be running when the human replied."

Which backend holds the checkpoint is a wiring choice, not a graph change, and
it decides what kind of pause you have. An in-memory saver survives a slow
reviewer but not a restart. A database-backed saver lets a fresh saver and a
fresh graph object read the thread back and resume: the state is in the
database, not in the process.

Worth being precise about what that shows. Reading a thread back through a new
saver proves the state is external; it doesn't prove a crash is survivable. The
crash case depends on the write policy (§5.2): a process killed mid-node resumes
without redoing completed work only if checkpoints were written before each
step, and restarts from scratch if they were flushed only on exit.

The cost of a durable pause is that every paused thread is now a row someone
owns until a reviewer answers it (§4.3).

### 4.2 Dynamic `interrupt()`

`interrupt_before` is a compile-time decision: every run pauses at that node,
unconditionally. LangGraph also has `interrupt()`, called from inside a node at
runtime, which makes the pause depend on what the node just computed. Instead of
every run stopping for review, only runs that produced low-confidence or
`uncertain` results call `interrupt()`; a run where everything validated cleanly
routes straight on.

The two answer different questions. The static form is a policy: no run reaches
the effect without a human. The dynamic form is a filter: humans see the runs
that need them. Conflating the two is how a human-in-the-loop system quietly
becomes one where the human sees only what the model chose to escalate.

The practical decision rule: when the pause guards an irreversible effect, keep
it static, because a conditional gate is only as trustworthy as its condition.
When review is a quality tool and a miss is cheap, make it dynamic and spend
reviewer time where the uncertainty is. Not every graph needs the second form; a
graph with an unconditional never-auto-publish rule doesn't.

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

A checkpoint records the graph's state at a superstep boundary. Everything
below follows from that single fact: what survives a crash, what doesn't, and
what you must build around the seam.

### 5.1 The checkpoint boundary

A checkpoint lands after every completed superstep. On resume, finished nodes
are not re-run; only the interrupted node restarts from the top of its function.
This gives you exactness between nodes but nothing inside one — the LLM call, the
database write, and everything else that happens within a single node's function
body is an atomic, uncheckpointed unit.

The checkpointer itself is a pluggable backend. A typical wiring looks like:

```python
# compile-time: pick your backend
checkpointer = AsyncPostgresSaver(...) if DATABASE_URL else MemorySaver()
graph = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["review_gate"],
)
# run-time: checkpointing is automatic
result = await graph.ainvoke(input, {"configurable": {"thread_id": "..."}})
```

A subgraph without its own checkpointer inherits the parent's, which means
subgraph internal steps are also persisted — but under a runtime namespace you
must discover, not construct from the thread ID alone.

### 5.2 Durability modes

The framework offers three write policies. "Write on exit" flushes the
checkpoint only when the graph finishes or raises — fastest, fewest writes, but a
hard kill loses everything since the last superstep. "Write before every step"
is the safest — a kill stops the graph without re-executing completed
nodes — at the cost of 10-15 I/O operations per run. A third mode writes
asynchronously: the graph continues without waiting for the checkpoint to land.

```python
# "sync" — safest, slowest
await graph.ainvoke(input, config, durability="sync")
# "exit" — fastest, loses granularity on hard kill
await graph.ainvoke(input, config, durability="exit")
```

These are not just performance levels. A hard-kill test shows the gap: under
"write on exit" a process killed during a node leaves no checkpoint at all,
so the entire run restarts from scratch. Under "write before every step" the
same kill preserves all prior work and the resumed process skips completed
nodes. The async mode is not safe to test for a hard kill — whether the last
background write lands is a race. For ordinary failures (exceptions, not
signals), all three modes behave identically: the exception is itself a
superstep boundary, so even "write on exit" checkpoints before it propagates.

### 5.3 Replay determinism

Because a node is an atomic unit, deterministic replay only holds at node
boundaries. If one node calls an LLM and the next node writes the result to a
database, the checkpoint between them guarantees the writer sees the LLM's
output exactly as it was. But if both calls live in the same node, a crash
after the LLM returns but before the checkpoint means the resumed run calls the
LLM again — and may get a different answer.

The architectural response: split the caller from the writer. Put the
nondeterministic work (LLM calls, API requests) in earlier nodes that are
checkpointed before their output reaches the effect. Keep effect nodes pure —
they read from checkpointed state and produce no nondeterministic calls of
their own. Temperature 0 is not a substitute for this architecture: model
providers can change output between runs, and provider fallback (§9.1) produces
the same class of divergence.

### 5.4 Intra-node memoization

A `@task`-style annotation can memoize individual function calls inside a node,
so a resumed run reuses the stored result instead of recomputing:

```python
@task
def parse_candidate(raw: dict) -> CourseCandidate:
    ...

async def extract_node(state: AgentState):
    # on resume these short-circuit instead of re-calling parse_candidate
    candidates = [parse_candidate(r) for r in state.results]
```

The mechanism narrows the window between a call and its effect, but it does not
close it: the memoized value is written asynchronously, so a hard kill can still
cause the function to run twice. Matching is by name and call position, not by
arguments, so a changed argument returns stale data.

The real limit is structural: memoization does not cross subgraph boundaries.
A subgraph's internal replay does not benefit from memoization of its parent's
scope. This aligns with the principle in §5.3 — the reliable seam is the node
boundary, not the call boundary inside a node.

### 5.5 Encapsulation cost: subgraph resume

A subgraph is one node to its parent. When a subgraph node is interrupted and
resumed, the entire subgraph re-runs — including already-successful parallel
workers that a standalone graph would skip. The parent has no visibility into
which subgraph workers completed.

This is a known behavior with a documented root cause: subgraph task IDs are
derived from the parent checkpoint ID, and that ID changes when the parent forks
for resume. The mechanism that re-attaches cached writes from completed nodes
(`skip_done_tasks` / `_reapply_writes_to_succeeded_nodes`) keys on task IDs and
finds nothing for the new subgraph instance — so every internal node looks
incomplete. Three open GitHub issues track the problem in different
configurations (#6792, #8458, and the resolved #6050 whose fix doesn't cover
all cases).

The obvious alternative — flattening the graph — works but at the cost of
exposing internal topology. There are also intermediate approaches within the
framework worth weighing explicitly:

**1. State-based dedup.** Track completed workers with a reducer-backed list and
check before re-doing work:

```python
def search_worker(state: AgentState, config):
    query = state["active_search_query"]
    if query in state.get("completed_queries", []):
        return {}  # already done, skip
    results = search(query)
    return {"tavily_results": results, "completed_queries": [query]}
```

The worker still runs — LangGraph dispatches it, the function is called, it just
short-circuits. This avoids the LLM call but not the scheduler overhead or the
subgraph namespace problem. Useful when workers are expensive but idempotent.

**2. Per-thread subgraph checkpointing.** A subgraph compiled with
`checkpointer=True` gets its own thread-scoped namespace. State accumulates
across calls instead of starting fresh each time. This does not, however, fix
interrupt/resume within a single call: the subgraph still re-executes any node
that contained the interrupt. Worse, per-thread subgraphs cannot be called in
parallel — two concurrent invocations collide on the same namespace, which is
why a parallel `Send` fan-out would break under this approach.

**3. Interrupt-aware nodes.** The config carries `config["configurable"]["checkpoint_id"]`
on resume. A node could detect it and skip work, but the namespace invalidation
issue means there is no framework API to read "what did this node output in the
previous, interrupted run." The node can only skip re-doing work it already
sees in state, which converges with approach 1.

**4. Separate the interrupt boundary.** Instead of interrupting inside the
subgraph, interrupt *before* the subgraph. The subgraph runs atomically — either
it completes or it doesn't — and the human reviews after the fact. This avoids
subgraph resume entirely but forces the reviewer to accept or reject the full
result, not inspect its internals mid-pipeline. The choice maps to how much
internal visibility the human needs.

None of these fully closes the gap to what a flat graph gives you — individual
node-level skip on resume — because the checkpoint namespace itself is the
problem, and only the LangGraph runtime can fix that. The practical decision
rule: if subgraph resume happens rarely (a few percent of runs), the cost of
re-running successful workers is noise and the encapsulation benefit dominates.
If it happens on every run (always-on human review inside the subgraph),
flattening or approach 4 may be the better bet.

### 5.6 Long-term vs. thread-scoped memory

Every checkpoint records the state of one thread. That thread-scoped storage is
what makes interrupt/resume and replay work. It is not, however, long-term
memory — it persists for exactly as long as the thread's checkpoints exist, and
is tied to that thread's identity.

```python
# checkpointer — thread-scoped, wired at compile time, invisible to nodes
graph = builder.compile(checkpointer=checkpointer)

# store — cross-thread, requires explicit reads and writes in node code
store.get(("user_prefs", user_id), "profile")
store.put(("user_prefs", user_id), "profile", {"goal": new_goal})
```

Cross-thread memory — user profiles, preferences, historical data — lives in a
separate store. The framework provides one, but it is also common to use a
plain database table for the same purpose. The key distinction is scope: the
checkpointer is implied by the graph itself and invisible to node code, while
cross-thread storage requires explicit reads and writes. Conflating the two is
a common source of confusion — the checkpointer persists state, so the instinct
is to treat it as long-term memory, but its lifetime is bounded by the thread's
checkpoint history. The durability that makes graphs resumable is also a
retention liability (§11.1) with no built-in TTL or redaction.

---

## 6. Effects: Making Side Effects Survive Replay

The checkpointer resumes the graph. It does not make an external call happen
once. Those are two separate recovery loops, and conflating them is the source
of the most common durability failure in checkpointed workflows.

### 6.1 Idempotent writes

A node that writes to a database and then completes will checkpoint after the
write. But if the process crashes between the write and the checkpoint, the
resumed run re-executes the write — and unless the write is naturally
idempotent, the result is a duplicate.

There are two approaches. The first is to structure the schema so that every
write can be applied twice:

```sql
-- key by natural content identity, so replay finds the same row
CREATE TABLE course_evidence (
    course_id  UUID REFERENCES courses(id),
    source_url TEXT,
    -- ON CONFLICT (course_id, source_url) DO UPDATE
);

-- or use a derived idempotency key that is stable across replays
CREATE TABLE recommendation_events (
    idempotency_key TEXT PRIMARY KEY,  -- "{run_id}:{course.url}"
    -- ON CONFLICT (idempotency_key) DO NOTHING
);
```

The key rule is that the idempotency key must be determinable before the node
executes — a key generated at runtime (a new UUID) produces a different value on
each replay and defeats the purpose. The run's own identifier, minted before the
graph starts, is the natural source.

The second approach is to fail closed. A write that encounters an unexpected
error (a constraint violation, a missing foreign key) should propagate the
failure rather than swallowing it into a log line. The graph pauses at the
failed node's checkpoint, and the state is preserved for inspection. A silent
drop of a write that "mostly works" is worse than a visible failure that stops
the run.

### 6.2 Outbox pattern

Not every effect can be made idempotent at the database level. Sending an
email, charging a payment, or calling a third-party API that does not
accept idempotency keys requires a different approach: don't call the external
system from the node at all.

The pattern is an outbox. Nodes call an internal gateway:

```python
# inside a node — never touches the external system
gateway.submit(Effect(key=f"publish:{run_id}", kind="publish_digest", payload=digest))
```

The key derives from the run identifier, so replay re-submits the same key and
the store ignores it (`ON CONFLICT DO NOTHING`). That is the whole trick: the
node's job shrinks to recording an intention, and delivery becomes someone
else's problem. Because nodes only see the gateway, the delivery side is
swappable, from an in-process adapter for development to a database outbox with
a worker for production.

Delivery is where the second recovery loop lives. The worker leases rows so a
crash releases them, counts attempts so a record that keeps killing its worker
ends up dead-lettered, and backs off between tries. Worth being precise about
the split: the checkpointer resumes the graph, the worker retries the effect,
and replay never retries a delivery, because it only re-submits a key that
already exists.

The cost shows up in the state contract. A boolean `published` becomes a status
(`queued`, `delivered`, `dead`), because the node can no longer say "done", only
"accepted". Anything downstream, a UI included, has to be written against that.
The atomicity boundary is also narrower than it looks: the outbox row can share
a transaction with the node's own domain write, but never with the checkpointer,
so the guarantee is at-least-once delivery plus idempotent submit, not
transactional consistency.

### 6.3 Multiple effects in one node

When a single node submits several effects, replay resubmits all of them.
Each effect has its own stable key, so existing keys are no-ops. Keys must
be attached to the effect's identity, not its position in a list — replay
reconstructs the list from checkpointed state and may produce a different
order. No ordering between effects is guaranteed.

### 6.4 Operational gaps

The patterns above handle submission, delivery, and dedup for a
single-process, single-worker setup. What they do not address — dead-letter
handling, handler timeouts, lease preemption, pruning — are problems that
real queue infrastructure (SQS, RabbitMQ, Kafka) solves natively. The dividing
line is scale: the outbox is for "a few effects per run, running in one
process." When the system grows to the point where a handler can outlive its
lease and a second worker picks up the same effect, the right move is to point
the gateway at a real queue instead of the in-process outbox store.

A production queue gives you lease-managed concurrency (a heartbeat extends the
visibility timeout; a silent worker loses the lease), dead-letter exchanges
after N retries, and configurable handler timeouts. None of this requires the
outbox pattern to grow up — it requires the outbox pattern to hand off to
something that already has these primitives. The outbox's job is deterministic
submission; the queue's job is reliable execution. Conflating the two into a
single component that tries to do both is where the gaps appear.

### 6.5 When a different architecture fits better

What the outbox approximates by hand — deterministic keys, leased rows, a
worker with backoff — is exactly what durable execution systems (Temporal,
Restate, DBOS) give as a first-class property. In those systems every step is
persisted, not only the superstep boundary. The dividing line: if most of a
workflow is long-running side-effect steps, each of which must run exactly
once, a durable execution system is the lighter fit (you stop approximating
and start declaring). If the workflow is mostly LLM calls with occasional
effects, LangGraph's checkpointer plus a lightweight outbox is the lighter
fit.

---

## 7. Node-Level Resilience: Retries, Timeouts, Caches

### 7.1 `RetryPolicy`

A node fails. The question is what the graph does about it, and the answer is not a single mechanism — it's three, each aimed at a different failure class.

`RetryPolicy` handles "the call failed" — a transient error (timeout, connection reset, rate limit, server error) that might succeed if tried again. It belongs on the node's `retry_policy=` parameter at `add_node` time, not inside the node's function body:

```python
from langgraph.types import RetryPolicy

retry = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    max_interval=8.0,
    jitter=True,
    retry_on=is_transient,  # timeouts, connection errors, 408/425/429/5xx
)
builder.add_node("search_worker", worker_fn, retry_policy=retry)
```

Which nodes get a policy depends on what retrying means for them. Read-only nodes (a cache lookup, a memory read) are safe because the call doesn't change anything observable — retrying a read is just retrying the read. Mutating nodes (a cache write, a side-effect submission) are safe only when their operations are idempotent: replay re-submits the same key, and the store ignores the duplicate (§6.1, §6.2). Without that foundation, retrying a writer duplicates the effect on every attempt.

A node that catches every exception makes `RetryPolicy` a no-op — the policy never sees the error to decide whether to retry. The pattern is to let transient errors propagate and convert only permanent failures into state. And the LLM client may ship its own retries, which multiply with the policy's — each framework-level retry that gets a timeout triggers a second policy-level retry, which starts a new call that itself retries. Nodes that already have a `RetryPolicy` should disable client-level retry; nodes without one opt back in.

When the policy's attempts are exhausted, the error propagates out of the graph — not into state — and the run stays resumable from its last checkpoint. The checkpoint preserves the state of every completed node; only the failed node re-executes on resume (§5.1).

The contrast to hold explicitly: `RetryPolicy` means "the call failed," a domain replanning loop means "the call succeeded but the result was insufficient." Retry is a policy on the node; replanning is a domain loop inside the graph. Two different failure classes, two different mechanisms. Conflating them is how a transient-error handler ends up hiding a bad answer, or a replanning loop ends up absorbing a timeout.

### 7.2 Timeouts and async nodes

There is no per-node `timeout=` parameter in langgraph 1.1.2 (verified against `add_node`'s actual signature — `defer, metadata, input_schema, retry_policy, cache_policy, destinations`). Timeouts belong inside the client the node calls, not on the node itself.

The three boundaries where a graph touches an external system are the same three that need explicit timeout configuration:

```python
# LLM call — passed to the chat client constructor
ChatOpenRouter(..., request_timeout=30_000)

# Network call — wrapped around the async I/O
results = await asyncio.wait_for(client.search(query), timeout=10)

# Database — connect and statement timeouts in the connection string
f"{db_url}?connect_timeout=5&options=-c statement_timeout=15000"
```

A timeout on a network call raises `TimeoutError`, which a `RetryPolicy` using a timeouts-and-connection-errors classifier (§7.1) catches and retries. The timeout wraps the *call*, not the node: the node's function may continue running until the policy re-invokes it, but the hung external call is bounded.

Async node functions give real I/O concurrency for these bounded calls. A fan-out dispatches several `async def` workers in parallel, each running its own network call; the event loop interleaves them without OS-thread overhead. This is not simulated parallelism — a synchronous `def` node that calls three blocking APIs sequentially would triple the wall-clock time. Worth calling out explicitly as a deliberate pattern, not an incidental fact: use `async def` for a node when the node's work is I/O-bound, not because the graph requires it.

### 7.3 `CachePolicy`

LangGraph also offers `CachePolicy`, passed per-node to `add_node` (or graph-level to the compiled graph). It memoizes a node's output keyed on input, so a second call with identical arguments returns the cached result without re-executing the node. This is a narrower mechanism than a whole-workflow cache, and a different one from the intra-node memoization `@task` provides (§5.4).

Notably, `CachePolicy` is **not** tied to in-memory storage. The compiled graph accepts a `cache` parameter that implements the `BaseCache` ABC — an abstract interface with `get`/`set`/`clear` keyed by `(Namespace, str)` tuples with optional TTL. LangGraph ships two implementations: `InMemoryCache` (process-local `dict`) and `RedisCache` (Redis MGET/pipeline, with TTL mapped to native Redis expiry). Rolling an S3-backed variant would mean implementing six methods. This makes the framework cache usable across process restarts without changing a single line of graph code.

The distinction is worth naming as a design choice. "Framework cache for same-input reruns" and "domain cache for known-valid results" are different responses to different questions. The framework cache saves compute on redundant graph execution; the domain cache saves an external round-trip. They are not the same thing, and a graph can benefit from both — but only if the gap each one fills is articulated. A `CachePolicy` on a cache-lookup node protects against the unlikely event that the graph calls the same node twice with the same state — a question of graph topology, not caching strategy.

### 7.4 Retry vs. circuit breaking

`RetryPolicy` handles transient failures. It does not protect against a sustained outage: retrying a fully down API three times burns budget and latency without improving the outcome, and if the node is one of several parallel workers, it delays the entire fan-out.

LangGraph gives you per-node retry and nothing at the graph level for a circuit breaker. If the same downstream API keeps failing, every affected node exhausts its own policy independently — each on its own clock, each unaware of the other's failures. An outbox worker (§6.2) can provide per-effect backoff with dead-lettering after N attempts, but that covers only effects that go through the outbox; nodes that call external APIs directly still have no shared failure state.

The practical options: a hand-rolled failure counter in state checked before dispatching the next parallel worker, or delegating circuit breaking to a gateway or client library that sits in front of the API. Either way, the gap is worth naming as a mental-model point: the graph's resilience knobs are per-node, not per-dependency. If you need per-dependency circuit breaking, it belongs one layer down (the HTTP client, the gateway) or one layer up (a state field that gates fan-out dispatch), not in the node's `RetryPolicy` configuration. §12.1 covers the cross-worker architecture.

---

## 8. API Brittleness by Design

[NOT DRAFTED] — see outline §8 (8.1, 8.3). Only §8.2 is drafted below.

### 8.2 Rate limiting

LangChain chat models accept any `BaseRateLimiter` through a `rate_limiter=`
argument, which smooths the rate at which calls start. It matters when several
nodes share one provider quota within a run, and more so when several runs share
it.

The limiter has to outlive the call. A limiter built per call never sees the
previous one, so the instance lives at module level and is handed to every node
that draws on the same quota. The node worth showing is the one called
repeatedly within a run, such as a review router hit once per round trip,
because that is where a shared bucket earns its keep. Bucket sizes in a demo are
placeholders, not tuned values.

Two boundaries worth stating. `InMemoryRateLimiter` is a token bucket inside one
process: it knows nothing about a second worker or a second user's run against
the same key, and real cross-process limiting belongs at the gateway or provider
account (§9.2). And it limits the rate of *starting* calls; it says nothing
about whether a call succeeded, which is the retry question in §7.1.

---

## 9. Provider-Level Resilience and Spend Control

[NOT DRAFTED] — §9.4 (guardrails) and the LiteLLM half of §9.2 are pending; see outline §9.

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

A third option moves the fallback out of the process entirely, to a gateway
(§9.2), so the LangChain side still sees exactly one chat model object.

### 9.2 OpenRouter as the single gateway

A gateway such as OpenRouter moves fallback out of the process. Every node
builds its chat model through one factory, and the request carries an ordered
`models: [...]` list. The gateway retries the next model on rate limits,
downtime, context-length errors or moderation flags, and bills only the one that
answered. The useful shape is a primary model with a cheaper fallback from a
different provider (DeepSeek and Gemini in the demo), so one provider's outage
doesn't take the node down.

The dedicated `langchain-openrouter` package's `ChatOpenRouter` has no
first-class field for that list, so it goes in through `model_kwargs`:

```python
ChatOpenRouter(
    model=PRIMARY_MODEL,
    temperature=0,
    model_kwargs={"models": [PRIMARY_MODEL, *FALLBACK_MODELS]},
    rate_limiter=rate_limiter,
    callbacks=[ServedModelLogger(node)],
)
```

Two behaviours are easy to get wrong. An invalid model ID is not a fallback
trigger: the gateway rejects the whole request with a 400 before any routing
happens, so the priority list protects against provider failures, not
configuration mistakes. And because the fallback is invisible to the caller, you
have to ask which model answered. The response carries a `model_name`; log it on
every call and flag it when it differs from the primary. Without that, a week of
silent fallbacks looks identical to a week of healthy primary calls.

One caveat on evidence: the fallback path was asserted in the outgoing request,
not triggered live, since forcing a real provider failure on demand isn't
practical.

The argument for a gateway isn't fallback; `.with_fallbacks()` gives you that
in-process. It's one bill, one rate-limit surface, and per-key spend caps across
providers. The cost is a hop through a third party: every prompt now transits
OpenRouter, which matters for §11.1.

### 9.3 When the gateway isn't enough

A gateway or LiteLLM covers the common case, and most graphs should stop there.
Both implement one idea: when a call fails at the provider (rate limit,
downtime, context length, a moderation flag), send the same request to the next
model. Writing your own fallback logic is justified only when a business
requirement can't be expressed that way. The deciding question isn't "do we need
fallback" but "what counts as a failure, and what is an acceptable substitute."
A gateway answers both with a fixed default: failure is a provider error, and
the substitute is another model.

Worth naming the cases where that default breaks, each as a requirement rather
than a technical limit:

**A wrong answer is worse than a failed call.** The gateway sees a 200 and moves
on. It can't know the output failed a schema check, cited no evidence, or
contradicts the user's filters. When correctness is what the business is buying,
the trigger for fallback is the application's own validation, and the fallback
is an escalation (a stronger model, a different prompt), not an outage
substitute:

```python
result = await primary.ainvoke(payload)
if not meets_evidence_bar(result):
    result = await stronger.ainvoke(payload)
```

**Scores must be comparable within a run.** A gateway switches models per
request. If a run ranks candidates with one model and a rate limit moves half of
them to another, the scores aren't on the same scale and the ranking is quietly
wrong. The requirement is consistency inside a unit of work, so the fallback
decision belongs to the run: pin one model per batch, or fail the whole batch
over together.

**The acceptable substitute isn't another model.** Some products would rather
serve a stale-but-correct result than a fresh answer from a weaker model: the
last known-good digest from a cache, a deterministic template, or a run parked
for later. That is a fallback to a different kind of path, and only the graph
knows which one is safe.

**Who may see the data varies by request.** A gateway's fallback list is a list
of providers. If residency or contract terms depend on the tenant or the data
class (EU customer data may only reach EU-hosted models, this field may never go
to a provider without a data-processing agreement), the list itself is a
compliance decision computed per request. Provider-level filters, where a
gateway offers them, help; a rule keyed on tenant or data class is application
logic.

**The budget or the deadline belongs to the run, not the call.** Per-key spend
caps bound a key. A requirement like "no single run may cost more than X" or
"the review digest must arrive within N seconds" spans several calls and several
nodes. The decision to drop to a cheaper model or skip an optional step needs
the run's accumulated cost and elapsed time, which live in graph state, not in
the gateway.

**A silent substitution is itself a defect.** In audited or regulated flows, a
reviewer approving output should know it came from the fallback, and the record
should say which model produced what. A gateway can log the served model (§9.2);
putting it in the review payload, or refusing to substitute without a human, is
application logic. This is the same boundary as §4.3: the interrupt is where a
degraded result has to become visible.

The cost of writing this yourself is that you now own the failure taxonomy (what
retries, what escalates, what degrades), the state that carries it, and the
tests. One part gets easier, not harder: a provider outage can't be summoned on
demand (§9.2), but a validation-triggered fallback can be forced with a stub
model, so that branch is testable in a way the gateway's never is.

The practical decision rule: keep the gateway for provider failure and add
custom logic only for the cases above, layered on top rather than instead. The
custom layer decides *whether* to fall back and to *what kind* of path; the
gateway still handles the provider-level retry underneath. Two layers, two
different failure classes, the same distinction as retry versus replanning
(§7.1).

---

## 10. Observability

[NOT DRAFTED] — see outline §10 (10.1–10.3).

---

## 11. What LangGraph Doesn't Own

### 11.1 GDPR / data retention

LangGraph has zero built-in compliance tooling, and that's worth framing as a
layer-of-abstraction point rather than a gap to fill.

The checkpointer and `Store` (see §5.6) are, mechanically, just a database of
user state. Whatever personal data flows through the state object (search
queries, stored preferences, career goals, completed-course history) gets
persisted at every superstep the checkpointer writes. There's no built-in TTL,
no redaction primitive, no right-to-erasure call. Deleting a user's data means
writing `DELETE WHERE thread_id = ...` (checkpointer) or `store.delete(...)`
(`Store`) yourself; LangGraph gives you the durability, not the retention policy
on top of it. Worth stating plainly: this durability is also a retention
liability you now own.

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

Schema drift shows up first as a serializer complaint, not a crash. With
`langgraph-checkpoint` 4.2.0 a durable saver logs a "Deserializing unregistered
type" warning once per Pydantic model in state, and enum-typed fields log a
separate "Blocked deserialization" message when the allowlist is built from
model classes alone. The fix is an explicit allowlist covering models and enums,
plus a round-trip test that asserts type and value on read-back. What a blocked
enum does to the restored value wasn't inspected, so treat the allowlist as the
fix, not the explanation. It is also a deserialization-safety control: without
one, anyone who can write to the checkpoint database can cause arbitrary type
construction on read.

Structural versioning is the half without evidence here. Nothing persists a
thread under one topology and resumes it under another, so that risk is
described, not observed.

### 11.3 Security/guardrail primitives

[NOT DRAFTED] — see outline §13.3 (cross-references §9.4 once drafted).

### 11.4 Access control on `thread_id`

A `thread_id` is a capability, not an identity. Whoever can present it can read
the thread with `get_state`, patch it with `update_state`, or resume it with
`ainvoke(None, config)`, and nothing in the checkpointer binds a thread to a
user. An unguessable id, such as a client-minted UUID, makes abuse unlikely by
accident, but that is obscurity, not an access rule.

The consequence is sharper than "someone can read another user's run." A review
gate exists so that no run reaches its effect without approval, and approval is
just state: an `update_state` call that writes the reviewer's decision, which a
router then turns into a publish. Whoever can write that field can open the
gate. The interrupt stops the graph from proceeding on its own; it says nothing
about who is allowed to answer it.

Worth being precise about where the check goes: outside the graph, in the layer
that calls it. Before any `get_state`, `update_state` or resume, compare the
authenticated caller with the owner recorded for the thread, or derive the
thread id server-side from the caller so it can't be supplied at all. Neither
belongs in a node, because a node only runs after the decision to resume has
been made. A single-user tool can skip the check; a multi-user one can't, and
LangGraph won't remind you.

---

## 12. Cross-Worker Coordination at Scale

The patterns so far assume one execution at a time. A checkpointed graph that handles one user's request is durable. A fan-out of `Send` workers that share one process is concurrent.

Production is a different class of concurrency: multiple processes, multiple machines, competing for the same downstream resources and the same shared state. The mechanisms below don't come from LangGraph — they come from running any stateful workflow system in a multi-worker deployment. What the framework gives you is the substrate to attach them: the `Store` for shared state, the checkpointer as a coordination point, and the discipline of deterministic keys.

### 12.1 Circuit breaking across workers

A retry policy handles one node's failure in isolation. When every worker shares the same failing dependency — a search provider returning 503s, a model endpoint timing out — each node burns through its own retry budget independently, on its own clock, unaware that the other nineteen workers are doing the same thing. The result is a thundering herd of retries against an already-down service, followed by a wave of exhausted-attempt failures that all surface at once.

A circuit breaker sits in front of the dependency, not in the graph. The architectural shape is a shared failure counter, visible to every worker, that trips when a threshold is crossed and blocks further attempts until a cooldown expires.

Where LangGraph enters the picture is the `Store`. A circuit breaker needs cross-worker state — a "did this already fail N times" flag that worker A writes and worker B reads. The `Store` is a write-through key-value store that all graph executions in a deployment share (backed by Postgres, Redis, or equivalent). When worker A's search call fails, it writes `circuit:tavily = {status: open, opened_at: T, failure_count: N}` to the `Store`; worker B, checking the `Store` before dispatching its search, sees the open circuit and skips the call — either recording a research note and continuing with what it has, or pausing the run for human review.

The check happens twice. Once at the queue boundary, before the graph even starts — the queue worker reads circuit state from the `Store` and requeues the message with backoff rather than starting a run that will fail. And once at the entry of the research subgraph itself, for runs that were already in progress when the circuit tripped. The outer check avoids wasted graph overhead; the inner check protects against mid-run failures when a long graph started before the outage and hit the failing call after it began.

When the cooldown expires, a probe request — the first run that reaches the failing call after the circuit opens — is let through. If it succeeds, the circuit closes and normal execution resumes. If it fails, the timer resets. That probe is the only request that reaches the dependency during the outage, which is the entire point: one worker probes instead of twenty retrying simultaneously.

Circuit breaking and retry compose, not overlap. Retry handles the single transient failure that recovers in milliseconds. Circuit breaking handles the sustained outage where retrying is worse than not trying. A `RetryPolicy` with three attempts on a node whose dependency is behind a circuit breaker serves the first two attempts normally; the breaker only trips after a pattern of failures that the retry policy could not resolve.

### 12.2 Outbox leasing at scale

The outbox pattern (§6.2) gives each effect a stable key and a separate worker for delivery. In a single-worker deployment the delivery is straightforward: one process claims unprocessed rows, dispatches them, and marks them done.

With multiple workers, the claim itself needs coordination. Two workers reading the same outbox table would both try to deliver the same effect. The pattern is row-level leasing: each worker claims rows atomically before processing them.

```sql
UPDATE outbox
SET    worker_id = :worker, locked_until = now() + interval '30 seconds'
WHERE  id IN (
    SELECT id FROM outbox
    WHERE  status = 'queued'
       AND (locked_until IS NULL OR locked_until < now())
    ORDER BY created_at
    LIMIT 10
    FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

`FOR UPDATE SKIP LOCKED` is the key: it makes the database coordinate access, not the application. Each worker claims a batch of rows that nobody else has locked. A worker that crashes mid-delivery leaves rows with an expired `locked_until`, which the next polling cycle reclaims. Attempts are counted at claim time, not at submit time, so a row that repeatedly crashes its worker — because the downstream system is down, because the payload is malformed — increments toward a dead-letter threshold that eventually removes it from the queue.

The cost is visible in the failure mode: a handler that outlives its `locked_until` can be claimed by a second worker before the first finishes. The consumer of the effect must be idempotent, because at-least-once delivery with lease-based coordination is exactly that — at least once. The outbox buys you deterministic submission; it does not buy you exactly-once delivery.

### 12.3 Concurrent resume

`graph.update_state(...)` followed by `ainvoke(None, config)` resumes a paused thread. Nothing in the checkpointer prevents two callers from doing this simultaneously on the same `thread_id`.

The first caller proceeds. The second caller also proceeds. Both run the same nodes, both call the same effect gateway with the same keys — the keys are deterministic (§6.1), so the second submit is a no-op, and the database writes are idempotent. The risk is not corruption. It is double work: two synthesized digests produced, two LLM calls billed, two sets of validation results that no one will read.

The fix is an application-level advisory lock, keyed on `thread_id`, acquired before any resume and released when the run completes. Postgres `pg_advisory_xact_lock` is the natural fit when the checkpointer is Postgres: the lock auto-releases on transaction commit or abort, so a crashed worker does not leak a lock. The graph does not participate — the lock sits in the layer that calls `ainvoke`, not inside the graph itself.

---

## 13. Scope, Limitations and Verification

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

## 14. What's Next

[NOT DRAFTED] — see outline §11.

---

## Appendix: Running the Demo

[NOT DRAFTED] — see outline appendix. (The old M1 appendix in
`specs/ARCHIVE_ARTICLE_M1_DRAFT.md` describes a different CLI surface —
`telegram_gate`, `worker_a/b/c` — and should not be reused verbatim; the
current CLI/router action set is PUBLISH/REWRITE/AUGMENT/RESET/DISCARD.)

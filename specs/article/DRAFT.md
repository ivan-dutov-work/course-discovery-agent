# LangGraph for Agentic Workflows: Production Patterns

Draft in progress. Structure follows `specs/article/OUTLINE.md`. Sections below are
either drafted prose or an explicit placeholder naming what's pending — nothing is
silently missing. Placeholders are marked `[NOT DRAFTED]` so a partial read never
gets mistaken for a finished section.

Drafted so far: §2.2, §3.2, §3.3, §4, §5, §6, §7, §8.1–§8.4, §9, §10.1, §10.2, §10.4, §11, §12.
Everything else is outline-only — see `specs/article/OUTLINE.md` for what each
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
```

Instead of "last write wins", LangGraph concatenates every branch's contribution
into the shared key, or sums it for a counter. Three workers in the same
superstep each return their own slice of the results, and the graph appends all
three rather than keeping one.

A reducer belongs on the schema where the branches meet, and only there. The research
subgraph is a node of the outer graph, and when it returns, the outer graph merges its
channels with the outer graph's own reducer. A channel declared `operator.add` on both sides
is therefore appended to itself on every pass: the subgraph starts from the outer value, adds
its branches, and the outer reducer adds the whole result again. After two review rounds the
query ledger read 2, 4, 8. The fix is to keep the reducer on the subgraph's schema and leave the
outer field a plain list, so the outer graph overwrites with a result that already contains
the old value.

Worth being precise about what *isn't* reduced. A field written once per branch,
where each branch produces a self-contained value, needs no reducer: there is no
cross-branch merge to protect. A list of extracted candidates produced by a
single downstream node is a plain field. The reducer is for state a worker
*appends to*, not state a worker *replaces*. Conflating the two is an easy
mistake: not every list-typed field in a fan-out needs `operator.add`, only the
ones shared across branches.

### 2.3 `input_schema`/`output_schema` separation

A graph that is also a node needs a public contract narrower than its working state. By
default a compiled subgraph takes its whole schema as input and hands the whole of it back, so
every internal channel becomes the parent's business, and every reducer channel a second write.
`input_schema` and `output_schema` split the two: the subgraph reads only the keys the parent
passes in and returns only the keys declared as output.

```python
StateGraph(ResearchState, input_schema=ResearchInput, output_schema=ResearchOutput)
```

Five keys go in, five come out (two of them bookkeeping), and the other channels (plans, ledgers, candidate
lists) stay behind the boundary. The boundary limits what the subgraph reads and returns, not what its checkpoint stores: the full parent state is still in its start channel, which is one more reason to encrypt checkpoints (§10.1). The parent's state shrinks to what the rest of the graph
reads, and the caller of `invoke()` sees the output schema, not the internals.

Private does not mean persistent. A subgraph's channels start empty on every call unless it is
compiled with `checkpointer=True`, and a loop that re-enters it with the previous pass's plan
needs exactly that. The stateful form has a sharp edge: when the subgraph is the first task of
a resumed step, after a crash or an `update_state(as_node=...)`, LangGraph resumes it instead of
starting it with the new input, and the round silently does not run. A no-op node in front of
the subgraph looks like the fix and is not: a crash after the node commits reaches the same window. What works is a counter the parent stamps and the subgraph echoes back; if the echo is stale, the parent sends the work through again, and the resume flag is spent by then. State that outlives a pass needs the opposite care: the subgraph clears its own ledger on a fresh pass, with `Overwrite`, because a reducer can add but not subtract.

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
        Send("search_web_for_courses", {**state, "active_search_query": query})
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
own `StateGraph` without interrupt config, because that is the parent's concern,
and mount it as one node:

```python
builder.add_node("course_research", build_research_graph(checkpointer=True))
```

From the parent's perspective the pipeline is just another node, one that
happens to run many steps internally. This pays off wherever the parent routes:
every outcome that means "do the work again" (a rewrite, an augmentation, a
reset) points at one entry node, `start_research_pass`, and the parent never needs to know which
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
    interrupt_before=["await_human_review"],
)
```

A naive `input()` inside a node pauses a Python stack frame, which lives only as long as the process. `interrupt_before` pauses the *graph*: LangGraph checkpoints state before the named node runs and returns control to the caller. Resuming is `graph.update_state(...)` followed by `ainvoke(None, config)`, keyed by `thread_id`, not by "the same process happened to still be running when the human replied."

The backend decides what kind of pause you have. An in-memory saver survives a slow reviewer but not a restart; a database-backed saver lets a fresh process read the thread back and resume. Worth being precise about what that shows: reading a thread back proves the state is external, not that a crash is survivable. That depends on the write policy (§5.2). The cost of a durable pause is that every paused thread is a row someone owns until a reviewer answers it.

### 4.2 Dynamic `interrupt()`

`interrupt_before` is a compile-time decision: every run pauses at that node. `interrupt()`, called from inside a node, makes the pause depend on what the node just computed, so only runs with `uncertain` results stop for review.

The two answer different questions. The static form is a policy: no run reaches the effect without a human. The dynamic form is a filter: humans see the runs that need them. Conflating the two is how a human-in-the-loop system quietly becomes one where the human sees only what the model chose to escalate.

The practical decision rule: when the pause guards an irreversible effect, keep it static, because a conditional gate is only as trustworthy as its condition. When review is a quality tool and a miss is cheap, make it dynamic. A graph with an unconditional never-auto-publish rule doesn't need the second form.

### 4.3 UX gaps around the interrupt boundary

The router's five outcomes (PUBLISH, REWRITE, AUGMENT, RESET, DISCARD) are a deliberate step past the binary approve/reject gate most human-in-the-loop write-ups show; `update_state` before resuming lets a reviewer patch state, not just gate the run. What LangGraph leaves to the application:

- **Staleness.** A paused thread sits in the checkpointer indefinitely, with no TTL for a review nobody answers.
- **Notification.** `graph.get_state(config)` reports that a thread is parked; pushing that fact to a reviewer is app-layer.
- **Concurrent resume.** Nothing stops two callers resuming the same `thread_id` (§11.3).
- **Who may resume.** The same `update_state` call can write the feedback that opens the publish gate, so resuming is an access question as well as a UX one (§10.4).

### 4.4 `recursion_limit` as a structural safety net

The replanning loop has a domain budget, `max_research_iterations`. `recursion_limit` is the generic backstop underneath it: a cap on supersteps per invocation that raises `GraphRecursionError` when a routing bug or a mis-set budget would otherwise loop. Two independent guardrails, one domain-aware and one structural. The limit is passed in the run config, and its default should be set above the worst-case legitimate run, not tuned against the average one.

---

## 5. Durability: What a Checkpoint Does and Doesn't Give You

A checkpoint records the graph's state at a superstep boundary. Everything below follows from that fact: what survives a crash, what doesn't, and what you must build around the seam.

### 5.1 The checkpoint boundary

A checkpoint lands after every completed superstep. On resume, finished nodes are not re-run; only the interrupted node restarts from the top of its function. That gives you exactness between nodes and nothing inside one: an LLM call and a database write in the same function body are one atomic, uncheckpointed unit.

The checkpointer is a pluggable backend:

```python
# compile-time: pick your backend
checkpointer = AsyncPostgresSaver(...) if DATABASE_URL else MemorySaver()
graph = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["await_human_review"],
)
# run-time: checkpointing is automatic
result = await graph.ainvoke(input, {"configurable": {"thread_id": "..."}})
```

A subgraph without its own checkpointer inherits the parent's, so its internal steps are persisted too, under a runtime namespace you must discover, not construct from the thread ID. Compiled with `checkpointer=True`, it keeps its state across calls and uses a fixed namespace, the node's name.

### 5.2 Durability modes

The framework offers three write policies. `"sync"` writes before every step: the safest, at the cost of a dozen or so I/O operations per run. `"exit"` flushes only when the graph finishes or raises: the fewest writes, but a hard kill loses everything. `"async"` writes in the background without waiting for the checkpoint to land.

```python
# "sync" — safest, slowest
await graph.ainvoke(input, config, durability="sync")
# "exit" — fastest, loses granularity on hard kill
await graph.ainvoke(input, config, durability="exit")
```

These are not just performance levels. Under `"exit"`, a process killed during a node leaves no checkpoint and the run restarts from scratch; under `"sync"` the same kill preserves prior work and the resumed process skips completed nodes. `"async"` can't be tested for a hard kill, since whether the last background write lands is a race. For ordinary exceptions all three behave identically, because the exception is itself a superstep boundary.

What the coarse mode gives up on an ordinary failure is history: the per-step modes wrote twelve to fourteen checkpoints where `"exit"` wrote two, and all three resumed without redoing finished nodes. Fewer checkpoints means fewer points to replay from or inspect. The practical decision rule: durability is chosen per invocation, not per node, so a run that ends in an effect belongs under per-step writes, and only a run that can restart from scratch earns the cheaper mode.

### 5.3 Replay determinism

Because a node is an atomic unit, deterministic replay holds only at node boundaries. If one node calls an LLM and the next writes the result, the checkpoint between them guarantees the writer sees the output exactly as it was. Put both in one node and a crash after the LLM returns re-calls it, possibly getting a different answer.

The architectural response: split the caller from the writer. Nondeterministic work (LLM calls, search) goes in earlier nodes, checkpointed before its output reaches the effect, and effect nodes stay pure. Temperature 0 is not a substitute: providers change output between runs, and provider fallback (§8.1) produces the same divergence, as does a search provider whose snippets change between calls.

In a review flow this is what makes approval mean something. If the effect node reads the reviewed artifact from checkpointed state, what the human approved is what gets submitted; route crash recovery back through the generator and the approval attaches to text the reviewer never saw.

Not every replay difference matters. A `now()` stamp that differs between a run and its replay is harmless when the only reader is a tie-break; a `delivered` status that becomes `queued` on replay is harmless until something branches on it. Audit replay differences by what reads them.

### 5.4 Intra-node memoization

`@task` memoizes individual calls inside a node, so a resumed run reuses the stored result:

```python
@task
def parse_candidate(raw: dict) -> CourseCandidate:
    ...

async def extract_node(state: AgentState):
    # on resume these short-circuit instead of re-calling parse_candidate
    candidates = [parse_candidate(r) for r in state.results]
```

It narrows the window between a call and its effect without closing it: the memo is written asynchronously, so a hard kill can still run the function twice, and matching is by name and call position, not arguments, so a changed argument returns stale data. It does not cross subgraph boundaries, is scoped to a superstep (a replan loop gets fresh executions), and the stored result must deserialize, so a return type outside the msgpack allowlist (§10.2) comes back as a plain dict. These were observed on langgraph 1.1.2, and the subgraph case has no identified mechanism, so reproduce it before relying on it. The reliable seam is still the node boundary (§5.3).

### 5.5 Encapsulation cost: subgraph resume

A subgraph is one node to its parent. When a subgraph node is interrupted and
resumed, the entire subgraph re-runs — including already-successful parallel
workers that a standalone graph would skip. The parent has no visibility into
which subgraph workers completed.

The cost is spend, not correctness. Re-running a sibling worker repeats its
search or model call, but a crash resume does not double-count reducer-backed
channels: the number of completed calls still equals the number of unique
queries. (Across review rounds that re-enter the subgraph the same channels do
double-count; see `notes/02`.) Whether the repeat is noise or a budget problem depends on what one
worker costs.

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

Every checkpoint records one thread's state, which is what makes interrupt, resume and replay work. It is not long-term memory: it lasts as long as that thread's checkpoints do.

```python
# checkpointer — thread-scoped, wired at compile time, invisible to nodes
graph = builder.compile(checkpointer=checkpointer)

# store — cross-thread, requires explicit reads and writes in node code
store.get(("user_prefs", user_id), "profile")
store.put(("user_prefs", user_id), "profile", {"goal": new_goal})
```

The `Store` is compiled in beside the checkpointer:

```python
graph = builder.compile(checkpointer=checkpointer, store=store)

def update_profile(state: AgentState, *, store: BaseStore):
    store.put(("user_prefs", state["user_id"]), "profile", {"goal": new_goal})
```

Cross-thread memory (profiles, preferences, accumulated history) belongs in the `Store`, keyed by namespace instead of thread. A node receives it only if it asks. The key distinction: the checkpointer is implied by the graph and invisible to node code, while the `Store` is explicit reads and writes. Two threads for the same user read the same item, where the checkpointer gives each its own history.

Worth naming: a `Store` write from a node is an effect, and replay re-runs it. `put` is an upsert, so it is replay-safe when the value derives from checkpointed state and unsafe when the node reads a counter and writes it back incremented (§6.1), and it shares no transaction with the checkpoint (§6.2). A plain table keyed by user does the same job, and is often better when the profile needs joins or a transaction with domain data. The `Store` earns its place through namespacing, an optional embedding index, and one backend shared with the checkpointer. Retention is the other cost: the checkpointer keeps history until someone deletes it, and `Store` TTL is opt-in per item (§10.1).

---

## 6. Effects: Making Side Effects Survive Replay

The checkpointer resumes the graph. It does not make an external call happen once. Those are two separate recovery loops, and conflating them is the source of the most common durability failure in checkpointed workflows.

### 6.1 Idempotent writes

A node that writes to a database and then completes checkpoints after the write. If the process crashes between the two, the resumed run re-executes the write, and unless the write is naturally idempotent the result is a duplicate. The first defense is a schema where every write can be applied twice:

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

The key must be determinable before the node executes. A key generated at runtime (a new UUID) differs on every replay and defeats the purpose; the run's own identifier, minted before the graph starts, is the natural source.

The two key shapes encode different meanings for a re-run. A content-identity key with `DO UPDATE` treats a re-fetch as a correction, so replay converges on one row, at the cost of history: last write wins. A run-derived key with `DO NOTHING` treats a re-run as a no-op and keeps the first result, the right shape for events that must be recorded once and never revised.

The second defense is to fail closed. A write that hits an unexpected error propagates it rather than swallowing it into a log line, so the graph stops at the failed node's checkpoint with state preserved. Where the line falls is a design decision. A read-side dependency can often degrade: a search that fails permanently becomes a note on state, and the run continues with what it has. That is correct only if the degradation is visible where a human decides, so the note travels into the digest the reviewer approves (§4.3). A write cannot degrade this way, because a dropped write is a loss no reviewer sees. The practical decision rule: degrade where a fallback is still correct and visible; fail where it would hide a loss. Transient errors propagate so `RetryPolicy` can act on them, and only permanent ones become state (§7.1).

### 6.2 Outbox pattern

Not every effect can be made idempotent at the database: an email, a payment, or a third-party API without idempotency keys. The alternative is not to call the external system from the node at all. Nodes call an internal gateway that records an intention:

```python
# inside a node — never touches the external system
gateway.submit(Effect(key=f"publish:{run_id}", kind="publish_digest", payload=digest))
```

The key derives from the run identifier, so replay re-submits the same key and the store ignores it (`ON CONFLICT DO NOTHING`). The node's job shrinks to recording an intention, and delivery becomes someone else's problem, swappable from an in-process adapter in development to a database outbox with a worker in production.

Delivery is the second recovery loop. The worker leases rows so a crash releases them, counts attempts so a record that keeps killing its worker ends up dead-lettered, and backs off between tries. Worth being precise about the split: the checkpointer resumes the graph, the worker retries the effect, and replay never retries a delivery, because it only re-submits a key that already exists.

The cost shows up in the state contract. A boolean `published` becomes a status (`queued`, `delivered`, `dead`), because the node can no longer say "done", only "accepted". The atomicity boundary is also narrower than it looks: the outbox row can share a transaction with the node's own domain write, but never with the checkpointer, so the guarantee is at-least-once delivery plus idempotent submit, not transactional consistency.

### 6.3 Multiple effects in one node

When a node submits several effects, replay resubmits all of them, and each stable key is a no-op. Keys attach to an effect's identity, not its position in a list, because replay rebuilds the list from checkpointed state and may reorder it. With `DO NOTHING` the first payload wins: if replay recomputes a different payload for the same key, the store keeps the original and the difference disappears without a trace. That is what you want when the payload derives from checkpointed state, and a warning sign when it does not.

### 6.4 Operational gaps

The outbox's job is deterministic submission; a queue's job is reliable execution. Handler timeouts, heartbeat-extended leases and dead-letter exchanges are queue territory (SQS, RabbitMQ, Kafka), and a component that tries to do both is where the gaps appear. The outbox is for a few effects per run in one process. When a handler can outlive its lease and a second worker picks up the same effect, point the gateway at a real queue.

The consumer side is where the guarantee ends. At-least-once delivery moves the dedup boundary to the receiver: deterministic keys make duplicate submission harmless and do nothing for duplicate delivery unless the key travels with it as an idempotency key. Forwarding it is the one piece the outbox can do itself, and a receiver that ignores it turns the guarantee into a hope.

Dead rows and old data need an operator path outside the delivery loop, and retention needs its own answer, since nothing here expires on its own (§10.1). Requeueing a dead record is a human replay, safe for the same reason as everything else here: the key makes redelivery idempotent. The cost is in the attempt counter: resetting it gives a fresh budget and discards the failure history.

### 6.5 When a different architecture fits better

What the outbox approximates by hand (deterministic keys, leased rows, a worker with backoff) durable execution systems such as Temporal, Restate and DBOS give as a first-class property, persisting every step, not only the superstep boundary. If most of a workflow is long-running side-effect steps that must each run exactly once, they are the lighter fit: you stop approximating and start declaring. If it is mostly LLM calls with occasional effects, the checkpointer plus a lightweight outbox is lighter.

---

## 7. Node-Level Resilience: Retries, Timeouts, Caches

### 7.1 `RetryPolicy`

A node fails, and three mechanisms answer three different failure classes. `RetryPolicy` handles "the call failed": a transient error (timeout, connection reset, rate limit, server error) that might succeed if tried again. It belongs on the node at `add_node` time, not in the function body:

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

Which nodes get a policy depends on what retrying means for them. Read-only nodes are safe because retrying a read changes nothing. Mutating nodes are safe only when their operations are idempotent (§6.1, §6.2); without that foundation, retrying a writer duplicates the effect on every attempt.

Two traps defeat the policy. A node that catches every exception makes it a no-op, since the policy never sees the error; let transient errors propagate and convert only permanent failures into state. And an LLM client's own retries multiply with the policy's, so nodes with a `RetryPolicy` should disable client-level retry. When attempts are exhausted, the error propagates out of the graph, not into state, and the run stays resumable from its last checkpoint (§5.1).

A structured-output parse failure is the clearest permanent error. The classifier leaves it alone, correctly: at temperature 0 a second attempt mostly reproduces the same malformed output. The node fails closed, turning it into structured error state, and the failure becomes information about the provider (§8.4).

The contrast to hold: `RetryPolicy` means "the call failed"; a replanning loop means "the call succeeded but the result was insufficient." Conflating them is how a transient-error handler ends up hiding a bad answer, or a replanning loop absorbs a timeout.

### 7.2 Timeouts and async nodes

langgraph 1.1.2 has no per-node `timeout=` on `add_node`. Timeouts belong in the client the node calls, at the three boundaries where a graph touches an external system:

```python
# LLM call — passed to the chat client constructor
ChatOpenRouter(..., request_timeout=30_000)

# Network call — wrapped around the async I/O
results = await asyncio.wait_for(client.search(query), timeout=10)

# Database — connect and statement timeouts in the connection string
f"{db_url}?connect_timeout=5&options=-c statement_timeout=15000"
```

A timeout on a network call raises `TimeoutError`, which a `RetryPolicy` with a timeouts-and-connection-errors classifier catches. The timeout wraps the call, not the node.

Async nodes give real I/O concurrency for these bounded calls: a fan-out dispatches several `async def` workers and the event loop interleaves their network calls. The rule is to use `async def` when the node's work is I/O-bound, not because the graph requires it, and the benefit lasts only while nothing blocks the loop (§9.3).

### 7.3 `CachePolicy`

`CachePolicy`, passed per node to `add_node`, memoizes a node's output keyed on its input, so identical arguments skip re-execution. It differs from the intra-node memoization of `@task` (§5.4) and is not tied to memory: the compiled graph takes any `BaseCache` implementation, and LangGraph ships `InMemoryCache` and `RedisCache`.

Worth naming as a design choice: a framework cache for same-input reruns and a domain cache for known-valid results answer different questions. The first saves compute on redundant graph execution; the second saves an external round-trip. A `CachePolicy` on a cache-lookup node only guards against the graph calling the same node twice with the same state, a question of topology, not caching strategy.

### 7.4 Retry vs. circuit breaking

`RetryPolicy` handles transient failures, not a sustained outage: retrying a fully down API three times burns budget and latency, and in a fan-out it delays every sibling. LangGraph offers per-node retry and nothing at the graph level for a breaker, so each affected node exhausts its policy independently. The graph's resilience knobs are per-node, not per-dependency. A per-dependency breaker belongs one layer down (the client or gateway) or one layer up (a state field gating fan-out); §11.1 covers the cross-worker form.

---

## 8. Provider-Level Resilience and Spend Control

### 8.1 Where call-level resilience lives

The graph decides which node runs next. What happens when the model behind a node is down is not a graph concern: the node calls a Runnable, and resilience is a property of that Runnable. `Runnable.with_fallbacks()` (`langchain_core`, not LangGraph-specific) tries a list of runnables in order, and `ModelFallbackMiddleware` is the newer multi-model version. Either way it is "wrap the chain, call it like any other Runnable", with no LangGraph glue. A third option moves the fallback out of the process to a gateway (§8.2), so the LangChain side still sees one chat model.

### 8.2 OpenRouter as the single gateway

A gateway moves fallback out of the process. Every node builds its chat model through one factory, and the request carries an ordered `models: [...]` list; the gateway tries the next model on rate limits, downtime, context-length errors or moderation flags, and bills only the one that answered. The useful shape is a primary with a cheaper fallback from a different provider, so one provider's outage doesn't take the node down. `ChatOpenRouter` has no first-class field for the list, so it goes in through `model_kwargs`:

```python
ChatOpenRouter(
    model=PRIMARY_MODEL,
    temperature=0,
    model_kwargs={"models": [PRIMARY_MODEL, *FALLBACK_MODELS]},
    rate_limiter=rate_limiter,
    callbacks=[ServedModelLogger(node)],
)
```

Two behaviours are easy to get wrong. An invalid model ID is not a fallback trigger: the gateway rejects the request with a 400 before any routing, so the list protects against provider failures, not configuration mistakes. And because the fallback is invisible to the caller, you have to ask which model answered: log the response's `model_name` on every call and flag it when it differs from the primary. Otherwise a week of silent fallbacks looks identical to a week of healthy calls.

The factory also takes a per-node model. The course tagger asks for `openai/gpt-6-luna` and carries no fallback list, so it is a second provider behind the same gateway, and `ServedModelLogger` compares the served model against the one that node requested, not against the global primary. Not yet wired into the graph.

The argument for a gateway isn't fallback, which `.with_fallbacks()` gives you in-process. It is one bill, one rate-limit surface, and per-key spend caps across providers. The cost is a hop through a third party: every prompt now transits it, which matters for §10.1.

LiteLLM is the self-hosted version of the same idea, and it comes in two shapes that are easy to conflate. The SDK (`langchain-litellm`: `ChatLiteLLM`, `ChatLiteLLMRouter`) is a library inside your process: routing and fallback with no new service, and no shared state between workers. The Proxy is a standalone service that speaks the OpenAI protocol; LangChain reaches it through a plain `ChatOpenAI` pointed at its URL, so the graph again sees one chat model and never learns that a gateway exists. Keys, spend tracking and rate limits live in the Proxy, which is what makes it the usual production choice.

Worth being precise about what that choice buys. A hosted gateway is someone else's operations; the Proxy is yours. In production use it behaves like a project that is still moving fast: it has a long tail of open issues, spend and cached-token accounting has been the roughest edge, upgrades need to be read rather than applied, and its configuration needs hands-on management. Scaling it past one instance adds infrastructure: Redis for the shared state (rate-limit counters, routing and cooldown state, spend caches) that several Proxy instances have to agree on. None of this touches the graph, which is the point of the abstraction, but the bill for "one rate-limit surface" is an extra stateful service to run. The practical decision rule: take the hosted gateway until a data-residency or cost-control requirement forces the self-hosted one, and budget for operating it when it does.

### 8.3 Rate limiting

Chat models accept a `BaseRateLimiter` through `rate_limiter=`, which smooths the rate at which calls start. The limiter has to outlive the call: one built per call never sees the previous one, so the instance lives at module level and is shared by every node drawing on the same quota. Two boundaries: `InMemoryRateLimiter` is a token bucket inside one process and knows nothing about a second worker, so cross-process limiting belongs at the gateway or provider account; and it limits the rate of *starting* calls, saying nothing about whether they succeeded, which is the retry question in §7.1.

### 8.4 When the gateway isn't enough

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

The same blind spot hides a provider that changes its output shape. The gateway
still returns 200, so the only record of the change is the application's own
validation failures, and they count only if something counts them. Fed into a
failure rate over a window, they become the signal for the volumetric response:
when the rate crosses a threshold the primary is swapped for the alternative,
and a probe polls the old one for recovery. That is the circuit breaker of §11.1
with a parse failure as its failure event. The limit is that a rate catches
shape, not meaning. A model that keeps the schema but starts judging more
leniently produces no failures at all, and catching that takes an evaluation
set, not a breaker.

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
should say which model produced what. A gateway can log the served model (§8.2);
putting it in the review payload, or refusing to substitute without a human, is
application logic. This is the same boundary as §4.3: the interrupt is where a
degraded result has to become visible.

The cost of writing this yourself is that you now own the failure taxonomy (what
retries, what escalates, what degrades), the state that carries it, and the
tests. One part gets easier, not harder: a provider outage can't be summoned on
demand (§8.2), but a validation-triggered fallback can be forced with a stub
model, so that branch is testable in a way the gateway's never is.

The practical decision rule: keep the gateway for provider failure and add
custom logic only for the cases above, layered on top rather than instead. The
custom layer decides *whether* to fall back and to *what kind* of path; the
gateway still handles the provider-level retry underneath. Two layers, two
different failure classes, the same distinction as retry versus replanning
(§7.1).

### 8.5 Guardrails: the same wrap, aimed at the input

Schema validation, which §8.4 leans on, catches malformed output. It does not
catch well-formed output that an attacker shaped. Any node that builds a prompt
from text it did not write (search snippets, scraped descriptions, cached rows
that came from them) has that exposure, and neither LangChain nor LangGraph core
ships a guard for it. The shape is the same as fallback: a check around the
call, owned by the node. In a graph the placement is the decision. The screen
runs inside the node that assembles the prompt, once per untrusted item, so one
poisoned listing cannot steer the verdict on its neighbours.

The useful property of a typed-decision model here is that it returns a
probability, not prose. Control flow stays in code: the node maps the score to
three actions (pass, send the model structured fields only, skip the model for
that item) and the thresholds live in the node, not in a prompt. The digest line
says when free text was withheld, which makes the substitution visible at the
interrupt, the same rule as §8.4.

Worth being precise about what this is not. The screen reads the same hostile
text it judges, and its vendor says as much: content that argues for its own
classification can move the answer. Observed here: a small live set (28
author-written samples, one run) separated cleanly, including seven injections
aimed at the screen itself, but that set is easy next to an adaptive attacker,
and the thresholds are borrowed from an independent benchmark, not fitted. So it
is one layer, to sit beside deterministic checks, not a boundary.

Two costs follow from the placement. A screen error is treated as the middle
band, so an outage withholds free text instead of passing it or failing the
run, which is the opposite trade from PII redaction (§10.1), where a miss cannot
be undone. And the screen runs again on every execution of the node, so a replay
(§5.3) can land a borderline item in a different band; not tested here.

---

## 9. Observability

### 9.1 Tracing a durable graph

A graph run is a tree of nodes, and a trace is its natural picture. With an OpenTelemetry instrumentor for LangChain attached, every node becomes a span, and the workers of a `Send` fan-out (§3.2) appear as siblings under the node that dispatched them: which ran, how long each took, which one held up the join.

Durability breaks that tree in two places. The first is the review boundary. `interrupt_before` ends one invocation and the resume is a second, possibly hours later in another process (§4.1), and a single trace can't span the gap without a span held open for the whole wait. The alternative is two traces, one root span per invocation, both tagged with the same run ID, the second carrying a span *link* to the first. The run ID answers "everything this run did"; the link answers "which invocation came before".

```python
with tracer().start_as_current_span(
    name,
    attributes={"course.run_id": run_id, "course.resume": resume},
    links=[Link(previous)] if previous else None,
) as span:
```

The second is the outbox. An effect is submitted inside a node and delivered later by a worker with no shared call stack (§6.2), and trace context doesn't cross that gap on its own. The W3C `traceparent` is serialized into the effect payload at submit time and extracted in the worker, so delivery joins the trace of the submission that queued it.

Both splits matter for sampling. A ratio sampler decides per root, so it can keep the resume and drop the start, leaving a link that points nowhere; keying the decision on the run ID makes every segment agree. A tail-based collector buffers a trace for a bounded window and may decide before a delivery minutes later arrives. Which strategy fits depends on where the run's boundaries fall, not on the tracing library.

Replay is the other durability consequence. Recovery from a checkpoint re-executes the nodes after it (§5.3), so the recovered trace holds spans for work that already ran once, indistinguishable from a busy day unless the recovery root carries its checkpoint and reason. The tag also gives a sampler something to keep in full. This repo records everything with the SDK default and does not tag replays yet.

Worth naming: the destination is an architectural choice too. Spans carry prompts and state, the data §10.1 calls a liability, and a hosted tracing product adds a second third-party processor next to the model gateway (§8.2). The vendor-neutral SDK against your own collector keeps them in place, with content redacted unless an explicit switch turns capture on. The cost is that agent-aware views are yours to build; the split trace and the outbox context above were written by hand. The other cost is at the edges: exporter failure is fail-open, so a broken collector means missing traces, not an error, and a hard kill loses the last batch of spans.

### 9.2 `stream_mode`

Tracing is for the operator; `stream_mode` is for the client, which otherwise waits for the whole invocation or polls state. A list of modes makes the stream yield `(mode, chunk)` tuples, and `subgraphs=True` prefixes each with the namespace of the subgraph that produced it.

```python
async for namespace, mode, chunk in graph.astream(
    graph_input, config, stream_mode=["updates", "custom"], subgraphs=True
):
```

`"updates"` emits each node's state delta as it finishes, enough for a progress line per step. `"custom"` carries whatever a node chooses to write, for progress inside a long node. `"values"` sends the full state each step, and `"debug"`, `"tasks"` and `"checkpoints"` expose the execution machinery, including `@task` boundaries (§5.4). Choosing a mode is choosing how much of the graph's internals a client may see. The stream ends at the interrupt like any invocation, so streaming doesn't close the gaps around the review boundary (§4.3).

### 9.3 The other pillars: metrics, logs, profiles

Metrics answer "what is happening across runs". The instruments worth having tie back to the mechanisms above: a counter for degraded paths by component and reason is the metric form of the silent-fallback problem (§8.2), outbox backlog and oldest-pending age catch the queue that grows quietly (§6.4), and cache hit rate tests the core claim, since a cache-first agent with a near-zero hit rate is a slower uncached agent. Metrics carry their own flush gap: counters since the last export are lost on a hard kill, so a number that must be exact belongs in the database. Logs earn their place by carrying trace and span IDs on every line, so a grep hit opens its trace, under the same redaction rule.

Profiles are the pillar an I/O-bound agent needs least, and one failure makes them worth having. A node's time is mostly waiting on a model, which a profile can't explain. The exception is time a trace can't attribute, and the fan-out produces it: parallel `async def` workers overlap only while each yields to the event loop (§7.2). One blocking call inside a worker (a synchronous driver, an in-process similarity scan, a large dedup) holds the loop and the workers run one after another. The trace shows the symptom, sibling spans that should start together starting staggered, but not the cause, which is CPU time on the loop thread. A sampling profiler on the running process names the function, though it can't say which run it happened in; the trace supplies that half. Not observed here: the mock's synchronous catalog scan is handed to `asyncio.to_thread`, the usual fix once a profile finds the culprit. It is the failure to expect when a real provider client or a local embedding step replaces the mock.

---

## 10. What LangGraph Doesn't Own

### 10.1 GDPR / data retention

LangGraph has no built-in compliance tooling, which is a layer-of-abstraction point rather than a gap to fill. The checkpointer and `Store` (§5.6) are mechanically a database of user state: whatever personal data flows through the state object (queries, preferences, goals, course history) is persisted at every superstep. There is no retention policy, no redaction primitive and no right-to-erasure call. `Store` expiry is an opt-in per-item TTL, and deleting a user's data means `DELETE WHERE thread_id = ...` or `store.delete(...)` yourself. This durability is also a retention liability you now own.

The same gap shows up as housekeeping. The saver deletes by `thread_id` but cannot enumerate threads or expire them by age, so pruning means querying the checkpoint tables yourself. An age cutoff cannot tell a finished run from one parked at a review gate, so a long-lived review boundary (§4) and aggressive pruning pull in opposite directions. The outbox has the same growth problem on a smaller scale (§6.4).

Personal data in a graph is a property of channels, and the graph's shape makes that property trackable. State is a fixed set of channels and nodes are the only writers, so a fact declared once about a channel can be followed through every node that reads it. That gives one place to say what is sensitive, and the controls that protect it (redaction, encryption, erasure) can be checked against that declaration instead of each being remembered separately.

The abstraction has three parts.

**Label the channel.** The state contract carries the fact, as `Annotated` metadata. LangGraph treats only a callable in `Annotated` as a reducer, so the marker is inert to the runtime and visible to tooling. It says whose data the channel holds and whether it was scrubbed on the way in.

**Declare the node.** A node is opaque Python, so the graph cannot see what it does with what it reads. A declaration says: which channels it reads and writes, where data leaves the graph (a sink: a model call, a search API, a table), which inputs it scrubs before use, and which outputs are clean despite tainted input, with the reason. A sink states which labels it may receive.

```python
@dataclass(frozen=True)
class Pii:
    subject: str
    redacted: bool = True

@dataclass(frozen=True)
class Sink:
    name: str
    kind: str                      # "store" | "external"
    accepts: frozenset[str]        # labels this sink may receive

@dataclass(frozen=True)
class NodeFlow:
    reads: frozenset[str]
    writes: frozenset[str]
    sinks: tuple[Sink, ...]
    redacts: frozenset[str]        # inputs scrubbed before use
    declassifies: dict[str, str]   # output channel -> why it is clean
```

```python
class AgentState(TypedDict):
    user_query: Annotated[str, Pii(subject="user_id")]
    user_memory: Annotated[UserMemory | None, Pii(subject="user_id", redacted=False)]
    ...

LLM = external("llm:openrouter", accepts=frozenset({"subject"}))

"parse_user_request": flow(
    reads={"user_query", "feedback_history"}, writes={"search_filters"},
    sinks=(LLM,), redacts={"user_query", "feedback_history"},
),
"verify_course_claims": flow(
    reads={"search_filters", "user_memory", "deduplicated_courses"},
    writes={"valid_courses"},
    declassifies={"valid_courses": "catalog rows; memory only filters them"},
),
```

**Check the graph.** Labels propagate along the declared reads and writes: a node that reads a labelled channel labels everything it writes, unless a declassification says otherwise, and a scrubbed input drops its `raw` label. Three rules run over the result, as ordinary predicates. A sink must accept every label that reaches it. A store that receives personal data must be one the erasure path covers. Every node in the compiled graph must have a declaration. A failure names the path:

```
[sink-accepts] synthesizer: 'raw' data reaches external sink 'llm:openrouter':
  research_notes <- (find_known_courses) <- user_memory
```

Propagation is conservative by construction, which makes the declassification the reviewable unit: each one is a written claim that the checker accepts and cannot verify.

**Enforce at the boundaries the checker refers to.** Three controls sit outside the graph, and each has a placement rule that comes from how checkpointing works. Input is checkpointed before any node runs, so scrubbing has to happen where the initial state is built, and again wherever new input enters, such as reviewer feedback on resume; a node that scrubs its own input has already let the raw text into the thread's first checkpoint. Encryption needs a wrapper as well as a serializer, because the Postgres saver stores primitive channel values as plaintext JSON beside the encrypted blobs; a wrapper over the saver's public methods seals those strings and works over any saver. Erasure needs an index the framework does not keep: `adelete_thread` is the primitive, a thread ID is opaque, and once state is encrypted the owner is unreadable, so the application records thread and user at run start and erases through that table. Erasure walks an explicit list of tables, and the second rule above is checked against that list, which ties the declaration to the erasure path.

**Verify the claims with a canary.** A declaration is a claim about code the checker never reads. A test plants unique tokens in the query and in the stored profile, runs the graph to publish, and scans every checkpoint channel and the outbox payload; a token may appear only in channels the checker labelled. The failure this catches is a wrong declassification, typically a node that echoes its input into an output declared clean, as a search result that repeats its query does.

The cost shows in the limits. Labels are per channel, not per field. A canary finds verbatim copies, and a model that paraphrases defeats it. Reads and sinks are declared, not observed, and branches a test run doesn't drive are unchecked. The practical decision rule: this pays off when several people add nodes to the graph, and for a handful of nodes a review checklist is cheaper.

The actual compliance lever sits one layer down, at the model-provider boundary: zero-data-retention flags, DPAs, which region processes the request. The graph doesn't know what the Runnable under a node does with its payload. Not-logging and actively stripping PII are different guarantees, so check the specific provider's current docs before treating them as one.

### 10.2 Checkpoint and graph versioning

Two failure modes, named separately.

**State schema versioning.** If `AgentState` changes shape (a field renamed, a type changed), checkpoints persisted under the old shape may not deserialize under the new one. This is LangGraph-specific risk, because the checkpointer serializes the state object directly rather than through a migration-aware layer. It shows up first as a serializer complaint, not a crash: `langgraph-checkpoint` 4.2.0 logs a "Deserializing unregistered type" warning per Pydantic model in state, and enum-typed fields log a separate "Blocked deserialization" message when the allowlist is built from model classes alone. The fix is an explicit allowlist covering models and enums, plus a round-trip test that asserts type and value on read-back. It is also a deserialization-safety control: without one, anyone who can write to the checkpoint database can cause arbitrary type construction on read.

**Graph structural versioning.** Adding, removing or renaming a node between deploys can break an in-flight thread even when `AgentState` didn't change, because the checkpoint records *where* execution was parked and that position may no longer exist. A structural change is observable, though: a thread paused at the review gate under an earlier graph still reads back on Postgres, but approval after `update_state` does not publish. The resumed thread re-enters the research pass, repeating its searches, staging writes and model call, and parks at the gate again with a different digest. Nothing raises, which is what makes it expensive.

The place to catch it is before the resume. Stamp a schema version beside the thread's owner record when the run starts, compare it when the thread is resumed, and refuse a mismatch with an error that names both versions. Two tests keep the stamp honest: a snapshot of channel and node names that fails until the version is bumped, and one stored checkpoint per released version, each marked as resumable or refused. The cost is that a refused thread is lost work. The practical decision rule: drain in-flight runs before a deploy that bumps the version, and build a migration only when draining is not possible.

Drift at the model boundary, a provider returning a different shape, is a separate failure with a separate answer: a failure rate rather than a migration (§8.4).

### 10.3 Security/guardrail primitives

LangGraph ships no security primitives, so what the graph decides is placement.
Two controls share the wrap-and-own-it shape and differ in where they must sit.
PII redaction belongs at the boundary that builds initial state, before the
first checkpoint, because the checkpointer persists whatever state holds (§10.1).
The injection screen belongs in the node that builds a prompt from web text,
before the model call, and its verdict does not need to persist (§8.5). Neither
covers the other, and neither stops a caller from resuming someone else's thread,
which is the third control (§10.4).

### 10.4 Access control on `thread_id`

A `thread_id` is a capability, not an identity. Whoever presents it can read the thread with `get_state`, patch it with `update_state`, or resume it with `ainvoke(None, config)`, and nothing in the checkpointer binds a thread to a user. An unguessable UUID makes abuse unlikely by accident, which is obscurity, not an access rule.

The consequence is sharper than reading another user's run. A review gate exists so no run reaches its effect without approval, and approval is just state: an `update_state` call that writes the reviewer's decision, which a router turns into a publish. Whoever can write that field can open the gate. The interrupt stops the graph from proceeding on its own; it says nothing about who may answer it.

The check goes outside the graph, in the layer that calls it: compare the authenticated caller with the thread's recorded owner before any `get_state`, `update_state` or resume, or derive the thread ID server-side so it can't be supplied. It cannot live in a node, because a node only runs after the decision to resume has been made.

The owner record needs a home the checkpointer doesn't provide. Erasure already forced one: an application table mapping each `thread_id` to its user, written when the run starts (§10.1). The same table answers the access question. A guard in front of the resume path looks the thread up, compares the owner with the caller, and refuses before `update_state` writes anything:

```python
authorize_thread(user_id, run_id)
await graph.aupdate_state(config, {"manager_feedback": feedback})
```

Two details matter more than the comparison. An unknown thread and someone else's thread raise the same error, so the guard can't be used to probe which IDs exist. And registering an ID that is already taken keeps the original owner: a caller who guesses or replays a `thread_id` gets no ownership from it. The cost is that the check is only as strong as the caller's identity, which the graph never sees, and that it runs on the paths that call it: a second entry point that skips the guard reopens the gate. A single-user tool can skip all of this; a multi-user one can't, and LangGraph won't remind you.

---

## 11. Cross-Worker Coordination at Scale

The patterns so far assume one execution at a time. A checkpointed graph that handles one user's request is durable. A fan-out of `Send` workers that share one process is concurrent.

Production is a different class of concurrency: multiple processes, multiple machines, competing for the same downstream resources and the same shared state. The mechanisms below don't come from LangGraph — they come from running any stateful workflow system in a multi-worker deployment. What the framework gives you is the substrate to attach them: the `Store` for shared state, the checkpointer as a coordination point, and the discipline of deterministic keys.

### 11.1 Circuit breaking across workers

A retry policy handles one node's failure in isolation. When every worker shares the same failing dependency — a search provider returning 503s, a model endpoint timing out — each node burns through its own retry budget independently, on its own clock, unaware that the other nineteen workers are doing the same thing. The result is a thundering herd of retries against an already-down service, followed by a wave of exhausted-attempt failures that all surface at once.

A circuit breaker sits in front of the dependency, not in the graph. The architectural shape is a shared failure counter, visible to every worker, that trips when a threshold is crossed and blocks further attempts until a cooldown expires.

Where LangGraph enters the picture is the `Store`. A circuit breaker needs cross-worker state — a "did this already fail N times" flag that worker A writes and worker B reads. The `Store` is a write-through key-value store that all graph executions in a deployment share (backed by Postgres, Redis, or equivalent). When worker A's search call fails, it writes `circuit:tavily = {status: open, opened_at: T, failure_count: N}` to the `Store`; worker B, checking the `Store` before dispatching its search, sees the open circuit and skips the call — either recording a research note and continuing with what it has, or pausing the run for human review.

The check happens twice. Once at the queue boundary, before the graph even starts — the queue worker reads circuit state from the `Store` and requeues the message with backoff rather than starting a run that will fail. And once at the entry of the research subgraph itself, for runs that were already in progress when the circuit tripped. The outer check avoids wasted graph overhead; the inner check protects against mid-run failures when a long graph started before the outage and hit the failing call after it began.

When the cooldown expires, a probe request — the first run that reaches the failing call after the circuit opens — is let through. If it succeeds, the circuit closes and normal execution resumes. If it fails, the timer resets. That probe is the only request that reaches the dependency during the outage, which is the entire point: one worker probes instead of twenty retrying simultaneously.

Circuit breaking and retry compose, not overlap. Retry handles the single transient failure that recovers in milliseconds. Circuit breaking handles the sustained outage where retrying is worse than not trying. A `RetryPolicy` with three attempts on a node whose dependency is behind a circuit breaker serves the first two attempts normally; the breaker only trips after a pattern of failures that the retry policy could not resolve.

### 11.2 Outbox leasing at scale

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

### 11.3 Concurrent resume

`graph.update_state(...)` followed by `ainvoke(None, config)` resumes a paused thread. Nothing in the checkpointer prevents two callers from doing this simultaneously on the same `thread_id`.

The first caller proceeds. The second caller also proceeds. Both run the same nodes, both call the same effect gateway with the same keys — the keys are deterministic (§6.1), so the second submit is a no-op, and the database writes are idempotent. The risk is not corruption. It is double work: two synthesized digests produced, two LLM calls billed, two sets of validation results that no one will read.

The fix is an application-level advisory lock, keyed on `thread_id`, acquired before any resume and released when the run completes. Postgres `pg_advisory_xact_lock` is the natural fit when the checkpointer is Postgres: the lock auto-releases on transaction commit or abort, so a crashed worker does not leak a lock. The graph does not participate — the lock sits in the layer that calls `ainvoke`, not inside the graph itself.

---

## 12. Scope, Limitations and Verification

This is not a production case study. Nothing here comes from operating the agent under real traffic: no load data, no incident log, no cost-at-scale numbers. It demonstrates LangGraph mechanics on a domain-shaped example, evaluated by reading the code and running it locally, and it describes what the mechanisms *are* and where they attach, not how they behaved under load.

Search is mocked, on purpose. Course listing pages are mostly JS/PHP-rendered and poorly indexed, so a real provider would add operational noise (rate limits, flaky results, API keys) unrelated to the article's subject. "Production would swap this one class" is a claim about the seam (`TavilyClient`'s `search()` signature), not a tested migration.

How the claims were checked. One contract suite runs against both outbox stores; Postgres integration tests cover replay, parallel workers and concurrent submits; a SIGKILL child-process test covers crash recovery for each durability mode; app-level tests cover the trace split, outbox propagation and redaction, canary tests plant unique tokens and scan every checkpoint channel and the outbox payload, and the CLI was run live against Jaeger. Where it matters the text says whether a finding was observed here, read from source, or taken from docs.

Known untested or unbuilt:

- `durability="async"` under a hard kill, a changed graph topology against a persisted thread, and strict msgpack mode for a type outside the schema.
- Circuit breaking across workers (§11.1) and the advisory lock (§11.3): designs, not code.
- Replay tagging and trace sampling (§9.1): described, not implemented.
- The provider fallback path (§8.2), asserted in the outgoing request and never triggered live; more than one effect per node (§6.3); and an approval surviving crash recovery end to end (§5.3), which holds by construction.
- Everything under real load.

The delivery handler is a stub that prints; the submit, lease, retry and dead-letter machinery around it is what the tests exercise. The outbox row and the domain write are separate transactions, not the shared one §6.2 describes as possible. Model tiering by task difficulty is out of scope: each node calls whatever Runnable it is bound to, a one-line fact, not a feature.

---

## 13. What's Next

[NOT DRAFTED] — see outline §13.

---

## Appendix: Running the Demo

[NOT DRAFTED] — see outline appendix. (The old M1 appendix in
`archive/ARTICLE_M1_DRAFT.md` describes a different CLI surface —
`telegram_gate`, `worker_a/b/c` — and should not be reused verbatim; the
current CLI/router action set is PUBLISH/REWRITE/AUGMENT/RESET/DISCARD.)

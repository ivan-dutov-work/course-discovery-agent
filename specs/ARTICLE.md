# LangGraph for Agentic Workflows: Production Patterns

Draft in progress. Structure follows `specs/ARTICLE_OUTLINE.md`. Sections below are
either drafted prose or an explicit placeholder naming what's pending — nothing is
silently missing. Placeholders are marked `[NOT DRAFTED]` so a partial read never
gets mistaken for a finished section.

Drafted so far: §2.2, §3.2, §3.3, §4, §5, §6, §7, §8.1–§8.4, §9, §10.1, §10.2, §10.4, §11, §12.
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

The checkpointer is a pluggable backend chosen at compile time (`AsyncPostgresSaver` or `MemorySaver`); checkpointing is then automatic. A subgraph without its own checkpointer inherits the parent's, so its internal steps are persisted too, under a runtime namespace you must discover, not construct from the thread ID.

### 5.2 Durability modes

The framework offers three write policies. `"sync"` writes before every step: the safest, at the cost of a dozen or so I/O operations per run. `"exit"` flushes only when the graph finishes or raises: the fewest writes, but a hard kill loses everything. `"async"` writes in the background without waiting for the checkpoint to land.

```python
await graph.ainvoke(input, config, durability="sync")   # safest, slowest
await graph.ainvoke(input, config, durability="exit")   # fastest, no mid-run recovery
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
def parse_candidate(raw: dict) -> CourseCandidate: ...

async def extract_node(state: AgentState):
    candidates = [parse_candidate(r) for r in state.results]  # short-circuits on resume
```

It narrows the window between a call and its effect without closing it: the memo is written asynchronously, so a hard kill can still run the function twice, and matching is by name and call position, not arguments, so a changed argument returns stale data. It does not cross subgraph boundaries, is scoped to a superstep (a replan loop gets fresh executions), and the stored result must deserialize, so a return type outside the msgpack allowlist (§10.2) comes back as a plain dict. These were observed on langgraph 1.1.2, and the subgraph case has no identified mechanism, so reproduce it before relying on it. The reliable seam is still the node boundary (§5.3).

### 5.5 Encapsulation cost: subgraph resume

A subgraph is one node to its parent. When it is interrupted and resumed, the entire subgraph re-runs, including parallel workers that already succeeded, and the parent cannot see which. The cost is spend, not correctness: reducer-backed channels don't double-count, so completed calls still equal unique queries. Whether the repeat is noise or a budget problem depends on what one worker costs.

The cause is documented. Subgraph task IDs derive from the parent checkpoint ID, which changes when the parent forks for resume, so the mechanism that re-attaches cached writes (`skip_done_tasks`) finds nothing for the new instance. Open issues #6792 and #8458, and #6050 whose fix doesn't cover every case, track it.

Flattening the graph fixes it at the cost of exposing internal topology. Within the framework, the options are partial:

- **State-based dedup.** The worker short-circuits when its query is already in a reducer-backed `completed_queries` list. It avoids the expensive call, not the scheduling.
- **`checkpointer=True` on the subgraph.** State accumulates across calls, but an interrupted node still re-executes, and per-thread subgraphs collide under a parallel `Send` fan-out.
- **Interrupt before the subgraph, not inside it.** The subgraph runs atomically and the reviewer judges the whole result, at the cost of mid-pipeline visibility.

None closes the gap to a flat graph's node-level skip, because the namespace itself is the problem. The practical decision rule: if subgraph resume is rare, re-running workers is noise and encapsulation wins. If it happens on every run (always-on review inside the subgraph), flatten or interrupt before the subgraph.

### 5.6 Long-term vs. thread-scoped memory

Every checkpoint records one thread's state, which is what makes interrupt, resume and replay work. It is not long-term memory: it lasts as long as that thread's checkpoints do.

```python
graph = builder.compile(checkpointer=checkpointer, store=store)

def update_profile(state: AgentState, *, store: BaseStore):
    store.put(("user_prefs", state["user_id"]), "profile", {"goal": new_goal})
```

Cross-thread memory (profiles, preferences, accumulated history) belongs in the `Store`, keyed by namespace instead of thread. The key distinction: the checkpointer is implied by the graph and invisible to node code, while the `Store` is explicit reads and writes. Two threads for the same user read the same item, where the checkpointer gives each its own history.

Worth naming: a `Store` write from a node is an effect, and replay re-runs it. `put` is an upsert, so it is replay-safe when the value derives from checkpointed state and unsafe when the node reads a counter and writes it back incremented (§6.1), and it shares no transaction with the checkpoint (§6.2). A plain table keyed by user does the same job, and is often better when the profile needs joins or a transaction with domain data. The `Store` earns its place through namespacing, an optional embedding index, and one backend shared with the checkpointer. Retention is the other cost: the checkpointer keeps history until someone deletes it, and `Store` TTL is opt-in per item (§10.1).

---

## 6. Effects: Making Side Effects Survive Replay

The checkpointer resumes the graph. It does not make an external call happen once. Those are two separate recovery loops, and conflating them is the source of the most common durability failure in checkpointed workflows.

### 6.1 Idempotent writes

A node that writes to a database and then completes checkpoints after the write. If the process crashes between the two, the resumed run re-executes the write, and unless the write is naturally idempotent the result is a duplicate. The first defense is a schema where every write can be applied twice:

```sql
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
gateway.submit(Effect(key=f"{run_id}:publish_digest", kind="publish_digest", payload=digest))
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
retry = RetryPolicy(
    max_attempts=3, initial_interval=0.5, backoff_factor=2.0, jitter=True,
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
ChatOpenRouter(..., request_timeout=30_000)                      # LLM
results = await asyncio.wait_for(client.search(query), timeout=10)  # network
f"{db_url}?connect_timeout=5&options=-c statement_timeout=15000"    # database
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

[NOT DRAFTED] — §8.5 (guardrails) is pending; see outline §8. It starts from the limit §8.4 leaves open: schema validation catches malformed output, not well-formed output that an attacker shaped.

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

The argument for a gateway isn't fallback, which `.with_fallbacks()` gives you in-process. It is one bill, one rate-limit surface, and per-key spend caps across providers. The cost is a hop through a third party: every prompt now transits it, which matters for §10.1.

### 8.3 Rate limiting

Chat models accept a `BaseRateLimiter` through `rate_limiter=`, which smooths the rate at which calls start. The limiter has to outlive the call: one built per call never sees the previous one, so the instance lives at module level and is shared by every node drawing on the same quota. Two boundaries: `InMemoryRateLimiter` is a token bucket inside one process and knows nothing about a second worker, so cross-process limiting belongs at the gateway or provider account; and it limits the rate of *starting* calls, saying nothing about whether they succeeded, which is the retry question in §7.1.

### 8.4 When the gateway isn't enough

A gateway covers the common case, and most graphs should stop there. It implements one idea: when a call fails at the provider, send the same request to the next model. The deciding question isn't "do we need fallback" but "what counts as a failure, and what is an acceptable substitute." A gateway answers both with a fixed default: failure is a provider error, and the substitute is another model. Three requirements break that default because the answer lives in graph state.

**A wrong answer is worse than a failed call.** The gateway sees a 200 and moves on; it can't know the output failed a schema check, cited no evidence, or contradicts the user's filters. When correctness is what the business is buying, the trigger for fallback is the application's own validation, and the fallback is an escalation, not an outage substitute:

```python
result = await primary.ainvoke(payload)
if not meets_evidence_bar(result):
    result = await stronger.ainvoke(payload)
```

The same blind spot hides a provider that changes its output shape. Counted over a window, validation failures become the signal for a breaker with a parse failure as its failure event (§11.1). The limit is that a rate catches shape, not meaning: a model that keeps the schema but judges more leniently produces no failures, and catching that takes an evaluation set.

**Scores must be comparable within a run.** A gateway switches models per request. If a rate limit moves half a batch of candidates to another model, the scores aren't on one scale and the ranking is quietly wrong. The fallback decision belongs to the run: pin one model per batch, or fail the whole batch over together.

**The budget or deadline belongs to the run, not the call.** Per-key caps bound a key. "No single run may cost more than X" or "the digest must arrive within N seconds" spans several calls and nodes, and the decision to drop to a cheaper model or skip an optional step needs the run's accumulated cost and elapsed time, which live in state.

Other cases have the same shape: residency rules keyed on tenant, a stale-but-correct cached result preferred over a weaker model's fresh one, and a silent substitution that a reviewer approving output should have seen (§4.3). Each is a decision the gateway can't see.

The cost of writing this yourself is owning the failure taxonomy (what retries, what escalates, what degrades), the state that carries it, and the tests. One part gets easier: a provider outage can't be summoned on demand, but a validation-triggered fallback can be forced with a stub model. The practical decision rule: keep the gateway for provider failure and add custom logic only for these cases, layered on top. The custom layer decides *whether* to fall back and to what kind of path; the gateway handles the provider-level retry underneath. Two layers, two failure classes, the same distinction as retry versus replanning (§7.1).

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

The actual compliance lever sits one layer down, at the model-provider boundary: zero-data-retention flags, DPAs, which region processes the request. The graph doesn't know what the Runnable under a node does with its payload. Not-logging and actively stripping PII are different guarantees, so check the specific provider's current docs before treating them as one.

### 10.2 Checkpoint and graph versioning

Two failure modes, named separately.

**State schema versioning.** If `AgentState` changes shape (a field renamed, a type changed), checkpoints persisted under the old shape may not deserialize under the new one. This is LangGraph-specific risk, because the checkpointer serializes the state object directly rather than through a migration-aware layer. It shows up first as a serializer complaint, not a crash: `langgraph-checkpoint` 4.2.0 logs a "Deserializing unregistered type" warning per Pydantic model in state, and enum-typed fields log a separate "Blocked deserialization" message when the allowlist is built from model classes alone. The fix is an explicit allowlist covering models and enums, plus a round-trip test that asserts type and value on read-back. It is also a deserialization-safety control: without one, anyone who can write to the checkpoint database can cause arbitrary type construction on read.

**Graph structural versioning.** Adding, removing or renaming a node between deploys can break an in-flight thread even when `AgentState` didn't change, because the checkpoint records *where* execution was parked and that position may no longer exist. This half is described, not observed.

Drift at the model boundary, a provider returning a different shape, is a separate failure with a separate answer: a failure rate rather than a migration (§8.4).

### 10.3 Security/guardrail primitives

[NOT DRAFTED] — see outline §10.3 (cross-references §8.5 once drafted).

### 10.4 Access control on `thread_id`

A `thread_id` is a capability, not an identity. Whoever presents it can read the thread with `get_state`, patch it with `update_state`, or resume it with `ainvoke(None, config)`, and nothing in the checkpointer binds a thread to a user. An unguessable UUID makes abuse unlikely by accident, which is obscurity, not an access rule.

The consequence is sharper than reading another user's run. A review gate exists so no run reaches its effect without approval, and approval is just state: an `update_state` call that writes the reviewer's decision, which a router turns into a publish. Whoever can write that field can open the gate. The interrupt stops the graph from proceeding on its own; it says nothing about who may answer it.

The check goes outside the graph, in the layer that calls it: compare the authenticated caller with the thread's recorded owner before any `get_state`, `update_state` or resume, or derive the thread ID server-side so it can't be supplied. It cannot live in a node, because a node only runs after the decision to resume has been made. A single-user tool can skip it; a multi-user one can't, and LangGraph won't remind you.

---

## 11. Cross-Worker Coordination at Scale

Everything so far assumes one execution at a time. Production runs many processes against the same downstream services and the same shared state, and the coordination that requires does not come from LangGraph. What the framework provides is the substrate to attach it to: the `Store` for shared state, the checkpointer as a coordination point, and deterministic keys.

### 11.1 Circuit breaking across workers

A `RetryPolicy` sees one node's failure. When twenty workers share a failing dependency, each burns its own retry budget on its own clock, and the outage turns into a herd of retries followed by a wave of exhausted-attempt failures.

The architectural response is a breaker in front of the dependency, not in the graph: a failure counter visible to every worker, tripped at a threshold, blocking attempts until a cooldown expires. The `Store` is where LangGraph enters, since worker A must write what worker B reads.

```python
store.put(("circuit", "search"), "state", {"status": "open", "opened_at": now, "failures": n})
```

The check runs twice: at the queue boundary, so a doomed run is requeued instead of started, and at the entry of the research subgraph, for runs already in flight when the circuit tripped. After the cooldown one probe is let through, so a single worker tests the dependency instead of twenty. The two mechanisms compose: retry absorbs the blip that recovers in milliseconds, the breaker handles the outage where retrying is worse than not trying.

### 11.2 Outbox leasing

The outbox (§6.2) gives an effect a stable key and a separate delivery worker. With several workers, the claim needs coordination, and the database does it: rows are claimed atomically under a time-limited lease.

```sql
SELECT id FROM outbox
WHERE status = 'queued' AND (locked_until IS NULL OR locked_until < now())
ORDER BY created_at LIMIT 10
FOR UPDATE SKIP LOCKED
```

A worker that crashes leaves an expired lease for the next poll to reclaim, and attempts counted at claim time push a repeatedly crashing row toward the dead-letter threshold. The cost is visible in one failure: a handler that outlives its lease can be claimed by a second worker while the first is still running. Delivery is at-least-once, so the consumer of the effect must be idempotent.

### 11.3 Concurrent resume

Nothing in the checkpointer stops two callers from resuming the same `thread_id` at once. Deterministic keys (§6.1) make the second run's effects no-ops, so the risk is not corruption but double work: two digests synthesized, two sets of LLM calls billed.

The fix sits above the graph. The caller takes an advisory lock keyed on `thread_id` before resuming, and Postgres advisory locks release when the connection dies, so a crashed worker does not leak one.

---

## 12. Scope, Limitations and Verification

This is not a production case study. Nothing here comes from operating the agent under real traffic: no load data, no incident log, no cost-at-scale numbers. It demonstrates LangGraph mechanics on a domain-shaped example, evaluated by reading the code and running it locally, and it describes what the mechanisms *are* and where they attach, not how they behaved under load.

Search is mocked, on purpose. Course listing pages are mostly JS/PHP-rendered and poorly indexed, so a real provider would add operational noise (rate limits, flaky results, API keys) unrelated to the article's subject. "Production would swap this one class" is a claim about the seam (`TavilyClient`'s `search()` signature), not a tested migration.

How the claims were checked. One contract suite runs against both outbox stores; Postgres integration tests cover replay, parallel workers and concurrent submits; a SIGKILL child-process test covers crash recovery for each durability mode; app-level tests cover the trace split, outbox propagation and redaction, and the CLI was run live against Jaeger. Where it matters the text says whether a finding was observed here, read from source, or taken from docs.

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
`specs/ARCHIVE_ARTICLE_M1_DRAFT.md` describes a different CLI surface —
`telegram_gate`, `worker_a/b/c` — and should not be reused verbatim; the
current CLI/router action set is PUBLISH/REWRITE/AUGMENT/RESET/DISCARD.)

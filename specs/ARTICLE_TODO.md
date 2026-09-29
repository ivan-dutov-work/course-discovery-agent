# Article TODO — Production LangGraph Patterns to Cover

Running list of principles the article should cover, beyond the base research-agent
walkthrough in `ARTICLE_OUTLINE.md`. Each item names the LangGraph/LangChain
mechanism, why it matters in production, and where in this repo it can be shown or
added. Verified against current docs where noted — do not restate an unverified item
as fact in the article without checking it first.

Check items off as they're written into the article draft (not just implemented in
code — implementation and article coverage are tracked separately).

## Status snapshot — implemented in code vs. next up

Implemented and tested, **not yet written into ARTICLE.md** (details in the items
below). Integration tests need `docker compose up -d` and
`TEST_DATABASE_URL=postgresql://course:course@localhost:55432/course_discovery`.

- Replay-safe writes: `migrations/002_idempotent_writes.sql`, `ON CONFLICT` on
  `course_evidence` (content key) and `recommendation_events` (`idempotency_key`).
- Local stack: `docker-compose.yml` (pgvector Postgres on host port 55432) and
  `tests/test_integration_postgres.py`.
- Replay coverage: outer-graph checkpoints, subgraph checkpoints, `interrupt_before`
  inside the subgraph, and a fresh `AsyncPostgresSaver` + graph reading the thread
  back and resuming through publish.
- **Outbox behind a port** (`course_discovery/effects/`, `migrations/003_outbox.sql`):
  `EffectGateway` port, `InMemoryOutboxStore` and `PostgresOutboxStore` behind one
  `OutboxStore` protocol, `OutboxWorker` (leases, backoff, dead-letter),
  `InlineGateway` (default) and `OutboxGateway` (`EFFECT_GATEWAY=outbox`, worker run
  with `python -m course_discovery.effects`). `publish_node` submits
  `publish:{run_id}`; `published: bool` is now `publish_status`
  (`queued | delivered | dead`). One contract suite (`tests/outbox_contract.py`) runs
  against both stores; Postgres adds parallel workers and concurrent-submit tests.
- **Write failures surface.** `record_feedback` and `upsert_courses` re-raise after
  logging; `record_feedback` creates the `users` row first, so the FK failure is gone.
- **`RetryPolicy` on idempotent nodes** (`course_discovery/resilience.py`):
  `gateway`, `tavily_search_worker`, `course_cache_upsert`, `publish_node`,
  `user_memory_update`, retrying only `is_transient` errors.
- **Client-level timeouts:** LLM request timeout, `asyncio.wait_for` on search,
  Postgres connect and statement timeouts. No node-level timeout (see below).
- **Durable checkpointer in the app path:** `open_checkpointer()` picks
  `AsyncPostgresSaver` when `DATABASE_URL` is set, else `MemorySaver`; both use the
  msgpack allowlist, now covering enums.
- Durability modes tested (`sync`, `async`, `exit`) against Postgres.

**Still open:**

- Nothing kills a process. Every "recovery" test is a new saver/graph in the same OS
  process, or a raised exception.
- Circuit breaker, `CachePolicy`, `Store` vs. checkpointer: untouched.
- Same-transaction outbox write with `recommendation_events` is not done; the
  outbox row and the domain write are separate transactions.
- Handler for `publish_digest` still prints to stdout; only the delivery machinery
  is real.
- Read paths (`load_user_memory`, cache lookup) still swallow DB errors and return
  empty results.
- Strict-mode behaviour for a type outside the state schema is untested.

## Already in the code — foreground these, don't just add new stuff

- [x] **Reducers for safe concurrent state merges.** `tavily_results`,
      `extracted_candidates`, `research_notes`, `completed_queries` all use
      `Annotated[..., add]`-style reducers so parallel `Send` workers don't clobber
      each other. Show the merge conflict this prevents. *(Drafted in ARTICLE.md §2.2 —
      correction: `extracted_candidates` is not actually reducer-backed, it's a plain
      overwritten list; §2.2 uses `tavily_results`/`completed_queries`/`research_notes`/
      `tavily_calls` as the real examples instead.)*
- [x] **`interrupt_before` as a durability-backed HIL boundary.** Contrast with a
      naive `input()` prompt: the graph actually checkpoints and can resume in a new
      process, after a crash, or from a different machine — not just "pause the
      loop." *(Drafted in ARTICLE.md §4.1 — correction: compiled checkpointer is
      `MemorySaver`, in-memory only; §4.1 states plainly that "resume from a different
      machine" needs a durable checkpointer this repo doesn't currently wire in.)*
      *(Verified since, test-only: `tests/test_integration_postgres.py` runs the graph
      on `AsyncPostgresSaver`, then a fresh saver + fresh graph reads the thread back
      from Postgres, replays, and resumes through approve -> publish. The default
      `build_graph()` is still `MemorySaver`; `checkpointer=` is now a parameter. This
      is a new saver in the same OS process, not a killed process — don't claim crash
      recovery. §4.1 wording is still accurate for the default; update it if the
      article shows the durable variant.)*
- [x] **Subgraphs for encapsulation.** `research_graph` compiled and mounted as a
      single node in `outer_graph`. The outer graph doesn't know or care about the
      research agent's internal node count. *(Drafted in ARTICLE.md §3.3.)*
      **Checkpoint behavior to add (observed on langgraph 1.1.2, not from docs):**
      the outer graph's history only shows checkpoints around `research_agent`, but a
      subgraph compiled without its own checkpointer inherits the parent's and its
      steps are stored under `checkpoint_ns = "research_agent:<task_id>"` in the same
      saver. `get_state_history(config_with_that_ns)` lists every subgraph step, and
      `ainvoke(None, snapshot.config)` on the *parent* replays from exactly that node
      (`course_cache_upsert` re-ran once, run continued to `review_gate`, tracked state
      identical). `<task_id>` is per-execution, so the namespace must be discovered at
      runtime (`saver.alist`, or `get_state(cfg, subgraphs=True)` while paused).
      Also observed: a subgraph's *own* checkpointer is ignored when a parent
      checkpointer exists (own `MemorySaver()` stayed empty); `checkpointer=True` only
      makes the namespace stable (`research_agent`). `interrupt_before` inside the
      subgraph works: `get_state(cfg, subgraphs=True)` shows the paused sub-state, resume
      and later replay both work. Not tested: `Command`/dynamic `interrupt()` inside a
      subgraph, subgraph with `checkpointer=False`, a real process kill.
      **Also observed (retry tests):** when one of several parallel `Send` workers in the
      subgraph fails, resuming the outer graph re-runs the workers that had already
      succeeded; the same research graph run standalone re-runs only the failed one. Cost
      of the encapsulation for fan-out inside a subgraph.
      Design alternative if namespace discovery feels fragile: keep mutating nodes at
      the top level, or have the subgraph return effects as data for an outer node.
- [x] **Plan-driven `Send` fan-out.** Worker count comes from `research_plan`, not
      from the code — contrast with a fixed number of hardcoded parallel branches.
      *(Drafted in ARTICLE.md §3.2, including the "not a distributed queue" boundary
      note.)*

## Production principles to add (small, illustrative — not fully productized)

- [ ] **Retry policy per node** — `RetryPolicy` passed to `add_node(..., retry_policy=RetryPolicy(...))`.
      Handles transient exceptions (timeout, rate limit) with backoff/jitter.
      Contrast with `max_research_iterations`: retry = "the call failed," replanning
      = "the call succeeded but the result was insufficient." Two different failure
      classes, two different mechanisms — don't conflate them in the article.
      **Scope: read-only nodes only** (LLM, search), where retry is safe. State plainly
      that this is in-process: it does not survive process death (compiled checkpointer
      is `MemorySaver`). Mutating nodes need the idempotency + outbox items below, not
      `RetryPolicy` — and note the DB-writing nodes catch and log exceptions, so a
      `RetryPolicy` on them would never fire.
      *(Implemented, not yet in the article. Correction to the scope above: the DB
      writers now re-raise, and `RetryPolicy` also sits on the idempotent writers
      (`course_cache_upsert`, `user_memory_update`) and on `publish_node`, which is safe
      to retry only because `submit` is keyed. `retry_on=is_transient` (timeouts,
      connection errors, `psycopg.OperationalError`, HTTP 408/425/429/5xx); integrity
      errors and `ValueError` are not retried. Findings: (1) nodes that caught every
      exception made `RetryPolicy` a no-op, so `gateway` and `tavily_search_worker` now
      re-raise transient errors and only convert permanent ones to state; (2) the LLM
      client ships its own retries (`ChatOpenRouter.max_retries=2`, up to ~300 s
      elapsed), which multiply with `RetryPolicy`, so `build_llm` defaults to 0 and the
      router/synthesizer, which have no policy, opt back in; (3) exhausted retries
      raise out of the graph and the run stays resumable from its checkpoint; (4)
      observed on langgraph 1.1.2: when one of several parallel `Send` workers fails,
      resuming a *standalone* graph re-runs only the failed worker, but the same graph
      mounted as a subgraph re-runs the already-successful sibling too
      (`tests/test_retries.py`, both directions asserted). The synthesizer keeps its
      hand-rolled per-course retry and canned fallback; no policy there.)*
- [ ] **Node-level cache policy** — `CachePolicy` (`langgraph.types.CachePolicy`,
      passed to `add_node`). Memoizes a single node's output keyed on input — narrower
      than a whole-workflow cache. Good contrast piece against our own
      `course_cache_lookup`, which is a *domain* cache we built ourselves; `CachePolicy`
      is a *framework* cache for identical-input reruns. **Correction (checked against
      the installed langgraph 1.1.2):** `add_node` has no `timeout=` or `error_handler=`
      parameter; its signature is `defer, metadata, input_schema, retry_policy,
      cache_policy, destinations`. Don't claim node-level timeouts without checking the
      version the article targets.
- [ ] **Durability modes** — `durability=` on `invoke`/`stream`, values `"exit"`
      (checkpoint only at graph exit, fastest/least safe), `"async"` (background
      checkpoint, balanced), `"sync"` (checkpoint before every step, safest/slowest).
      Frame as a tuning knob: candidate-extraction steps don't need `"sync"`; a
      publish-adjacent step might.
      *(Tested, not in the article: `test_durability_modes_differ_in_checkpoint_granularity_not_in_resume`.
      Run against `AsyncPostgresSaver` with a transient failure late in the run: `sync`
      and `async` wrote ~12-14 checkpoints, `exit` wrote 2. In all three modes the failed
      run resumed with `ainvoke(None, config)` without re-running the completed
      `evidence_validator`, because `exit` still checkpoints when the graph exits via an
      exception. What `exit` loses is history granularity (replay/time-travel points) and,
      untested here, anything in flight at a hard kill. Nothing kills the process, so the
      safety difference is not demonstrated, only the granularity.)*
- [ ] **`Store` vs. checkpointer — the memory-scope distinction.** Checkpointer =
      thread-scoped run state, wired in via `compile(checkpointer=...)`, is what makes
      `interrupt_before` resumable. `Store` = cross-thread long-term memory (e.g.
      user_id-scoped), requires explicit `store.get`/`store.put` in node code. Our
      Postgres `UserMemory` table is conceptually what `Store` is for — name that
      explicitly as "here's the API for what we hand-rolled," even without migrating
      to it. Common confusion point worth resolving in the article.
- [ ] **Async node execution.** Nodes as `async def`, real concurrency for I/O-bound
      work (LLM calls, search) vs. simulated parallelism. Already used for
      `tavily_search_worker` — call it out explicitly as a pattern, not an incidental
      detail.
- [ ] **Recursion limit as a structural safety net.** `recursion_limit` (config param,
      raises `GraphRecursionError`) as a backstop *underneath* our own
      `max_research_iterations` domain-level budget — two independent guardrails
      against runaway loops, one generic/structural, one domain-aware.
- [ ] **OpenTelemetry integration.** *Verified*: LangSmith has native end-to-end OTel
      support — `LANGSMITH_OTEL_ENABLED=true` plus standard
      `OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_EXPORTER_OTLP_HEADERS`; traces can route to
      Datadog/Grafana/Jaeger over plain OTLP, no bridging library required.
      OpenInference/Traceloop bridges still exist for LangSmith-independent setups.
      Strong section: "you get real distributed tracing across your whole stack, not
      just an isolated agent UI."
- [x] **Provider fallback via `Runnable.with_fallbacks()`.** *Verified*: this is
      `langchain_core.runnables`, not LangGraph-specific — tries fallback runnables in
      order until one succeeds, at single-call or full-chain granularity. Inside a
      LangGraph node it's just "wrap the chain, call it like any other Runnable," no
      LangGraph glue needed. Good point: the graph is only responsible for
      *structural* control flow (which node runs next); the Runnable layer underneath
      already owns *call-level* resilience, so LangGraph doesn't need to reinvent it.
      *(Drafted in ARTICLE.md §7.1 as the in-process alternative. This repo uses
      neither — it delegates fallback to OpenRouter's `models` array.)*
- [x] **`ModelFallbackMiddleware`** — *verified*, this is the current native
      multi-model fallback mechanism (`langchain.agents.middleware`), e.g.
      `ModelFallbackMiddleware("openai:gpt-5.5", "anthropic:claude-...")`. Distinct
      from and newer than `.with_fallbacks()`. **Correction to an earlier assumption**:
      `init_chat_model()` does *not* natively take a fallback list — don't claim that
      in the article. *(Drafted in ARTICLE.md §7.1.)*
- [x] **OpenRouter as a production fallback/spend-control layer.** *Verified*:
      dedicated `langchain-openrouter` package (`ChatOpenRouter`) is the modern
      integration path (preserves OpenRouter-specific metadata — reasoning content,
      routing info — that a generic `ChatOpenAI`-pointed-at-OpenRouter setup loses,
      though that older pattern still works via `openai_api_base`). OpenRouter's own
      fallback is a `models: [...]` priority array in the request body — auto-retries
      the next model on context-length errors, moderation flags, rate limits, or
      downtime; you're billed only for whichever model actually served the request
      (response includes a `model` field). Note: `fallbacks` and `models` params
      cannot both be set (400 error). Framing for the article: the real production
      value isn't "fallback" per se — `.with_fallbacks()`/`ModelFallbackMiddleware`
      already give you that in-process — it's *one bill, one rate-limit surface, and
      per-key spend caps* across providers. *(Drafted in ARTICLE.md §7.2, and now
      wired in code: `app/llm.py:build_llm()`, DeepSeek V4.1 Flash primary, Gemini
      2.5 Flash Lite fallback. Observed, not from docs: `ChatOpenRouter` has no
      `models` field, so it goes via `model_kwargs`; an invalid model ID is a 400,
      not a fallback trigger; `ServedModelLogger` logs the response `model_name`.
      **Still open:** the fallback path was never triggered live — only asserted
      in the outgoing request payload. Don't claim it was exercised. Also still
      unverified: OpenRouter data-retention/ZDR controls — the article notes every
      prompt now transits a third party, per §9.1.)*
- [ ] **LiteLLM — two distinct integration shapes, don't conflate them.** *Verified*:
      (a) **SDK, in-process**: `langchain-litellm` package provides `ChatLiteLLM` and
      `ChatLiteLLMRouter` (wraps LiteLLM's own `Router` for load-balancing/fallback) —
      no separate service, just a library. (b) **Proxy, a real gateway service**:
      standalone FastAPI process exposing an OpenAI-compatible endpoint, handling
      routing/key-management/spend-tracking/rate-limits; LangChain then talks to it via
      plain `ChatOpenAI` pointed at the proxy URL. The user's own production experience:
      Proxy is the common pattern, but spend/cache-token billing accuracy has been a
      rough edge — worth naming as a known operational cost of self-hosting this layer
      vs. OpenRouter's hosted equivalent.
- [ ] **`StateGraph` input/output schema separation.** *Verified*, current param names:
      `StateGraph(OverallState, input_schema=InputState, output_schema=OutputState)`.
      `invoke()` return value is filtered to just the output schema's fields, not the
      full internal state — this is the "public API vs. internal contract" distinction
      worth pairing with the state-contract section.
- [ ] **`stream_mode` — full current list.** *Verified*: `"values"`, `"updates"`,
      `"messages"`, `"custom"`, `"debug"`, plus the less-commonly-cited `"checkpoints"`
      and `"tasks"`. Passing a list yields `(mode, chunk)` tuples; a single string
      yields bare chunks. Relevant to the performance/responsiveness section — this is
      how a client gets partial progress without polling.
- [ ] **Boundary to be explicit about: `Send` is in-process fan-out, not a
      distributed queue.** If the real system needs cross-service work distribution
      (SQS, Celery, Temporal), that is a different infrastructure layer LangGraph does
      not replace — conflating "parallel `Send` workers" with "a job queue" is a
      real mistake worth calling out directly.

## New: API brittleness — a dedicated section

The user's own APIs backing this agent (Gemini, and in production a real search
provider) are exactly the kind of dependency that fails in the field: rate limits,
timeouts, schema drift, partial outages. This deserves its own treatment, distinct
from the "retry/cache/durability" list above because it's about *designing for*
brittleness, not just the mechanical knobs.

- [ ] **This repo already demonstrates graceful degradation** — use it as the worked
      example before introducing new mechanisms:
  - Gateway/synthesis/router fall back to deterministic parsing when
    `OPENROUTER_API_KEY` is absent (no LLM call attempted at all, not a failed call).
  - `course_cache_lookup` falls back to the in-memory seed cache when `DATABASE_URL`
    is absent.
  - `tavily_search_worker` catches exceptions and writes a `research_notes` entry
    instead of collapsing the run — "fail closed with a visible limitation," not
    silent hallucination or a crashed graph. (Now serving from a mock catalog, but
    the failure-handling shape is what a real provider integration would reuse.)
- [ ] **Retry + backoff vs. circuit breaking.** `RetryPolicy` handles transient
      failures; it does *not* protect against a sustained outage (retrying a fully
      down API just burns budget and latency). Worth naming the gap: LangGraph gives
      you per-node retry, not a circuit breaker — that's either hand-rolled (a
      failure-count field in state, checked before dispatching the next `Send`) or
      delegated to the client library/gateway in front of the API.
- [ ] **Idempotency under checkpoint replay.** If a node crashes mid-execution and the
      graph resumes from the last checkpoint, does re-running that node do something
      unsafe (double-charge, double-publish, duplicate insert)? `course_cache_upsert`
      is a good example of a node that's naturally idempotent (upsert semantics); a
      node with a side effect like "send an email" would not be, and needs explicit
      dedup (e.g. an idempotency key derived from `run_id` + node name). Call out
      which of our own nodes are safe to replay and which would need this if they
      called a real external API.
      **Correction (checked against code):** `course_cache_upsert` is only half
      idempotent — `courses` uses `ON CONFLICT ... DO UPDATE`, but `course_evidence`
      is a plain `INSERT`, so replay duplicates evidence rows. `user_memory_update`
      (`recommendation_events`) is a plain `INSERT` too: replay double-records
      feedback and skews personalization. `publish_node` is a stdout stub today and
      the canonical non-idempotent effect once real. Fix: deterministic idempotency
      key (`run_id` + node) with a unique constraint.
      *(Implemented, not yet in the article: `migrations/002_idempotent_writes.sql`.
      `course_evidence` dedups on content, unique `(course_id, source_url,
      quote_or_summary)` + `ON CONFLICT DO NOTHING`; `recommendation_events` gets an
      `idempotency_key` = `{run_id}:{course.url}` (NULL when no `run_id`, i.e. no
      dedup). Integration tests replay `user_memory_update` and `course_cache_upsert`
      from checkpoints, in-memory and Postgres savers; with the indexes dropped and the
      code fix stashed all four replay tests fail `10 != 5`. `publish_node` is still a
      stub, so it isn't covered.)*
      **Found while testing, now fixed:** `recommendation_events.user_id` has an FK to
      `users`, nothing created the user, and `record_feedback` swallowed the resulting
      error into a log line — on a fresh DB the CLI's `cli-user` feedback was silently
      dropped. `record_feedback` now inserts the user row (`ON CONFLICT DO NOTHING`) in
      the same transaction, and both writers re-raise after logging. Tests: unknown user
      end to end, and a `CHECK (false)` constraint proving the failure surfaces, the run
      stays paused at `user_memory_update`, and no partial rows land. Good concrete
      example for the "fail closed, visibly" point.
- [ ] **Out-of-process effects: outbox behind a port.** *(Implemented in
      `course_discovery/effects/`; not yet in the article. Departures from the sketch
      below: the port also has `status(key)`; the worker leases rows
      (`locked_until`) so a crashed worker's rows are reclaimed, attempts are counted at
      claim time so a record that keeps crashing its worker ends up dead-lettered; the
      inline adapter attempts delivery once during `submit` and leaves failures queued
      for a worker; `PermanentEffectError` and an unknown `kind` dead-letter
      immediately. The `publish_digest` handler still prints. Not done: same-transaction
      write with `recommendation_events`.)* Retry of a *mutation* is a
      different problem from retry of a *call*. Sketch, don't productize:
  - **Port:** nodes call `EffectGateway.submit(Effect(key, kind, payload))` and never
    the external system. The adapter inserts an outbox row (`ON CONFLICT (key) DO
    NOTHING`) and returns; the node writes `status="queued"`, not "done". The key is
    derived deterministically from state (`run_id` + node), never generated at
    execution time, or replay mints a new key and defeats dedup.
  - **Worker:** separate process claims rows (`FOR UPDATE SKIP LOCKED`), dispatches by
    `kind`, backs off, dead-letters after N attempts. This is the part that actually
    delivers "survives the service going down"; the outbox table alone does not.
  - **Two recovery loops, don't conflate:** checkpointer resumes the *graph*; the
    worker retries the *effect*. Replay re-submits the same key, so submit is a no-op.
  - **Atomicity limit:** outbox row can share a transaction with the node's own domain
    write (e.g. `recommendation_events`), but not with the checkpointer write. At-least-
    once delivery, so the consumer stays idempotent.
  - **State contract change:** `published: bool` becomes a status
    (queued/delivered/failed). Say so — it's a real cost of the pattern.
  - **Adapters:** Postgres outbox (best fit here: Postgres already present, gives
    same-transaction write); in-memory/inline adapter for demo and tests, matching the
    mock-search and seed-cache philosophy; SQS/Celery via an outbox relay if
    cross-service; Temporal if many long-running side-effect steps (overlaps with
    LangGraph checkpointing — a different architecture, not an add-on). Don't claim
    LangGraph Platform background-run semantics without verifying.
  - Framing: illustrative sketch, not validated under load (see scope boundary below).
- [ ] **Timeouts as a first-class concern, not an afterthought.** ~~Node-level
      `timeout=`~~ (not available in langgraph 1.1.2, see `add_node` above) plus
      per-call timeouts inside the API client itself (a real HTTP client needs an
      explicit timeout or a hung request blocks the whole fan-out branch).
      *(Implemented, not in the article: `LLM_TIMEOUT_SECONDS` (30) on the chat client,
      `asyncio.wait_for` with `SEARCH_TIMEOUT_SECONDS` (10) around the search call, and
      Postgres `connect_timeout` plus `statement_timeout`. A search timeout raises
      `TimeoutError`, which is transient, so it is retried by the node's `RetryPolicy`;
      tested with a hanging client. The timeouts wrap the call, not the node.)*
- [x] **Rate limiting.** LangChain chat models accept a `rate_limiter=` (e.g.
      `InMemoryRateLimiter`) to smooth outbound call rate — relevant when
      `research_planner`/`evidence_validator`/`synthesizer` all call the same Gemini
      quota within one run, and worse under concurrent users. *(Drafted in
      ARTICLE.md §6.4. Correction: the planner/validator are deterministic Python,
      so the real LLM nodes sharing quota are gateway/router/synthesizer. The
      limiter is on the router only, with untuned demo values, and is in-process —
      it does not coordinate across workers.)*
- [ ] **Schema drift / structured-output validation as a brittleness surface.**
      Pydantic structured output (`ResearchPlan`, `CandidateValidation`, etc.) is
      itself a defense against a brittle API: if the LLM provider changes behavior and
      returns malformed output, validation fails loudly instead of silently
      propagating garbage downstream. Worth tying back to the "fail closed" framing
      already in `CLAUDE.md`.

## Explicit scope boundary — not a case study

- [x] **State up front this is not a production case study.** No item in this article
      comes from operating this agent under real traffic — no load data, no incident
      log, no cost-at-scale numbers. It's a demonstration of LangGraph mechanics using
      a domain-shaped example, evaluated by reading the code and running it locally.
      Say this once, plainly, probably in the intro or "Known Limitations" (outline
      section 9) — don't let the production-principles framing (retry/cache/durability/
      OTel/fallback) imply operational validation that didn't happen.
- [x] Corollary: "production would swap this one class" (mock search -> real provider)
      is an architectural claim, not a tested migration. Say so. *(Drafted in
      ARTICLE.md §10.)*

## Explicitly ruled out — don't re-litigate

- [x] **Model tiering by task difficulty (cheap model for easy nodes, strong model for
      hard ones) is out of scope.** It's a cost/quality policy decision, orthogonal to
      LangGraph's actual job of wiring control flow. The only LangGraph-adjacent
      consequence is trivial — each node just calls whatever Runnable it's bound to, so
      per-node model choice is a one-line fact, not a feature — worth at most a
      parenthetical where per-node model binding is already discussed, never its own
      section. *(Drafted in ARTICLE.md §10.)*

## Data protection / GDPR — a boundary, not a feature

- [x] LangGraph has zero built-in compliance tooling. Frame this as a layer-of-
      abstraction point, not a gap to fill. *(Drafted in ARTICLE.md §9.1 — the
      "this durability is also a retention liability" line references §4.5, which
      is not drafted yet; revisit the cross-reference once §4.5 lands. The
      OpenRouter PII-filtering claim was left as an open question in the article
      text rather than cited as fact, per the unverified note here.)*
  - The checkpointer and `Store` are literally a database of user state — whatever
    PII flows through `AgentState` (queries, `UserMemory` fields) gets persisted at
    every superstep. No built-in TTL, redaction, or right-to-erasure primitive;
    deleting a user's data means writing `DELETE WHERE thread_id=...` /
    `store.delete(...)` yourself. One explicit line in the Store-vs-checkpointer
    section: "this durability is also a retention liability you now own."
  - The actual compliance lever sits one layer down, at the model-provider boundary —
    ZDR flags, DPAs, which region processes the request. Invisible to LangGraph
    entirely; the graph doesn't know or care what the Runnable underneath a node does
    with the payload. **Note**: a claimed "OpenRouter PII filtering" feature needs
    verification against current OpenRouter docs before citing — I could not confirm
    this is a real toggle distinct from OpenRouter's data-retention/ZDR policy
    controls, which are a different guarantee (not-logging vs. detecting/stripping
    PII). Don't conflate them in the article.

## Security / guardrails — same wrap-the-Runnable shape as fallback

- [ ] LangChain/LangGraph core has no native prompt-injection or PII-guardrail
      primitive. Reuse the fallback section's framing: a guardrail is a function
      wrapped around a call, not a graph feature — the graph decides which node runs
      next, not what happens inside a call.
- [ ] **Concrete choice for this repo's example: an in-process library, not an
      external API** (Presidio or `llm-guard` — local classifier/rules, no network
      hop, no new data-sharing surface). Show the general wrap shape, then this one
      concrete instantiation.
- [ ] **Add an integration test asserting the guardrail actually catches a known
      case** (a canned prompt-injection string or PII pattern) — not just that it's
      wired in, but that it reliably fires. This is the difference between "we added
      a library" and "we verified the mechanism works."
- [ ] Name the two concrete insertion points in this codebase: `candidate_extractor` /
      `evidence_validator` consume untrusted external content (search results) as LLM
      input — the injection surface. `synthesizer` produces user-facing output — the
      PII/leak-check surface if `user_memory` ever carries sensitive fields.
- [ ] Tie back to the existing "schema drift / structured-output validation" item:
      Pydantic validation catches malformed output, but not maliciously-crafted-but-
      well-formed output. Different defense, same "fail closed" framing.

## Checkpoint and graph versioning — two distinct failure modes

- [x] **State schema versioning.** If `AgentState`'s shape changes, old checkpoints
      persisted under the previous shape may not deserialize. This is a LangGraph-
      specific problem (not generic app versioning) because the checkpointer
      serializes your TypedDict/Pydantic state directly. *(Drafted in ARTICLE.md
      §9.2.)*
      **Add — msgpack allowlist (verified):** with `langgraph-checkpoint` 4.2.0 a
      durable saver logs `Deserializing unregistered type course_discovery.domain.models.X
      from checkpoint. This will be blocked in a future version` once per Pydantic model
      in state (6 here). Two fixes, each verified to give 0 warnings and correct state
      on read-back: (a) `JsonPlusSerializer(allowed_msgpack_modules=[(module, name), ...])`
      passed as `serde=` to the saver; (b) `LANGGRAPH_STRICT_MSGPACK=true`, no code — at
      compile time LangGraph derives an allowlist from the state schema
      (`langgraph/_internal/_serde.py`), so schema-reachable models are allowed
      automatically. Not tested: what strict mode blocks for a type outside the schema.
      The warning is once-per-type-per-process, so assert it in a fresh process. This is
      a deserialization-safety control (a writer to the checkpoint DB can otherwise
      trigger arbitrary type construction) — worth one sentence beyond "schema drift".
      **Found later:** an allowlist built from `BaseModel` subclasses only is not enough.
      Enum-typed state fields (`RoutingAction`, `DeliveryStatus`) log `Blocked
      deserialization of ... - not in allowed_msgpack_modules` under the same allowlist,
      a different message from the "unregistered type" warning above. The allowlist in
      `persistence/checkpointer.py` now includes `Enum` subclasses and
      `tests/test_checkpointer.py` round-trips a model and both enums through it
      asserting type and value. The effect of a blocked enum on the restored value was
      not inspected. The same serde is now used by the default `MemorySaver`.
- [x] **Graph structural versioning — a separate failure mode.** Changing node
      topology (add/remove/rename a node) between deploys can break in-flight
      checkpointed threads from the old graph shape even when `AgentState` itself
      didn't change. Name both as independent risks, not one bucket. *(Drafted in
      ARTICLE.md §9.2 — added the note that neither risk is currently exercised
      since the compiled checkpointer is `MemorySaver`, not durable.)*

## HITL — dynamic interrupt and the UX gaps around it

- [x] **Dynamic `interrupt()` vs. this repo's static `interrupt_before`.** This repo
      uses `interrupt_before=["review_gate"]` — a compile-time, blanket "always pause
      here." LangGraph also has the `interrupt()` function, called *inside* a node at
      runtime, so review can be conditional (e.g., only low-confidence evidence
      triggers a pause; high-confidence candidates skip straight through). A real,
      distinct mechanism worth naming explicitly — "should every run stop for review,
      or only the ones that need it." *(Drafted in ARTICLE.md §4.2.)*
- [x] **The router's five outcomes (PUBLISH/REWRITE/AUGMENT/RESET/DISCARD) are already
      a good example — say so explicitly.** Most HITL write-ups only show a binary
      approve/reject gate. Call this out in prose as an intentional design point using
      our own code as the positive example, not just plumbing. *(Drafted in
      ARTICLE.md §4.3.)*
- [x] **What LangGraph doesn't own here, briefly** (the example itself is brief, so
      keep this tight — sketch, don't design): *(Drafted in ARTICLE.md §4.3, all four
      sub-points below.)*
  - A paused thread sits in the checkpointer indefinitely; no built-in TTL or
    staleness handling for an unanswered review.
  - **Notifying a human that a thread is waiting is entirely app-layer.** LangGraph
    (self-hosted, this repo's setup) has no push mechanism — only
    `graph.get_state(config)` telling you execution is parked at a node. (LangGraph
    Platform, the managed offering, does have run webhooks — flag as unverified
    exact trigger semantics before citing.) Sketch the shape in one paragraph: the API
    layer calls invoke/stream, observes the run paused at `review_gate`, and pushes a
    single event (thread_id, run_id, payload) over a websocket/SSE channel; the
    frontend subscribes per-user/thread and renders the pending review. This is a
    one-paragraph sketch, not a protocol design — ties to the "What's Next" webhook/
    dashboard bullets already in `ARTICLE_OUTLINE.md` section 10.
  - Reviewers aren't limited to approve/reject — `graph.update_state(...)` before
    resuming lets a reviewer patch state and continue, not just gate it.
  - Concurrent-resume race: nothing stops two callers resuming the same `thread_id`
    simultaneously; that's an app-level lock, not something the checkpointer
    arbitrates.

## Open question — not yet resolved

- [ ] Whether to restructure `ARTICLE_OUTLINE.md`'s section list around this material
      (new sections for retry/cache/durability/Store/OTel/fallback/brittleness) or
      fold it into the existing sections (6 "Control Shell", 8 "Observability", 9
      "Known Limitations"). Needs a decision before drafting starts — don't draft
      prose until this is settled.

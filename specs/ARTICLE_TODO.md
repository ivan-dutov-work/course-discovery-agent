# Article TODO — Production LangGraph Patterns to Cover

Running list of principles the article should cover, beyond the base research-agent
walkthrough in `ARTICLE_OUTLINE.md`. Each item names the LangGraph/LangChain
mechanism, why it matters in production, and where in this repo it can be shown or
added. Verified against current docs where noted — do not restate an unverified item
as fact in the article without checking it first.

Check items off as they're written into the article draft (not just implemented in
code — implementation and article coverage are tracked separately).

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
- [x] **Subgraphs for encapsulation.** `research_graph` compiled and mounted as a
      single node in `outer_graph`. The outer graph doesn't know or care about the
      research agent's internal node count. *(Drafted in ARTICLE.md §3.3.)*
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
- [ ] **Node-level cache policy** — `CachePolicy` (`langgraph.types.CachePolicy`,
      passed to `add_node`). Memoizes a single node's output keyed on input — narrower
      than a whole-workflow cache. Good contrast piece against our own
      `course_cache_lookup`, which is a *domain* cache we built ourselves; `CachePolicy`
      is a *framework* cache for identical-input reruns. Also worth mentioning: newer
      `timeout=` and `error_handler=` params on `add_node`.
- [ ] **Durability modes** — `durability=` on `invoke`/`stream`, values `"exit"`
      (checkpoint only at graph exit, fastest/least safe), `"async"` (background
      checkpoint, balanced), `"sync"` (checkpoint before every step, safest/slowest).
      Frame as a tuning knob: candidate-extraction steps don't need `"sync"`; a
      publish-adjacent step might.
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
- [ ] **Provider fallback via `Runnable.with_fallbacks()`.** *Verified*: this is
      `langchain_core.runnables`, not LangGraph-specific — tries fallback runnables in
      order until one succeeds, at single-call or full-chain granularity. Inside a
      LangGraph node it's just "wrap the chain, call it like any other Runnable," no
      LangGraph glue needed. Good point: the graph is only responsible for
      *structural* control flow (which node runs next); the Runnable layer underneath
      already owns *call-level* resilience, so LangGraph doesn't need to reinvent it.
- [ ] **`ModelFallbackMiddleware`** — *verified*, this is the current native
      multi-model fallback mechanism (`langchain.agents.middleware`), e.g.
      `ModelFallbackMiddleware("openai:gpt-5.5", "anthropic:claude-...")`. Distinct
      from and newer than `.with_fallbacks()`. **Correction to an earlier assumption**:
      `init_chat_model()` does *not* natively take a fallback list — don't claim that
      in the article.
- [ ] **OpenRouter as a production fallback/spend-control layer.** *Verified*:
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
      per-key spend caps* across providers.
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
    `GOOGLE_API_KEY` is absent (no LLM call attempted at all, not a failed call).
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
- [ ] **Timeouts as a first-class concern, not an afterthought.** Node-level
      `timeout=` (see `add_node` above) plus per-call timeouts inside the API client
      itself (the mock client has none because it's local; a real HTTP client needs
      an explicit timeout or a hung request blocks the whole fan-out branch).
- [ ] **Rate limiting.** LangChain chat models accept a `rate_limiter=` (e.g.
      `InMemoryRateLimiter`) to smooth outbound call rate — relevant when
      `research_planner`/`evidence_validator`/`synthesizer` all call the same Gemini
      quota within one run, and worse under concurrent users.
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

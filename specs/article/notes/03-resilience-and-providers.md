# Notes: resilience and providers (§7–§8)

Evidence for drafting. Load when working on these sections. Observed on langgraph 1.1.2
unless marked as documentation.

## Node-level resilience (§7)

- **`RetryPolicy` (§7.1).** `add_node(..., retry_policy=RetryPolicy(...))`. Retry means the
  call failed; replanning means the call succeeded but the result was insufficient. Two
  failure classes, two mechanisms. Applied to read-only and idempotent nodes
  (`parse_user_request`, `load_user_profile`, `find_known_courses`, `search_web_for_courses`,
  `save_verified_courses`, `send_approved_courses`, `record_review_outcome`) with
  `retry_on=is_transient`: timeouts, connection errors, `psycopg.OperationalError`, HTTP
  408/425/429/5xx. Integrity errors and `ValueError` are not retried. `send_approved_courses` is
  safe to retry only because `submit` is keyed. Findings:
  1. Nodes that caught every exception made `RetryPolicy` a no-op; the nodes now re-raise
     transient errors and convert only permanent ones to state.
  2. The LLM client ships its own retries (`ChatOpenRouter.max_retries=2`, up to ~300 s
     elapsed), which multiply with `RetryPolicy`. `build_llm` defaults to 0; the router and
     synthesizer, which have no policy, opt back in.
  3. Exhausted retries raise out of the graph and the run stays resumable from its checkpoint.
  4. Parallel `Send` siblings: see `01-state-and-control-flow.md`.
  - The synthesizer keeps a hand-rolled per-course retry and a canned fallback; no policy.
  - The policy is in-process: it doesn't survive process death.
- **Timeouts (§7.2).** `add_node` has no `timeout=` or `error_handler=`; its signature is
  `defer, metadata, input_schema, retry_policy, cache_policy, destinations`. Timeouts live in
  the clients: `LLM_TIMEOUT_SECONDS` (30), `asyncio.wait_for` with `SEARCH_TIMEOUT_SECONDS`
  (10), Postgres `connect_timeout` and `statement_timeout`. A search timeout raises
  `TimeoutError`, which is transient, so `RetryPolicy` retries it (tested with a hanging
  client). They wrap the call, not the node. A hung request without a timeout blocks its whole
  fan-out branch. Nodes are `async def` so I/O-bound work is concurrent.
- **`CachePolicy` (§7.3, not in code).** `langgraph.types.CachePolicy` memoizes one node's
  output keyed on input: a framework cache for identical-input reruns, contrasted with the
  hand-built domain cache in `find_known_courses`. This topology doesn't produce same-input
  reruns.
- **Retry versus circuit breaking (§7.4).** `RetryPolicy` handles transient failures, not a
  sustained outage (retrying a down API burns budget and latency, and in a fan-out delays every
  sibling). LangGraph gives per-node retry, not a circuit breaker: hand-roll one (a failure
  count in state checked before the next `Send`) or delegate to the client or gateway.
- **Degradation is a design choice (§6.1).** No `OPENROUTER_API_KEY` means no LLM call at all
  (deterministic parse, summary, routing), not a failed call. No `DATABASE_URL` means the seed
  cache. A permanent search failure writes a `research_notes` entry: "fail closed with a
  visible limitation," not hallucination or a crashed graph.
- **Structured output as a brittleness surface (§7.1, §8.4).** Pydantic output fails loudly on
  malformed responses, tying back to fail-closed. It doesn't catch well-formed malicious output;
  that is a security topic (§8.5, §10.3). State-schema drift is §10.2.

## Provider-level resilience (§8)

- **Call-level fallback lives in the Runnable (§8.1).** `Runnable.with_fallbacks()` is
  `langchain_core.runnables`, not LangGraph; inside a node it is just a wrapped chain. The graph
  owns structural control flow; the Runnable owns call-level resilience.
- **`ModelFallbackMiddleware`** (`langchain.agents.middleware`) is the newer native multi-model
  fallback. `init_chat_model()` does not take a fallback list; don't claim it does.
- **OpenRouter (§8.2).** `langchain-openrouter` (`ChatOpenRouter`) preserves OpenRouter-specific
  metadata that a generic `ChatOpenAI` pointed at OpenRouter loses. Fallback is a `models: [...]`
  priority array in the request; it retries the next model on context-length errors, moderation
  flags, rate limits or downtime; you're billed for the model that served it; `fallbacks` and
  `models` cannot both be set (400). Observed: `ChatOpenRouter` has no `models` field, so it
  goes through `model_kwargs`; an invalid model ID is a 400, not a fallback trigger;
  `ServedModelLogger` logs the response `model_name`. Framing: the value is one bill, one
  rate-limit surface and per-key spend caps, not fallback itself. The fallback was never
  triggered live, only asserted in the request; OpenRouter retention and ZDR controls are
  unverified (BACKLOG).
- **LiteLLM, two shapes, don't conflate.** SDK in-process (`langchain-litellm`: `ChatLiteLLM`,
  `ChatLiteLLMRouter`) versus Proxy as a standalone OpenAI-compatible gateway service that
  LangChain reaches through `ChatOpenAI`. Production experience to keep: the Proxy is a fast-moving
  project with many issues, needs manual management, needs Redis to scale past one instance
  (Redis contents are from LiteLLM docs, not this repo), and spend and cache-token billing
  accuracy has been a rough edge.
- **Rate limiting (§8.3).** Chat models accept `rate_limiter=`; it smooths the rate at which calls
  start and must outlive the call (one built per call never sees the previous one). The
  limiter is on the router only, untuned demo values, in-process, no cross-worker coordination.
  The nodes sharing quota are `parse_user_request`, `interpret_review_feedback` and
  `rank_and_summarize_courses`; the planner and validator are deterministic.
- **Guardrails (§8.5, §10.3).** Core LangChain and LangGraph have no prompt-injection or PII
  primitive. Same wrap-the-Runnable shape as fallback: a guardrail is a function around a call,
  not a graph feature. Repo choice: an in-process library, no external API. PII redaction
  is built (see `04-observability-and-compliance.md`); the prompt-injection check is not (BACKLOG).

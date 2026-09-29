# Using LangGraph to Implement Complex AI Agents

## 0. TL;DR

Most so-called agents fail for predictable reasons: they hallucinate data, drift away from user constraints, and execute side effects before a human can stop them. The fix is not a better prompt. The fix is architecture.

In this article, we build a Course Discovery Agent as an explicit state machine using LangGraph. The flow is simple to describe but robust in practice: parse user intent into structured filters, run multiple discovery workers in parallel, deduplicate deterministically, synthesize a digest with controlled LLM calls, then pause before publication for mandatory review. We also keep checkpointed execution so the run can resume from the same point using a thread id.

Important M1 scope note: filter parsing is fully implemented, but worker retrieval is still mocked and not yet filter-aware end to end. So this milestone validates orchestration and control flow first; production-grade retrieval policy enforcement comes in later milestones.

The result is an agent that is inspectable, resumable, and safe by default.

---

## 1. The Problem: Why Naive LLM Wrappers Fail

If you build your first agent by wiring an LLM call directly to an output channel, it often looks impressive in the first demo and unreliable in real operation.

Here are the three failure modes that matter most.

### 1.1 Hallucinated content looks plausible enough to ship

When you ask a general model for course recommendations, it can confidently produce realistic titles and links that do not exist. The output sounds useful, but users hit dead URLs and lose trust quickly.

This is not a model bug in isolation. It is a system design bug. If your architecture allows unconstrained generation to become published content, hallucinations are expected behavior.

### 1.2 Constraint drift under multi-step generation

You might ask for free courses with certificates and a minimum rating. A plain chat flow can satisfy that in the first paragraph and violate it in the second. Models optimize for helpful continuation, not strict policy compliance across many turns unless the system enforces structure.

In production, this shows up as subtle failures: paid courses in a free-only list, low-rated content in a curated digest, or language mismatches for multilingual communities.

In this M1 implementation, we explicitly separate two concerns: parsing constraints now, and fully enforcing them in real retrieval workers later. That distinction matters, and we call it out throughout this article to avoid over-claiming current behavior.

### 1.3 Autonomous side effects without approval

The most damaging pattern is direct auto-publish. One bad run can push low-quality or incorrect recommendations to a public channel. Even if the next ten runs are good, credibility is already damaged.

A robust agent must treat any external side effect as a gated operation. Human approval is not a nice-to-have; it is a system boundary.

### 1.4 What we actually need

For agents that interact with real users, we need a pipeline where:

- topology is controlled by the developer, not improvised by the model,
- LLM outputs are schema-validated, logged, and deterministic where possible,
- side effects are blocked until explicit review,
- execution state survives handoffs and can resume from checkpoints.

LangGraph is a strong fit because it models all of this directly: typed state, explicit nodes and edges, conditional routing, fan-out, and interrupt/resume semantics.

---

## 2. LangGraph in 5 Minutes

LangGraph is a framework for building stateful AI workflows as directed graphs. Think less "chat loop," more "orchestrated control plane."

### 2.1 Core model

The execution model has three primitives.

- Nodes: Python callables that read state and return updates.
- Edges: control-flow links between nodes, static or conditional.
- State: a typed shared contract that all nodes read/write.

In this project, that contract is AgentState, implemented as a TypedDict.

### 2.2 Why this matters

Once you adopt graph semantics, behavior becomes auditable.

- You can inspect exactly which node ran.
- You can reason about state transitions by key.
- You can enforce review gates with interrupts.
- You can branch to rewrite, reset, or discard paths without burying routing logic inside prompts.

This is a large step up from monolithic chains that hide control flow inside prompt text.

### 2.3 Key LangGraph features used in this implementation

| Feature               | Role in this project                 |
| --------------------- | ------------------------------------ |
| StateGraph            | Defines typed workflow shape         |
| add_node/add_edge     | Registers node steps and fixed paths |
| add_conditional_edges | Routes decisions from router output  |
| Send API              | Parallel fan-out to worker nodes     |
| interrupt_before      | Pauses before review gate            |
| MemorySaver           | In-memory checkpoint backend         |
| thread_id             | Resumable execution key              |

### 2.4 LangGraph vs simple chains

Use a plain chain when your task is single-step and stateless.

Use LangGraph when you need any of these:

- multi-step control flow,
- branching decisions,
- fan-out/fan-in parallelism,
- mandatory human-in-the-loop gating,
- resumability across invocations.

The Course Discovery Agent needs all five.

---

## 3. The Agent We Are Building

The Milestone 1 system is an article-ready demo that proves the orchestration pattern end to end without external production integrations.

### 3.1 Input and outcome

- Input: free-form user query, for example "find free Python courses with certificates for beginners".
- Outcome: a ranked markdown digest reviewed by a human before final publish or discard.

### 3.2 Pipeline overview

1. Gateway parses query into SearchFilters via structured LLM output.
2. Three mock workers execute in parallel and produce course candidates.
3. Aggregation merges worker outputs and caps volume.
4. Dedup removes duplicates with deterministic rules.
5. Synthesizer creates ranked digest text with retry/fallback.
6. Execution interrupts before telegram_gate.
7. Human feedback is written into checkpoint state.
8. Router classifies feedback and dispatches to publish, rewrite, augment, reset, or discard paths.

### 3.3 Architecture (current M1)

```text
User query
	 |
	 v
[Gateway Node] --parse--> SearchFilters
	 |
	 v
  (Send fan-out)
  worker_a   worker_b   worker_c
		\        |        /
		 \       |       /
		  v      v      v
		[aggregate_workers]
				 |
				 v
			 [dedup]
				 |
				 v
		  [synthesizer]
				 |
				 v
 [interrupt_before: telegram_gate]
				 |
				 v
			 [router]
		/    |    |    |    \
 publish rewrite augment reset discard
```

One accuracy note for the current codebase: AUGMENT routing is scaffolded in the graph, but the augment dispatch node is currently a stub and does not itself mutate state. We will discuss that explicitly in Section 9.

### 3.4 M1 scope boundaries (what is real vs mocked)

- Real now: typed state contract, graph topology, fan-out/fan-in orchestration, deterministic dedup, synthesis retry/fallback, interrupt/resume review loop, and routing decisions.
- Mocked now: discovery workers return fixed fixture-like data and currently do not apply search_filters as hard retrieval constraints.
- Partially scaffolded now: AUGMENT branch target selection exists, but augment_dispatch does not mutate state by itself.
- Deferred by design: production integrations (Telegram, API/crawler workers, durable checkpoint backend, cross-run semantic dedup).

---

## 4. Designing the State

The state contract is the foundation of this agent. Nodes do not call each other directly. They communicate only by reading and writing keys on AgentState.

### 4.1 AgentState in practice

The M1 implementation includes both product-level fields and runtime bookkeeping fields.

```python
class AgentState(TypedDict):
	 user_query: str
	 search_filters: SearchFilters | None
	 scraped_courses: list[CourseCandidate]
	 deduplicated_courses: list[CourseCandidate]
	 digest: str | None
	 manager_feedback: str | None
	 rewrite_instructions: str | None
	 routing_decision: RoutingAction | None
	 iteration_count: int
	 max_iterations: int
	 run_id: str
	 worker_a_courses: list[CourseCandidate]
	 worker_b_courses: list[CourseCandidate]
	 worker_c_courses: list[CourseCandidate]
	 error: str | None
	 published: bool
	 discard_reason: str | None
```

Why include additional runtime keys such as worker_a_courses and error?

- Fan-out workers need isolated output buffers before fan-in aggregation.
- Error and discard_reason allow deterministic fail-closed routing.
- published gives a concrete completion signal for the CLI loop.

### 4.2 Data models are contracts, not suggestions

Two Pydantic models anchor reliability:

- SearchFilters defines normalized discovery constraints.
- CourseCandidate defines the schema passed from workers through dedup and synthesis.

The key practical win is predictable downstream behavior. If a worker can emit arbitrary dicts, dedup and ranking logic become fragile. With Pydantic contracts, incompatible payloads fail early.

### 4.3 Flat state design keeps debugging tractable

A common anti-pattern is nesting state into deeply hierarchical dicts. That makes diffing and logging hard. Flat, typed keys improve observability and reduce accidental shape drift.

In this project, every major phase has explicit keys:

- scraped_courses for fan-in raw results,
- deduplicated_courses for filtered set,
- digest for publishable text,
- manager_feedback/rewrite_instructions/routing_decision for loop control.

This separation is why the graph remains understandable despite multiple branches.

---

## 5. Building the Graph Skeleton

Before writing node internals, we define topology. This is one of the highest-leverage habits in complex agent systems.

### 5.1 Node registration

The graph registers gateway, workers, aggregate, dedup, synthesizer, telegram gate, router, augment dispatch, publish node, and discard node.

```python
builder = StateGraph(AgentState)

builder.add_node("gateway", gateway_node)
builder.add_node("worker_a", worker_a_node)
builder.add_node("worker_b", worker_b_node)
builder.add_node("worker_c", worker_c_node)
builder.add_node("aggregate_workers", aggregate_node)
builder.add_node("dedup", dedup_node)
builder.add_node("synthesizer", synthesizer_node)
builder.add_node("telegram_gate", telegram_gate_node)
builder.add_node("router", router_node)
builder.add_node("augment_dispatch", augment_dispatch_node)
builder.add_node("publish_node", publish_node)
builder.add_node("discard_node", discard_node)
```

### 5.2 Edges and branch points

There are two major branch points.

1. Gateway fan-out: conditional edge returns a list of Send targets to parallel workers.
2. Router decision: conditional edge maps RoutingAction to next node.

Linear chain between them is explicit:

- workers -> aggregate_workers -> dedup -> synthesizer -> telegram_gate -> router

### 5.3 Compile-time operational behavior

Compilation sets two crucial runtime behaviors:

```python
graph = builder.compile(
	 checkpointer=MemorySaver(),
	 interrupt_before=["telegram_gate"],
)
```

- MemorySaver enables checkpoint persistence for the process lifetime.
- interrupt_before guarantees pause before any review-sensitive transition.

This is what turns a graph from "flowchart" into resumable runtime.

---

## 6. Parallel Execution with the Send API

Parallel fan-out is where LangGraph moves beyond linear chains in a practical way.

### 6.1 Fan-out from gateway

After gateway parses filters, the graph returns Send objects for each worker target.

```python
return [
	 Send("worker_a", base),
	 Send("worker_b", base),
	 Send("worker_c", base),
]
```

Each worker receives the same input baseline and writes to its own output key.

### 6.2 Why isolated output buffers matter

Workers emit into worker_a_courses, worker_b_courses, and worker_c_courses. They do not append directly to scraped_courses.

This isolation prevents concurrent write conflicts and keeps ownership clear.

Aggregation then becomes a single deterministic mutation point.

### 6.3 Worker behavior in M1

Workers are intentionally mock implementations, but realistic enough to stress the pipeline.

- Each worker simulates latency with asyncio.sleep(1.5).
- Each returns two CourseCandidate objects.
- Worker outputs intentionally include duplicate variants:
  - UTM query variation,
  - trailing slash variation,
  - near-duplicate title casing.

In other words, workers in M1 validate orchestration mechanics, not final retrieval quality. Parsed filters are available in state, but mock workers do not yet constrain output using those fields.

These fixtures are deliberate and make dedup behavior demonstrable.

### 6.4 Aggregate node responsibilities

The aggregate node performs fan-in and a lightweight URL-based merge pass:

- combine existing scraped_courses with new worker buffers,
- strip trailing slash variance for quick duplicate collapse,
- enforce hard cap at 200 merged entries.

This is not the full dedup logic. It is a pre-filter at merge time.

### 6.5 Practical performance impact

With three workers each waiting 1.5 seconds, sequential execution would take about 4.5 seconds minimum plus overhead. With fan-out, wall-clock time is close to one worker duration plus merge overhead.

As real I/O replaces mocks in later milestones, the relative gain from parallelism becomes significantly larger.

---

## 7. LLM Nodes Done Right

This pipeline has three LLM-backed nodes in M1: gateway, synthesizer, and router. They all follow the same reliability strategy: temperature zero, schema-constrained outputs where needed, and explicit fallback behavior.

### 7.1 Gateway node: structured parsing into SearchFilters

Gateway uses ChatGoogleGenerativeAI with model gemini-2.5-flash-lite and temperature set to zero.

```python
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)
structured = llm.with_structured_output(SearchFilters)
result = structured.invoke([
	 SystemMessage(content=GATEWAY_SYSTEM_PROMPT),
	 HumanMessage(content=query),
])
```

This approach gives two major safety properties.

1. Output is validated against SearchFilters, so missing/invalid fields cannot silently propagate.
2. Deterministic temperature improves reproducibility for debugging and evaluation.

Boundary note: this is strong parser-level reliability, not yet full data-plane policy enforcement in M1, because mock workers are intentionally static.

#### RESET merge behavior

On RESET routing, gateway appends manager feedback as override context and merges parsed fields into existing filters via model_copy(update=...). This keeps prior state while applying explicit changes, instead of resetting to a blank slate.

#### Failure behavior

If parsing fails (for example missing API key or model failure), gateway returns error and forces routing_decision to DISCARD. This is fail-closed design, not "best effort" drift.

### 7.2 Synthesizer node: controlled generation with fallback

Synthesizer consumes deduplicated_courses and applies deterministic ranking:

- free-first,
- rating descending,
- recency descending.

It then caps to top 15 and generates one highlight per course.

```python
courses = _rank_courses(state.get("deduplicated_courses", []))
top_courses = courses[:15]
```

Each highlight call has one retry.

- Attempt 1: normal LLM invocation.
- Attempt 2: immediate retry on failure.
- On second failure: deterministic fallback sentence using raw course fields.

This ensures the pipeline completes even under intermittent model errors.

#### REWRITE mode

When router action is REWRITE, synthesizer receives rewrite_instructions and injects them into the system prompt clause. Critically, this path reuses existing deduplicated_courses rather than re-scraping.

That keeps rewrite cycles fast and cheap while preserving content selection.

### 7.3 Router node: classification, not generation

Router is also an LLM node, but its output is a strict RoutingDecision schema.

- Direct keyword approvals are short-circuited to PUBLISH for low-latency happy path.
- Otherwise, structured classification maps feedback into one of five actions.

It increments iteration_count on decision outputs and enforces max-iteration guard to avoid endless loops.

### 7.4 Logging is not optional

All three nodes emit structured logs with event names, and most runtime node logs include run id for traceability. For LLM systems, logging is part of correctness, not just observability:

- you can trace decision lineage,
- inspect retry/fallback frequency,
- identify systemic prompt failures instead of anecdotal ones.

---

## 8. Human-in-the-Loop with interrupt_before

This is the centerpiece pattern of the implementation.

### 8.1 What interrupt_before does

When the graph is compiled with interrupt_before set to telegram_gate, execution pauses right before that node and returns control to application code.

At this point, state already contains the generated digest.

### 8.2 CLI review loop in M1

The human review interface in M1 is terminal-based and implemented in main.py.

Flow:

1. Invoke graph with initial state and configurable.thread_id = run_id.
2. Graph runs until interrupt and returns paused state.
3. CLI prints digest for review.
4. PM enters feedback text.
5. Application writes feedback into checkpoint state.
6. Application resumes graph with ainvoke(None, config).

Code shape:

```python
result = await graph.ainvoke(_initial_state(query, run_id), config)
...
graph.update_state(config, {"manager_feedback": pm_feedback})
result = await graph.ainvoke(None, config)
```

This pair, update_state plus ainvoke(None), is the practical interrupt/resume contract.

### 8.3 Why this is safer than polling designs

A naive architecture might run an infinite loop that polls for approval while holding mutable process state. That creates three classes of problems:

- crash loses in-memory context,
- polling latency delays response,
- concurrent runs become race-prone.

Interrupt-based resumability solves this by externalizing run progression into checkpoint-backed graph state keyed by thread id.

### 8.4 Current scope boundary

In Milestone 1, telegram_gate is intentionally a no-op node used as an interrupt marker, while actual human interaction lives in CLI code. Telegram bot integration is a Milestone 2 concern and does not change the core pause/resume pattern.

---

## 9. Multi-Path Routing with Conditional Edges

Routing is where a workflow becomes an agent instead of a one-shot generator.

### 9.1 The action set

The router recognizes exactly five actions:

- PUBLISH
- REWRITE
- AUGMENT
- RESET
- DISCARD

These are encoded as a strict enum and used by conditional edges.

### 9.2 Router logic in M1

Router behavior is layered:

1. Guard: if iteration_count >= max_iterations, force DISCARD.
2. Guard: if feedback empty, DISCARD with reason.
3. Shortcut: direct approval words map to PUBLISH.
4. Else: LLM structured classification into RoutingDecision.

This gives deterministic handling of common cases while still supporting nuanced textual feedback.

### 9.3 Edge wiring

Router output dispatches like this:

- PUBLISH -> publish_node -> END
- REWRITE -> synthesizer
- AUGMENT -> augment_dispatch
- RESET -> gateway
- DISCARD -> discard_node -> END

This topology keeps policy in graph edges, not hidden in node internals.

### 9.4 Important implementation caveat: AUGMENT in current code

The graph scaffolds AUGMENT fan-out selection logic, and the helper function can choose worker targets based on feedback hints. However, the augment_dispatch node currently returns an empty dict and does not itself transform state.

What this means for article readers:

- You can study the architecture and routing shape now.
- Full end-to-end augment accumulation behavior is partially scaffolded in M1 and should be treated as an incremental completion item.

This caveat is intentional here to keep implementation claims accurate.

### 9.5 Loop safety

The max_iterations guard (default 3) prevents infinite refinement loops. Agent loops without hard stopping conditions are failure-prone in real operations, especially when feedback is ambiguous.

---

## 10. Checkpointing: Resume Semantics and Persistence Limits

Checkpointing is the operational backbone of this architecture, but in M1 it is scoped to in-process resumability.

### 10.1 What MemorySaver gives us now

In M1, the graph uses MemorySaver.

- zero infrastructure setup,
- ideal for local demo and article walkthrough,
- supports pause/resume semantics for a single process lifecycle.

It does not survive process restart, so this milestone does not provide crash/restart safety. That limitation is acceptable for M1 and explicitly documented.

### 10.2 What gets checkpointed

State transitions are captured as nodes complete. Because execution is keyed by thread_id, subsequent invocations with the same id can resume from the interrupt boundary and continue routing.

In practice, this means:

- you do not rebuild digest context manually,
- human feedback can be applied to the same run,
- loop progression remains consistent across resume calls.

### 10.3 Path to production

Milestone 4 replaces in-memory checkpointing with Postgres-backed saver. The architectural model does not change. You keep the same graph semantics and swap persistence backend.

That is a core design benefit: orchestration logic remains stable while infrastructure maturity increases.

---

## 11. LangGraph Best Practices Recap

The most useful lessons from this implementation are architectural, not prompt-level.

1. Keep deterministic nodes deterministic.
   Set temperature to zero for parsing, classification, and controlled synthesis tasks.

2. Use schema-constrained outputs.
   Prefer structured output into Pydantic models over free-form JSON parsing.

3. Treat state as the only source of truth.
   Avoid hidden mutable globals between nodes.

4. Separate parallel writes from merge points.
   Worker-specific buffers plus one aggregate mutation point reduce race-like complexity.

5. Fail closed on critical errors.
   Gateway and router error paths should route to discard-safe outcomes.

6. Require human approval before side effects.
   interrupt_before is a first-class safety boundary.

7. Cap loops and outputs.
   max_iterations and top-N digest limits prevent runaway behavior.

8. Log every important transition.
   Event names, run id, and decision metadata are essential for debugging and evaluation.

9. Write graph topology before node internals.
   Clear control flow upfront prevents spaghetti routing.

---

## 12. Known Limitations and Gray Areas (Current M1)

These are practical boundaries that matter for operators and reviewers.

1. Parsed filters are not yet strict retrieval constraints in workers.
   Gateway parsing is schema-validated, but mock workers are fixture-based.

2. Dedup is deterministic but heuristic.
   URL normalization + fingerprint + fuzzy title ratio are strong for obvious duplicates, but still prone to edge-case false positives/negatives.

3. Fuzzy dedup is order-sensitive.
   First-kept candidate influences what later titles are considered duplicates.

4. Ranking date parsing has a hard fallback.
   Invalid or unexpected date formats fall back to a baseline timestamp, which can affect ordering.

5. Router shortcut is intentionally narrow.
   Direct approvals are short-circuited only for exact phrases; all other feedback goes through model classification.

6. AUGMENT semantics are scaffolded, not complete.
   Route selection by hint exists, but augment_dispatch is a no-op mutation point in M1.

---

## 13. What Is Next

---

Milestone 1 proves the orchestration skeleton and human-gated run loop with a fully runnable local demo.

The next milestones add realism without replacing core architecture.

- Milestone 2: swap CLI review surface for real Telegram bot interactions and inline actions.
- Milestone 3: replace mock workers with API-backed and crawler-based discovery.
- Milestone 4: add embedding-based cross-run dedup and Postgres checkpoint persistence.
- Milestone 5: production concerns such as scheduling, multilingual support, metrics, and dashboards.

One specific note from current M1 status: AUGMENT routing structure exists, but full augment mutation flow is not yet completed in node behavior. This is a natural next hardening item as the system progresses toward production parity.

The key takeaway is that graph-first design scales: you can add infrastructure and worker sophistication over time without rewriting the orchestration model.

---

## Appendix: Running the Demo

### A.1 Setup

1. Install dependencies.

```bash
uv sync
```

2. Provide API key.

```powershell
$env:GOOGLE_API_KEY="your-key"
```

You can also place GOOGLE_API_KEY in .env; the app auto-loads dotenv when available.

3. Run.

```bash
python main.py
```

### A.2 Expected terminal flow

1. The program asks for a query (or uses default).
2. The graph executes gateway -> workers -> aggregate -> dedup -> synthesizer.
3. Execution pauses before telegram_gate due to interrupt_before.
4. Digest is printed to terminal for review.
5. PM enters feedback (approve, rewrite, augment, reset, discard).
6. Program writes manager_feedback into checkpoint state.
7. Graph resumes with the same run id and routes to next action.
8. Flow repeats until publish or discard, with a safety loop limit.

### A.3 Minimal operator commands for article demo

- approve: publish current digest
- rewrite: regenerate wording using existing deduplicated courses
- augment: request additional options from selected worker sources (scaffolded in M1)
- reset: re-parse constraints and rerun discovery path
- discard: end run without publishing

This is enough to demonstrate all core LangGraph patterns in a single local session.

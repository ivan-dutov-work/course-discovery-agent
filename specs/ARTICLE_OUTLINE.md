# Article Outline: "Using LangGraph to Implement Complex AI Agents"

## 0. TL;DR (opening hook, ~100 words)

Most "AI agents" are just LLM API wrappers duct-taped together. They
hallucinate results, ignore constraints, and publish garbage if you
leave them running overnight. In this article I'll show you how
LangGraph forces you to build agents the right way: explicit state,
parallel execution, mandatory human review before any side-effect, and
crash-safe checkpointing. We'll build a real Course Discovery Agent
that searches for free online courses, deduplicates them across runs,
generates a curated digest, and suspends for your approval before it
publishes anything.

---

## 1. The Problem: Why Naive LLM Wrappers Fail (~400 words)

**1.1 Three failure modes that hurt real users**

- **Hallucinated content.** Ask a bare GPT-4 call for "free Python
  courses with certificate" and it confidently invents plausible-looking
  URLs. The course doesn't exist. Your community gets a 404.
- **Ignored constraints.** "Max price: $0" becomes "$29 course, look how
  affordable!" on the third paragraph. The model optimizes for
  helpfulness, not adherence.
- **Autonomous risk.** A single unchecked LLM call wired to publish
  destroys trust instantly. One bad run, one community upset.

**1.2 What we actually need**

A pipeline where:

- the graph topology (what runs, in what order) is defined by the
  developer, not inferred by the model;
- LLM nodes are stochastic but _controlled_ (temperature=0, structured
  output, logged);
- a human must actively approve before any broadcast occurs;
- a crash mid-run doesn't re-scrape everything from scratch.

This is exactly what LangGraph is designed for.

---

## 2. LangGraph in 5 Minutes (~600 words)

**2.1 What is LangGraph?**

LangGraph is a library for building stateful, multi-actor applications
with LLMs. It models your agent as a directed graph where:

- **Nodes** are Python functions (or async coroutines) that read and
  write a shared state object.
- **Edges** define the execution order. They can be static
  (`add_edge`) or conditional (`add_conditional_edges`), branching
  based on values in state.
- **State** is a `TypedDict` that flows through the entire graph. Every
  node receives the full state and returns a (partial) update.

**2.2 Key concepts used in this article**

| Concept                 | What it does                                                  |
| ----------------------- | ------------------------------------------------------------- |
| `StateGraph`            | Your graph, typed against your state schema                   |
| `add_node`              | Register a node function                                      |
| `add_edge`              | Unconditional flow A → B                                      |
| `add_conditional_edges` | Branch on state value (e.g., router output)                   |
| `Send` API              | Fan-out: run N nodes in parallel, merge results               |
| `interrupt_before`      | Pause graph before a node; wait for human input               |
| `MemorySaver`           | In-memory checkpoint store; persists state across invocations |
| `thread_id`             | Identifies a graph run; required for checkpoint lookup        |

**2.3 When to use LangGraph vs. a simple chain**

Use LangGraph when your agent needs any of: branching, parallelism,
human-in-the-loop pauses, or crash recovery. For a single-turn
LLM call with no routing, a plain `chain.invoke()` is sufficient and
simpler.

_Code snippet:_ Minimal "Hello LangGraph" — a two-node graph with a
conditional edge.

---

## 3. The Agent We're Building (~300 words)

Introduce the Course Discovery Agent at a high level.

- **Input:** free-form user query ("find me free Python courses with a certificate")
- **Pipeline:** parse filters → parallel scrape → dedup → synthesize digest → human approval → publish
- **Key constraint:** nothing is published autonomously.

Include the architecture diagram (Mermaid or ASCII):

```
User query
    │
    ▼
[Gateway Node] ──parse LLM──▶ SearchFilters
    │
    ▼
┌──────────────────────────────────┐
│  Worker A  │  Worker B  │  Worker C  │  (parallel)
└──────────────────────────────────┘
    │ merge
    ▼
[Dedup Node] ──fuzzy match + hash──▶ deduplicated_courses
    │
    ▼
[Synthesizer Node] ──LLM──▶ markdown digest
    │
    ▼
[Telegram Gate] ◀── interrupt_before ── PM reviews here
    │
    ▼
[Feedback Router] ──conditional──▶ PUBLISH / REWRITE / AUGMENT / RESET / DISCARD
```

---

## 4. Designing the State (~500 words)

**The state is the contract between every node.**

Show the full `AgentState` TypedDict and explain each field's role.
Establish the rule: _nodes never talk to each other directly — they
only read and write state._

```python
from typing import TypedDict, Optional
from agent.models import SearchFilters, CourseCandidate, RoutingDecision

class AgentState(TypedDict):
    search_filters: Optional[SearchFilters]
    scraped_courses: list[CourseCandidate]
    deduplicated_courses: list[CourseCandidate]
    digest: Optional[str]
    manager_feedback: Optional[str]
    rewrite_instructions: Optional[str]
    routing_decision: Optional[RoutingDecision]
    iteration_count: int
    max_iterations: int
    run_id: str
```

Highlight the `CourseCandidate` Pydantic model as the shared data
contract between workers and downstream nodes. Show key fields: `title`,
`url`, `price`, `rating`, `certificate_type`, `source_worker`.

**Practice: keep state flat.** Nested dicts hide bugs; TypedDict
surfaces them at type-check time.

---

## 5. Building the Graph Skeleton (~400 words)

Show the complete graph construction before filling in any node logic.
Readers should see the full shape of the agent at a glance.

```python
from langgraph.graph import StateGraph, END
from agent.state import AgentState

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

builder.set_entry_point("gateway")
# ... edges shown below
```

Explain: write the skeleton first, then fill nodes. This forces you
to think about data flow before implementation.

---

## 6. Parallel Execution with the Send API (~600 words)

**6.1 The fan-out pattern**

LangGraph's `Send` API lets you route to the same or different nodes
with different state payloads — achieving true parallel execution.

Show how the gateway node returns a list of `Send` objects to trigger
all three workers simultaneously:

```python
from langgraph.types import Send

def gateway_node(state: AgentState) -> list[Send]:
    filters = parse_filters(state["user_query"])  # LLM call
    return [
        Send("worker_a", {**state, "search_filters": filters}),
        Send("worker_b", {**state, "search_filters": filters}),
        Send("worker_c", {**state, "search_filters": filters}),
    ]
```

**6.2 Aggregating results**

The `aggregate_workers` node merges `scraped_courses` lists from all
worker branches, enforces the 200-item hard cap, and deduplicates
within-run by URL before passing to the Dedup node.

**6.3 Why this matters**

Without parallel execution, three sequential workers with 5s simulated
latency each = 15s minimum. With `Send`, all three complete in ~5s.
In production with real API calls, this is a 3–10× wall-clock saving.

**Practice: isolate state mutations.** Each worker writes only to its
own list and returns it; the aggregator merges. Never have two nodes
write to the same state key concurrently.

---

## 7. LLM Nodes Done Right (~700 words)

Walk through the two most important LLM nodes: Gateway and Synthesizer.

**7.1 Gateway Node — Structured Output with Pydantic**

Show the pattern: LangChain `with_structured_output` tied to a Pydantic
model. Temperature=0. System prompt that includes the user's detected
language.

```python
from langchain_openai import ChatOpenAI
from agent.models import SearchFilters

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
structured_llm = llm.with_structured_output(SearchFilters)

def gateway_node(state: AgentState):
    result: SearchFilters = structured_llm.invoke([
        SystemMessage(GATEWAY_SYSTEM_PROMPT),
        HumanMessage(state["user_query"]),
    ])
    return {"search_filters": result}
```

Explain: `with_structured_output` forces the LLM to return a valid
`SearchFilters` object or raise a validation error — no manual JSON
parsing, no silent field omissions.

**Best practice: log every LLM call.** Show a simple decorator or
LangSmith callback that records prompt + response + latency. You cannot
debug a hallucinationg agent you cannot observe.

**7.2 Synthesizer Node — Controlled Generation**

Show how the Synthesizer iterates over `deduplicated_courses`, calls
the LLM once per course for a highlight summary, and assembles the
markdown digest.

Show the REWRITE path: when `rewrite_instructions` is set in state,
inject it into the system prompt as "Apply this feedback: {instructions}"
and regenerate — no re-scraping, just a text diff.

**Best practice: one retry, then degrade gracefully.** On LLM failure,
retry once with the same prompt. On second failure, emit raw fields
without summaries. Never stall the pipeline.

---

## 8. Human-in-the-Loop: The interrupt_before Pattern (~700 words)

**This is the most important section of the article.**

**8.1 What interrupt_before does**

Compiling the graph with `interrupt_before=["telegram_gate"]` tells
LangGraph to pause execution _before_ that node runs and save a
checkpoint. The graph returns control to your application code. No
state is lost. The graph will resume exactly from this point.

```python
graph = builder.compile(
    checkpointer=MemorySaver(),
    interrupt_before=["telegram_gate"],
)
```

**8.2 Running to the interrupt**

```python
config = {"configurable": {"thread_id": run_id}}
# First invoke — runs until interrupt_before=telegram_gate
result = graph.invoke({"user_query": query, ...}, config)
# At this point, the digest is in state. Graph is paused.
print(result["digest"])
```

**8.3 Resuming after human feedback**

The PM reads the digest and types feedback. We write it back into
state and call `invoke` again with the same `thread_id`. LangGraph
loads the checkpoint and continues from the pause point.

```python
pm_input = input("Your feedback (or 'approve'): ")
# Update state at the interrupt point
graph.update_state(config, {"manager_feedback": pm_input})
# Resume — runs Feedback Router and beyond
final = graph.invoke(None, config)
```

**8.4 Why this beats a polling loop**

Common anti-pattern: `while True: check_for_feedback(); sleep(5)`.
Problems: state lives in memory (lost on crash), poll interval adds
latency, concurrent runs step on each other.

LangGraph's interrupt pattern solves all three: state is in the
checkpointer, resume is event-driven, and each `thread_id` is an
isolated execution context.

**8.5 The CLI demo**

Show the full terminal session: query → digest printed → PM types
feedback → router fires → PUBLISH logs the digest. Readers can run
this themselves with `python main.py`.

---

## 9. Multi-Path Routing with Conditional Edges (~600 words)

**9.1 The routing matrix**

Revisit the five paths:

| Decision | Next node         | State effect                                     |
| -------- | ----------------- | ------------------------------------------------ |
| PUBLISH  | END               | Broadcasts digest                                |
| REWRITE  | synthesizer       | Uses `rewrite_instructions`                      |
| AUGMENT  | worker_b (or all) | Appends new results, full dedup re-run           |
| RESET    | gateway           | Merges filter overrides, clears courses + digest |
| DISCARD  | END               | Logs reason, no broadcast                        |

**9.2 Implementing the router**

```python
from langchain_openai import ChatOpenAI
from agent.models import RoutingDecision

router_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
structured_router = router_llm.with_structured_output(RoutingDecision)

def router_node(state: AgentState):
    if state["iteration_count"] >= state["max_iterations"]:
        return {"routing_decision": "DISCARD"}
    decision = structured_router.invoke([
        SystemMessage(ROUTER_SYSTEM_PROMPT),
        HumanMessage(state["manager_feedback"]),
    ])
    return {
        "routing_decision": decision.action,
        "iteration_count": state["iteration_count"] + 1,
    }
```

**9.3 Wiring conditional edges**

```python
builder.add_conditional_edges(
    "router",
    lambda state: state["routing_decision"],
    {
        "PUBLISH": "publish_node",
        "REWRITE": "synthesizer",
        "AUGMENT": "worker_b",
        "RESET": "gateway",
        "DISCARD": END,
    },
)
```

**Best practice: guard the loop with max_iterations.** An agent that
loops forever is not robust — it's broken. Enforce a ceiling and
surface the failure to the operator.

---

## 10. Checkpointing: Crash Safety for Free (~400 words)

**10.1 MemorySaver vs. PostgresSaver**

|                | MemorySaver        | PostgresSaver                |
| -------------- | ------------------ | ---------------------------- |
| Persistence    | In-process RAM     | Postgres table               |
| Survives crash | No                 | Yes                          |
| Suitable for   | Dev / article demo | Production                   |
| Setup          | Zero               | `psycopg` + schema migration |

For this article we use `MemorySaver`. The switch to `PostgresSaver`
at M4 requires one line change: swap the checkpointer argument.

**10.2 What gets checkpointed**

After every node completion, LangGraph writes the full `AgentState`
to the checkpointer under the `thread_id` key. This includes partially
accumulated `scraped_courses` from parallel workers. On resume (same
`thread_id`), the graph starts from the last successful node —
no re-scraping of already-completed API calls.

Show: calling `graph.get_state(config)` to inspect the checkpoint
between nodes. Useful for debugging.

**Best practice: always pass `thread_id`.** Without it, each invoke
starts a new execution context. With it, you get resumability for free.

---

## 11. LangGraph Best Practices Recap (~300 words)

Consolidate the practices scattered through the article:

1. **Temperature=0 on every deterministic node.** Gateway, Synthesizer,
   Router — you want the same output for the same input. Non-zero
   temperature makes logs useless and bugs non-reproducible.
2. **Log every LLM call.** Prompt, response, latency, model, token
   count. Use LangSmith or a simple structured log.
3. **Never publish autonomously.** `interrupt_before` the broadcast
   node. One bad run with auto-publish costs you trust you can't buy back.
4. **Guard loops with max_iterations.** Define it in state, decrement
   on each feedback loop, force-DISCARD at 0.
5. **Fail loudly, degrade gracefully.** On LLM error: retry once, then
   return partial results. Never stall the pipeline silently.
6. **State is the single source of truth.** Nodes do not share mutable
   objects, caches, or globals. If it's not in state, it doesn't exist
   to the graph.
7. **Sketch the graph skeleton before writing nodes.** Topology clarity
   prevents "spaghetti routing" where conditional logic lives inside
   nodes instead of edges.

---

## 12. What's Next (~200 words)

The M1 codebase proves the LangGraph patterns work. The remaining
milestones add production realism:

- **M2:** Replace the CLI HIL stub with a real Telegram bot. The
  interrupt/resume mechanism doesn't change — only the I/O surface.
- **M3:** Swap mock workers for real API calls (Google, YouTube, Udemy,
  Reddit) and a web crawler. The graph topology doesn't change.
- **M4:** Replace `MemorySaver` with `PostgresSaver` and add
  embedding-based cross-run deduplication via pgvector. One import
  change + a new Dedup layer.
- **M5:** Cron scheduling, multilingual support (Ukrainian), metrics,
  and a WebSocket dashboard.

Each milestone adds a layer without restructuring the core graph —
proof that a well-designed LangGraph skeleton scales.

**Link to the repository** (M1 tag / branch).

---

## Appendix: Running the Demo

```bash
git clone https://github.com/your-org/course-discovery-agent
cd course-discovery-agent
cp .env.example .env        # add OPENAI_API_KEY
uv sync
python main.py
```

Expected terminal output walkthrough (annotated screenshot or
code block showing each node firing, the digest, the CLI prompt,
and the routing decision).

---

## Estimated Word Count

| Section                | ~Words     |
| ---------------------- | ---------- |
| 0. TL;DR               | 100        |
| 1. Problem             | 400        |
| 2. LangGraph intro     | 600        |
| 3. Agent overview      | 300        |
| 4. State design        | 500        |
| 5. Graph skeleton      | 400        |
| 6. Parallel execution  | 600        |
| 7. LLM nodes           | 700        |
| 8. Human-in-the-loop   | 700        |
| 9. Conditional routing | 600        |
| 10. Checkpointing      | 400        |
| 11. Best practices     | 300        |
| 12. What's next        | 200        |
| Appendix               | 100        |
| **Total**              | **~5,900** |

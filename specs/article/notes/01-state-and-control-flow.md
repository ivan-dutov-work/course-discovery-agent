# Notes: state and control flow (§2–§3)

Evidence for drafting. Load when working on these sections. Observations are from langgraph
1.1.2 unless marked as documentation.

## Reducers (§2.2)

- Channels written by parallel `Send` workers use `Annotated[..., operator.add]`-style reducers
  so branches don't clobber each other: `tavily_results`, `completed_queries`,
  `research_notes`, `tavily_calls`.
- `extracted_candidates` is a plain overwritten list even though every branch writes it.
  Whether that raises `InvalidUpdateError` with two or more queries is unverified (BACKLOG).
  Don't use it as the example until it is.

## Plan-driven `Send` fan-out (§3.2)

- Worker count comes from `research_plan.search_queries`, not from code. Contrast with a
  fixed set of hardcoded parallel branches.
- Boundary to state directly: `Send` is in-process fan-out, not a distributed queue. Work
  distribution across services (SQS, Celery, Temporal) is a different layer LangGraph does
  not replace.
- `max_concurrency` (config) bounds the fan-out; without it a planner that emits many
  queries fires them all. Not covered yet (BACKLOG, optional).

## Subgraphs (§3.3)

- The research graph is compiled and mounted as one node. The outer graph doesn't know its
  internal node count.
- A subgraph compiled without its own checkpointer inherits the parent's; its steps are stored
  under `checkpoint_ns = "research_agent:<task_id>"` in the same saver (observed under the
  pre-rename node name; after the rename it should read `course_research:<task_id>`, not re-observed).
- `get_state_history(config_with_that_ns)` lists every subgraph step. `ainvoke(None,
  snapshot.config)` on the parent replays from exactly that node (`save_verified_courses`
  re-ran once, the run continued to `await_human_review`, tracked state identical).
- `<task_id>` is per execution, so the namespace must be discovered at runtime
  (`saver.alist`, or `get_state(cfg, subgraphs=True)` while paused).
- A subgraph's own checkpointer is ignored when a parent checkpointer exists (an own
  `MemorySaver()` stayed empty). `checkpointer=True` only makes the namespace stable.
- `interrupt_before` inside the subgraph works: `get_state(cfg, subgraphs=True)` shows the
  paused sub-state; resume and later replay both work.
- Not tested: `Command` or dynamic `interrupt()` inside a subgraph; `checkpointer=False`.
- **Cost of the encapsulation:** when one of several parallel `Send` workers fails, resuming
  the outer graph re-runs the workers that already succeeded; the same graph run standalone
  re-runs only the failed one (`tests/test_retries.py`, both directions asserted). See
  `02-durability-replay-effects.md` for the reducer double-count check.
- Alternative if namespace discovery feels fragile: keep mutating nodes at the top level, or
  have the subgraph return effects as data for an outer node.

## `input_schema` / `output_schema` (§2.3, not in code)

- Documentation, verified: `StateGraph(OverallState, input_schema=InputState,
  output_schema=OutputState)`. `invoke()` returns only the output schema's fields, not the
  full internal state. The point to pair with the state-contract section: public API versus
  internal contract.
- Only the outer graph would benefit; the research subgraph has no narrower public contract.
- Needs a working example or a doc-only label.

## `Command` (§3.4, not in code)

- Routing today lives on conditional edges (`_dispatch_search_queries`). A `Command(update=...,
  goto=[Send(...)])` return would fold the routing into the node. Not implemented; label the
  section doc-only or add a small honest use.

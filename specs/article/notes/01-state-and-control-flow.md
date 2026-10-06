# Notes: state and control flow (§2–§3)

Evidence for drafting. Load when working on these sections. Observations are from langgraph
1.1.2 unless marked as documentation.

## Reducers (§2.2)

- Channels written by parallel `Send` workers use `Annotated[..., operator.add]`-style reducers
  so branches don't clobber each other: `tavily_results`, `completed_queries`,
  `research_notes`. A fourth, the `tavily_calls` counter, was removed as a duplicate of
  `metrics.tavily_calls`.
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

## `input_schema` / `output_schema` (§2.3, used by the curator subgraph)

- Documentation, verified: `StateGraph(OverallState, input_schema=InputState,
  output_schema=OutputState)`. `invoke()` returns only the output schema's fields, not the
  full internal state. The point to pair with the state-contract section: public API versus
  internal contract.
- Two working examples now: the curator and the research subgraph (below). The curator in code: `memory_curator/graph.py` compiles
  `StateGraph(CuratorState, input_schema=CuratorInput, output_schema=CuratorOutput)` and mounts it
  as a node of the outer graph.
- Verified with langgraph 1.1.2, langchain-core 1.6.5 (`tests/test_memory_e2e.py`,
  `tests/test_curator_checkpoint.py`): the subgraph reads only the five input keys, its private
  channels (`messages`, `steps`, `proposals`, `finished`, `failure`) never appear in the parent's
  state, and the parent receives only `memory_update`. `feedback_history`, a shared
  `operator.add` channel, comes back unchanged (`[round_one, round_two]`, not doubled), because
  it is an input key and not an output key.
- A subgraph node's parent-level update appears in `astream(..., subgraphs=True)` at namespace
  `()` under the node's name, so `tests/test_flow_rules.py` skips it like `course_research`.

### The research subgraph: private channels need `checkpointer=True`

langgraph 1.1.2, memory saver and Postgres. `build_research_graph` compiles with
`input_schema=ResearchInput` (five keys: `user_id`, `search_filters`, `routing_decision`,
`rewrite_instructions`, `research_pass`) and `output_schema=ResearchOutput` (`valid_courses`, `digest`,
`metrics`, `discard_reason`, `research_pass`); the other 14 channels are private and the outer
`AgentState` has 15, the extra one being `research_retries`, which bounds the stale-result retry.

- **Private is not persistent.** A toy graph (parent loop, subgraph with a private `operator.add`
  channel, parent paused between passes) showed the private channel empty on every re-entry with
  the default checkpointer and carried over only with `compile(checkpointer=True)`. AUGMENT
  re-enters at `plan_gap_search` and reads `completed_queries`, `research_plan` and
  `validation_results` from the previous pass, so the research subgraph needs it. With it the
  subgraph's checkpoints live in the fixed namespace `course_research` (no task id), readable
  with `checkpointer.aget_tuple({... "checkpoint_ns": "course_research"})`
  (`tests/research_view.py`). `get_subgraphs()` and `aget_state` on the compiled node did not find
  it ("Subgraph course_research not found").
- **A stateful subgraph that is the first task of a resumed step ignores its new input.** After
  `update_state(..., as_node="interpret_review_feedback")` with `routing_decision=AUGMENT`,
  `astream(None)` ran `course_research` for zero steps and returned the previous pass's
  `valid_courses`. The same happens after a crash between the router and the start of the
  subgraph. Mechanism confirmed in langgraph 1.1.2 `pregel/_loop.py`: `_first` sets `CONFIG_KEY_RESUMING` for the whole first tick when the input is `None`, a stateful subgraph with a saved checkpoint takes its resuming branch and never applies the new input, and the flag is popped only at the end of the tick (line 573). The normal
  path (`update_state` with `manager_feedback`, then `astream(None)`) does not hit it because
  `await_human_review` is the first task. A no-op node in front of the subgraph only moved the problem: with `update_state(as_node="start_research_pass")` the round was still skipped (found in review). Fix: `start_research_pass` stamps `research_pass`, the subgraph echoes it, and a mismatch after `course_research` returns to `start_research_pass`, where the flag is spent. `tests/test_state_hygiene.py` covers both `as_node` windows and fails when the retry edge is removed.
- A candidate-list view of the subgraph's history is still in the checkpoints (`checkpoint_ns`
  `course_research`); `tests/test_nested_checkpoints_postgres.py` shows those rows are sealed under
  `CHECKPOINT_ENCRYPTION_KEYS` (a canary in the query appears in 4 plaintext rows without keys, 0
  with) and that `adelete_thread` removes them.
- **Output keys must be parent channels.** A planning failure therefore leaves through `discard_reason`, which the outer graph already routes on (`tests/test_state_hygiene.py`).
- **`Overwrite` clears an `operator.add` channel** (langgraph 1.1.2): `begin_pass` returns `Overwrite([])` for the ledger, notes and results on a fresh pass; a RESET test fails without it.

## `Command` (§3.4)

- `verify_course_claims` returns `Command(update=..., goto=...)` (langgraph 1.1.2). The graph
  drops its conditional edge; the compiled graph still has both `verify_course_claims` edges,
  because the return annotation `Command[Literal["save_verified_courses", "plan_gap_search"]]`
  declares them (`tests/test_graph_topology.py`). Without the annotation LangGraph cannot see the
  destinations, so the graph drawing and the edge test would lose them; routing itself still works.
- Fan-out stays on `_dispatch_search_queries`: it needs the plan the planner has already written,
  and a conditional edge reads it after the update commits.
- Verified with a two-node graph on langgraph 1.1.2: without the annotation the routing runs but
  the drawn graph has an edge to `__end__` and none to the target. Not tested: a kill and resume
  while the `Command` node is the pending task inside the `course_research` subgraph.

## Removing a field from a checkpointed model

Verified with langgraph 1.1.2 and the repo's msgpack allowlist serde: a `ResearchPlan` written
with `cache_query`, then loaded after the field was deleted from the model, restores as a
`ResearchPlan` without the field and with no serde warning. Pydantic ignores the extra key.
Removing a field is safe for old checkpoints; adding a required one is not (old payloads lack
it). Pinned by `tests/test_checkpointer.py`.

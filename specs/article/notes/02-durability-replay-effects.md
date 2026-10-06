# Notes: durability, replay and effects (§4–§6)

Evidence for drafting. Load when working on these sections. Observed on langgraph 1.1.2
unless marked as documentation. Rule that ties them together: checkpoints land at superstep
boundaries; completed nodes are not re-run on resume, the interrupted node is re-run from the top.

## Human review (§4)

- **`interrupt_before` as a durable boundary (§4.1).** Contrast with an `input()` prompt: the
  graph checkpoints and can resume in a new process. Tested: `tests/test_integration_postgres.py`
  runs on `AsyncPostgresSaver`, then a fresh saver and fresh graph read the thread back,
  replay, and resume through approve and publish. That is a new saver in the same OS process;
  the SIGKILL test covers crash recovery separately.
- **Dynamic `interrupt()` (§4.2, not in code).** Called inside a node at runtime, so review can
  be conditional (only low-confidence evidence pauses). A dynamic pause elsewhere needs
  `get_state(config).next` to find which node is paused, and resumes with
  `Command(resume=...)`, a different call from the static gate's `update_state` plus
  `ainvoke(None)`. Must not create a path to `send_approved_courses` that skips
  `await_human_review`.
- **Five outcomes (§4.3).** PUBLISH, REWRITE, AUGMENT, RESET and DISCARD are a deliberate
  design point; most write-ups show a binary gate.
- **What LangGraph doesn't own (§4.3).** A paused thread sits in the checkpointer with no TTL.
  Notifying a human is app-layer: self-hosted LangGraph has no push, only `get_state` showing
  where execution is parked (the managed platform has run webhooks, unverified trigger
  semantics). `update_state` before resuming lets a reviewer patch state, not just gate it.
  Two callers can resume the same `thread_id`; that is an app-level lock (§11.3).
- **`recursion_limit` (§4.4).** Config parameter raising `GraphRecursionError`, a structural
  backstop underneath the domain budget `max_research_iterations`. Two independent guardrails,
  one generic, one domain-aware.

## Durability (§5)

- **Modes.** `durability="exit" | "async" | "sync"`. Test
  `test_durability_modes_differ_in_checkpoint_granularity_not_in_resume`, against
  `AsyncPostgresSaver` with a late transient failure: `sync` and `async` wrote about 12–14
  checkpoints, `exit` wrote 2. In all three the failed run resumed with `ainvoke(None, config)`
  without re-running `verify_course_claims`, because `exit` still checkpoints when the graph
  exits via an exception. What `exit` loses is history granularity and anything in flight at a
  hard kill.
- **Real kill.** `tests/test_integration_kill.py` SIGKILLs a child inside
  `save_verified_courses`. With `sync`, a fresh process resumes without re-running
  `verify_course_claims`. With `exit` the thread has no checkpoints and restarts from scratch.
  `async` is untested in that file: whether the last background write lands is a race.
- **Library default and the CLI (read from source, langgraph 1.1.2).**
  `.venv/lib/python3.13/site-packages/langgraph/pregel/main.py:2429-2430`: `_defaults` takes
  `config[CONF][CONFIG_KEY_DURABILITY]` and falls back to `"async"` when the argument is `None`;
  `stream` and `astream` both call it (the `astream` docstring says "defaults to `async`"). The
  CLI has one call site, `graph.astream` in `_stream_until_pause` (`app/cli.py`), used for the first
  run and the resume, and it now passes `durability="sync"`. `aupdate_state` has no such argument.
  `tests/test_cli_durability.py` spies on the compiled graph through `_run` and fails naming the
  call when the kwarg is missing.
- **Observed (3 trials, 2026-10-06, Postgres, same code path).** `tests/kill_child_cli.py` starts a
  run through `_stream_until_pause` and SIGKILLs itself inside `stage_courses`; a fresh process
  resumes through the same function and counts `evidence_validator_node` calls. With
  `durability="sync"`: 0 validator calls, parked at `await_human_review`. With the kwarg stripped
  (library default `async`): 1 validator call in 3 of 3 trials, so the work done before the kill
  was not all on disk. One scenario and three trials: not a general statement about `async`.
  The `library_default` variant in `tests/kill_child_cli.py` reproduces it but is not run by the
  suite, because the outcome is a race: start the child with that argument and resume it with
  the same counter. The evals runner (`evals/runners.py`, one `astream` call) does not pass
  `durability`, so it runs on the default; only the CLI is changed.
- **Replay determinism.** An effect node derives its payload only from checkpointed state;
  the search or LLM call that produced the input lives in an earlier node. Audit:
  `send_approved_courses`, `save_verified_courses` and `record_review_outcome` build from
  state and call no LLM; `run_id` is minted by the CLI (`uuid4()`) before the graph starts, so
  `publish:{run_id}` is stable. Extraction, planning and validation are deterministic; the
  fragility is a real search provider returning changing snippets, not an LLM.
- **`@task` memoization.** Experiments, scripts not kept, each crash timing run once:
  - Works in a plain `StateGraph` node, sync or async; on a top-level node, completed tasks
    short-circuit on resume with the identical stored value.
  - **Did not memoize inside a subgraph** (tasks re-ran with new values), including with
    subgraph `checkpointer=None` or `True` and after `interrupt()`. Mechanism not identified.
    The research pipeline is a subgraph, so `@task` would not protect it. Reproduce before citing.
  - Matching is by function name and call index, not arguments: `A(99)` after a run that
    called `A(1)` returned the stale `A(1)` result. Task id composition is from reading source.
  - Scope is per superstep: replan iterations get fresh executions; replay from an old
    checkpoint re-runs tasks.
  - The result is a pending write, written asynchronously: SIGKILL right after the effect ran
    it twice on resume in `sync` and `async`; with a 0.3 s gap, once. `exit` persisted nothing.
    Not exactly-once.
  - Return types must be in the msgpack allowlist or the value comes back as a plain dict.
  - `@task(retry_policy=...)` is in-run only; `cache_policy` is key-based and cross-thread;
    neither is the resume memoization.
- **Resume cost in a subgraph.** Resuming re-runs successful sibling `Send` workers (extra
  spend; `tests/test_retries.py` counts calls). It does not double-count reducer output: after
  a subgraph resume `tavily_calls == len(set(completed_queries))`, same as standalone.
- **A shared reducer channel double-counts across passes, not across a crash resume.** The
  subgraph returns its whole channel value to the parent, and the parent's reducer adds it to
  what it already holds. Measured on langgraph 1.1.2 (memory saver, no LLM key, two consecutive
  `rewrite:` rounds): `tavily_calls` and `len(completed_queries)` went 1, 2, 4 across rounds,
  and a new `operator.add` channel `feedback_history` went `[a]` to `[a, a, b]`. Fix for a
  channel the subgraph never uses: build the subgraph on a state schema without it
  (`domain/state.py:ResearchState`), so it is neither passed in nor echoed back. Not applicable
  to `completed_queries`, `tavily_results` and `research_notes`, which the parent needs from the
  subgraph (AUGMENT re-enters at `plan_gap_search` and reads them). Fixed by declaring the
  reducer only on the subgraph's schema (`ResearchState`, where the `Send` branches meet) and
  leaving the parent's copy a plain list: the subgraph receives the parent's value, appends to
  it, and the parent overwrites with the result. Before the fix, two consecutive `rewrite:` rounds
  gave `len(completed_queries)` 2, 4, 8 and `tavily_results` 10, 20, 40; after it 2, 2, 2 and 10,
  10, 10 (a REWRITE plans no new query because every planned one is already in
  `completed_queries`), and `augment:` gave 2, 3, 3. Pinned by `tests/test_state_hygiene.py`
  (fails on the earlier schema) and, for `feedback_history`, by `tests/test_memory_e2e.py`.
- **Budgets in `configurable` are per invocation, not per thread.** langgraph 1.1.2, memory saver,
  no LLM key: a run started with `max_review_rounds=1` and resumed with a config that omits the key
  ran its second review round on the default (3) instead of discarding
  (`tests/test_top_level_state.py`). The checkpoint stores the run's state, not the caller's
  config. `max_research_iterations` behaves the same: 0, 1 and the default gave 0, 1 and 2
  replans on an all-rejected run. `tavily_calls` is no longer a channel; `metrics.tavily_calls`
  is `len(completed_queries)`.
- **Removing channels breaks resume of an old pause (cause isolated, verified on Postgres).**
  langgraph 1.1.2. A thread paused under the old graph (`725f85a`: `run_id`, `iteration_count`
  and four more channels) and resumed under the slimmed one accepts `update_state` and then
  `astream(None)` yields nothing: no error, still parked at `await_human_review`. Mechanism, from
  reading source: on resume `pregel/_loop.py` (~L718) records "seen at last interrupt" only for
  channels the *new* graph has; `should_interrupt` (`_algo.py` ~L150) re-interrupts while any
  version in the checkpoint's `channel_versions` is newer than that record. A channel that no
  longer exists keeps its version forever, so the gate re-fires on every resume. The earlier
  pickle-round-trip observation was incidental: the toy graph reproduces it on a shared in-memory
  saver. Only resuming from an `interrupt_before` is affected; a stale channel with no interrupt
  configured completes. See "Schema evolution" below.

## Schema evolution of a paused thread (verified, langgraph 1.1.2)

Method: pause a thread under the old graph, build the new graph on the same saver, `invoke(None)`,
classify the outcome as `RESUMES`, `STUCK` (still parked) or `SILENT_FINISH` (`next == ()` but the
downstream node never ran). A 17-variant toy graph (`a -> gate[interrupt_before] -> c`) on the
in-memory saver and on `AsyncPostgresSaver` gave identical results, then the real graph on Postgres
(old `725f85a` against the working tree). Scripts not kept.

| Change between pause and resume | Outcome |
|---|---|
| add a channel, with or without a writer; add or remove a reducer; change a channel's type | resumes. The type is not checked: a `str` channel keeps its old `int` |
| add a node after, before or between existing nodes; rename a node not yet reached | resumes |
| remove or rename a channel, even an unused one | **STUCK** |
| remove a node that already ran in this thread (its `branch:to:<node>` channel is stale) | **STUCK**. A node that never ran in this thread (branch not taken) is safe |
| remove or rename the node the thread is parked at | **SILENT_FINISH**: the thread ends as if complete and nothing downstream runs |
| Pydantic field with a default added or removed | resumes; defaults are filled |
| Pydantic field added without a default, renamed, or retyped | resumes, then fails lazily: the value is rehydrated without validation, so `AttributeError` at first read, or the old type persists |
| Pydantic class moved or renamed | value comes back as a plain `dict` (not in the msgpack allowlist) |

**It is predictable from the checkpoint alone.** Stale channels are
`set(checkpoint.channel_versions) - set(new_graph.channels)`. Pending nodes are the
`branch:to:<node>` keys present in `checkpoint.channel_values` (consumed trigger channels are
re-versioned but carry no value, so version comparison misclassifies them). Rule: a pending node
missing from the new graph is `SILENT_FINISH`; any stale channel on a graph with `interrupt_before`
is `STUCK`; otherwise `RESUMES`. It matched all 17 toy variants on both savers and the real graph
(predicted stale set equals the six removed channels).

**It is repairable for everything except a deleted pending node.** Writing a new checkpoint
(`aput`, parent = the old one) that renames moved channels in `channel_values`, `channel_versions`,
`versions_seen` and `updated_channels`, and drops every stale key, made every `STUCK` variant
resume with values intact (a renamed channel carried its value into the new name), and made a
renamed pending node resume. `updated_channels` matters: without renaming inside it the renamed
node is not scheduled. On the real graph the repaired thread approved to `publish_status=delivered`,
and a `rewrite:` re-entered `course_research` and parked at review again. A deleted pending node has
no repair, only a refusal, since the intent (the review step) is gone.
- **Approved digest equals published digest.** `send_approved_courses` reads `digest` from
  checkpointed state. The router LLM can classify differently if re-run after a crash, but the
  reviewer's text is already in `manager_feedback`. Not tested end to end.
- **Small replay side effects.** `save_verified_courses` uses `now()` for
  `last_seen_at`; the only reader is a tie-break, so a replay reordering ties is benign.
  `publish_status` can differ between a run and its replay under `InlineGateway`
  (`DELIVERED` vs `QUEUED`); `record_review_outcome` treats both as accepted, but any future
  branch on it would be replay-sensitive.
- **`Store` vs. checkpointer (§5.6).** Checkpointer is thread-scoped run state, what makes
  `interrupt_before` resumable. `Store` is cross-thread memory, needing explicit `get` and
  `put` in node code. The hand-rolled `UserMemory` table is what `Store` is for; name it as
  "the API for what we hand-rolled." Doc-and-experiment only (node injection, cross-thread
  visibility and Postgres TTL checked). `Store` has `TTLConfig`; the checkpointer does not.

## Effects (§6)

- **Idempotent writes (§6.1).** Found by reading code: `courses` upserted but `course_evidence`
  was a plain `INSERT`, and `recommendation_events` too, so replay duplicated rows and
  double-recorded feedback. Fixed in `migrations/002` and `004`: `recommendation_events`
  carries `idempotency_key = {run_id}:{course.url}` (NULL without a `run_id`, so no dedup);
  `course_evidence` is keyed `(course_id, source_url)` with `DO UPDATE`, trade-off one row per
  source, last write wins, no history. Replay tests on in-memory and Postgres savers; with the
  indexes dropped and the code fix removed, all four replay tests fail `10 != 5`.
- **Failure that was swallowed.** `recommendation_events.user_id` has an FK to `users`,
  nothing created the user, and `record_feedback` logged the error and returned, so on a fresh
  DB the CLI's feedback was silently dropped. Now `record_feedback` inserts the user row in
  the same transaction and both writers re-raise. Tests: unknown user end to end, and a
  `CHECK (false)` constraint proving the failure surfaces, the run stays paused at
  `record_review_outcome`, and no partial rows land. Good example for "fail closed, visibly."
- **Outbox (§6.2).** Port: nodes call `EffectGateway.submit(Effect(key, kind, payload))` and
  never the external system. The adapter inserts a row (`ON CONFLICT (key) DO NOTHING`); the
  node writes `queued`, not `done`. Keys derive from state, never generated at execution time,
  or replay mints a new key. Worker: separate process, claims rows with `FOR UPDATE SKIP LOCKED`,
  dispatches by `kind`, backs off, dead-letters. The port also has `status(key)`; attempts are
  counted at claim time so a record that keeps crashing its worker dead-letters;
  `InlineGateway` attempts delivery once during `submit`; `PermanentEffectError` and an unknown
  `kind` dead-letter immediately. Two recovery loops: the checkpointer resumes the graph, the
  worker retries the effect. State contract cost: `published: bool` became `publish_status`.
  Atomicity limit: the outbox row could share a transaction with a domain write, never with
  the checkpointer write; not built. The `publish_digest` handler prints.
- **Multiple effects in one node (§6.3, reasoning only, not implemented).** Each effect has
  its own key; replay re-submits all and existing keys are no-ops; keys must be stable per
  effect, not list position; `DO NOTHING` keeps the first payload if replay recomputes a
  different one; no ordering. `InlineGateway.submit` calls `worker.run_once()` on a
  still-queued record (whether that targets the key or any due row is unchecked).
- **Operations (§6.4).** Dead letters: worker logs `effects_dead_lettered` at error level;
  `python -m course_discovery.effects dead` lists rows, `requeue <key>` resets attempts to 0
  (history lost); no alert sink. Lease versus handler duration: not built (queue territory); a
  handler that outlives `locked_until` can be claimed twice. Growth: `prune --older-than-days N
  [--checkpoints]` deletes delivered rows and, with the flag, threads older than N days by
  `run_threads.last_activity_at`, so a run parked at review longer than N days is deleted too.
- **When a different architecture fits (§6.5).** Temporal, Restate and DBOS give durable
  execution at the activity level, which the outbox plus keys approximates by hand. A short
  "when to reach for it instead" paragraph, not a comparison. Don't claim LangGraph Platform
  background-run semantics without verifying.

- **Removing nodes from a graph with a paused checkpoint (verified, langgraph 1.1.2, Postgres
  checkpointer).** A run was paused at `await_human_review` under the graph that still had
  `start_research`, `prepare_augmented_search` and `end_research_on_error`, then resumed
  (`update_state` as `await_human_review`, `ainvoke(None)`) under the graph without them. It ran
  to `send_approved_courses` and finished with `publish_status` set. The checkpoint stores the
  pending node name, so removing nodes that are not pending is safe; renaming the pending node
  is not. Checked with two checkouts (old at HEAD, new working tree) against one database.
  Refined by "Schema evolution" above: this is consistent with those nodes never having run in
  that thread (not re-checked). Removing a node that did run leaves its `branch:to:` channel stale
  and the thread stuck at the gate.

- **A checkpoint from the pre-boundary graph does not resume (verified, langgraph 1.1.2, Postgres).**
  Paused at `await_human_review` under 98d61f9 (28 outer channels, flat research channels), then
  opened by the current graph on the same database: `aget_state` returns the thread with
  `next == ("await_human_review",)`, but after `aupdate_state(manager_feedback="approve")` and
  `ainvoke(None)` it returns without `publish_status` and stays at the gate. A control thread
  created and approved entirely under the current graph publishes.
- **Resuming that thread re-runs the research pass (verified 2026-10-05, langgraph 1.1.2,
  Postgres, with the `research_pass` change).** Streaming the resume with `subgraphs=True` shows
  `start_research_pass`, then every node of `course_research` from `begin_pass` through
  `rank_and_summarize_courses`, then `__interrupt__`. The search calls, the `pending_courses`
  staging and the synthesis call all run again, and the digest is new. So the earlier "stays at
  the gate" understates it: the failure is a silent re-execution with side effects. Cause: not
  isolated beyond the observed node order; the checkpoint predates the channel that triggers
  `start_research_pass`. The fix is to refuse before the resume (`DECISIONS.md`, "State contract
  changes are versioned"). Pinned by `tests/test_checkpoint_compatibility.py` (a stored v1
  fixture must raise `IncompatibleThreadError` and leave the checkpoint rows unchanged) and
  `tests/test_state_contract.py`. Both fail if the guard or the version bump is removed (checked by
  mutation).
- **Resume into a stateful subgraph (found in review of S1, langgraph 1.1.2).** See
  `01-state-and-control-flow.md`, "The research subgraph": with `checkpointer=True`, a crash or
  `update_state(as_node=...)` that leaves the subgraph node as the first task of the resume makes
  the subgraph skip its new input. `start_research_pass` is the guard.
- **Private subgraph channels through the encrypted Postgres checkpointer (verified, langgraph
  1.1.2).** The curator's `messages` channel holds `SystemMessage`, `HumanMessage`, `AIMessage`
  with `tool_calls` and `ToolMessage`. A full outer run with `AsyncPostgresSaver`, the msgpack
  allowlist and `CHECKPOINT_ENCRYPTION_KEYS` set checkpointed, read back and deleted the thread
  without a serde error (`tests/test_curator_checkpoint.py`). The other private channels hold
  only primitives, and proposals are stored as plain dicts so no new model needs allowlisting.


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
  `async` is untested: whether the last background write lands is a race. The code does not set
  `durability=` explicitly.
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
  to `completed_queries`, `tavily_calls` and `research_notes`, which the parent needs from the
  subgraph (AUGMENT re-enters at `plan_gap_search` and reads them); those need a reducer that is
  idempotent over the echo. Open, `BACKLOG.md` S1. Pinned for the new channel by
  `tests/test_memory_e2e.py`.
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

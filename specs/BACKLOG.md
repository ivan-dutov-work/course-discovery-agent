# Backlog: open work only

Delete an item when it's done. On completion: add a line to `STATUS.md`, put the evidence in
the matching file under `specs/article/notes/`, and record any decision in `DECISIONS.md`.
Definition of done is in `CLAUDE.md`.

Items are in priority order within each section and across the first two: do them top to
bottom. Gap numbers refer to "Known gaps" in `ARCHITECTURE.md`. Gaps 3 and 4 are not being
fixed in this pass.

## This pass: gap 5

Every item ends with tests (success and failure modes) and the doc updates named in `CLAUDE.md`.
Before starting P6 read `specs/FEEDBACK.md`; it holds the case list the tests come from.

### P6. End-to-end feedback tests with a judge

Spec: `specs/FEEDBACK.md`, "Test layers".

- `tests/test_memory_e2e.py` already holds the run-one/run-two scenario (feedback to curator to
  profile to run two, in-memory and Postgres) with a scripted curator model. Extend it to the
  cases as parametrized fixtures; case 6 waits for its consumer. Layer 2 (stubbed model, CI)
  asserts run two's results exactly. Layer 3 (live model plus judge) skips without
  `OPENROUTER_API_KEY`.
- `tests/judge.py`: Pydantic verdict, the five-point rubric, three calls and majority, through
  `build_llm("judge")`. Free-text fields only; structured fields stay exact assertions.
- Record in notes/04 which cases the live model fails and how often, with the model versions.

### S1. State hygiene

`AgentState` is 32 flat channels across seven concerns. Decision and reasoning are in
`DECISIONS.md` ("Do not restructure `AgentState` wholesale"). The P5 curator subgraph was the pilot
for private schemas: `input_schema`/`output_schema` kept its channels private and returned only a
status string, so the reducer echo did not occur. Apply that to the research subgraph here.

0. Fix the reducer double-count: `tavily_calls`, `completed_queries` and `research_notes` grow
   1, 2, 4 across REWRITE and AUGMENT rounds because the subgraph returns its channel value and
   the parent's `operator.add` adds it again (`article/notes/02`). They cannot simply leave the
   subgraph schema, since the parent needs them for AUGMENT; use reducers that are idempotent
   over the echo, and add a regression test over two rounds.
1. One source for the counters: `cache_hits` and `tavily_calls` exist both as channels and in
   `ResearchRunMetrics`. Keep one and update every reader and `flow_specs.py`.
2. Move `max_iterations` and `max_research_iterations` out of state into run configuration.
   Check that a resumed run keeps the budget it started with.
3. Verify whether the search-loop channels (`research_plan`, `active_search_query`,
   `tavily_results`, `completed_queries`) can be private to `course_research` with an
   `output_schema`. AUGMENT re-enters at `plan_gap_search` and reads `completed_queries`,
   `research_plan` and `validation_results` from the previous pass, so this only works if they
   persist across invocations; test that on LangGraph 1.1.2 before changing anything.
4. Keep the intermediate candidate lists (`extracted_candidates`, `scraped_courses`,
   `deduplicated_courses`) as they are: they are the checkpoint history the article shows.
5. Check that `flow_specs.py` and `Pii` markers still see every channel after any regrouping;
   nested fields are invisible to a channel-level check.
6. Checkpoints written before the change do not resume (see "Naming" in `ARCHITECTURE.md`).

Closes the state-size item under Optional and gives the article's §2.3 (`input_schema`,
`output_schema`) an honest use.

## Next, not this pass

### N1. Promotion cascade for the shared cache

Decision and rationale are in `DECISIONS.md` ("Shared-cache promotion is a cascade"). Build as a
separate graph off the request path (gap 4), triggered through the outbox.

1. `promotion_status` column: `pending | promoted | rejected | needs_human`; the request-path
   lookup reads `promoted` only; `upsert_courses` writes `pending`.
2. `TypedVerifier` port (label, probability, confidence) with a stub and a JEV adapter; treat
   JEV vendor claims as unverified until measured.
3. LLM reviewer node (structured verdict plus written critique) for items below threshold.
4. Human queue: `interrupt_before` a review node; open items expire after a TTL.
5. Asymmetric thresholds (promotion higher than rejection), disagreement between tiers
   escalates, and a random audit sample of auto-accepted items goes to the human queue.
6. Fit thresholds on labelled data from step 5; do not reuse published thresholds.
7. Reverification of stale promoted courses.

### N2. Decide the fate of the per-run review gate

Open question, not a task. If runs become self-serve for many users, `send_approved_courses` and
the per-run `interrupt_before` become a per-user "save" action, and the human gate lives in N1.
Until that is decided, `CLAUDE.md`'s "Never auto-publish" stands and nothing here changes it.

## Verify

- **`extracted_candidates` has no reducer** but is written by every parallel `Send` branch.
  Add a regression test with a plan of two or more queries. If it raises `InvalidUpdateError`,
  add the reducer and make it the §2.2 example; if it doesn't, record why in the notes.
- **OpenRouter fallback was never triggered live,** only asserted in the outgoing payload.
  Either exercise it (force a primary failure) or keep the article's "not exercised" wording.
  OpenRouter data-retention and ZDR controls are also unverified. Feeds §8.2.

## Code

- **Course topic tagging.** Fill `courses.topics` from the course's own title, description and
  evidence, never from the user's query (`DECISIONS.md`). Probably a fifth LLM node through
  `build_llm()` with a rule fallback (keyword extraction), run before `save_verified_courses`.
  The input is untrusted search text, so do it with the prompt-injection item below. Then
  re-embed with `python -m course_discovery.research_agent.embeddings backfill` (extend it to
  `--all`), rerun `scripts/calibrate_topic_floor.py` and refit the floor. Expected gain on the
  mock catalog: recall 0.75 to 0.81 on the hashing embedder.
- **Prompt-injection check for untrusted search content.** The old plan named
  `extract_courses_from_results` and `verify_course_claims` as the insertion points, but both
  are rules, not LLM nodes. Re-derive the real surface (search snippets reaching the
  `rank_and_summarize_courses` prompt) before building. Feeds §8.5 and §10.3.

## Article drafting

Placeholders marked `[NOT DRAFTED]` in `specs/article/DRAFT.md`:

- §0 TL;DR, §1 Agents vs. workflows, §2.1 state as the single channel, §3.1 conditional
  edges, §13 What's next, and the demo appendix.
- §2.3 `input_schema`/`output_schema` now has an honest use (the curator subgraph, `memory_curator/graph.py`); §3.4 `Command` is not used by the code, so either
  add a small honest use or label it doc-only.
- §8.5 guardrails and §10.3 security primitives: depend on the prompt-injection item above.
- §13, prose only, no code: the many-user reframing (self-serve runs, human review at
  shared-cache promotion, N1) and implicit feedback (weighted counters with decay, an embedding
  moving average, batched LLM personas; collaborative filtering only at a scale this domain
  will not reach soon). Say plainly these are not implemented.

New sections proposed, not yet in the outline:

- **Deployment shape:** self-hosting the library versus LangGraph Server, `langgraph.json`
  and Studio; who owns the queue, workers and checkpointer. One short section; label
  platform semantics unverified until checked against current docs.
- **Evals and regression for the LLM nodes:** "did the parse, route or summary get worse after
  a model swap or fallback." Ties to §8.2 and to counting validation failures (§8.4). P6 is the
  first working example. The old metrics and testing proposals are in `archive/`.

## Freshness

- **Draft pass after the Postgres, PII and OpenTelemetry work.** Some earlier drafted
  sections were written when the checkpointer was `MemorySaver` and search nodes were
  described as LLM calls. Read `DRAFT.md` against `STATUS.md` and correct stale statements.

## Optional

- Pending writes: why a failed `Send` sibling doesn't lose its successful siblings' results;
  gives the §5.5 subgraph re-run finding a mechanism.
- State size and checkpoint growth: every superstep persists full state; encryption
  multiplies the cost. Design rule: keep large payloads out of state.
- `max_concurrency`: bounds plan-driven fan-out; pairs with §3.2 and §8.3.
- Time travel and forking (`get_state_history`, `update_state`): check whether §5 already
  covers it under other words.
- Same-transaction write of the outbox row with `recommendation_events`; the `publish_digest`
  handler still prints.

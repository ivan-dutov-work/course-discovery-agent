# Backlog: open work only

Delete an item when it's done. On completion: add a line to `STATUS.md`, put the evidence in
the matching file under `specs/article/notes/`, and record any decision in `DECISIONS.md`.
Definition of done is in `CLAUDE.md`.

Items are in priority order within each section and across the first two: do them top to
bottom. Gap numbers refer to "Known gaps" in `ARCHITECTURE.md`. Gaps 3 and 4 are not being
fixed in this pass.

## This pass: gap 5

Every item ends with tests (success and failure modes) and the doc updates named in `CLAUDE.md`.
Before starting P4 to P6 read `specs/FEEDBACK.md`; it holds the case list the tests come from.

### P3a. OpenRouter `Embedder` (before or right after merging the topic-cache PR)

Proves the `Embedder` port swap works with a real semantic model; the hashing default stays for
tests and offline runs.

- `OpenRouterEmbedder` implementing `Embedder` (`dimension = 1536`), selected in
  `embeddings/base.py:_default_embedder` by an env switch (default stays `HashingEmbedder`).
  Pick a model that returns or accepts 1536 dimensions (column size is fixed by migration 001).
  Transient errors propagate so `RetryPolicy` can fire; failures raise `EmbeddingError`.
- Run `scripts/calibrate_topic_floor.py` against it (parametrize the script by embedder), refit
  the topic floor and the 0.7 / 0.2 / 0.1 blend weights if they do not transfer, and record both
  sets of numbers in `notes/05-scale-and-scope.md` and the outcome in `DECISIONS.md`.
- Existing rows keep hashing vectors: document that switching embedders needs
  `python -m course_discovery.research_agent.embeddings backfill`.
- Tests: stubbed HTTP success, wrong dimension, transport error; a live test that skips without
  `OPENROUTER_API_KEY`.
- Docs: `ARCHITECTURE.md`, `STATUS.md`, CLAUDE.md environment section, article claim upgraded
  from "topic-aware" to "semantic" only if the calibration supports it.

### P4. Writable profile with consumers (gap 5, part 1)

Spec: `specs/FEEDBACK.md`, "Current state" and "Memory shape".

- `UserMemory`: add `preferred_course_length` and `notes: list[MemoryNote]`.
- `memory/repository.py`: `save_user_memory(user_id, patch)` upserts `users` and
  `user_preferences` in one transaction with a row lock, merging into the stored profile and
  into `raw_memory_json` for the two URL lists. Writers re-raise. Redact free text first.
- Send DISCARD through `record_review_outcome` too (`discard_run -> record_review_outcome ->
  END`); today rejections on discard record nothing.
- Add `feedback_history: Annotated[list[str], operator.add]` to `AgentState`, appended (redacted)
  by `interpret_review_feedback` each round; declare it in `privacy/flow_specs.py`.
- Consumers, one per field, as listed in `FEEDBACK.md`: ranking boost and prompt notes in
  `rank_and_summarize_courses`; default constraints in `parse_user_request`.
  `profile_embedding` is written on each profile save and used as a ranking tie-break; if that
  grows the item, split it out.
- Tests: save and reload round trip; concurrent saves merge; failure raises; every learned
  field changes run two's ranking or filtering (exact assertions); erasure removes the
  profile (`privacy/sources.py`, `tests/test_erasure.py`).

### P5. Memory curator subgraph (gap 5, part 2). Depends on P4

Spec: `specs/FEEDBACK.md`, "Curator". Bounded tool loop with a closed tool set, a step cap,
fail-closed commit, idempotent per `run_id`.

- New package `course_discovery/memory_curator/` (schema, tools, graph). Mounted after
  `record_review_outcome`, so the graph becomes `... -> record_review_outcome ->
  curate_user_memory -> END` for both PUBLISH and DISCARD.
- New migration: `memory_updates(run_id primary key, user_id, patch jsonb, created_at)`. Add it
  to `privacy/sources.py:USER_DATA_SOURCES` (`tests/test_erasure.py` fails otherwise).
- `build_llm("curator")` only; declare the node and channels in `privacy/flow_specs.py`; the
  outbox and effect rules do not apply (no external effect), so no `EffectGateway`.
- Docs: `ARCHITECTURE.md` (fourth LLM node, table row, diagram), `STATUS.md`, a `DECISIONS.md`
  entry already exists for the shape; add evidence to the matching notes file.
- Tests: every unit case in `FEEDBACK.md` (rows 3, 10, 11, 13 to 18 and the tool-call trace of
  row 7), with a stubbed model.

### P6. End-to-end feedback tests with a judge. Depends on P4, P5

Spec: `specs/FEEDBACK.md`, "Test layers".

- `tests/test_memory_e2e.py`: the 18 cases as parametrized fixtures. Layer 2 (stubbed model, CI)
  asserts run two's results exactly. Layer 3 (live model plus judge) skips without
  `OPENROUTER_API_KEY`.
- `tests/judge.py`: Pydantic verdict, the five-point rubric, three calls and majority, through
  `build_llm("judge")`. Free-text fields only; structured fields stay exact assertions.
- Record in notes/04 which cases the live model fails and how often, with the model versions.

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
  mock catalog: recall 0.72 to 0.81.
- **Prompt-injection check for untrusted search content.** The old plan named
  `extract_courses_from_results` and `verify_course_claims` as the insertion points, but both
  are rules, not LLM nodes. Re-derive the real surface (search snippets reaching the
  `rank_and_summarize_courses` prompt) before building. Feeds §8.5 and §10.3.

## Article drafting

Placeholders marked `[NOT DRAFTED]` in `specs/article/DRAFT.md`:

- §0 TL;DR, §1 Agents vs. workflows, §2.1 state as the single channel, §3.1 conditional
  edges, §13 What's next, and the demo appendix.
- §2.3 `input_schema`/`output_schema` and §3.4 `Command`: not used by the code, so either
  add a small honest use or label the section doc-only.
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

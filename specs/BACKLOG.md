# Backlog: open work only

Delete an item when it's done. On completion: add a line to `STATUS.md`, put the evidence in
the matching file under `specs/article/notes/`, and record any decision in `DECISIONS.md`.
Definition of done is in `CLAUDE.md`.

## Verify

- **`extracted_candidates` has no reducer** but is written by every parallel `Send` branch.
  Add a regression test with a plan of two or more queries. If it raises `InvalidUpdateError`,
  add the reducer and make it the §2.2 example; if it doesn't, record why in the notes.
- **OpenRouter fallback was never triggered live,** only asserted in the outgoing payload.
  Either exercise it (force a primary failure) or keep the article's "not exercised" wording.
  OpenRouter data-retention and ZDR controls are also unverified. Feeds §8.2.

## Code

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

New sections proposed, not yet in the outline:

- **Deployment shape:** self-hosting the library versus LangGraph Server, `langgraph.json`
  and Studio; who owns the queue, workers and checkpointer. One short section; label
  platform semantics unverified until checked against current docs.
- **Evals and regression for the LLM nodes:** "did the parse, route or summary get worse after
  a model swap or fallback." Ties to §8.2 and to counting validation failures (§8.4). The old
  metrics and testing proposals are in `archive/`.

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

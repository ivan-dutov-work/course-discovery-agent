# Backlog: open work only

Delete an item when it's done. On completion: add a line to `STATUS.md`, put the evidence in
the matching file under `specs/article/notes/`, and record any decision in `DECISIONS.md`.
Definition of done is in `CLAUDE.md`.

Items are in priority order within each section: do them top to bottom. Gap numbers refer to
"Known gaps" in `ARCHITECTURE.md`. Gaps 3 and 4 are not being fixed in this pass.

## Next

### E1. Eval harness and CI

Spec: `specs/EVALS.md` (levels L0 to L5, case format, judge, statistics, CI, build order).
The judge exists (`tests/judge.py`, built in P6) and is reused by milestone 3, not rewritten. Six
milestones in the spec's "Build order". Seed cases come from observed failures and
`FEEDBACK.md`, tagged `source: seed`, and are replaced by `review` and `prod` cases as they exist.
Milestones 1 and 2 are built (`STATUS.md`, "Evals"): the existing `tests/` checks are not yet moved onto cases and L1a has no extractor or ranking rows. `ci.yml` is not yet seen green on GitHub, and the PR-gate is not branch-protected. Next: the labelled set and L1b live for milestone 3.
Closes the "OpenRouter fallback never triggered live" item under Verify (L4 fault injection).

### N1. Promotion cascade for the shared cache

Decision and rationale are in `DECISIONS.md` ("Shared-cache promotion is a cascade"). Build as a
separate graph off the request path (gap 4), triggered through the outbox.

1. Staging exists as `pending_courses` (per run; `DECISIONS.md`), promoted by `promote_approved_courses`
   after the human gate. The cascade replaces that gate with tiers; add `rejected | needs_human`
   states to the staging rows then, and a TTL prune for runs that are never resolved.
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

- **Run the live memory layer.** `LIVE_LLM_TESTS=1 OPENROUTER_API_KEY=... uv run python -m unittest
  tests.test_memory_e2e_live tests.test_judge_live` (`LIVE_TRIALS`, default 3). Record in `notes/04`
  which cases the live model fails and how often, the model versions served, and the judge's
  agreement with the six hand labels. Closes gap 5 once recorded.
- **OpenRouter fallback was never triggered live,** only asserted in the outgoing payload.
  Either exercise it (force a primary failure) or keep the article's "not exercised" wording.
  OpenRouter data-retention and ZDR controls are also unverified. Feeds §8.2.

## Code

- **Provider names are redacted as `<PERSON>`.** `redact_pii` rewrites capitalised `Udemy` and
  `Coursera` in review feedback, so the curator cannot see which provider the user named (`notes/04`).
  Options: an allowlist of provider names ahead of Presidio, or a recogniser score threshold for
  `PERSON`. Either changes the redaction decision in `DECISIONS.md` ("instructor names are redacted as a
  known trade-off"), so decide it first. When fixed, drop `history` from the four cases in
  `tests/memory_cases.py`.
- **Course topic tagging.** Fill `courses.topics` from the course's own title, description and
  evidence, never from the user's query (`DECISIONS.md`). Probably a fifth LLM node through
  `build_llm()` with a rule fallback (keyword extraction), run before `save_verified_courses`.
  The input is untrusted search text, so route it through `screen_untrusted` (see the injection
  follow-ups below). Then
  re-embed with `python -m course_discovery.research_agent.embeddings backfill` (extend it to
  `--all`), rerun `scripts/calibrate_topic_floor.py` and refit the floor. Expected gain on the
  mock catalog: recall 0.75 to 0.81 on the hashing embedder.
- **`Command` in the replan node (§3.4).** `verify_course_claims` returns
  `Command(update=..., goto="plan_gap_search" | "save_verified_courses")` in place of its
  conditional edge. Leave the gateway failure on `discard_reason` (`DECISIONS.md`). Update
  `test_graph_topology.py`, `ARCHITECTURE.md` and `STATUS.md` ("Not in the code"), then draft §3.4.
- **Injection screen follow-ups.** The JEV screen on the synthesis prompt is built (`STATUS.md`).
  Left:
  1. A deterministic layer beside it (control-pattern stripping, fencing the untrusted text as data
     in `SYNTHESIZER_SYSTEM_PROMPT`), because JEV is steerable by the text it screens.
  2. Label real data and fit the 0.35 and 0.70 thresholds; add adaptive attacks that iterate
     against the screen. The first live run was 28 author-written samples.
  3. Screen the tagger's input (and anything else that sends web text to a model), and decide
     whether cached rows are screened at write time instead of at every read.
  4. Find the cause of the one unexplained live-test error (log the response on failure) and
     confirm the 12,000-character chunk boundary against the live endpoint.
  5. The Decisions API path is `alpha`; recheck the contract before relying on it.

## Article drafting

Placeholders marked `[NOT DRAFTED]` in `specs/article/DRAFT.md`:

- §0 TL;DR, §1 Agents vs. workflows, §2.1 state as the single channel, §3.1 conditional
  edges, §13 What's next, and the demo appendix.
- §3.4 `Command` waits for the replan-node item under Code.
- §13, prose only, no code: the many-user reframing (self-serve runs, human review at
  shared-cache promotion, N1) and implicit feedback (weighted counters with decay, an embedding
  moving average, batched LLM personas; collaborative filtering only at a scale this domain
  will not reach soon). Say plainly these are not implemented.

New sections proposed, not yet in the outline:

- **Deployment shape:** self-hosting the library versus LangGraph Server, `langgraph.json`
  and Studio; who owns the queue, workers and checkpointer. One short section; label
  platform semantics unverified until checked against current docs.
- **Evals for a LangGraph agent in production** (a section; decided to add, drafted after E1):
  the level split (contract, component, subgraph, graph scenarios, reliability), trace grading
  over `get_state_history` with first-failing-node attribution, trials and pass^k, a judge from
  another model family, cassette replay as the PR gate and live runs nightly. Ties to §8.2
  (fallback) and §8.4 (validation counts). Say plainly that the cases are seeds on a 14-course
  mock catalog and measure plumbing, not generalization, and that no production traffic backs
  it. Needs an outline entry with a word budget first. P6 is the first working example. The old
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

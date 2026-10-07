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
Milestones 1 and 2 are built (`STATUS.md`, "Evals"): the existing `tests/` checks are not yet moved onto cases and L1a has no extractor or ranking rows. `ci.yml` is not yet seen green on GitHub, and the PR-gate is not branch-protected. Milestone 3 is partly built: the 32-output label set is labelled by the owner and scored (TPR and TNR per criterion, `STATUS.md`); next is the larger labelled set with a train, dev and test split, then L1b live for router and synthesizer.
Closes the "OpenRouter fallback never triggered live" item under Verify (L4 fault injection).

### N1. Promotion cascade for the shared cache

Decision and rationale are in `DECISIONS.md` ("Shared-cache promotion is a cascade"). Build as a
separate graph off the request path (gap 4), triggered through the outbox.

1. Staging exists as `pending_courses` (per run; `DECISIONS.md`), promoted by `promote_approved_courses`
   after the human gate. The cascade replaces that gate with tiers; add `rejected | needs_human`
   states to the staging rows then. Abandoned runs are already pruned by inactivity (`STATUS.md`);
   unresolved items in the cascade need their own TTL.
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
Partly settled by the chat product (`DECISIONS.md`, "Product shape: one continuous chat"): in chat
mode the parked thread is a conversation wait, acceptance is implicit and only catalogue promotion
is gated. The `CLAUDE.md` wording is drafted and awaits the owner's approval.

## Chat product and load: worktrees

Eight worktrees, each a branch an agent can take whole. They mostly own different files but share
the state contract, the migration numbers and the spec documents, so they run in batches, not all
at once. Each batch ends in a checkpoint you can run, and the next batch branches from `main` after
it merges. The batches and merge notes are at the end of this section. The source of each item is
the grilling session of 2026-10-06 (decisions recorded in `DECISIONS.md`).

Product shape in one paragraph: one continuous Telegram chat per person. Each message is first
classified (JEV, app layer, before a thread exists) as continuing the person's latest parked thread
or starting a new topic. Inside a thread the existing review loop (`REWRITE`, `AUGMENT`, `RESET`)
handles refinement. There is no approval step: the person's next message is the feedback, and a
thread that is left behind counts as implicitly accepted. Staff review happens only when a course
enters the shared catalogue (N1). Two automated job shapes sit beside the chat: course tagging
(one run per course) and a recurring digest (one run per user). At most one run is active per user.

### Rules for every agent on these items

- Load the `minimal-comments` skill before writing any code comment or commit message, and put the
  same instruction in any prompt you delegate. No ticket or item codes (`W3`, `N1`) in comments or
  commit messages.
- Read `CLAUDE.md`, `ARCHITECTURE.md`, `STATUS.md`, `DECISIONS.md` first. Stop and ask if an item
  contradicts a `DECISIONS.md` entry or the code disagrees with the docs about something the item
  depends on.
- Definition of done is `CLAUDE.md`'s, with one change: edits to `DRAFT.md` belong to W7 only. Every
  other worktree adds a "Draft follow-up" bullet to its report instead of touching the article.
- Changing a state channel, a node or a node that can hold a pause means bumping
  `STATE_SCHEMA_VERSION`, running `uv run python -m tests.contract_snapshot`, adding a fixture and
  manifest entry, and updating `privacy/flow_specs.py` (`DECISIONS.md`, "State contract changes are
  versioned").
- A new table goes in `privacy/sources.py:USER_DATA_SOURCES` or `NOT_USER_DATA` (`tests/test_erasure.py`
  fails otherwise), and anything storing user text stores it after `redact_pii`.
- **How to verify.** Every item below ends with a "Verify end to end" block. Run it, and paste the
  commands and observed output into the final report. Rules for all of them:
  1. Postgres comes from `docker compose up -d` (use the `docker-compose-up` skill if Docker is
     down), with `TEST_DATABASE_URL=postgresql://course:course@localhost:55432/course_discovery`
     and `scripts/apply_migrations.py` run on an empty database. A check that skips without it does
     not count as run.
  2. No network and no API key unless the block says `live`. Use stubs and a fake Telegram. A `live`
     check without a key is reported as "not run", never as passed.
  3. Mutation check: break the behaviour the test guards, show the test fail, restore it, show it
     pass. A test that cannot be made to fail does not count.
  4. Finish with `uv run python -m unittest discover tests` and `uv run pytest evals -n 4`, and report
     the pass counts next to the counts before your change.
  5. Record evidence (library version, how it was checked) in the matching `specs/article/notes/` file.

### W3. `wt/continuation-routing`: which thread does this message belong to

Branch from `main`. Owns a new `course_discovery/conversation/` package, `evals/cases/` rows for
it, and tests. Does not touch the graphs.

Built and measured (`STATUS.md`, `notes/04`): the port, the JEV adapter with three outcomes
(`NO_SIGNAL`, `CONTINUE`, `NEW_TOPIC`), the stub, the scripted table, fault injection, the PII canary,
the ownership test, the probe and the calibration. What remains:

1. Decide whether to adopt the fitted continue threshold (0.45 in `evals/baselines/thread_selector_bands.json`;
   `CONTINUE_THRESHOLD` is 0.70 and the 0.35 to 0.70 middle band that degrades to `NEW_TOPIC` is still
   in force; a fitted value inside that band means the band needs rethinking too). Re-fitting after
   reading test is not allowed.
2. A held-out check of the reworded prompt needs new test pairs written after this change; the
   current test split was read once under the old prompt.
3. Nothing about accuracy goes in the article beyond "rough, on a small author-written set".

Verify end to end:
- A scripted table of at least twelve conversations (refinement, unrelated query, same topic after a
  gap, a message that only says "thanks", a message in another language) runs through the real
  selector code against a stub transport and every row gets the expected class.
- Fault injection against the stub transport: 500, timeout, malformed JSON, a middle score. Each
  returns `NEW_TOPIC` and increments the degradation metric. Change the error path to return
  `CONTINUE`: the test fails.
- PII canary: a message containing an email and a phone number; the captured outgoing request body
  contains neither.
- Ownership: with user A's thread parked and user B writing, the selector's input never contains A's
  data (assert on the captured request).
- `live` (needs `OPENROUTER_API_KEY`): the probe runs against the real endpoint and records latency
  and the score for the twelve scripted rows. Without a key, say "not run".
- Measurement: `uv run python -m evals.calibrate_thread_selector` (new, modelled on
  `evals.calibrate_judge`) prints TPR and TNR for the three classes with the item counts per split, and
  fails if a test-split id appears in the tuning inputs.

### W6. `wt/batch-jobs`: digest job, one run per user and period

The tagging job is built (`STATUS.md`). What is left is the digest job in `course_discovery/jobs/`,
after W8 merges, because it needs W5's per-user lease and the chat outbound effect.

Ask first: does the digest job search the web or serve from the cache only?

1. Digest job: the unit is one user and one period, key `digest:{user_id}:{period}`; delivered through
   the chat outbound effect; never runs beside an active chat run for the same user (take the same
   `chat_user_leases` row).

Verify end to end:
- Fifty users, digest job run twice for one period: one digest per user. A user with an active chat
  run is skipped or waits, and a concurrency assertion shows no overlap.
- Draft follow-up: §3.2 and §5.5 gain the per-unit versus batch `Send` comparison from
  `scripts/send_vs_runs.py` (numbers in `notes/05`; the sibling re-run claim did not hold when the
  siblings finish before the failure, so say what was measured).

### W7. `wt/article`: article changes from the grilling

Branch from `main`, merged last. Owns `specs/article/DRAFT.md` and `OUTLINE.md`. Load the
`course-article-style` skill. Every change below is prose labelled with what was observed, read from
source, or designed and not built. Do not describe unbuilt behaviour as present.

1. §1: lift the Classification table from `ARCHITECTURE.md` into §1.2 and reframe it as where each
   decision sits between code-owned and model-owned and what bounds it. Drop the binary "workflow,
   not agent" wording. Keep the curator as the example of the mix, no dedicated section. §0 follows,
   last.
2. §5.2: the demo uses the library default (or `sync` once W1 merges; read what the code does that
   day). §5.3: the reviewed digest and the promoted `valid_courses` are different sets in the demo,
   and the product moves review to promotion.
3. §4 and §6: the per-run gate is a stand-in; one paragraph on the chat product (a parked thread is a
   conversation wait, acceptance implicit, staff review at promotion), labelled "not built" until W4
   merges.
4. Label as "design, not built" and mark any invented snippet as illustrative: §4.2 (dynamic
   `interrupt()`), §5.6 (`Store`), §7.3 (`CachePolicy`), §7.4 and all of §11. Remove nothing.
5. §6 and §13: Telegram effect keys and inbound dedupe as design only; §13: the continuous chat, the
   continuation classifier, the two job shapes.
6. `OUTLINE.md`: rewrite the word budgets to the draft's real counts (a script counts words per
   section), correct §9.1 and §13, and say the draft is the article, not a trimmed outline.
7. Evals section: not now. Draft it after E1's held-out set and W3's labelled pairs exist.

Verify end to end:
- A script prints words per section from `DRAFT.md`; the outline table matches it within five words.
- `grep -c "design, not built"` shows a label in each section listed in item 4, and the list of
  sections with invented snippets is in the report.
- Every fenced snippet that claims to come from the repo is looked up in the source with `grep`;
  the report lists the `path:line` for each one touched.
- No sentence in a changed section uses the present tense for a mechanism that `STATUS.md` lists
  under "Not in the code" or that a worktree above has not merged; the agent lists the sentences it
  checked.

### W8. `wt/chat-integration`: the whole chat, after W3, W4 and W5 merge

Branch from `main` after those three. Owns `scripts/chat_e2e.py`, new L3 scenarios in
`evals/cases/l3/` and the glue between the pieces (thread selection wired into the inbox worker).

1. On a new message the worker calls the selector. `CONTINUE`: `authorize_thread`, `update_state`
   with the merged text as feedback, resume. `NEW_TOPIC`: close the old parked thread through W4's
   implicit acceptance, mint a `thread_id`, register it in `run_threads`, start.
2. L3 scenarios for chat, so the PR gate runs them (`requires: [postgres]`).

Verify end to end: `scripts/chat_e2e.py` plays a three-day script on Postgres through the fake
Telegram server, and asserts at each step.
- Day one: a query, a digest reply, a refinement (`REWRITE`), a second reply.
- Day two: an unrelated message. The selector returns `NEW_TOPIC`, the old thread is closed with one
  implicit-accept event, a new thread exists, and the `courses` table is unchanged.
- A duplicated update and a SIGKILL during a run: no duplicate replies, no lost message.
- A second user's chat cannot resume the first user's thread.
- With JEV stubbed to fail: every message starts a new thread and the degradation metric counts them.
- `erase --user-id X --execute` leaves no row for the user in any table and exits 0.
- The script exits non-zero on the first failed assertion and prints a one-line summary per step.

### Batches and checkpoints

Why not all at once: W2 renumbers gap references across files every other item edits; W4 and W6
tagging can both bump `STATE_SCHEMA_VERSION`; W5 and W6 both add migrations; and W4 is the riskiest
item and starts with an owner sign-off, so W5 and W8 should build on it merged, not on a guess.
Items inside a batch run in parallel; batches run in order. A batch is done when its items are
merged to `main` and its checkpoint passes there, not on the branches.

| Batch | Items | Checkpoint (run on `main` after merge) |
|---|---|---|
| 0. Clean base | W2, W1 | Test counts unchanged, every gap reference resolves, the CLI passes `durability="sync"` on every call. |
| 1. Core semantics | W4, W3 | CLI demo still runs. A chat-mode run on Postgres with a fake transport shows digest, `REWRITE`, second digest, implicit close, with exactly-once effects. The thread selector is measured on the held-out split. |
| 2. Surface | W5, W6 (tagging part) | `scripts/chat_smoke.py` passes: fake Telegram, webhook, queue, graph and captured reply, with dedupe and the per-user lock. Tagging runs 200 courses exactly once each. |
| 3. Integration | W8, then W6 (digest part) | `scripts/chat_e2e.py` passes the three-day script. The digest job runs once per user and period and never beside an active chat run. |
| 4. Article | W7 | The draft matches what is merged; "design, not built" labels remain only on what is still unbuilt. |

Batch notes:
- Stop between batches. Report the checkpoint output and any inconsistencies found; start the next
  batch only after the owner has looked.
- W2 goes first in batch 0 and merges before W1 is rebased. W1 only touches `app/cli.py`.
- In batch 1, W4 waits for the owner's answer to its first step. W3 can start at once, but its
  open question (does thread age force a new topic) is asked before the selector is built, and the
  owner labelling is the slow part.
- In batch 2, W5 and W6 each take the next free migration number at merge time; the later one
  renumbers. Only one of them may bump `STATE_SCHEMA_VERSION` per merge; the other rebases and
  regenerates the snapshot.
- Every worktree adds one line to `STATUS.md`; merge conflicts there are resolved by keeping both
  lines. W2, W4 and W5 all edit `ARCHITECTURE.md`; rebase on `main` before merging.
- Each worktree's "Draft follow-up" bullets are W7's input, so keep them in the merged reports.

## Verify

- **Live memory layer, rerun after the provider-name fix.** First measurement is in `notes/04`
  (1 trial: 7 of 12 fail, 5 from `<PERSON>` redaction). Then run with `LIVE_TRIALS=3` (about an hour),
  log the model id OpenRouter served, give `case_13` a live expectation, and judge `case_04`'s
  extra math note. Closes gap 5 once recorded.
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
- **Course topic tagging, model tagger.** The job, the content-hash key, the injection screen and a
  keyword tagger are built (`course_discovery/jobs/`). Left: an LLM tagger behind `TopicTagger`
  (module data is not stored, so `tag_course` does not fit as is). Fill `courses.topics` from the course's own title, description and
  evidence, never from the user's query (`DECISIONS.md`). Probably a fifth LLM node through
  `build_llm()` with a rule fallback (keyword extraction), run before `save_verified_courses`.
  The input is untrusted search text, so route it through `screen_untrusted` (see the injection
  follow-ups below). Then
  re-embed with `python -m course_discovery.research_agent.embeddings backfill` (extend it to
  `--all`), rerun `scripts/calibrate_topic_floor.py` and refit the floor. Expected gain on the
  mock catalog: recall 0.75 to 0.81 on the hashing embedder.
- **Screen stored notes.** Length, count and the course-only prompt rule are built. Left: run
  `screen_untrusted` on each note at `commit` (drop on a high or middle score, fail closed on
  error), render notes in `rank_and_summarize_courses` as fenced data and screen them there, and
  add attack cases to the curator's live layer beyond case 19.
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

The changes that follow from the chat product and the labelling pass are item W7 above. Evals
section: after E1's held-out set and W3's labelled pairs.

Placeholders marked `[NOT DRAFTED]` in `specs/article/DRAFT.md`:

- §0 TL;DR, §1 Agents vs. workflows, §2.1 state as the single channel, §3.1 conditional
  edges, §13 What's next, and the demo appendix.
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

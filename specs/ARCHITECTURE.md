# Architecture: the course-discovery workflow

Reference for the graph as implemented. It records what each node does, who owns
each decision, and where the current design is a stand-in. `STATUS.md` lists what is
implemented and `BACKLOG.md` what is next; this file holds the shape.

## Classification

The graph is a workflow with LLM decision points, not a free-running agent. Code
owns the control flow: the node sequence, the fan-out, the loop bound and the
human pause are all fixed at compile time. The one exception is `curate_user_memory`,
a bounded tool loop after the run ends (below): there the model picks which of four
tools to call next, inside a step cap, and can only propose, never write.

Four nodes call an LLM, each through `build_llm()`:

| Node | What the model decides | Bound on the decision |
|---|---|---|
| `parse_user_request` | Which structured filters a free-text query implies | Pydantic schema; rule-based parser as fallback |
| `interpret_review_feedback` | Which of five routes free-text reviewer feedback selects | Closed `RoutingAction` enum; runs only after the human pause |
| `rank_and_summarize_courses` | Wording and ranking highlights of the digest | Template fallback; ranks only courses already validated |
| `curate_user_memory` | Which stored preferences the review feedback implies, if any | Closed tool set, `MAX_CURATOR_STEPS` (4), validated patch, only `commit` writes; skipped without a key |

Everything else is deterministic, including planning, extraction and validation.
Worth naming: the planner decides "cache or web" with a threshold on the cache hit
count, not with a model. That is a deliberate default. A model-driven planner is the
one change that would move this graph toward the agent end of the spectrum, and it
would keep the same bounds (`max_research_iterations`, validation before synthesis).

## Outer graph

```
parse_user_request ──ok──▶ start_research_pass ──▶ course_research ──▶ [interrupt] await_human_review ──▶ interpret_review_feedback
        │                          │                  │ stale result: retry_research_pass ─▶ course_research (once, then error)
        │                          └──────────────────┤ planning failure: discard_run
        └─error─▶ discard_run ─▶ drop_pending_courses ─▶ record_review_outcome ─▶ curate_user_memory ─▶ END
                                                                                            ├─ PUBLISH ─▶ send_approved_courses ─▶ promote_approved_courses ─▶ record_review_outcome ─▶ curate_user_memory ─▶ END
                                                                                            ├─ REWRITE ─▶ start_research_pass ─▶ course_research
                                                                                            ├─ AUGMENT ─▶ start_research_pass ─▶ course_research
                                                                                            ├─ RESET   ─▶ parse_user_request
                                                                                            └─ DISCARD ─▶ discard_run ─▶ drop_pending_courses ─▶ record_review_outcome ─▶ curate_user_memory ─▶ END
```

`interrupt_before=["await_human_review"]` is always compiled in. Nothing publishes
without passing it.

## Research subgraph

```
START ─▶ begin_pass ──(AUGMENT)───────────────────────────────────────────▶ plan_gap_search
             │
             └─▶ load_user_profile ─▶ find_known_courses ─▶ plan_web_search
                                                      │
                 ┌──── no queries needed ─────────────┤
                 ▼                                    ├─ error ─▶ END
       merge_known_and_found_courses ◀─ extract_courses_from_results ◀─ search_web_for_courses (×N, Send)
                     │
        remove_duplicate_courses ─▶ verify_course_claims ──┬─ enough valid, or budget spent ─▶ save_verified_courses ─▶ rank_and_summarize_courses ─▶ END
                                                           └─ too few, budget left ─────────▶ plan_gap_search ─▶ (fan-out again, or merge)
```

The basic valid version is the spine: parse the request, load what is already known,
plan the gap, search the gap, extract, merge, deduplicate, verify, save, summarize,
review, publish. The replanning loop and the AUGMENT, RESET and REWRITE routes extend
that spine.

## Nodes

One responsibility each. "Rules" means deterministic code with no model call.

### Outer graph

| Node | Responsibility | Kind |
|---|---|---|
| `parse_user_request` | Redact PII from the query and parse it into `SearchFilters`; fill the stored budget and certificate defaults where the query is silent (reads the profile from the repository, not from state); on RESET, re-parse with the latest `feedback_history` entry and merge the new constraints into the old ones; on failure set `discard_reason`, which routes to `discard_run` | LLM, rule fallback |
| `start_research_pass` | Stamp `research_pass = len(feedback_history)`; the subgraph echoes it back, and a mismatch after `course_research` means the stateful subgraph ignored its input on a resumed tick; resets `research_retries` (`article/notes/01`) | rules |
| `retry_research_pass` | Re-stamp `research_pass` and count the retry; raises `StaleResearchResultError` once `MAX_STALE_RETRIES` (1) is spent, so a result that stays stale fails with a named error instead of looping to the recursion limit | rules |
| `course_research` | Run the research subgraph; sees five input keys, returns `valid_courses`, `digest`, `metrics`, `discard_reason` and the echoed `research_pass` | subgraph |
| `await_human_review` | The pause point where the interrupt fires; does nothing itself | anchor |
| `interpret_review_feedback` | Map reviewer feedback to one routing action, append the redacted feedback to `feedback_history` and clear the `manager_feedback` inbox; rounds already completed are `len(feedback_history)` | LLM, rule fallback |
| `send_approved_courses` | Submit the publish effect through `EffectGateway` with a key derived from `run_id` | side effect |
| `promote_approved_courses` | Move this run's staged courses that appear in the approved `valid_courses` into the shared cache (embedding computed here), then clear the run's staging rows; idempotent | DB write |
| `drop_pending_courses` | Delete this run's staged courses without promoting; runs on every discard path | DB write |
| `record_review_outcome` | Record accept or reject events for the user, with the whole `feedback_history` as the text; runs after publish and after discard | DB write |
| `curate_user_memory` | Turn the review feedback into a validated patch to the stored profile (subgraph, below); writes only the `memory_update` status channel | subgraph, LLM |
| `discard_run` | End the run as discarded, with a reason | terminal |

### Research subgraph

| Node | Responsibility | Kind |
|---|---|---|
| `begin_pass` | On a fresh pass (`routing_decision` empty: first run or RESET) clear the ledger, notes, accumulated search results and iteration counter; REWRITE and AUGMENT keep them | rules |
| `load_user_profile` | Load preferences, completed and rejected courses | DB read |
| `find_known_courses` | Fetch previously validated courses from the cache, ranked by topic similarity | DB read |
| `plan_web_search` | Decide which queries are needed: none if the cache holds enough candidates | rules |
| `search_web_for_courses` | Run one query against the search provider; dispatched once per query via `Send` | I/O (mocked) |
| `extract_courses_from_results` | Turn raw search results into `CourseCandidate`s with evidence | rules |
| `merge_known_and_found_courses` | Combine cache and web candidates into one list | pure |
| `remove_duplicate_courses` | Drop duplicates by normalized URL, title+host fingerprint and fuzzy title | pure |
| `verify_course_claims` | Mark each candidate valid, uncertain or rejected against the filters and the user profile | rules |
| `plan_gap_search` | Build new queries from missing evidence and increment the iteration counter | rules |
| `save_verified_courses` | Stage valid web-sourced courses in `pending_courses`, keyed by run; the shared cache is not touched until approval | DB write |
| `rank_and_summarize_courses` | Rank valid courses (preferred provider, level and language first) and write the digest, with the profile's durable and topic-matching notes in the prompt; each course's web text is screened for injection first (below) | LLM, template fallback |

Evidence rule: a missing piece of evidence yields `uncertain`, never `valid`.

### Memory curator subgraph

```
load_context ─┬─ nothing to learn ─▶ END
              └─▶ curator_model ─┬─ tool calls ─▶ run_tools ─┬─ finish, cap or failure ─▶ commit ─▶ END
                      ▲          └─ LLM error ───────────────┤
                      └─────────────── more steps ───────────┘
```

Compiled with its own `input_schema` (four shared channels) and `output_schema` (only
`memory_update`), so its private channels (`messages`, `steps`, `proposals`, ...) never reach the
parent and the shared reducer channel `feedback_history` is not echoed back and double-counted.

| Node | Responsibility | Kind |
|---|---|---|
| `load_context` | Redact the feedback lines again; skip (status `skipped:*`) when there is no user, no feedback beyond bare approvals, no key, or the run was already applied | rules |
| `curator_model` | Choose the next tool call; an LLM error ends the loop with `failed:llm_error` | LLM |
| `run_tools` | Execute `read_profile`, `read_run_events`, `propose_patch`, `finish`; validation errors go back to the model as text | rules |
| `commit` | Merge the accepted proposals, stamp notes with `run_id` and time, write through `save_user_memory`; a cap or no `finish` writes nothing | DB write, `RetryPolicy` |

- The user id and run id come from state; no tool takes either, so feedback text cannot aim a
  write at another user.
- `propose_patch` is refused before `read_profile`, refuses fields without a consumer
  (`career_goals`, `learning_style_notes`), refuses unknown fields and
  top-level extras, and never stores `this_run` or `not_a_preference`. A `topic:<x>` scope takes
  notes only and stamps the scope.
- Idempotent per `run_id`: the claim row in `memory_updates` (also the audit trail, holding the
  redacted patch) is inserted in the same transaction as the profile write, under the
  `user_preferences` row lock.
- LLM failures degrade (`memory_update = failed:*`, `record_degradation("curator", ...)`) and never
  fail the run; a database failure in `commit` raises, and `RetryPolicy` retries it safely
  because of the claim row.

## Injection screen

Course titles, descriptions and evidence quotes come from search snippets and cached rows, so they
are untrusted text on its way into the synthesizer prompt. Before each course's model call,
`guardrails/injection.py:screen_untrusted` scores that text through an `InjectionScreen` (the
adapter calls `typesafe/jev-1.13` on OpenRouter's Decisions API):

| Score | Action |
|---|---|
| below 0.35 | model call as before |
| 0.35 to 0.70 | model call with structured fields only; the digest line says free text was withheld |
| 0.70 and above | no model call; title and provider only, with the same note |
| screen error | treated as the middle band |

The rules, not the model, own the decision. The screen is skipped without `OPENROUTER_API_KEY`
(no prompt to protect) and with `INJECTION_GUARD=off`. Thresholds are the benchmark's, not fitted.
It covers only this prompt: `parse_user_request` and the curator read no web text, and the
tagger does not pass through it.

## Cache lookup

`find_known_courses` embeds `filters.topic` and ranks cached courses by cosine similarity to
`courses.course_embedding`. The structural filters (price, certificate, level, language,
completed, rejected, avoided provider) are unchanged.

- Embedder: `research_agent/embeddings/`, a port (`dimension`, `embed`) with a local
  `HashingEmbedder` default: feature-hashed word and word-pair counts into 1536 dimensions,
  L2-normalised, no network. It is lexical, not semantic: it matches shared words, not meaning.
  `EMBEDDER=openrouter` selects `OpenRouterEmbedder` (`/embeddings`, `dimensions=1536`, batches
  of 100). The floor is a property of the embedder (`topic_floor`, fitted separately for each);
  the weights are shared. After a switch, run `embeddings backfill --all`; after changing the model,
  rerun `scripts/calibrate_topic_floor.py` and set `EMBEDDING_TOPIC_FLOOR` and
  `EMBEDDING_RELATIVE_CUTOFF`.
  Level, price and certificate words (`free`, `beginner`, `certificate`, ...) are removed from the
  topic and from the stored course text before embedding (`embeddings/facets.py`), because they
  are separate filters and would otherwise match unrelated courses.
- Floor: `topic_floor()`, read from the embedder: 0.12 for `HashingEmbedder`, 0.22 for
  `OpenRouterEmbedder` on `openai/text-embedding-3-small` (`EMBEDDING_TOPIC_FLOOR` overrides it;
  an embedder without the attribute gets `MIN_TOPIC_SIMILARITY = 0.12`). Below it a course is
  dropped. After the floor, `topic_relative_cutoff()` drops courses scoring below that fraction of
  the best remaining similarity: 0.70 for `OpenRouterEmbedder` (`EMBEDDING_RELATIVE_CUTOFF`), none for
  `HashingEmbedder`, whose scores are not compressed and lose recall under it (SQL: a `matched`
  CTE, so the best is taken over courses that already pass the facet filters). Rows with a `NULL`
  embedding (written before the embeddings existed) skip the floor and sort last until
  `python -m course_discovery.research_agent.embeddings backfill` fills them (`--all` re-embeds
  every row, needed after the stored text or the model changes). A topic with no
  content words (only facet words) skips the floor and similarity ordering.
- Score: `0.7 * similarity + 0.2 * validation_confidence + 0.1 * min(use_count, 10) / 10`,
  descending, ties by `id`. The weights were checked, not fitted: ordering quality is flat across
  the range tried on both embedders, with 0.7 / 0.2 / 0.1 at or tied for the best (`DECISIONS.md`).
- Write side: `promote_staged_courses` calls `upsert_courses` after approval, which stores the embedding of title, description and the row's stored
  `topics` in the same transaction as the row. `topics` describes the course and is never
  derived from the user's query, so nothing writes it yet and it is left untouched on conflict.
  An embedder error or a wrong dimension aborts the write; `promote_approved_courses` carries the
  `RetryPolicy`. `save_verified_courses` only writes JSON into `pending_courses`.
- Without `DATABASE_URL`, `seed_cache` ranks with the same embedder, floor and score.
- The planner's threshold on cache hit count (`min_valid = 3`) now counts topical hits.
- The HNSW index (`migrations/007`) exists for a later nearest-neighbour pre-filter; the
  blended `ORDER BY` does not use it, and at this size a scan is fine.
- A remote `Embedder` receives the topic (from the redacted query), course text and the redacted
  profile text; it is declared as a sink in `privacy/flow_specs.py`.
- Profile tie-break: `save_user_memory` writes `user_preferences.profile_embedding` (career goals,
  learning style, notes, level, preferred providers) in the save transaction; the cache query
  returns `1 - cosine` as `CourseCandidate.profile_similarity`, and the seed path computes the
  same from `UserMemory`. The vector never enters state. `_rank_courses` orders by preference
  score, price, similarity rounded to 0.1, then rating. Web candidates have no stored
  embedding and carry `None`, which ranks as 0.

## State and memory

Three stores, three lifetimes.

- **`AgentState`** is the per-run working memory: filters, candidates, validation
  results, iteration counter, notes. It is checkpointed per thread and is the only
  scratchpad the graph has.
- **User profile** (`user_preferences`, `recommendation_events`) is per-user and
  outlives runs. `load_user_profile` reads it at the start; `record_review_outcome`
  writes feedback events after publish or discard. `save_user_memory` is the one writer of
  `user_preferences` (one transaction, row lock, merge); only the curator's `commit` calls it.
- **Fan-in reducers live on the subgraph's schema only.** `tavily_results`, `completed_queries` and
  `research_notes` are `operator.add` channels of `ResearchState` and are not in `AgentState`
  (`domain/state.py`), so the `Send` branches merge inside the subgraph and the parent never adds
  the subgraph's value to its own.
- **`feedback_history`** is the one channel that keeps every review round (`manager_feedback`
  is only the inbox the reviewer's text arrives in, cleared once interpreted). It is outer-graph only: the research subgraph runs on
  `ResearchState`, which omits it, because a reducer channel that a subgraph shares is added to
  a second time when the subgraph returns (`article/notes/02`).
- **Course cache** (`courses`, `course_evidence`) is shared across users and holds
  only validated, human-approved courses. A run's candidates wait in `pending_courses`
  (`run_id`, URL, candidate and validation JSON) until `promote_approved_courses`; a discard
  deletes them. Uncertain courses are no longer persisted: nothing served them. Rows written
  before migration 010 stay as they are.

The cost of this split: the profile is read at the start of each research pass, and
feedback is written only after publish, so feedback given during a run reaches the
next run's profile, not the pass in progress.

### Top-level state and run configuration

The outer graph owns fifteen channels. The research subgraph runs on `ResearchState` with
`input_schema=ResearchInput` and `output_schema=ResearchOutput`, so its other fourteen channels
(plan, queries, candidate lists, validation results, notes, iteration, profile) are private and
persist across passes only because it is compiled with `checkpointer=True`; they are checkpointed
under the namespace `course_research` and are not in the outer state. A channel is only held at
the top level if it cannot be derived.

| Channel | Role | Written by |
|---|---|---|
| `user_id`, `user_query` | Run inputs; `user_id` is the PII subject anchor | initial state |
| `search_filters` | Parsed constraints | `parse_user_request` |
| `manager_feedback` | Inbox for the reviewer's text (`update_state` at the pause) | caller, cleared by `interpret_review_feedback` |
| `feedback_history` | Review ledger (reducer); its length is the number of completed review rounds | `interpret_review_feedback` |
| `routing_decision` | One-shot control signal, consumed by `parse_user_request` on RESET | `interpret_review_feedback` |
| `rewrite_instructions` | Router to research hand-off | `interpret_review_feedback` |
| `research_pass`, `research_retries` | Round stamp the subgraph echoes back, and the count of stale-result retries in this round | `start_research_pass`, `retry_research_pass` |
| `valid_courses`, `digest` | Research output | `course_research` |
| `metrics` | Research counters (`ResearchRunMetrics`); the CLI and the run span read cache hits, search calls and the valid, rejected and uncertain counts from here | `course_research` |
| `publish_status` | Delivery state | `send_approved_courses` |
| `discard_reason` | Why the run ended discarded; also the gateway-failure signal | `parse_user_request`, `interpret_review_feedback` |
| `memory_update` | Curator status, read by nothing at the outer level | `curate_user_memory` |

Run identity and budgets are not state. The run id is the `thread_id` in the invocation
config (`domain/run_config.py`: `run_id_of`, `current_run_id`). `max_review_rounds` (default 3)
and `max_research_iterations` (default 2) are optional `configurable` keys with the same
defaults. LangGraph does not persist them with the checkpoint: the caller supplies them on
every invocation, and a resume that omits a key falls back to the default
(`tests/test_top_level_state.py`). The CLI passes none, so it always runs on the defaults.

`discard_run` and `record_review_outcome` write no channel; `send_approved_courses` writes
only `publish_status`.

## Known gaps

Each of these is a stand-in that the article should name as such, not a finished
design.

1. **Courses have no topics.** The embedding sees only title and description, so a course
   whose text lacks the topic word is missed (recall 0.75 against 0.81 with ideal tags on the
   mock catalog, `notes/05`). A course-side tagging step that fills `courses.topics` from the
   course's own content is not built (`BACKLOG.md`).
2. **The profile tie-break covers cache candidates only.** Web candidates have no embedding
   and are not embedded on the fly, so they never get a profile bonus.
3. **Deduplication is lexical.** URL, title+host hash and fuzzy title cannot merge the
   same course listed under different titles on different hosts.
4. **Search and ingestion share the request path.** Discovery, extraction, validation
   and cache writes all run while a user waits. The production shape separates an
   asynchronous ingestion pipeline (crawl, extract, validate, embed, deduplicate) from
   a request path that does hybrid retrieval first and falls back to web search only
   for thin coverage, feeding results back through ingestion.
5. **Learned preferences are narrow and not yet checked against a live model.** The curator
   writes only fields with a consumer: providers, level, language, budget, certificate,
   rejected and completed URLs, and scoped notes. `career_goals` and `learning_style_notes` have
   no consumer beyond the profile vector, so feedback about them is dropped, not stored. The stubbed-model tests pin the loop and the next run's behavior for eleven cases; how a real model
   behaves on the case list is unmeasured (the live layer exists and has not been run). Capitalised provider names (`Udemy`, `Coursera`) are redacted as
   `<PERSON>` before the curator reads the feedback, so a live model cannot tell which provider
   was named (`notes/04`, `BACKLOG.md`). Cases: `FEEDBACK.md`.
7. **The injection screen is one layer, on one prompt.** JEV can be steered by text that argues
   for its own classification, no deterministic rule or prompt fencing sits beside it, the
   thresholds are unfitted, and the tagging step is not screened (`BACKLOG.md`).

## Known gaps, research boundary

- **Subgraph storage is wider than its schema.** `input_schema` limits what the subgraph reads, not
  what its checkpoint holds: the `__start__` channel keeps the full parent state it was started
  with, and the `Send` payloads in `__pregel_tasks` carry the whole `ResearchState`, `user_memory`
  included. Encryption covers both (`tests/test_nested_checkpoints_postgres.py`); the PII canary
  skips these runtime channels.
- **Candidate lists and plans carry across REWRITE and AUGMENT on purpose,** and are cleared by
  `begin_pass` only on a first pass or RESET. `metrics` is not reset by `begin_pass`, but it is not a run total either: the validator recomputes `queries_run`, `tavily_calls` and the valid, rejected and uncertain counts from the current ledger and candidate lists, so they describe the digest under review and drop after a RESET. Found by `tests/test_e2e_review_loops_postgres.py`.
- **Threads paused under an older state schema are refused, not migrated.** `run_threads` stamps
  `schema_version` (`domain/contract.py:STATE_SCHEMA_VERSION`) when a thread is registered, and
  `authorize_thread` raises `IncompatibleThreadError` on a mismatch, after the ownership check and
  before any `update_state`. Without the guard the old thread does not publish: it re-runs the
  whole research pass (searches, `pending_courses` staging, synthesis call) and parks at the gate
  again (`notes/02`). Rows from before versioning have a NULL version and count as v1. Changes to
  channels or nodes are caught by `tests/test_state_contract.py`; each version keeps a stored
  checkpoint in `tests/fixtures/checkpoints/` marked `resumes` or `refused`
  (`tests/test_checkpoint_compatibility.py`). Without `DATABASE_URL` there is no registry and no
  guard, which is fine for the in-memory saver because its threads do not outlive the process.
  Drain in-flight runs before a deploy that bumps the version.

## Known gaps, cache staging

- **Abandoned runs leave staging rows.** A run that is never resumed or discarded keeps its
  `pending_courses` rows; nothing prunes them yet (`BACKLOG.md`).
- **Approval is per digest.** The reviewer approves the digest, and every valid web-sourced
  course in it is promoted; there is no per-course approval.

## Naming

Node names are span names, checkpoint interrupt targets and `flow_specs.py` keys, so
they appear in traces and in stored checkpoints. Checkpoints written before the
rename hold the old names, so a run paused at the old `review_gate` should be treated
as unable to resume against the renamed graph. Not verified for that rename; finish or discard
in-flight runs before deploying it (`STATE_SCHEMA_VERSION` now enforces this at resume). The top-level state pass removed six channels from stored
checkpoints and is verified to break resume: a run paused under the earlier graph, resumed
under the new one after `update_state`, only re-yields `__interrupt__` and never publishes
(memory saver, pickled round trip, found in review; the research-boundary change breaks resume the same way on Postgres, see Known gaps, research boundary). Finish or
discard in-flight runs before deploying it. Removing the three no-op anchors is different: a run
paused at `await_human_review` under the earlier graph resumed and published under the
current one (`notes/02`), because the paused node's name did not change.

`await_human_review` is the one anchor left. It does no work and stays because
`interrupt_before` needs a real node to pause in front of.

| Before | After |
|---|---|
| `gateway` | `parse_user_request` |
| `research_agent` | `course_research` |
| `research_entry` | removed (conditional entry point) |
| `user_memory_lookup` | `load_user_profile` |
| `course_cache_lookup` | `find_known_courses` |
| `research_planner` | `plan_web_search` |
| `tavily_search_worker` | `search_web_for_courses` |
| `candidate_extractor` | `extract_courses_from_results` |
| `aggregate` | `merge_known_and_found_courses` |
| `dedup` | `remove_duplicate_courses` |
| `evidence_validator` | `verify_course_claims` |
| `replanner` | `plan_gap_search` |
| `course_cache_upsert` | `save_verified_courses` (stages; promotion is `promote_approved_courses`) |
| `synthesizer` | `rank_and_summarize_courses` |
| `research_done` | removed (edge to `END`) |
| `review_gate` | `await_human_review` |
| `router` | `interpret_review_feedback` |
| `augment_dispatch` | removed (direct edge to `course_research`) |
| `publish_node` | `send_approved_courses` |
| `user_memory_update` | `record_review_outcome` |
| `discard_node` | `discard_run` |

The Python functions keep their `*_node` names, and the `build_llm("router")` role
labels and `record_degradation` component labels are unchanged, so metric and
config keys are stable. `RoutingAction` values are also unchanged: they are
persisted in checkpoints and named in the router prompt.

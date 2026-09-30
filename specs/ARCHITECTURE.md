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
parse_user_request ──ok──▶ course_research ──▶ [interrupt] await_human_review ──▶ interpret_review_feedback
        │                                                                                   │
        └─error─▶ discard_run ─▶ record_review_outcome ─▶ curate_user_memory ─▶ END          ├─ PUBLISH ─▶ send_approved_courses ─▶ record_review_outcome ─▶ curate_user_memory ─▶ END
                                                                                            ├─ REWRITE ─▶ course_research
                                                                                            ├─ AUGMENT ─▶ course_research
                                                                                            ├─ RESET   ─▶ parse_user_request
                                                                                            └─ DISCARD ─▶ discard_run ─▶ record_review_outcome ─▶ curate_user_memory ─▶ END
```

`interrupt_before=["await_human_review"]` is always compiled in. Nothing publishes
without passing it.

## Research subgraph

```
START ──(AUGMENT)───────────────────────────────────────────────────────▶ plan_gap_search
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
| `parse_user_request` | Redact PII from the query and parse it into `SearchFilters`; fill the stored budget and certificate defaults where the query is silent (reads the profile from the repository, not from state); on RESET, merge the new constraints into the old ones | LLM, rule fallback |
| `course_research` | Run the research subgraph | subgraph |
| `await_human_review` | The pause point where the interrupt fires; does nothing itself | anchor |
| `interpret_review_feedback` | Map reviewer feedback to one routing action and append the redacted feedback to `feedback_history` | LLM, rule fallback |
| `send_approved_courses` | Submit the publish effect through `EffectGateway` with a key derived from `run_id` | side effect |
| `record_review_outcome` | Record accept or reject events for the user, with the whole `feedback_history` as the text; runs after publish and after discard | DB write |
| `curate_user_memory` | Turn the review feedback into a validated patch to the stored profile (subgraph, below); writes only the `memory_update` status channel | subgraph, LLM |
| `discard_run` | End the run as discarded, with a reason | terminal |

### Research subgraph

| Node | Responsibility | Kind |
|---|---|---|
| `load_user_profile` | Load preferences, completed and rejected courses | DB read |
| `find_known_courses` | Fetch previously validated courses from the cache, ranked by topic similarity | DB read |
| `plan_web_search` | Decide which queries are needed: none if the cache holds enough candidates | rules |
| `search_web_for_courses` | Run one query against the search provider; dispatched once per query via `Send` | I/O (mocked) |
| `extract_courses_from_results` | Turn raw search results into `CourseCandidate`s with evidence | rules |
| `merge_known_and_found_courses` | Combine cache and web candidates into one list | pure |
| `remove_duplicate_courses` | Drop duplicates by normalized URL, title+host fingerprint and fuzzy title | pure |
| `verify_course_claims` | Mark each candidate valid, uncertain or rejected against the filters and the user profile | rules |
| `plan_gap_search` | Build new queries from missing evidence and increment the iteration counter | rules |
| `save_verified_courses` | Persist valid and uncertain courses to the cache with the run topic and an embedding | DB write |
| `rank_and_summarize_courses` | Rank valid courses (preferred provider, level and language first) and write the digest, with the profile's durable and topic-matching notes in the prompt | LLM, template fallback |

Evidence rule: a missing piece of evidence yields `uncertain`, never `valid`.

### Memory curator subgraph

```
load_context ─┬─ nothing to learn ─▶ END
              └─▶ curator_model ─┬─ tool calls ─▶ run_tools ─┬─ finish, cap or failure ─▶ commit ─▶ END
                      ▲          └─ LLM error ───────────────┤
                      └─────────────── more steps ───────────┘
```

Compiled with its own `input_schema` (five shared channels) and `output_schema` (only
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
  (`career_goals`, `learning_style_notes`, `preferred_course_length`), refuses unknown fields and
  top-level extras, and never stores `this_run` or `not_a_preference`. A `topic:<x>` scope takes
  notes only and stamps the scope.
- Idempotent per `run_id`: the claim row in `memory_updates` (also the audit trail, holding the
  redacted patch) is inserted in the same transaction as the profile write, under the
  `user_preferences` row lock.
- LLM failures degrade (`memory_update = failed:*`, `record_degradation("curator", ...)`) and never
  fail the run; a database failure in `commit` raises, and `RetryPolicy` retries it safely
  because of the claim row.

## Cache lookup

`find_known_courses` embeds `filters.topic` and ranks cached courses by cosine similarity to
`courses.course_embedding`. The structural filters (price, certificate, level, language,
completed, rejected, avoided provider) are unchanged.

- Embedder: `research_agent/embeddings/`, a port (`dimension`, `embed`) with a local
  `HashingEmbedder` default: feature-hashed word and word-pair counts into 1536 dimensions,
  L2-normalised, no network. It is lexical, not semantic: it matches shared words, not meaning.
  Level, price and certificate words (`free`, `beginner`, `certificate`, ...) are ignored,
  because they are separate filters and would otherwise match unrelated courses.
- Floor: `MIN_TOPIC_SIMILARITY = 0.12`. Below it a course is dropped. Rows with a `NULL`
  embedding (written before the embeddings existed) skip the floor and sort last until
  `python -m course_discovery.research_agent.embeddings backfill` fills them. A topic with no
  content words (only facet words) skips the floor and similarity ordering.
- Score: `0.7 * similarity + 0.2 * validation_confidence + 0.1 * min(use_count, 10) / 10`,
  descending, ties by `id`. The weights are a choice, not a fit; only the floor was calibrated.
- Write side: `upsert_courses` stores the embedding of title, description and the row's stored
  `topics` in the same transaction as the row. `topics` describes the course and is never
  derived from the user's query, so nothing writes it yet and it is left untouched on conflict.
  An embedder error or a wrong dimension aborts the write; `save_verified_courses` keeps its
  `RetryPolicy`.
- Without `DATABASE_URL`, `seed_cache` ranks with the same embedder, floor and score.
- The planner's threshold on cache hit count (`min_valid = 3`) now counts topical hits.
- The HNSW index (`migrations/007`) exists for a later nearest-neighbour pre-filter; the
  blended `ORDER BY` does not use it, and at this size a scan is fine.
- A remote `Embedder` would receive the topic, which derives from the redacted query. Declare
  it as a sink in `privacy/flow_specs.py` when adding one.

## State and memory

Three stores, three lifetimes.

- **`AgentState`** is the per-run working memory: filters, candidates, validation
  results, iteration counter, notes. It is checkpointed per thread and is the only
  scratchpad the graph has.
- **User profile** (`user_preferences`, `recommendation_events`) is per-user and
  outlives runs. `load_user_profile` reads it at the start; `record_review_outcome`
  writes feedback events after publish or discard. `save_user_memory` is the one writer of
  `user_preferences` (one transaction, row lock, merge); only the curator's `commit` calls it.
- **`feedback_history`** is the one channel that keeps every review round (`manager_feedback`
  holds only the latest). It is outer-graph only: the research subgraph runs on
  `ResearchState`, which omits it, because a reducer channel that a subgraph shares is added to
  a second time when the subgraph returns (`article/notes/02`).
- **Course cache** (`courses`, `course_evidence`) is shared across users and holds
  only validated courses.

The cost of this split: the profile is read at the start of each research pass, and
feedback is written only after publish, so feedback given during a run reaches the
next run's profile, not the pass in progress.

## Known gaps

Each of these is a stand-in that the article should name as such, not a finished
design.

1. **Courses have no topics.** The embedding sees only title and description, so a course
   whose text lacks the topic word is missed (recall 0.72 against 0.81 with ideal tags on the
   mock catalog, `notes/05`). A course-side tagging step that fills `courses.topics` from the
   course's own content is not built (`BACKLOG.md`).
2. **`profile_embedding` is provisioned but unused.** `course_embedding` is written and read;
   the profile column is not. Fix: P4b.
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
   no consumer and `preferred_course_length` has none until P4c, so feedback about them is
   dropped, not stored. The stubbed-model tests pin the loop; how a real model behaves on the
   case list is unmeasured (P6). Cases: `FEEDBACK.md`.
6. **Reducer channels double-count across review rounds.** `tavily_calls`, `completed_queries`
   and `research_notes` are added to again each time `course_research` returns, so a REWRITE or
   AUGMENT round doubles them (measured 1, 2, 4; `article/notes/02`). Fix: backlog S1.

## Naming

Node names are span names, checkpoint interrupt targets and `flow_specs.py` keys, so
they appear in traces and in stored checkpoints. Checkpoints written before the
rename hold the old names, so a run paused at the old `review_gate` should be treated
as unable to resume against the renamed graph. Not verified for that rename; finish or discard
in-flight runs before deploying it. Removing the three no-op anchors is different: a run
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
| `course_cache_upsert` | `save_verified_courses` |
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

# Architecture: the course-discovery workflow

Reference for the graph as implemented. It records what each node does, who owns
each decision, and where the current design is a stand-in. `STATUS.md` lists what is
implemented and `BACKLOG.md` what is next; this file holds the shape.

## Classification

The graph is a workflow with LLM decision points, not a free-running agent. Code
owns the control flow: the node sequence, the fan-out, the loop bound and the
human pause are all fixed at compile time. The model never chooses its own tools,
its own step count or its own stopping point.

Three nodes call an LLM, each through `build_llm()`:

| Node | What the model decides | Bound on the decision |
|---|---|---|
| `parse_user_request` | Which structured filters a free-text query implies | Pydantic schema; rule-based parser as fallback |
| `interpret_review_feedback` | Which of five routes free-text reviewer feedback selects | Closed `RoutingAction` enum; runs only after the human pause |
| `rank_and_summarize_courses` | Wording and ranking highlights of the digest | Template fallback; ranks only courses already validated |

Everything else is deterministic, including planning, extraction and validation.
Worth naming: the planner decides "cache or web" with a threshold on the cache hit
count, not with a model. That is a deliberate default. A model-driven planner is the
one change that would move this graph toward the agent end of the spectrum, and it
would keep the same bounds (`max_research_iterations`, validation before synthesis).

## Outer graph

```
parse_user_request ──ok──▶ course_research ──▶ [interrupt] await_human_review ──▶ interpret_review_feedback
        │                                                                                   │
        └─error─▶ discard_run ─▶ END                                                        ├─ PUBLISH ─▶ send_approved_courses ─▶ record_review_outcome ─▶ END
                                                                                            ├─ REWRITE ─▶ course_research
                                                                                            ├─ AUGMENT ─▶ prepare_augmented_search ─▶ course_research
                                                                                            ├─ RESET   ─▶ parse_user_request
                                                                                            └─ DISCARD ─▶ discard_run ─▶ END
```

`interrupt_before=["await_human_review"]` is always compiled in. Nothing publishes
without passing it.

## Research subgraph

```
start_research ──(AUGMENT)──────────────────────────────────────────────▶ plan_gap_search
      │
      └─▶ load_user_profile ─▶ find_known_courses ─▶ plan_web_search
                                                          │
                     ┌──── no queries needed ─────────────┤
                     ▼                                    ├─ error ─▶ end_research_on_error ─▶ END
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
| `parse_user_request` | Redact PII from the query and parse it into `SearchFilters`; on RESET, merge the new constraints into the old ones | LLM, rule fallback |
| `course_research` | Run the research subgraph | subgraph |
| `await_human_review` | The pause point where the interrupt fires; does nothing itself | anchor |
| `interpret_review_feedback` | Map reviewer feedback to one routing action | LLM, rule fallback |
| `prepare_augmented_search` | Anchor before an augment rerun; does nothing itself | anchor |
| `send_approved_courses` | Submit the publish effect through `EffectGateway` with a key derived from `run_id` | side effect |
| `record_review_outcome` | Record accept or reject feedback for the user | DB write |
| `discard_run` | End the run as discarded, with a reason | terminal |

### Research subgraph

| Node | Responsibility | Kind |
|---|---|---|
| `start_research` | Entry anchor; routes an AUGMENT rerun straight to gap planning | anchor |
| `load_user_profile` | Load preferences, completed and rejected courses | DB read |
| `find_known_courses` | Fetch previously validated courses from the cache | DB read |
| `plan_web_search` | Decide which queries are needed: none if the cache holds enough candidates | rules |
| `search_web_for_courses` | Run one query against the search provider; dispatched once per query via `Send` | I/O (mocked) |
| `extract_courses_from_results` | Turn raw search results into `CourseCandidate`s with evidence | rules |
| `merge_known_and_found_courses` | Combine cache and web candidates into one list | pure |
| `remove_duplicate_courses` | Drop duplicates by normalized URL, title+host fingerprint and fuzzy title | pure |
| `verify_course_claims` | Mark each candidate valid, uncertain or rejected against the filters and the user profile | rules |
| `plan_gap_search` | Build new queries from missing evidence and increment the iteration counter | rules |
| `save_verified_courses` | Persist valid and uncertain courses to the cache | DB write |
| `rank_and_summarize_courses` | Rank valid courses and write the digest | LLM, template fallback |
| `end_research_on_error` | Terminal for the error path | anchor |

Evidence rule: a missing piece of evidence yields `uncertain`, never `valid`.

## State and memory

Three stores, three lifetimes.

- **`AgentState`** is the per-run working memory: filters, candidates, validation
  results, iteration counter, notes. It is checkpointed per thread and is the only
  scratchpad the graph has.
- **User profile** (`user_preferences`, `recommendation_events`) is per-user and
  outlives runs. `load_user_profile` reads it at the start; `record_review_outcome`
  writes feedback events after publish.
- **Course cache** (`courses`, `course_evidence`) is shared across users and holds
  only validated courses.

The cost of this split: the profile is read at the start of each research pass, and
feedback is written only after publish, so feedback given during a run reaches the
next run's profile, not the pass in progress.

## Known gaps

Each of these is a stand-in that the article should name as such, not a finished
design.

1. **The cache ignores topic.** `find_known_courses` filters on price, certificate,
   level, language and the user's rejected or completed URLs. No topic predicate and
   no similarity term appear in the query, so results depend on `use_count`, not on
   relevance. `ResearchPlan.cache_query` carries the topic but nothing reads it.
2. **Embeddings are provisioned but unused.** `course_embedding` and
   `profile_embedding` exist in migration 001; no code writes or reads them.
3. **Deduplication is lexical.** URL, title+host hash and fuzzy title cannot merge the
   same course listed under different titles on different hosts.
4. **Search and ingestion share the request path.** Discovery, extraction, validation
   and cache writes all run while a user waits. The production shape separates an
   asynchronous ingestion pipeline (crawl, extract, validate, embed, deduplicate) from
   a request path that does hybrid retrieval first and falls back to web search only
   for thin coverage, feeding results back through ingestion.
5. **Feedback is recorded, not folded back.** `record_review_outcome` stores events;
   whether they update `user_preferences` automatically is not established here.
6. **Four anchor nodes do no work.** `start_research`, `await_human_review`,
   `prepare_augmented_search` and `end_research_on_error` exist for routing and the
   interrupt, not for computation.

## Naming

Node names are span names, checkpoint interrupt targets and `flow_specs.py` keys, so
they appear in traces and in stored checkpoints. Checkpoints written before the
rename hold the old names, so a run paused at the old `review_gate` should be treated
as unable to resume against the renamed graph. Not verified here; finish or discard
in-flight runs before deploying the rename.

| Before | After |
|---|---|
| `gateway` | `parse_user_request` |
| `research_agent` | `course_research` |
| `research_entry` | `start_research` |
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
| `research_done` | `end_research_on_error` |
| `review_gate` | `await_human_review` |
| `router` | `interpret_review_feedback` |
| `augment_dispatch` | `prepare_augmented_search` |
| `publish_node` | `send_approved_courses` |
| `user_memory_update` | `record_review_outcome` |
| `discard_node` | `discard_run` |

The Python functions keep their `*_node` names, and the `build_llm("router")` role
labels and `record_degradation` component labels are unchanged, so metric and
config keys are stable. `RoutingAction` values are also unchanged: they are
persisted in checkpoints and named in the router prompt.

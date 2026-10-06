# Course Discovery Agent

Built as part of the engineering work at [Halo Lab](https://www.halolab.us).

This repository implements a personalized, cache-first course research agent controlled by LangGraph.

The agent:

- parses a learner query into structured filters;
- loads durable user preferences;
- checks a shared course cache before web search;
- plans searches only for missing or stale evidence (search results are served from a mock catalog — see below);
- extracts course candidates with evidence (rule-based, not an LLM);
- deduplicates and validates candidates (rule-based);
- replans when too few valid courses exist;
- synthesizes an evidence-backed digest;
- interrupts before publication for human review;
- records useful courses and feedback through the persistence layer.

## Architecture

```text
parse_user_request
  -> course_research subgraph
  -> [interrupt_before: await_human_review]
  -> interpret_review_feedback
      -> PUBLISH -> send_approved_courses -> promote_approved_courses -> record_review_outcome -> END
      -> REWRITE -> course_research subgraph
      -> AUGMENT -> course_research subgraph
      -> RESET   -> parse_user_request
      -> DISCARD -> discard_run -> drop_pending_courses -> END
```

The research subgraph owns the bounded research loop. Node responsibilities and known gaps are in `specs/ARCHITECTURE.md`.

```text
load_user_profile
  -> find_known_courses
  -> plan_web_search
  -> search_web_for_courses(s) via Send when needed
  -> extract_courses_from_results
  -> merge_known_and_found_courses
  -> remove_duplicate_courses
  -> verify_course_claims
  -> plan_gap_search when validation is insufficient
  -> save_verified_courses (stages; the cache is written after approval)
  -> rank_and_summarize_courses
```

Without `DATABASE_URL` the graph checkpoints in memory. With it, the CLI checkpoints to Postgres (optionally AES-GCM sealed), the course cache and user memory live in Postgres, and publishing can go through an outbox worker (`EFFECT_GATEWAY=outbox`, `python -m course_discovery.effects`). Cache lookup ranks courses by topic similarity using stored embeddings (`course_embedding`; the default is a local lexical `HashingEmbedder`, not a semantic model; `EMBEDDER=openrouter` selects `OpenRouterEmbedder`), and `python -m course_discovery.research_agent.embeddings backfill` fills rows written before that (`specs/ARCHITECTURE.md`, cache lookup).

## Search is mocked

`search_web_for_courses` reads from a small static catalog (`course_discovery/research_agent/search/mock_catalog.py`) instead of calling a real search API. This project demonstrates LangGraph patterns, not course-search product quality — real course listing pages are mostly JS/PHP-rendered and poorly indexed, so a real search integration adds operational noise unrelated to the article's subject. `TavilyClient.search()` keeps the shape a real provider (Tavily, Serper, Brave) would have, so swapping it in production is a one-class change; no graph or node code depends on it being mocked.

## Environment

Optional keys:

```powershell
$env:OPENROUTER_API_KEY="..."   # structured LLM parsing/synthesis/router
$env:DATABASE_URL="postgresql://..." # durable memory/cache
```

If `OPENROUTER_API_KEY` is absent, `parse_user_request`, `rank_and_summarize_courses` and `interpret_review_feedback` use deterministic fallbacks. If `DATABASE_URL` is absent, the cache lookup uses a small local seed cache. No search API key is needed — search results are served from a mock catalog (see below).

Other switches (full detail in `CLAUDE.md`): `CHECKPOINT_ENCRYPTION_KEYS` and `CHECKPOINT_ENCRYPTION_REQUIRED` for encryption at rest, `PII_GUARDRAIL=off` for local debugging only, `EFFECT_GATEWAY=inline|outbox`, and `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_TRACES_EXPORTER=console` to turn tracing on (`docker compose --profile tracing up -d jaeger`).

## Run

```bash
uv sync
uv run python main.py
```

At review time the CLI prints the digest plus cache/search/validation counts. Feedback commands:

- `approve` or `publish`
- `rewrite: ...`
- `augment: ...`
- `reset: ...`
- `discard`

## Roadmap

The current implementation is a bounded workflow with three LLM decision points (memory, cache,
planning, search, extraction, validation, replanning and synthesis, with search mocked). Planning,
extraction and validation are rules. Production layers are built: Postgres checkpointing, PII
redaction, encryption at rest, erasure, an outbox for effects, and OpenTelemetry.
What is built is in [`specs/STATUS.md`](specs/STATUS.md); open work is in [`specs/BACKLOG.md`](specs/BACKLOG.md).


## Key Files

- `course_discovery/workflows/outer_graph.py`: review/publish control shell.
- `course_discovery/workflows/research_graph.py`: the bounded research subgraph.
- `course_discovery/domain/`: state and Pydantic contracts.
- `course_discovery/research_agent/`: memory, cache, planning, search, extraction, validation, and synthesis capabilities.
- `course_discovery/review/`: human gate and feedback router.
- `course_discovery/persistence/`: Postgres adapter, checkpointer factory, checkpoint encryption.
- `course_discovery/privacy/`, `guardrails/`, `pii_redaction/`: thread registry, erasure, PII data-flow check, redaction.
- `course_discovery/effects/`: `EffectGateway` port, outbox stores and delivery worker.
- `course_discovery/observability/`: structured logging, OpenTelemetry tracing and metrics.
- `course_discovery/app/`: CLI, prompts, and request parsing.
- `specs/ARCHITECTURE.md`: the implemented graph and its known gaps.
- `specs/article/DRAFT.md`: the current article draft; `specs/article/OUTLINE.md` its outline.
- `archive/`: superseded plans and drafts, kept for history.
- `specs/`: full implementation plan, article drafts, proposed metrics and testing approach.

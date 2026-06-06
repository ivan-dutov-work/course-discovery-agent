# Course Discovery Agent — Implementation Plan

## Project Goal

Build a structurally deterministic, multi-agent Course Discovery pipeline
using LangGraph. The agent scrapes, deduplicates, synthesizes, and
publishes free course recommendations to a Telegram community channel,
with a mandatory human-in-the-loop review stage before any broadcast.

---

## Milestone Summary

| #   | Milestone                  | Days       | Primary Focus                             |
| --- | -------------------------- | ---------- | ----------------------------------------- |
| M1  | **Article-Ready Core**     | Days 1–3   | LangGraph patterns, mock workers, CLI HIL |
| M2  | Telegram Integration       | Days 4–7   | Real bot, live status, inline buttons     |
| M3  | Real Discovery Workers     | Days 8–17  | API workers, web crawler, normalization   |
| M4  | Embedding Dedup + Postgres | Days 18–24 | pgvector, LangGraph Postgres checkpointer |
| M5  | Production Hardening       | Days 25–33 | Scheduling, multilingual, monitoring      |

---

## Milestone 1 — Article-Ready Core (Days 1–3)

> **Article milestone.** The deliverable of this milestone is a
> fully end-to-end runnable agent that demonstrates every major
> LangGraph pattern. It is the codebase used in the article
> _"Using LangGraph to Implement Complex AI Agents"_.

### Goal

Prove that LangGraph can orchestrate a complex, multi-path, stateful
pipeline with parallel execution, human interruption, and recovery —
without any real external APIs, databases, or bots. Every feature is
demonstrable from a single `python main.py` command.

### Scope

**State + Schema**

- Define the full `AgentState` TypedDict with all fields from the
  architecture spec: `search_filters`, `scraped_courses`,
  `deduplicated_courses`, `digest`, `manager_feedback`,
  `rewrite_instructions`, `routing_decision`, `iteration_count`,
  `max_iterations`, `run_id`.
- Define the `CourseCandidate` Pydantic model (all fields, no optional
  fields omitted — the schema is the contract).
- Define `SearchFilters` as a Pydantic model with all Gateway fields
  and their defaults.

**Gateway Node (LLM-powered)**

- Real LLM call (Gemini 2.0 Flash) with structured output via
  Pydantic.
- Parses free-form user query into validated `SearchFilters`.
- On validation failure: returns an error message to the user and
  stops; no auto-retry.
- On RESET path: merges new filter overrides into existing state
  instead of starting from scratch.

**Discovery Workers (Mock, Parallel)**

- Three mock worker nodes (`worker_a`, `worker_b`, `worker_c`) each
  returning a hardcoded list of `CourseCandidate` objects realistic
  enough to exercise the full pipeline.
- Executed in parallel via LangGraph's `Send` API / fan-out pattern.
- Aggregate results into `scraped_courses`. Hard cap: 200 items
  (enforced in the aggregation step).
- Workers simulate I/O delay (`asyncio.sleep`) to make parallelism
  observable in logs.

**Dedup Node (Layer 1 only)**

- URL normalization: strip UTM params and trailing slashes.
- Fuzzy title dedup using `rapidfuzz` (threshold: 90).
- Content hash check via `SHA-256(normalized_title + url_hostname)`.
- No vector store or embedding lookup — deferred to M4.
- Outputs `deduplicated_courses`.

**Synthesizer Node (LLM-powered)**

- Real LLM call: generates a 2–3 sentence highlight per course.
- Ranks: free first → rating desc → recency desc.
- Caps at 15 courses; logs the rest.
- Formats a Telegram-ready markdown digest.
- REWRITE path: re-uses existing `deduplicated_courses`,
  regenerates digest text using `rewrite_instructions`.
- On LLM failure: retries once, then falls back to raw field output.

**Human-in-the-Loop (CLI-based)**

- `interrupt_before=["telegram_gate"]` suspends execution.
- CLI prompt replaces the real Telegram bot — the PM types feedback
  directly in the terminal.
- Graph resumes by writing PM feedback into state and calling
  `graph.invoke` with the updated state.
- This is the core LangGraph HIL pattern and the article's centrepiece.

**Feedback Router (all 5 paths)**

- LLM-based classification of PM feedback into:
  `PUBLISH | REWRITE | AUGMENT | RESET | DISCARD`.
- Conditional edge dispatch using LangGraph's `add_conditional_edges`.
- `max_iterations` guard: force-DISCARD when limit is reached.
- PUBLISH: logs the digest to stdout (no real broadcast).
- AUGMENT: routes back to one or all mock workers based on PM
  instruction; new results appended, dedup re-runs on full list.

**Checkpointing**

- `MemorySaver` (in-memory). Zero infrastructure, crash on restart —
  acceptable for article demo, clearly documented as dev-only.
- Demonstrates the checkpoint/resume API: show that re-invoking with
  the same `thread_id` after the interrupt resumes from where the
  graph paused.

**Project Bootstrap**

- `uv` project with `pyproject.toml`.
- Dependencies: `langgraph`, `langchain-google-genai`, `pydantic`, `rapidfuzz`.
- `.env.example` with `GOOGLE_API_KEY`.
- `python main.py` runs a demo with a hardcoded query and interactive
  terminal HIL.

### LangGraph Features Demonstrated

| Feature                             | Where Used                   |
| ----------------------------------- | ---------------------------- |
| `StateGraph` + `TypedDict` state    | Entire graph                 |
| `add_node` / `add_edge`             | Graph construction           |
| `add_conditional_edges`             | Feedback Router              |
| `Send` API (fan-out)                | Parallel workers             |
| `interrupt_before`                  | Telegram Gate node           |
| `MemorySaver` checkpointing         | Full graph                   |
| Checkpoint resume via `thread_id`   | HIL resume flow              |
| `temperature=0` + structured output | Gateway, Router, Synthesizer |

### Deliverables

- `main.py` — demo entrypoint with end-to-end run
- `agent/state.py` — `AgentState`, `CourseCandidate`, `SearchFilters`
- `agent/graph.py` — graph definition, node wiring, compilation
- `agent/nodes/gateway.py`
- `agent/nodes/workers.py` — all three mock workers + aggregator
- `agent/nodes/dedup.py`
- `agent/nodes/synthesizer.py`
- `agent/nodes/hil.py` — CLI interrupt handler + resume logic
- `agent/nodes/router.py`
- `pyproject.toml` with locked dependencies
- `.env.example`

### Out of Scope

- Real Telegram bot (M2)
- Any real API calls in workers (M3)
- Vector embeddings and Layer 2 dedup (M4)
- Postgres checkpointing (M4)
- Scheduling, multilingual, domain blocklist (M5)

---

## Milestone 2 — Telegram Integration (Days 4–7)

### Goal

Replace the CLI HIL stub with a real aiogram Telegram bot. The PM
reviews digests and provides feedback entirely via Telegram.

### Scope

- `aiogram` bot setup; `/start`, `/run <query>` commands.
- Telegram Gate node: `interrupt_before` now suspends and notifies
  the PM via Telegram instead of printing to CLI.
- Live status via `bot.edit_message_text` after each node completes
  (Gateway, Workers, Dedup, Synthesizer).
- Inline keyboard buttons for PUBLISH / REWRITE / AUGMENT / RESET /
  DISCARD mapped to the Feedback Router.
- Graph resume triggered by incoming callback_query or text reply.
- Telegram failure handling: exponential backoff queue
  (1 min → 5 min → 15 min → 1 hr, max 5 retries).
- Store `message_id` in state for `/unpublish` support.
- Multi-run serialization: queue a second run if one is pending review.

### Dependencies Added

`aiogram`, `python-dotenv` (Telegram token)

---

## Milestone 3 — Real Discovery Workers (Days 8–17)

### Goal

Replace mock workers with real API-backed and web-crawling workers.
Volumetric and quality coverage improves dramatically.

### Scope

**Worker A — Google + YouTube**

- Google Custom Search API: structured queries with filters.
- YouTube Data API v3: keyword search, `publishedAfter` filter.
- Normalize results to `CourseCandidate`.

**Worker B — Udemy + Coursera**

- Udemy Affiliate API (fallback: public page scrape + JSON-LD).
- Coursera API (fallback: `site:coursera.org` via SerpAPI + parse).
- Rich metadata: `rating`, `enrollment_count`, `last_updated`.

**Worker C — Reddit**

- OAuth app; search `r/learnprogramming`, `r/datascience`, etc.
- Extract course URLs + social proof (upvotes, comment sentiment).

**Worker D — Web Crawler (Phase 1)**

- Brave Search API as primary; SerpAPI as fallback.
- HTTP-only, no headless browser yet.
- Domain whitelist ~50 trusted sites (Prometheus, Coursera, ed-era, etc.).
- Schema.org JSON-LD → OpenGraph → heuristic extraction pipeline.
- fasttext language detection; skip pages outside `content_languages`.
- robots.txt cache per domain; 1 req/5 s per domain rate limit.
- Domain quality gate: affiliate density, content length.

**Cross-cutting**

- `domain_blacklist` from `search_filters` applied in all workers.
- Graceful failure: empty list + error flag on API failure.
- Integration tests with VCR cassettes (recorded HTTP fixtures).

### Dependencies Added

`httpx`, `beautifulsoup4`, `fasttext`, `praw` (Reddit), `chardet`

---

## Milestone 4 — Embedding Dedup + Postgres Persistence (Days 18–24)

### Goal

Add cross-run dedup via vector embeddings and replace in-memory
checkpointing with PostgreSQL. The agent becomes stateful across
restarts.

### Scope

**Dedup Layer 2 — Embeddings**

- Embed `title + description + platform` via
  `multilingual-e5-large` (sentence-transformers).
- Query `pgvector` Postgres extension; cosine similarity threshold
  `0.92` (monolingual) / `0.88–0.90` (cross-lingual).
- Metadata diff rules: re-surface on PRICE DROP ≥50%, RATING CHANGE
  ≥0.5, `last_updated` change.
- `cross_lingual_dedup` flag in state.
- Calibrate thresholds against labelled EN/UK duplicate dataset.

**PostgreSQL Checkpointer**

- Migrate from `MemorySaver` to LangGraph's `PostgresSaver`.
- Each node completion writes checkpoint to Postgres.
- On restart: graph resumes from last checkpoint with same
  `thread_id`.

**Persistence Tables**

- `user_search_history`: `user_id`, `last_search_date`,
  `search_filters` (JSONB), `run_id`.
- `courses` vector table: full `CourseCandidate` fields +
  `embedding` (vector), `last_seen_run_id`, `last_seen_date`.
- `run_log`: `run_id`, `status`, `routing_decisions`, `iteration_count`,
  `pm_feedback`, `published_message_id`.

### Dependencies Added

`sentence-transformers`, `pgvector`, `psycopg[binary]`, `alembic`

---

## Milestone 5 — Production Hardening (Days 25–33)

### Goal

The agent runs reliably in production: scheduled, monitored, multilingual,
and guarded against degradation.

### Scope

**Scheduling**

- `APScheduler` cron job fires at configurable interval.
- Synthetic user message written into state using last-used
  `search_filters`.
- Run serialization: queue if run is pending PM review.

**Multilingual Support**

- `user_language` and `search_languages` fields fully wired.
- Digest generated in `user_language`; course titles include
  translation when `detected_language != user_language`.
- Ukrainian locale: currency, date formatting.
- Gateway few-shot examples for Ukrainian queries.
- LLM benchmark: Gemini 2.0 Flash vs. Claude 3.5 Sonnet on 50 Ukrainian
  course summaries.

**Domain Blocklist Management**

- `/block domain.com` Telegram command updates blocklist in Postgres.
- Effective on the next run.

**Metrics + Monitoring**

- Structured JSON logging via `structlog` for every node execution,
  LLM call, and routing decision.
- Prometheus metrics exported: run count, latency histogram per node,
  error rate, PM effort score, dedup hit rate.
- Alerting on run STALLED state (Telegram DM to admin).

**WebSocket Dashboard**

- FastAPI + WebSocket endpoint streams node-level progress events.
- Non-PM stakeholders can watch runs in real time via browser.

**Worker D Phase 2**

- Playwright headless browser for JS-heavy sites.
- Proxy rotation for high-value domains.
- Brave Search API upgraded to paid plan for no per-query cap.

### Dependencies Added

`apscheduler`, `structlog`, `prometheus-client`, `fastapi`,
`playwright`, `websockets`

---

## Technology Stack Summary

| Layer                   | Technology                                        | First Used |
| ----------------------- | ------------------------------------------------- | ---------- |
| Agent orchestration     | LangGraph                                         | M1         |
| LLM calls               | LangChain + Gemini 2.0 Flash                      | M1         |
| Schema validation       | Pydantic v2                                       | M1         |
| In-memory checkpointing | LangGraph `MemorySaver`                           | M1         |
| Fuzzy dedup             | `rapidfuzz`                                       | M1         |
| Telegram bot            | `aiogram`                                         | M2         |
| Web crawling            | `httpx`, `beautifulsoup4`                         | M3         |
| Language detection      | `fasttext`                                        | M3         |
| Reddit scraping         | `praw`                                            | M3         |
| Multilingual embeddings | `sentence-transformers` (`multilingual-e5-large`) | M4         |
| Vector store            | `pgvector` + PostgreSQL                           | M4         |
| Postgres checkpointing  | LangGraph `PostgresSaver`                         | M4         |
| Migrations              | `alembic`                                         | M4         |
| Scheduling              | `APScheduler`                                     | M5         |
| Logging                 | `structlog`                                       | M5         |
| Metrics                 | `prometheus-client`                               | M5         |
| Real-time dashboard     | FastAPI + WebSocket                               | M5         |
| Headless browser        | `playwright`                                      | M5         |

---

## Risk Register

| Risk                           | Likelihood | Mitigation                                    |
| ------------------------------ | ---------- | --------------------------------------------- |
| Coursera API deprecated        | High       | Fallback: public page scraper (M3)            |
| Udemy Affiliate approval delay | Medium     | Fallback: JSON-LD page scrape (M3)            |
| Google CSE 100 req/day cap     | High       | Fallback: SerpAPI at M3 start                 |
| multilingual-e5-large latency  | Medium     | Benchmark at M4 start; fallback to Cohere API |
| Cross-lingual dedup thresholds | Medium     | Calibrate on labelled dataset before M4 ship  |
| Playwright CAPTCHA blocks      | Low        | Skip domain, alert PM; no solving service     |

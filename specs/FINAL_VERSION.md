**Course Discovery Agent**

Architecture Outline

_Multi-Agent LangGraph · Telegram HIL · Embedding-Based Dedup · RAG
Curriculum Verification_

**1. Problem Statement**

**1.1 The SEO Trap in Informal Education**

Students searching for bootcamps, crash courses, or certificates are
immediately met with SEO-optimized marketing pages. Finding high-signal,
low-cost courses that actually deliver a verifiable certificate is a
massive time sink.

**1.2 Failure Modes of Naive LLM Wrappers**

- **Hallucinated Curriculums:** LLM invents plausible-sounding courses
  that do not exist.

- **Ignored Constraints:** Routinely fails to respect strict filters
  (e.g., pitches a \$2,000 bootcamp to a broke student).

- **Autonomous Risk:** If wired to publish automatically, a single
  hallucination destroys community trust.

- **Stale Results:** No awareness of previously surfaced courses;
  re-surfaces the same content on each run.

**1.3 Goal**

Build a structurally deterministic Course Discovery Agent that acts less
like a chatbot and more like a ruthless academic advisor --- one that
enforces strict quality controls, respects human editorial authority,
and never publishes autonomously. The pipeline graph and node execution
order are fixed; LLM-powered nodes (Gateway, Synthesizer, Feedback
Router) are stochastic but controlled via temperature=0 and full
prompt/response logging.

**2. High-Level Architecture**

**2.1 Graph Overview (LangGraph)**

The system is a stateful directed graph with five major node clusters
and one interrupt boundary:

- **Gateway Node** --- Request Formatter; parses free-form user input
  into strict JSON filters.

- **Discovery Workers** --- Parallel scraping agents for
  Google/YouTube, Udemy/Coursera, Reddit, and general web (Worker D).

- **Dedup Node** --- Two-layer deduplication: URL/title fuzzy match +
  embedding similarity vs. vector store.

- **Synthesizer Node** --- Ranks, verifies URLs, and compiles a
  human-readable digest.

- **Telegram Gate** --- interrupt_before boundary; suspends execution,
  notifies PM, waits for feedback.

- **Feedback Router** --- Parses PM reply into one of four routing
  paths (REWRITE, AUGMENT, RESET, DISCARD). PUBLISH is a terminal
  broadcast action at graph END, not a routing path.

**Checkpointing:** LangGraph checkpointing is enabled. State is
persisted to Postgres after each node completes. On crash or restart,
the graph resumes from the last successful checkpoint --- no re-scraping
of already-completed API calls.

**Run Serialization:** One active run at a time per community. If a new
run is triggered while another is pending PM review, it is queued and
the PM is notified: "Run #{N} queued --- finish current review first."

**2.2 Run Triggers**

Runs can be initiated in two ways:

- **Interactive:** User taps a button or sends a free-form prompt in
  the aiogram Telegram bot. The message is passed directly to the
  Gateway Node.

- **Scheduled:** A cron job fires at a configured interval (e.g., daily
  or weekly). It writes a synthetic user message into state using the
  last-used search_filters for that community, then enters the pipeline
  at the Gateway Node like any interactive run.

Both entry points produce the same state shape and follow the same
graph path from Gateway onward.

**2.3 State Object (TypedDict)**

The shared graph state carries all data between nodes:

- **search_filters** --- validated JSON (topic, max_price, language,
  requires_certificate, from_date, course_level, prerequisites,
  target_audience)

- **scraped_courses** --- list of CourseCandidate objects from all
  workers. Hard cap: 200 items. On overflow, lowest-ranked candidates
  (by rating, then recency) are evicted before new results are
  appended.

- **deduplicated_courses** --- output of the Dedup Node

- **digest** --- compiled markdown string from the Synthesizer

- **manager_feedback** --- raw string from the Telegram PM

- **rewrite_instructions** --- extracted from manager_feedback when
  routing is REWRITE; read by the Synthesizer to know what to change.
  Null on all other routes.

- **routing_decision** --- resolved enum: PUBLISH \| REWRITE \|
  AUGMENT \| RESET \| DISCARD

- **iteration_count** --- int, starts at 0, incremented on each
  feedback loop

- **max_iterations** --- int, default 5. When iteration_count reaches
  this limit, the run is force-DISCARDed and the PM is alerted.

- **user_id** --- for fetching last_search_date from persistent store

- **run_id** --- unique identifier for this execution

- **user_language** --- ISO 639-1 code for the user's preferred language (bot interactions and digest output). Default: detected from Telegram client locale (`message.from.language_code`).

- **search_languages** --- List of ISO 639-1 codes for course content to search. Default: `[user_language]`. Set to `["uk", "en"]` for bilingual runs.

- **cross_lingual_dedup** --- Boolean. When true, deduplication compares candidates across languages using multilingual embeddings. Default: true when `search_languages` contains more than one entry.

**3. Node Specifications**

**3.1 Gateway Node --- Request Formatter**

Converts a free-form user message into a validated Pydantic JSON object.
Acts as the pipeline's single point of truth for search parameters.

**LLM Error Handling:** On parse failure (Pydantic validation error or
LLM returning malformed output), the Gateway immediately asks the user
to rephrase via Telegram. No automatic retry --- the user's rephrased
message re-enters the Gateway as a fresh attempt.

**Input Schema Fields**

- **topic** --- Required. Extracted from user message. e.g. "API
  development", "Python basics".

- **max_price** --- Default: 0 (free only). User can override.

- **requires_certificate** --- Default: true. Boolean.

- **target_audience** --- Default: "student". Enum: student \|
  professional \| hobbyist. Used by Workers to bias search queries
  (e.g., append "for students" or "for professionals" to query
  strings).

- **language** --- Default: "en". ISO 639-1 code.

- **course_level** --- Default: null (any). Enum: beginner \|
  intermediate \| advanced.

- **prerequisites** --- Default: \[\]. List of required prior knowledge
  strings. Used by Workers to filter results where platform metadata
  allows (e.g., Coursera exposes prerequisite data). Included in
  CourseCandidate for digest display.

- **from_date** --- Default: user's last_search_date from DB. User may
  specify any date no more than 7 days before their last search date.
  Validator clamps or rejects out-of-range values.

- **source_types** --- Default: `["platform_api", "general_web"]`. Enum list: `platform_api | general_web`. Controls whether Workers query structured platform APIs only, general web (Worker D), or both.

- **content_languages** --- Default: `[search_filters.language]`. List of ISO 639-1 codes for the language(s) of course content to retrieve. Independent from `user_language` (the UI and bot interaction language). Example: a Ukrainian user searching English courses sets `language: "en"` in filters but retains `user_language: "uk"` in state.

- **domain_whitelist** --- Default: null (no restriction). Optional list of allowed domains for Worker D. When set, Worker D restricts web search to these domains only. Used during Phase 1 to limit crawl scope.

- **domain_blacklist** --- Default: built-in list of known low-quality domains. PM can append entries via the `/block domain.com` Telegram command. Changes take effect on the next run.

**from_date Validation Rules**

- If user has no prior search history: default to 30 days ago.

- If user specifies a date: validate it is >= (last_search_date - 7
  days).

- Behavior on out-of-range: clamp to nearest valid date + log warning
  (friendlier UX than hard rejection).

- First-run fallback: store from_date of first run as the user's
  anchor date.

> _⚠️ The Gateway Node is also the target of the RESET routing path ---
> it must be able to merge new filter overrides onto the existing state
> rather than always starting from scratch._

**Multilingual Query Handling:** The Gateway accepts queries in any language present in `search_languages`. The LLM system prompt is tagged with the detected input language: "The user query is in [LANGUAGE]. Extract search parameters and return as JSON." Few-shot examples are maintained for both English and Ukrainian inputs. The extracted `topic` field preserves the user's original phrasing; localized query expansion (e.g., appending "безкоштовний" for Ukrainian free-course searches) is handled per-worker, not by the Gateway.

**3.2 Discovery Workers (Parallel)**

Four independent worker nodes execute simultaneously via LangGraph's
parallel branch support.

**Worker A --- Google + YouTube**

- Google Custom Search API: queries structured as {topic} +
  {course_level} + {target_audience} + free course + certificate
  site:youtube.com OR site:coursera.org OR site:udemy.com

- YouTube Data API v3: search by keyword, filter by publishedAfter
  (maps to from_date), retrieve title, channel, description, view
  count, duration.

- Output: list of raw hits, normalized to the CourseCandidate schema.

**Worker B --- Udemy + Coursera**

- Udemy Affiliate API: filter by price (0 for free), language,
  category, rating > 4.0.

- Coursera API: filter by free audit availability, subject, language.
  Where available, use prerequisite metadata to filter by
  prerequisites from search_filters.

- Output: list of CourseCandidate objects with richer metadata (rating,
  enrollment count, last_updated).

**Worker C --- Reddit**

- Searches relevant subreddits (e.g., r/learnprogramming,
  r/datascience) for course recommendations matching the topic and
  level.

- Extracts course URLs and social proof data (upvotes, comment
  sentiment) from threads.

- Output: list of CourseCandidate objects with social_proof metadata.

**Worker D --- Universal Web Crawler (Multilingual)**

Discovers courses on any publicly accessible domain via general web search. Runs in parallel with Workers A, B, and C when `source_types` in `search_filters` includes `"general_web"`.

- **Search layer:** Brave Search API as primary provider (multilingual coverage, no per-query daily caps on paid plan). SerpAPI as fallback, with `hl` and `gl` parameters set per `content_languages`. Queries are constructed in each language from `search_languages`:
  - English: `"{topic}" free course certificate {course_level} {target_audience}`
  - Ukrainian: `"{topic_uk}" безкоштовний курс сертифікат {course_level_uk}`
  - `domain_whitelist` and `domain_blacklist` from `search_filters` are applied to filter URLs before fetching.

- **Fetch & extract layer:** Standard HTTP with realistic browser headers as the first attempt. Falls back to Playwright headless browser when JS rendering is detected (response body < 500 chars, SPA shell indicators). `Accept-Language` header is set to match `content_languages`. Encoding detection via chardet handles Windows-1251 and other legacy encodings common on older Ukrainian educational sites.

- **Structured data extraction (priority order):**
  1. Schema.org `Course` / `CourseInstance` JSON-LD --- `extraction_confidence: 1.0`.
  2. OpenGraph / meta tags --- `extraction_confidence: 0.8`.
  3. Heuristic regex patterns for price, duration, certificate keywords (multilingual patterns maintained for EN and UK) --- `extraction_confidence: 0.7`.
  4. LLM-based field extraction via Gemini 2.0 Flash with Pydantic structured output as last resort --- `extraction_confidence: 0.5`. Used only when all heuristics yield fewer than 3 fields.

- **Language detection:** fasttext `lid.176.bin` model detects the language of each fetched page. Pages in a language not present in `content_languages` are filtered out. Detected language is stored in the `detected_language` field of CourseCandidate.

- **Domain quality gate:** Affiliate link density > 40%, extracted content < 250 words, or DA < 20 (Phase 2 only) causes the page to be skipped and logged.

- **Rate limiting:** 1 request per 5 seconds per domain by default. robots.txt is checked per domain at worker startup and cached for the run duration; paths covered by `Disallow` are skipped. Global cap: 80 concurrent requests per run.

- **Startup strategy:** Phase 1 runs against a curated domain whitelist (~50 trusted sites, including Ukrainian platforms: prometheus.org.ua, ed-era.com, osvitoria.com, osvita.diia.gov.ua) using plain HTTP only --- no proxies or headless browsers. Phase 2 opens to general web with proxy rotation and Playwright after Phase 1 validates coverage and demand.

- **CAPTCHA handling:** On CAPTCHA detection or persistent 403 responses, the domain is skipped for the current run. The PM receives a note in the status ticker: "N domains skipped (bot-protected)." No CAPTCHA-solving service is used --- see FUTURE_IDEAS for discussion.

- Output: list of CourseCandidate objects normalized to the same schema as Workers A–C. Sets `source_worker: "worker_d"`.

**CourseCandidate Schema**

All workers normalize their output to this shared schema:

- **course_id: str** --- platform-specific ID or generated hash
  (SHA-256 of url).
- **title: str** --- course title as listed on the platform.
- **url: str** --- canonical URL, normalized (no UTM params, no
  trailing slashes).
- **platform: Enum** --- udemy \| coursera \| youtube \| google \|
  reddit.
- **description: str** --- raw description from source.
- **price: float** --- 0.0 for free.
- **currency: str** --- ISO 4217 code, default "USD".
- **rating: Optional\[float\]** --- 0.0--5.0 scale.
- **enrollment_count: Optional\[int\]** --- number of enrolled
  students.
- **language: str** --- ISO 639-1 code.
- **course_level: Optional\[Enum\]** --- beginner \| intermediate \|
  advanced.
- **certificate_type: Enum** --- none \| platform \| accredited.
  Classification heuristic: YouTube → none, Udemy → platform, Coursera
  with university partner → accredited.
- **instructor: Optional\[str\]** --- instructor or channel name.
- **duration_hours: Optional\[float\]** --- total content length.
- **last_updated: Optional\[datetime\]** --- when course content was
  last refreshed.
- **source_worker: str** --- "worker_a", "worker_b", "worker_c", or "worker_d".
- **prerequisites: List\[str\]** --- extracted from platform metadata,
  or empty list.
- **target_audience: Optional\[Enum\]** --- student \| professional \|
  hobbyist. Inferred from metadata where available.
- **detected_language: str** --- ISO 639-1 code detected from page content via fasttext. May differ from `language` (the user's filter or the platform's declared language).
- **domain: str** --- hostname extracted from `url`. Used for per-domain rate limiting, reputation scoring, and PM-driven blocklist management.
- **domain_authority: Optional\[int\]** --- Domain Authority score (0–100) from reputation API. Null if unavailable or not configured. Not applied to platform API results (always trusted).
- **structured_data_source: Enum** --- `api | schema_org | json_ld | opengraph | heuristic | llm_extracted`. How course metadata was obtained.
- **extraction_confidence: float** --- 0.0–1.0. Confidence in extracted metadata accuracy: 1.0 for structured API / JSON-LD; 0.8 for OpenGraph; 0.7 for heuristic; 0.5 for LLM extraction. Used by the Synthesizer to flag low-confidence entries for PM attention.
- **content_hash: str** --- SHA-256 of normalized title + URL hostname. Enables fast cross-domain duplicate detection in Dedup Layer 1, before embedding comparison.

**Graceful Failure**

- On API rate limit or 0 results: workers set an error flag in state
  and return an empty list --- no hallucination.

- Synthesizer Node handles the empty-state case explicitly.

**3.3 Dedup Node**

Prevents re-surfacing courses the community has already seen. Two-layer
architecture: fast metadata check, then semantic embedding check.

On AUGMENT re-entry, the Dedup Node re-runs on the **full**
scraped_courses list (not just newly appended results) to catch
cross-source duplicates introduced by the new worker run.

**Layer 1 --- Within-Run Dedup**

- Normalize URLs (strip UTM params, trailing slashes).

- Fuzzy title match using rapidfuzz (threshold: 90) to catch same
  course listed on multiple platforms.

- Runs in memory --- no DB call needed.

**Layer 2 --- Cross-Run Embedding Dedup**

- Embed each surviving candidate: concatenate title + description +
  platform, pass through text-embedding-3-small.

- Query vector store (pgvector on Postgres; migrate to Qdrant at
  scale for native metadata filtering) with cosine similarity.

- Threshold: 0.92. Above threshold = known course.

- If known course: check metadata diff against stored record using the
  following explicit rules:
  - Price decreased by ≥50% OR became free → re-surface with
    \[PRICE DROP\] tag.
  - Rating changed by ≥0.5 → re-surface with \[RATING CHANGE\] tag.
  - last_updated changed (course content refreshed) → re-surface with
    \[UPDATED\] tag.
  - All other metadata changes: do **not** re-surface.

- If no re-surface trigger and last_seen within from_date window: drop.

- If new course: upsert vector + metadata to store with current run_id
  and timestamp.

**Recommended Stack**

- pgvector + Postgres --- zero extra infra, integrates with existing
  user DB. Migrate to Qdrant when query volume or dataset size demands
  better performance and native metadata filtering.

- Embedding model: `multilingual-e5-large` (self-hosted via sentence-transformers or HuggingFace Inference API) for any deployment that includes non-English languages. Strong cross-lingual performance for Ukrainian–English retrieval. Fallback: Cohere `embed-multilingual-v3.0` (~\$0.10/1M tokens, API-based). **Do not use `text-embedding-3-small` for multilingual runs** --- it is English-optimized and produces unreliable cross-lingual similarity scores.
- **Cross-lingual dedup threshold:** Lower the similarity threshold to 0.88–0.90 when `cross_lingual_dedup` is true (vs. 0.92 for monolingual runs). Calibrate against a labeled EN/UK duplicate dataset before production deployment.

> _⚠️ Do not use cosine similarity alone for dedup --- also check
> metadata diff. A course that went from \$200 to free should be
> re-surfaced even if its embedding is nearly identical._

**3.4 Synthesizer Node**

Compiles the deduplicated course list into a structured digest ready for
PM review and eventual community broadcast.

**URL Liveness Check:** Before compiling the digest, the Synthesizer
performs an HTTP HEAD request on each course URL. Courses returning
4xx/5xx status codes or redirecting to generic error pages are dropped
from the digest and logged for debugging.

**Ranking and Digest Generation:**

- Rank courses by: free first, then by rating, then by recency
  (last_updated).

- Apply digest size cap: **max_courses_per_digest = 15**. After
  ranking, take the top 15. Remaining courses are logged but not
  included in the digest. Digest footer reads: "15 of {N} courses
  shown. {N-15} additional courses available on request."

- Generate a short Gemini 2.0 Flash summary per course: 2-3 sentence highlight,
  not marketing copy.

- Format as a Telegram-ready markdown digest with: course title,
  platform, price, level, certificate_type (none / platform /
  accredited), curriculum_score and rigor_label (where available), and
  link.

- If 0 courses survived dedup + liveness check: synthesizer emits an
  empty-digest signal and routes directly to Telegram Gate with a
  no-results message for PM.

**LLM Error Handling:** If Gemini 2.0 Flash fails to generate summaries, the
Synthesizer retries once with the same input. On second failure, it
sends raw course data (title, URL, price, rating, certificate_type) to
the PM without LLM-generated summaries. The PM can still review and
publish.

**Language-Aware Digest Generation:** The Synthesizer generates course summaries in `user_language` from state. When a course's `detected_language` differs from `user_language`, the summary is written in `user_language` but preserves the original title with a translation: `"Introduction to Python (Вступ до Python)"`. Currency and date values are formatted per locale (see Section 9).

**Low-Confidence Flagging:** Courses with `extraction_confidence < 0.7` (Worker D heuristic/LLM-extracted metadata) are flagged in the digest with ⚠️ and a note: "Metadata auto-extracted — verify before publishing." The PM can investigate the course further before approving.

**REWRITE Behavior:** When re-entered via the REWRITE routing path, the
Synthesizer reads `rewrite_instructions` from state. It does **not**
re-scrape or re-dedup --- only the digest text is regenerated using the
same `deduplicated_courses` and the PM's rewrite guidance.

**3.5 Telegram Gate (interrupt_before)**

The hard boundary between autonomous agent execution and human editorial
control. The graph suspends here and will not continue until PM sends a
reply.

**Live Status via edit_message**

As each upstream node completes, the bot sends or edits a single
Telegram message to the PM to show real-time progress.

- Node start: bot sends initial message --- 'Starting run #42...'

- After Gateway: edits message --- 'Filters locked: {topic: Python,
  max_price: 0, from_date: 2025-06-01}'

- After Workers: edits --- 'Scraped 38 candidates (Worker A: 21,
  Worker B: 12, Worker C: 5)'

- After Dedup: edits --- '12 new courses after dedup (26 dropped as
  seen/unchanged)'

- After Synthesizer: sends full digest as a new message with
  approve/edit/reject inline buttons.

**WebSocket Dashboard:** A WebSocket-based dashboard provides real-time
node-level streaming for non-PM stakeholders who want visibility without
being in the Telegram thread.

**Telegram Failure Handling:** On Telegram API failure, messages are
queued with exponential backoff: 1 min → 5 min → 15 min → 1 hour, max
5 retries over ~2 hours. If all retries are exhausted, the run is
marked as STALLED in the Run Log and an alert is written to the
application log. After 24 hours with no recovery, the run is
auto-DISCARDed.

**3.6 Feedback Router**

Parses the PM's Telegram reply (text or inline button) and returns the
name of the next node to execute. This is the core of the multi-path HIL
system. Each feedback loop increments `iteration_count`; when
`max_iterations` (default: 5) is reached, the run is force-DISCARDed
and the PM is alerted.

**LLM Error Handling:** The router uses an LLM call with a strict output
enum to classify the PM's freeform reply, prompted with few-shot
examples of each routing case. On low-confidence classification, the
router echoes its best guess back to the PM: "I understood this as
REWRITE. Correct?" The PM confirms or corrects before execution
continues.

**Routing Matrix**

| Action                               | Behavior                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PUBLISH** (Approve / Looks good)   | Terminal action. Broadcasts the approved digest to the preconfigured Telegram community channel. Not a graph node --- it is a side-effect triggered at graph END. The Telegram message_id is stored in the Run Log for unpublish reference.                                                                                                             |
| **REWRITE** (Tone/copy rewrite)      | Routes back to Synthesizer. No re-scraping or re-dedup. PM's rewrite guidance is stored in `rewrite_instructions`. Cheap.                                                                                                                                                                                                                               |
| **AUGMENT** (Add new source)         | Routes back to the relevant worker(s) --- **dynamically resolved** based on the PM's instruction (e.g., "also check Udemy" → Worker B; "add Reddit" → Worker C; unspecified → all workers). New results are appended to `scraped_courses`. Dedup re-runs on the **full** list. Synthesizer regenerates the digest from the full `deduplicated_courses`. |
| **RESET** (Change filters)           | Routes to Gateway Node. Merges new filter overrides into `search_filters`. **Clears** `scraped_courses`, `deduplicated_courses`, and `digest`. Full pipeline re-run from Workers onward.                                                                                                                                                                |
| **DISCARD** (Reject / Skip this run) | Routes to END without publishing. Logs the PM's reason in the Run Log.                                                                                                                                                                                                                                                                                  |

**Unpublish:** After a PUBLISH, the PM can issue a `/unpublish` command
or reply to the published community message to delete it from the
channel. The bot uses the stored Telegram message_id to locate and
remove the post.

**4. Data Persistence Layer**

**4.1 User Search History Table**

- Stores: user_id, last_search_date, search_filters (JSONB), run_id.

- Used by Gateway Node to resolve default from_date.

- Updated at the end of each successful PUBLISH run.

**4.2 Course Vector Store**

- Stores: course_id, embedding (vector), title, url, platform, price,
  currency, rating, enrollment_count, certificate_type, last_updated,
  last_seen_run_id, last_seen_date.

- Queried by Dedup Node Layer 2.

- Upserted by Dedup Node on new or changed courses.

**4.3 Run Log Table**

- Stores: run_id, user_id, timestamp, routing_decisions (list),
  iteration_count, final_status (PUBLISHED \| DISCARDED \| STALLED \|
  IN_PROGRESS), telegram_message_id (for unpublish).

- Useful for debugging feedback routing in production.

**5. RAG Curriculum Verification**

Curriculum verification via RAG confirms that informal courses actually
map to formal academic standards.

> _⚠️ This feature is only viable when course providers offer accessible
> syllabus PDFs. For courses without syllabi, a heuristic quality gate
> applies: courses with >4.5 rating + >10k enrollments pass
> automatically._

**5.1 Syllabus Ingestion Node**

- Downloads syllabus PDFs from course landing pages (where available).

- Chunks and embeds syllabus content into a separate Curriculum Vector
  Store.

**5.2 Academic Cross-Reference Node**

- Queries a reference vector store of formal university course
  outlines (MIT OpenCourseWare, etc.).

- Computes curriculum overlap score: % of syllabus modules that match
  a formal discipline.

- Courses below a threshold (e.g. <40% overlap) are flagged as
  low-rigor in the digest.

**5.3 Digest Enhancement**

- Synthesizer Node gains a new field per course: curriculum_score and
  rigor_label (academic \| practical \| marketing).

- PM can use this to filter out shallow content before publishing.

**5.4 Ukrainian Academic Syllabus Corpus**

To support multilingual curriculum verification, a dedicated Ukrainian academic syllabus corpus is built alongside the English reference store:

- **Corpus sources:** Syllabus PDFs from Ukrainian universities (institutional repositories, Ministry of Education and Science official standards, open-access course outlines from Ukrainian HEIs).

- **Ingestion pipeline:** PDF download → OCR where needed (tesseract with Ukrainian language pack) → chunk by section heading (module / тема) → embed with the multilingual embedding model (Section 9.4) → upsert into a dedicated Ukrainian Curriculum Vector Store (separate from the English reference store).

- **Metadata per chunk:** institution, academic year, module title, level (bachelor / master), discipline code, language.

- **Cross-reference behavior:** The Academic Cross-Reference Node (5.2) queries the appropriate store based on `content_languages` --- English queries hit the MIT OCW store; Ukrainian queries hit the Ukrainian syllabus store; multilingual runs query both and take the higher overlap score.

- **Heuristic fallback remains:** For courses without accessible syllabi, the existing gate applies: rating > 4.5 + enrollment > 10K passes automatically.

**6. Feature Roadmap**

| Feature                               | Notes                                                                                                                     |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Request Formatter (Pydantic)          | Core gateway, strict JSON schema, default injection.                                                                      |
| from_date with 7-day window           | Validated by Pydantic, clamped on violation, fallback for new users.                                                      |
| Google + YouTube Worker               | Free-form search, normalized to CourseCandidate schema.                                                                   |
| Udemy + Coursera Worker               | Structured API, richer metadata.                                                                                          |
| Reddit Worker                         | Social proof data from community recommendations.                                                                         |
| Layer 1 Dedup (URL + fuzzy title)     | In-memory, fast, no DB.                                                                                                   |
| Layer 2 Dedup (embeddings + pgvector) | Cross-run semantic dedup with explicit metadata diff rules.                                                               |
| URL Liveness Check                    | HTTP HEAD before digest compilation; drop dead links.                                                                     |
| Certificate Type Classification       | none / platform / accredited per course.                                                                                  |
| Synthesizer + Ranking                 | Gemini 2.0 Flash summaries, sorted by free > rating > recency. Max 15 per digest.                                         |
| Telegram Gate + edit_message          | Live status ticker, interrupt_before boundary.                                                                            |
| WebSocket Dashboard                   | Real-time node-level streaming for non-PM stakeholders.                                                                   |
| 4-Path Feedback Router                | REWRITE / AUGMENT / RESET / DISCARD + PUBLISH terminal action.                                                            |
| LLM Error Handling                    | Per-node strategies: rephrase, retry+fallback, confirmation echo.                                                         |
| State Checkpointing                   | Postgres-backed, resume from last node on crash.                                                                          |
| Telegram Retry Queue                  | Exponential backoff, 5 retries, STALLED after 2h, auto-DISCARD at 24h.                                                    |
| Run Serialization                     | One active run per community; others queued.                                                                              |
| Syllabus RAG + Curriculum Score       | Academic rigor verification via PDF ingestion + cross-reference.                                                          |
| Multi-user / Multi-community          | Parameterize user_id and community_id throughout graph state.                                                             |
| Worker D (Universal Web Crawler)      | General web search via Brave Search API; trafilatura extraction; Playwright JS fallback; multilingual query construction. |
| Multilingual Embedding Model          | Replace text-embedding-3-small with multilingual-e5-large; tune cross-lingual dedup threshold to 0.88–0.90.               |
| Translation Layer (DeepL)             | Ukrainian ↔ English translation with Redis caching; used for dedup normalization and digest generation.                   |
| Localized Bot Interactions            | Message catalog (gettext/Fluent) for EN and UK; plural-aware formatting; locale-specific date and currency display.       |
| Multilingual LLM Prompts              | Language-tagged system prompts; few-shot examples in EN and UK for Gateway and Feedback Router.                           |
| Ukrainian Platform Profiles           | Dedicated extraction configs within Worker D whitelist for Prometheus, EdEra, Osvitoria, Diia.Digital Education.          |
| Ukrainian Syllabus Corpus (RAG)       | PDF ingestion pipeline for Ukrainian university syllabi; dedicated curriculum vector store per Section 5.4.               |
| Domain Blocklist Management           | PM-driven `/block` command; hot-reloadable JSON blocklist; DA threshold gate (Phase 2).                                   |

**7. Implementation Order**

Recommended build sequence --- each step is independently testable:

1.  Pydantic filter schema + from_date validation logic (no LangGraph
    yet --- test as a standalone function).

2.  CourseCandidate schema definition + unit tests for normalization.

3.  TypedDict state object + LangGraph graph skeleton (nodes as stubs).

4.  Worker A (Google + YouTube) --- test with hardcoded filter JSON.

5.  Worker B (Udemy + Coursera) --- run Workers A and B in parallel.

6.  Worker C (Reddit) --- add to parallel branch.

7.  Layer 1 Dedup --- URL normalization + rapidfuzz title match.

8.  pgvector setup + Layer 2 embedding dedup + metadata diff rules.

9.  URL liveness check (HTTP HEAD).

10. Synthesizer Node --- ranking logic + digest size cap + Gemini 2.0 Flash
    digest generation + certificate_type display.

11. Telegram bot setup (aiogram) + edit_message live status integration.

12. interrupt_before + webhook resume --- wire graph pause/resume.

13. Feedback Router --- 4-path conditional edge with LLM classifier +
    confirmation echo.

14. PUBLISH broadcast + unpublish command.

15. LLM error handling (Gateway rephrase, Synthesizer fallback, Router
    confirmation).

16. State checkpointing + Telegram retry queue + run serialization.

17. Syllabus RAG --- ingestion node + academic cross-reference + digest
    enhancement.

18. WebSocket dashboard for non-PM stakeholders.

19. Multi-user / multi-community parameterization.

20. End-to-end integration test with a real user prompt.

> _⚠️ Steps 1-9 can be built and tested without any Telegram or
> LangGraph dependency. Integrate the graph last, once the business
> logic is stable._

**Multilingual & Web Extension (build after core pipeline is stable):**

21. Deploy multilingual embedding model (`multilingual-e5-large`) and benchmark against a labeled corpus of 100 EN + 100 UK course pairs.

22. Implement translation layer (DeepL API) + Redis translation cache.

23. Build message catalog (gettext) with EN and UK strings for all bot-facing text.

24. Update Gateway Node system prompts with language detection and few-shot EN/UK examples.

25. Worker D Phase 1 --- plain HTTP search + extraction against curated domain whitelist (~50 sites).

26. Add Ukrainian platform extraction profiles (Prometheus, EdEra, Osvitoria, Diia.Digital Education).

27. Add domain quality gate (spam heuristics, per-domain rate limiting, robots.txt cache) to Worker D.

28. Tune cross-lingual dedup threshold against a labeled UK/EN duplicate dataset.

29. Update Feedback Router with Ukrainian few-shot classification examples.

30. Ukrainian Academic Syllabus Corpus: PDF download + OCR → chunk → embed → vector store (Section 5.4).

31. Worker D Phase 2 --- enable Playwright headless browser and proxy rotation; transition from whitelist to blocklist model.

32. End-to-end multilingual integration test: Ukrainian user query → mixed-language sources → Ukrainian-language digest.

> _⚠️ Steps 21–32 depend only on a working embedding infrastructure (step 8) and can be started in parallel with steps 10–20._

**8. Open Constraints & Decisions**

Items deliberately left flexible --- revisit as implementation
progresses:

- **Dedup threshold:** 0.92 cosine similarity is a starting point.
  Tune after first 100 runs based on false positive/negative rates.

- **from_date clamping vs. rejection:** Currently: clamp + warn. If
  data integrity is critical, switch to hard rejection and prompt user
  to re-specify.

- **Embedding model:** `multilingual-e5-large` is the chosen model for multilingual deployments. See Section 9.4 for details and fallback options.

- **PM feedback classification:** LLM classifier with few-shot examples
  - confirmation echo. If misclassification rate remains high, add
    structured inline button UI in Telegram to eliminate ambiguity
    entirely.

- **Rate limits:** Udemy Affiliate API has strict rate limits. Add
  exponential backoff + dead-letter queue if worker failures become
  frequent.

- **Max iterations:** Default 5. Adjust based on observed PM behavior
  --- if PMs routinely need 6+ loops, raise the cap; if most runs
  resolve in 1-2, lower it.

- **Scraped courses cap:** 200 candidates max in state. Adjust if
  typical runs produce significantly more or fewer results.

- **Cross-lingual dedup threshold:** 0.88–0.90 is the starting point for multilingual runs. Tune after the first 50 multilingual runs based on observed false positive/negative rates. If UK–EN dedup errors remain high, consider separate per-language vector stores with a translation bridge.

- **Worker D scope (Phase 1 whitelist):** ~50 domains is the initial target. Expand based on PM feedback about coverage gaps. Growth is PM-driven.

- **Translation provider:** DeepL as primary for Ukrainian–English quality. Revisit if additional Slavic languages are added --- NLLB-200 (local, zero API cost) may become viable for a broader language set.

- **`user_language` vs. `content_languages` coupling:** Currently specified as separate fields. If users always want UI language = content language, consider collapsing them into a single `language` field to simplify state.

---

**9. Internationalization Architecture**

**9.1 User Language Preference**

- `user_language` (ISO 639-1) is stored in the User table and propagated into graph state on each run.
- Initial value: Telegram client locale detected on first interaction (`message.from.language_code`).
- User override: `/language uk` or `/language en` bot command.
- Affects: all bot status messages, button labels, digest language, and LLM prompt instructions.
- `content_languages` in `search_filters` is a separate concept --- users can interact in Ukrainian but search English-language content, or vice versa.

**9.2 Localized Bot Interactions**

- All bot-facing strings (status messages, button labels, error messages, confirmations) are stored in a message catalog (Python gettext or Fluent format).
- Supported locales at launch: `en` (English), `uk` (Ukrainian).
- Ukrainian-specific formatting rules:
  - **Pluralization:** Ukrainian has 4 plural forms (1 / 2–4 / 5–19 / 20+). Use a plural-aware library (babel or fluent-runtime) rather than manual if/else logic.
  - **Date format:** `DD.MM.YYYY` or `D MMMM YYYY р.` (e.g., "11 березня 2026 р.").
  - **Currency:** Hryvnia shown as "₴0" when `CourseCandidate.currency` is UAH; USD as "\$0".

**9.3 Multilingual LLM Prompts**

- **Gateway Node:** System prompt is language-tagged: "The user query is in [LANGUAGE]. Extract search filters and return as JSON." Few-shot examples maintained for English and Ukrainian inputs covering all filter fields.
- **Synthesizer Node:** Instructed to write summaries in `user_language`. When a course's `detected_language` does not match `user_language`, the summary is in `user_language` but preserves the original title with a translation appended: `"Introduction to Python (Вступ до Python)"`.
- **Feedback Router:** Few-shot classification examples include Ukrainian phrases (e.g., "виглядає добре" → PUBLISH; "додай ще курси" → AUGMENT; "зміни фільтри" → RESET; "відхилити" → DISCARD).

**9.4 Multilingual Embedding Strategy**

Replace `text-embedding-3-small` with a multilingual model for all deployments that include non-English languages:

- **Primary:** `multilingual-e5-large` via sentence-transformers or HuggingFace Inference API. Strong performance on Ukrainian–English cross-lingual retrieval.
- **Fallback:** Cohere `embed-multilingual-v3.0` (API-based, ~\$0.10/1M tokens).
- **Threshold adjustment:** Cosine similarity 0.88–0.90 when `cross_lingual_dedup` is enabled (see Section 8).
- **Validation:** A/B test on a labeled dataset of 100 known English–Ukrainian course pairs before production.

**9.5 Translation Layer**

Used in two contexts: (a) normalizing candidate titles for cross-lingual dedup; (b) generating digests and summaries in the user's preferred language.

- **Primary provider:** DeepL API. Superior Ukrainian ↔ English quality. Free tier: 500K characters/month; paid: ~\$25/month for 1M characters.
- **Fallback:** Google Translate API.
- **Caching:** All translations cached in Redis by `(sha256(source_text), source_lang, target_lang)`. The same course title is never translated twice across runs.
- **Cost estimate at 50 runs/day:** ~500K characters/month (15 courses × ~700 chars/summary). Fits DeepL free tier for low volume; switch to paid plan for production.

**9.6 Ukrainian Platform Coverage**

Before enabling general web search, dedicated extraction profiles are added within Worker D's whitelist for high-value Ukrainian educational platforms. These have predictable page structure and achieve `extraction_confidence ≥ 0.9` without LLM fallback:

| Platform               | Domain             | Content Notes                                  |
| ---------------------- | ------------------ | ---------------------------------------------- |
| Prometheus             | prometheus.org.ua  | MOOCs with certificates, Ukrainian and English |
| EdEra                  | ed-era.com         | Ukrainian-language MOOCs                       |
| Osvitoria              | osvitoria.com      | Mixed Ukrainian educational content            |
| Diia.Digital Education | osvita.diia.gov.ua | Government digital-skills program              |

---

**10. Web Scraping Infrastructure**

**10.1 Startup Strategy**

Worker D launches in two phases to validate demand before scaling infrastructure:

- **Phase 1 --- Whitelist mode:** Plain HTTP requests against ~50 trusted educational domains. No proxies or headless browsers required. Validates extraction quality and PM acceptance with minimal overhead.
- **Phase 2 --- General web:** Enables proxy rotation (BrightData or ScraperAPI) and Playwright. Transitions from whitelist to blocklist model (block known spam/SEO farms; allow all domains with DA ≥ 20).

**10.2 HTTP Client Configuration**

- Realistic browser `User-Agent` (Chrome on Windows). Rotate across a small set if needed.
- `Accept-Language` header set to the primary entry of `content_languages`.
- Timeouts: 5s connect, 30s read.
- Retry: up to 2 retries on connection timeout only. No retry on 4xx/5xx.
- Encoding detection: chardet as fallback for pages missing charset declarations. Handles Windows-1251 prevalent on older Ukrainian educational sites.

**10.3 JavaScript Rendering**

- Playwright (async API) headless browser pool, pre-warmed to 3 instances per Worker D run (configurable).
- Triggered by: response body < 500 chars, `<noscript>` present, or SPA shell indicators in HTML.
- Page load timeout: 15 seconds.
- Browser instances are reused within a single run to reduce startup overhead.

**10.4 Content Extraction Pipeline**

Structured data extraction by priority:

1. **JSON-LD** with `@type: Course` or `CourseInstance` --- `extraction_confidence: 1.0`.
2. **OpenGraph** (`og:title`, `og:description`) --- `extraction_confidence: 0.8`.
3. **Heuristic patterns:** regex for price strings ("Free", "Безкоштовно", "₴0"), duration ("40 hours", "40 годин"), certificate keywords ("certificate", "сертифікат") --- `extraction_confidence: 0.7`.
4. **LLM extraction** via Gemini 2.0 Flash with Pydantic structured output --- `extraction_confidence: 0.5`. Invoked only when all heuristics yield fewer than 3 fields.

Main content is extracted with trafilatura before pattern matching to strip ads, navigation, and boilerplate.

**10.5 Domain Management**

- **Blocklist:** JSON file of domain patterns (exact match and wildcard). Loaded at worker startup, hot-reloadable without restart.
- **Whitelist:** JSON file with optional per-domain overrides (custom extraction profile, rate limit, `js_required: true`).
- **PM command:** `/block domain.com [reason]` appends to the blocklist; takes effect on the next run.
- **DA threshold (Phase 2):** Domains with DA < 20 are skipped unless explicitly whitelisted.
- **robots.txt compliance:** Checked per-domain at startup, cached for the run. Paths under `Disallow` are skipped without error.

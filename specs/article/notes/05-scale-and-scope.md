# Notes: scale and scope (§11–§12)

Evidence for drafting. Load when working on these sections.

## Cross-worker coordination (§11)

All three patterns are architectural descriptions; no code implements them.

- **Circuit breaking across workers (§11.1).** Store as shared state for circuit state; a
  two-tier check (queue boundary and subgraph entry); half-open probes; composition with
  `RetryPolicy`. Also the reason for choosing vendor-neutral tracing (state and prompts must not
  leave for a third party).
- **Outbox leasing at scale (§11.2).** `FOR UPDATE SKIP LOCKED`, lease expiry, claim-time attempt
  counting. The SQL pattern is the one the existing worker uses
  (`course_discovery/effects/worker.py`); describe it architecturally rather than pointing at code.
- **Concurrent resume (§11.3).** An application-level advisory lock before `ainvoke`. The risk is
  double work (LLM calls, synthesis), not corruption, because keys are deterministic (§6.1).

## Scope and verification (§12)

- State once, plainly: this is not a production case study. See `DECISIONS.md`.
- "Production would swap this one class" is an architectural claim, not a tested migration.
- Strongest evidence in the repo: the SIGKILL child-process test. `async` durability is
  deliberately untested for a hard kill.
- Strict-mode msgpack behaviour for a type outside the state schema is untested.
- Not verified: the OpenRouter fallback path live, OpenRouter retention controls, `Command` or
  dynamic `interrupt()` inside a subgraph, `@task` inside a subgraph mechanism.

## Topic-aware cache lookup (§8, §12)

Measured with `scripts/calibrate_topic_floor.py` (run: `PYTHONPATH=. uv run python
scripts/calibrate_topic_floor.py`; deterministic, no network, prints the full topic-by-course
matrix). Embedder: `HashingEmbedder`, 1536 dimensions. Data: the 2 seed courses and the 15
`mock_catalog.py` listings. Stored form is title + description (the snippet), since
`courses.topics` is course-derived and nothing populates it yet; the script also scores a
reference form with the first catalog keyword appended as a tag, to show what a course-side
tagger could add. Labels are the catalog's own `keywords` (topic `python` is relevant to courses
whose keywords contain `python`), not tuned to the result. 13 topics, 15 courses.

- **Facet words broke the first version.** With no facet stopwords the fallback parser's topic
  `Find free Python certificate beginners` scored `Responsive Web Design Certification` 0.249,
  above `CS50's Introduction to Programming with Python` 0.237 and `Python for Everybody` 0.219.
  After ignoring `free`, `beginner`, `intermediate`, `advanced`, `certificate`, `certification`,
  `certified` it scores identically to `python` (seed: 0.492 and 0.380). A first attempt filtered
  stopwords before stemming and `beginners` leaked through; a unit test caught it.
- **Floor table**, stored form (title + snippet), final embedder:

  | floor | tp | fp | fn | precision | recall |
  |---|---|---|---|---|---|
  | 0.05 | 26 | 9 | 6 | 0.74 | 0.81 |
  | 0.10 | 24 | 6 | 8 | 0.80 | 0.75 |
  | **0.12** | 23 | 4 | 9 | 0.85 | 0.72 |
  | 0.15 | 19 | 3 | 13 | 0.86 | 0.59 |
  | 0.20 | 5 | 3 | 27 | 0.62 | 0.16 |

  0.12 is the last floor before recall falls off (0.59 at 0.15); precision is preferred over
  recall because a miss is filled by web search and a wrong-topic hit is shown. The 4 false
  positives are all `Data Analysis with Python` for Python topics (the title says Python; its
  keywords do not). The 9 misses are courses whose text lacks the topic word: `IBM Data Science`
  for the four Python topics, `Python for Everybody` and `100 Days of Code` for `python
  programming` (0.082 and 0.090), `React` for `javascript`, `Data Analysis with Python` for
  `sql`, `AWS Certified Cloud Practitioner` for `cloud computing` (0.101). A lexical embedder
  cannot bridge those. Hash-collision noise reached about -0.09 to +0.09.
- **What course topics would add.** With the first catalog keyword appended as a tag, floor 0.12
  gives tp 26, fp 3, fn 6 (precision 0.90, recall 0.81). That is the ceiling for a course-side
  tagger with ideal tags; not built.
- **Same database, old lookup versus new** (5 valid rows written by 4 real graph runs; the old
  lookup is the `HEAD` checkout, before this change):

  | topic | old | new |
  |---|---|---|
  | python | 5 | 2 |
  | javascript | 5 | 1 |
  | sql | 5 | 0 |
  | cybersecurity | 5 | 1 |
  | cooking | 5 | 0 |
  | machine learning | 5 | 0 |

  The old lookup returned the same 5 courses for every topic. The new one returns only courses
  whose text shares the topic. The rows missing for `python` (IBM Data Science, and the
  JavaScript and cybersecurity courses that the mock search returned for the query) are the
  misses and true negatives above.
- **Agent runs.** Four runs through the compiled graph against Postgres, no LLM key (rules
  path), auto-approved: `Find free Python courses with certificate for beginners` (cache hits
  0, 2 web calls, 5 valid), the same query again (cache hits 2, 2 web calls: two topical hits
  are below the planner's threshold of 3, so it still searches), `Find free JavaScript courses`
  (cache hits 1, 1 web call), `Find cooking courses` (cache hits 0, 3 web calls, 0 valid). All
  published. Every stored row had a 1536-dimension embedding.
- **An earlier version tagged saved courses with the run's topic** and was dropped: it put
  user-derived text into a table erasure cannot reach (the flow-rule check caught it), and it
  let loosely matched courses pollute later lookups. With it, the same runs gave `python` 5
  hits, `javascript` 4, `cybersecurity` 1 and the second Python run 5 cache hits.
- Tested on Postgres 17 with pgvector 0.8.6.
- **Calibration against a real embedder is not done.** `scripts/calibrate_topic_floor.py` now
  takes `EMBEDDER=hashing|openrouter` and prints the embedder it used; the floors it sweeps run
  to 0.50 so a semantic model's higher cosines are covered. Every number above is still
  `HashingEmbedder`; the hashing output was reproduced unchanged after the script change.

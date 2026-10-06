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
- Strongest evidence in the repo: the SIGKILL child-process test. `async` durability has
  one three-trial hard-kill observation (`notes/02`), not a measured rate.
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
  | 0.05 | 30 | 5 | 6 | 0.86 | 0.83 |
  | 0.10 | 28 | 2 | 8 | 0.93 | 0.78 |
  | **0.12** | 27 | 0 | 9 | 1.00 | 0.75 |
  | 0.15 | 22 | 0 | 14 | 1.00 | 0.61 |
  | 0.20 | 8 | 0 | 28 | 1.00 | 0.22 |

  0.12 is the last floor before recall falls off (0.61 at 0.15); precision is preferred over
  recall because a miss is filled by web search and a wrong-topic hit is shown. The 4 false
  positives of the first fit were all `Data Analysis with Python` for Python topics (the title
  says Python; its keywords do not), which is why the labels now also count a keyword that
  appears in the course title; with that, hashing has none. The 9 misses are courses whose text lacks the topic word: `IBM Data Science`
  for the four Python topics, `Python for Everybody` and `100 Days of Code` for `python
  programming` (0.082 and 0.090), `React` for `javascript`, `Data Analysis with Python` for
  `sql`, `AWS Certified Cloud Practitioner` for `cloud computing` (0.101). A lexical embedder
  cannot bridge those. Hash-collision noise reached about -0.09 to +0.09.
- **What course topics would add.** With the first catalog keyword appended as a tag, floor 0.12
  gives tp 29, fp 0, fn 7 (precision 1.00, recall 0.81). That is the ceiling for a course-side
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
- **Calibration against the real embedder** (`EMBEDDER=openrouter`, `openai/text-embedding-3-small`,
  1536 dimensions; same catalog, labels and stored form as above). The hashing floor does not
  transfer: at 0.12 precision is 0.24 (tp 36, fp 116, fn 0), because every course scores 0.06 to
  0.18 against unrelated topics (`cooking`, `quantum physics`), and the fallback parser's topic
  `Find free Python certificate beginners` scored 0.34 to 0.51 against unrelated courses (facet
  words are stopwords in the hashing embedder, not in the real model).
- **What each change bought** (same labels; each row at its own best floor):

  | stage | floor | tp | fp | fn | precision | recall | f1 |
  |---|---|---|---|---|---|---|---|
  | facet words in topic and course text | 0.24 | 32 | 15 | 4 | 0.68 | 0.89 | 0.771 |
  | strip the topic | 0.24 | 31 | 6 | 5 | 0.84 | 0.86 | 0.849 |
  | strip the course text too | 0.25 | 31 | 6 | 5 | 0.84 | 0.86 | 0.849 |
  | relative cutoff 0.70 | 0.25 | 31 | 5 | 5 | 0.86 | 0.86 | 0.861 |

  Stripping the topic did the work. Stripping the stored text changed nothing on this catalog (its
  snippets hold few facet words); it stays because the same words in a real course description
  would pull it toward every query. The relative cutoff adds 0.012 at the best floor; its value
  is that the floor stops being a cliff:

  | floor | 0.18 | 0.20 | 0.22 | 0.24 | 0.25 | 0.26 | 0.27 |
  |---|---|---|---|---|---|---|---|
  | f1, no cutoff | 0.610 | 0.689 | 0.756 | 0.816 | 0.849 | 0.845 | 0.794 |
  | f1, cutoff 0.70 | 0.838 | 0.849 | **0.849** | 0.849 | 0.861 | 0.845 | 0.794 |

  Shipped: floor 0.22 (middle of the 0.19 to 0.24 plateau), cutoff 0.70. At those settings: tp 31,
  fp 6, fn 5, precision 0.84, recall 0.86. The 6 false positives are `Machine Learning` and `SQL
  for Data Analysis` for `data science`, `Responsive Web Design` for `javascript`, and `IBM Data
  Science`, `Machine Learning` and `Google Cybersecurity` for `cloud computing`; the 5 misses are
  `IBM Data Science` for four Python topics and `Data Analysis with Python` for `sql`. Some of
  those false positives are arguable, so precision is a lower bound. The relative cutoff hurts
  hashing (f1 0.857 to 0.642 at its floor), whose scores are not compressed, so it is a property of
  the embedder, as is the floor. 13 topics and 14 courses: approximate, not a benchmark.
- **Against hashing at its floor** (precision 1.00, recall 0.75) the real model trades precision
  for recall: it bridges misses a lexical embedder cannot (`React` for `javascript`, `AWS` for
  `cloud computing`) but still ranks loosely related courses above the floor.
  That does not support a claim of "semantic" retrieval on this catalog.
- **Blend weights.** `confidence` and `use_count` have no labels, so the check draws them at
  random (300 draws, fixed seed) and measures mean average precision of the courses past the
  floor and cutoff, with `w_conf : w_use` held at 2 : 1:

  | w_sim | 1.0 | 0.9 | 0.8 | **0.7** | 0.6 | 0.5 |
  |---|---|---|---|---|---|---|
  | real model, floor 0.22, cutoff 0.70 | 0.920 | 0.922 | 0.924 | **0.924** | 0.922 | 0.920 |
  | hashing, floor 0.12 | 1.000 | 1.000 | 1.000 | **1.000** | 1.000 | 1.000 |

  The range is flat and 0.7 / 0.2 / 0.1 is at or tied for the best on both, so the weights are
  kept.
- **Blank topic.** An empty topic reached the embeddings endpoint and got HTTP 400 ("expected
  string to have >=1 characters"); the hashing embedder returns a zero vector for it, so offline
  runs never showed it. `topic_vector` now returns `None` for a blank topic, which skips the
  topic filter. Found only by running the calibration live.
- **Tests.** The `tests/test_cache_topic.py` cases that assume lexical scores pin
  `HashingEmbedder`, so they pass under `EMBEDDER=openrouter`. The relative cutoff is covered with
  a fixed-vector embedder on both the seed and the Postgres path. Checked live on the seed cache:
  `free certificate beginners cooking` and `cooking` return nothing, and `Find free Python
  certificate beginners` returns the two Python courses.

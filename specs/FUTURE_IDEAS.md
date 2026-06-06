**Course Discovery Agent --- Future Ideas & Deferred Decisions**

Items that are recognized as valuable but deferred from the current
architecture scope. Revisit as the system matures.

---

**1. API Availability Risks + Fallback Strategies**

Several external APIs assumed in the architecture carry access or
rate-limit risks:

- **Coursera API:** Has been severely restricted / effectively
  deprecated for public use. Primary risk: Worker B may be partially
  blocked on day 1.
  - _Fallback:_ Scrape Coursera's public catalog pages with
    rate-limited requests (respect robots.txt, add 2-5s delay between
    requests). Parse course cards for title, URL, price, rating.
  - _Alternative:_ Use SerpAPI or similar to search
    `site:coursera.org` and parse structured data from results.

- **Udemy Affiliate API:** Requires affiliate program approval, which
  is not guaranteed and may take weeks.
  - _Fallback:_ Scrape Udemy's public browse/search pages with rate
    limiting. Udemy renders course metadata in structured JSON-LD on
    product pages.
  - _Alternative:_ Use Udemy's public GraphQL endpoint (undocumented
    but widely used by third-party tools). Fragile --- may break
    without notice.

- **Google Custom Search API:** Free tier = 100 queries/day. At scale
  (multiple communities, multiple topics), this is a hard wall.
  - _Fallback:_ SerpAPI (\$50/mo for 5,000 searches) as a drop-in
    replacement for Google Custom Search.
  - _Alternative:_ Bing Search API (higher free tier: 1,000
    transactions/month) with similar query structure.

- **Reddit API:** Rate-limited to 100 requests/minute for OAuth apps.
  Sufficient for typical usage but may bottleneck during batch runs.
  - _Fallback:_ Use Pushshift API (if available) for historical
    search, or cache Reddit results aggressively (course
    recommendations don't change hourly).

**Recommendation:** Before any production deployment, conduct an API
access audit --- verify that all required API keys are obtainable and
test actual rate limits under expected load.

---

**2. Multi-Language Dedup**

The current dedup pipeline uses text-embedding-3-small, which is
English-optimized. Cross-lingual embedding similarity
(e.g., detecting that "Introducción a Python" and "Introduction to
Python" are the same course) is not well-characterized for this model.

**Options (ordered by implementation effort):**

- **a) Scope dedup to single language per run.** Simplest. If
  `language: "en"`, only embed/compare English-language courses. Courses
  in other languages returned by workers are either filtered out by the
  Gateway or treated as distinct. No cross-lingual dedup needed.

- **b) Translate titles to English before dedup.** Add a lightweight
  translation step (e.g., Google Translate API or a local model like
  NLLB-200) that normalizes all titles to English before embedding.
  Increases latency and cost but enables cross-lingual dedup with the
  existing embedding model.

- **c) Use a multilingual embedding model.** Replace
  text-embedding-3-small with a multilingual model (e.g.,
  multilingual-e5-large, or Cohere's multilingual embed). Better
  semantic quality across languages but higher cost per embedding
  and different similarity threshold requirements.

**Status update:** This is no longer deferred. Multilingual embedding and translation are now part of the active architecture (Sections 9 and 10 of FINAL_VERSION.md). Option (c) with `multilingual-e5-large` is the chosen approach. Option (b) (translate-then-embed) remains available as a fallback if cross-lingual dedup quality proves insufficient in practice.

---

**3. Multilingual LLM Quality Research**

When generating digests and parsing queries in Ukrainian, LLM output quality varies across providers. Gemini 2.0 Flash supports Ukrainian but has not been extensively benchmarked for educational domain tasks in Slavic languages.

**Suggested research (not blocking initial launch):**

- Benchmark Gemini 2.0 Flash vs. Claude 3.5 Sonnet vs. a locally hosted Ukrainian-tuned model on 50 sample course summaries in Ukrainian.
- Evaluate: grammatical correctness, appropriate formal register, preservation of domain terminology, handling of noun declension.
- Ukrainian has grammatical gender, complex declension, and field-specific terminology that English-first models occasionally mistranslate or use in the wrong case.

**Action when needed:** Choose provider based on benchmark results and document the quality bar as a regression test. Not required for initial launch at low volume with PM reviewing all digests before publishing.

---

**4. CAPTCHA Handling Strategy**

Worker D skips domains that return CAPTCHA challenges or persistent 403 responses. This is the correct default: CAPTCHA bypass services add cost, maintenance burden, and potential ToS violations.

**Recommendation:** Do not integrate a CAPTCHA-solving service unless a high-value educational site is consistently blocked and its content cannot be retrieved via other means. If that case arises:

- First: contact the site operator for API access or a data partnership.
- Second: check for an official API, RSS feed, or sitemap that bypasses the scraping requirement entirely.
- Last resort: evaluate bypass service cost vs. content value on a per-site basis.

Log all CAPTCHA-blocked domains per run. If the same domain is blocked in more than 50% of runs over 30 days, escalate to PM for a manual coverage decision.

---

**5. Legal & Compliance Review for Web Scraping**

General web scraping introduces legal considerations that should be reviewed before production deployment:

- **robots.txt:** Mandatory compliance is already enforced by Worker D. This is a technical minimum, not a substitute for legal review.
- **Terms of Service:** Many platforms prohibit automated scraping in their ToS. Review the ToS for every whitelisted domain before adding it.
- **Copyright:** Course descriptions and syllabus content are copyright of the provider. The digest stores excerpts (title, short description, URL) --- generally fair use / informational linking, but a legal opinion under Ukrainian law is advisable.
- **Ukrainian data protection law:** The system stores user search history and course metadata. If operating under the Law of Ukraine "On Personal Data Protection," ensure data retention policies, user consent mechanisms, and right-to-erasure procedures are in place.
- **Third-party API terms:** Verify that Brave Search, DeepL, and any other APIs used permit the intended use case (automated search, commercial use if applicable).

**Action:** Before production launch, conduct a ToS and legal review for the top 20 whitelisted domains and all third-party API agreements.

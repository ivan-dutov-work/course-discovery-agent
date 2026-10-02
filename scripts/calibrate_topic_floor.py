"""Print topic-to-course cosine similarity for the configured embedder over the seed cache and mock catalog.

Run from the repo root: PYTHONPATH=. uv run python scripts/calibrate_topic_floor.py
Select the embedder with EMBEDDER=hashing (default) or EMBEDDER=openrouter (needs OPENROUTER_API_KEY).
"""

from __future__ import annotations

from course_discovery.app.gateway import _fallback_parse_filters
from course_discovery.domain.models import SearchFilters, UserMemory
from course_discovery.research_agent.cache.seed_data import seed_cache
from course_discovery.research_agent.embeddings import course_text, get_embedder
from course_discovery.research_agent.search.mock_catalog import CATALOG

STANDARD_QUERY = "Find free Python courses with certificate for beginners"

TOPICS = [
    _fallback_parse_filters(STANDARD_QUERY).topic,
    "python",
    "python programming",
    "Python beginner",
    "data analysis with python",
    "machine learning",
    "data science",
    "javascript",
    "react",
    "sql",
    "web design",
    "cloud computing",
    "cybersecurity",
    "cooking",
    "quantum physics",
]

embedder = get_embedder()
print(f"embedder: {type(embedder).__name__} {getattr(embedder, 'model', '')}".strip())


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


seed = seed_cache(SearchFilters(topic=""), UserMemory(), limit=99)
documents = {
    "seed": [(c.title, course_text(c.title, c.description, [])) for c in seed],
    "catalog (title+snippet)": [(c.title, course_text(c.title, c.snippet, [])) for c in CATALOG],
    "reference: catalog + first keyword as an ideal course tag": [
        (c.title, course_text(c.title, c.snippet, [c.keywords[0]])) for c in CATALOG
    ],
}

DETAIL_FLOOR = 0.12
FLOORS = [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
RELEVANT_KEYWORD = {
    TOPICS[0]: "python",
    "python": "python",
    "python programming": "python",
    "Python beginner": "python",
    "machine learning": "machine",
    "data science": "science",
    "javascript": "javascript",
    "react": "react",
    "sql": "sql",
    "cloud computing": "cloud",
    "cybersecurity": "cybersecurity",
    "cooking": None,
    "quantum physics": None,
}
KEYWORDS = {c.title: c.keywords for c in CATALOG}


def evaluate(name: str, docs: list[tuple[str, str]]) -> None:
    vectors = embedder.embed([text for _, text in docs])
    print(f"\n--- floor evaluation: {name}")
    print("floor   tp  fp  fn  precision  recall")
    for floor in FLOORS:
        tp = fp = fn = 0
        for topic, keyword in RELEVANT_KEYWORD.items():
            query = embedder.embed([topic])[0]
            for (title, _), vector in zip(docs, vectors):
                relevant = keyword is not None and keyword in KEYWORDS.get(title, [])
                returned = cosine(query, vector) >= floor
                tp += relevant and returned
                fp += (not relevant) and returned
                fn += relevant and not returned
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        print(f"{floor:.2f}  {tp:3d} {fp:3d} {fn:3d}  {precision:9.2f}  {recall:6.2f}")
    print(f"errors at floor {DETAIL_FLOOR}:")
    for topic, keyword in RELEVANT_KEYWORD.items():
        query = embedder.embed([topic])[0]
        for (title, _), vector in zip(docs, vectors):
            relevant = keyword is not None and keyword in KEYWORDS.get(title, [])
            score = cosine(query, vector)
            if relevant != (score >= DETAIL_FLOOR):
                kind = "miss" if relevant else "false positive"
                print(f"  {kind:14s} topic={topic!r} {score:.3f} {title}")


for name, docs in documents.items():
    if name != "seed":
        evaluate(name, docs)

for name, docs in documents.items():
    vectors = embedder.embed([text for _, text in docs])
    print(f"\n=== {name}: {len(docs)} courses")
    for topic in TOPICS:
        query = embedder.embed([topic])[0]
        scored = sorted(((cosine(query, v), title) for (title, _), v in zip(docs, vectors)), reverse=True)
        print(f"\ntopic: {topic!r}")
        for score, title in scored:
            print(f"  {score:.3f}  {title}")

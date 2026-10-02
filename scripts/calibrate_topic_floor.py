"""Print topic-to-course cosine similarity for the configured embedder over the seed cache and mock catalog.

Run from the repo root: PYTHONPATH=. uv run python scripts/calibrate_topic_floor.py
Select the embedder with EMBEDDER=hashing (default) or EMBEDDER=openrouter (needs OPENROUTER_API_KEY).
"""

from __future__ import annotations

import random
import re
from functools import lru_cache

from course_discovery.app.gateway import _fallback_parse_filters
from course_discovery.domain.models import SearchFilters, UserMemory
from course_discovery.research_agent.cache.seed_data import seed_cache
from course_discovery.research_agent.embeddings import course_text, get_embedder
from course_discovery.research_agent.embeddings.facets import strip_facets
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


@lru_cache(maxsize=None)
def query_vector(topic: str) -> list[float]:
    return embedder.embed([strip_facets(topic)])[0]


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

DETAIL_FLOOR = getattr(embedder, "topic_floor", 0.12)
DETAIL_RELATIVE = getattr(embedder, "relative_cutoff", 0.0)
FLOORS = sorted({0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30, 0.35, 0.40, 0.50} | {i / 100 for i in range(21, 30)})
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
RELATIVE_CUTOFFS = [0.0, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]


def is_relevant(keyword: str | None, title: str) -> bool:
    return keyword is not None and (
        keyword in KEYWORDS.get(title, []) or keyword in re.findall(r"[a-z0-9]+", title.lower())
    )


def retrieved(scores: list[float], floor: float, relative: float) -> list[bool]:
    passing = [score for score in scores if score >= floor]
    best = max(passing, default=0.0)
    return [score >= floor and score >= relative * best for score in scores]


def counts(docs: list[tuple[str, str]], vectors: list[list[float]], floor: float, relative: float):
    tp = fp = fn = 0
    for topic, keyword in RELEVANT_KEYWORD.items():
        scores = [cosine(query_vector(topic), vector) for vector in vectors]
        for (title, _), returned in zip(docs, retrieved(scores, floor, relative)):
            relevant = is_relevant(keyword, title)
            tp += relevant and returned
            fp += (not relevant) and returned
            fn += relevant and not returned
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return tp, fp, fn, precision, recall, f1


def evaluate(name: str, docs: list[tuple[str, str]]) -> None:
    vectors = embedder.embed([text for _, text in docs])
    print(f"\n--- floor evaluation: {name}")
    print("floor   tp  fp  fn  precision  recall      f1")
    for floor in FLOORS:
        tp, fp, fn, precision, recall, f1 = counts(docs, vectors, floor, 0.0)
        print(f"{floor:.2f}  {tp:3d} {fp:3d} {fn:3d}  {precision:9.2f}  {recall:6.2f}  {f1:6.3f}")
    print(f"\n--- relative cutoff x floor: {name} (f1 / precision / recall)")
    print("floor  " + "  ".join(f"rel={r:.2f}        " for r in RELATIVE_CUTOFFS))
    for floor in FLOORS:
        cells = []
        for relative in RELATIVE_CUTOFFS:
            _, _, _, precision, recall, f1 = counts(docs, vectors, floor, relative)
            cells.append(f"{f1:.3f}/{precision:.2f}/{recall:.2f}")
        print(f"{floor:.2f}   " + "  ".join(cells))
    print(f"errors at floor {DETAIL_FLOOR}, relative cutoff {DETAIL_RELATIVE}:")
    for topic, keyword in RELEVANT_KEYWORD.items():
        scores = [cosine(query_vector(topic), vector) for vector in vectors]
        for (title, _), score, returned in zip(docs, scores, retrieved(scores, DETAIL_FLOOR, DETAIL_RELATIVE)):
            if is_relevant(keyword, title) != returned:
                kind = "miss" if is_relevant(keyword, title) else "false positive"
                print(f"  {kind:14s} topic={topic!r} {score:.3f} {title}")


def average_precision(flags: list[bool]) -> float | None:
    hits = total = 0.0
    for rank, flag in enumerate(flags, 1):
        if flag:
            hits += 1
            total += hits / rank
    return total / hits if hits else None


def weight_sensitivity(docs: list[tuple[str, str]], floor: float, relative: float, draws: int = 300) -> None:
    vectors = embedder.embed([text for _, text in docs])
    queries = {topic: query_vector(topic) for topic in RELEVANT_KEYWORD}
    print(f"\n--- blend weights, floor {floor}, relative cutoff {relative}: mean average precision with random confidence and use_count")
    for w_sim in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
        weights = (w_sim, (1 - w_sim) * 2 / 3, (1 - w_sim) / 3)
        rng = random.Random(7)
        scores = []
        for _ in range(draws):
            confidence = {title: rng.uniform(0.6, 1.0) for title, _ in docs}
            uses = {title: rng.randint(0, 10) for title, _ in docs}
            for topic, keyword in RELEVANT_KEYWORD.items():
                if keyword is None:
                    continue
                rows = []
                similarities = [cosine(queries[topic], vector) for vector in vectors]
                for (title, _), similarity, kept in zip(docs, similarities, retrieved(similarities, floor, relative)):
                    if kept:
                        score = weights[0] * similarity + weights[1] * confidence[title] + weights[2] * uses[title] / 10
                        rows.append((score, is_relevant(keyword, title)))
                value = average_precision([flag for _, flag in sorted(rows, reverse=True)])
                if value is not None:
                    scores.append(value)
        print(f"w_sim={w_sim:.1f} w_conf={weights[1]:.2f} w_use={weights[2]:.2f}  mean_ap={sum(scores) / len(scores):.3f}")


for name, docs in documents.items():
    if name != "seed":
        evaluate(name, docs)

weight_sensitivity(documents["catalog (title+snippet)"], DETAIL_FLOOR, DETAIL_RELATIVE)

for name, docs in documents.items():
    vectors = embedder.embed([text for _, text in docs])
    print(f"\n=== {name}: {len(docs)} courses")
    for topic in TOPICS:
        query = query_vector(topic)
        scored = sorted(((cosine(query, v), title) for (title, _), v in zip(docs, vectors)), reverse=True)
        print(f"\ntopic: {topic!r}")
        for score, title in scored:
            print(f"  {score:.3f}  {title}")

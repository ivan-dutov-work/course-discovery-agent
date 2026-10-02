"""Compare cosine, JEV and their rank fusion on hand-labelled queries over the mock catalog.

Needs OPENROUTER_API_KEY. Run from the repo root:
    PYTHONPATH=. uv run python scripts/eval_jev_rerank.py [--runs N]
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys

import httpx

from course_discovery.app.gateway import _fallback_parse_filters
from course_discovery.research_agent.embeddings import HashingEmbedder, course_text
from course_discovery.research_agent.search.mock_catalog import CATALOG, MockListing

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
RRF_K = 60
RELEVANT_GRADE = 2

RUBRIC = [
    "Does not fit: wrong topic or violates a stated requirement",
    "Partial fit: right topic but misses a stated requirement",
    "Good fit: right topic and meets the stated requirements",
    "Exact fit: right topic, meets every stated requirement",
]

# Grade 3 meets every stated constraint, 2 misses one, 1 misses two; unlisted courses are 0.
LABELS: dict[str, dict[str, int]] = {
    "free beginner Python course with a certificate": {
        "Python for Everybody Specialization": 3,
        "Data Analysis with Python": 2,
        "IBM Data Science Professional Certificate": 2,
        "CS50's Introduction to Programming with Python": 2,
        "100 Days of Code: The Complete Python Pro Bootcamp": 1,
        "Real Python Advanced Python Tutorials": 1,
    },
    "intermediate JavaScript course": {
        "The Complete JavaScript Course 2024": 3,
        "React - The Complete Guide": 2,
        "JavaScript: The Advanced Concepts": 1,
        "freeCodeCamp JavaScript Algorithms and Data Structures": 1,
    },
    "free JavaScript course with certificate": {
        "freeCodeCamp JavaScript Algorithms and Data Structures": 3,
        "The Complete JavaScript Course 2024": 1,
        "JavaScript: The Advanced Concepts": 1,
        "React - The Complete Guide": 1,
    },
    "advanced Python": {
        "Real Python Advanced Python Tutorials": 3,
        "100 Days of Code: The Complete Python Pro Bootcamp": 1,
        "Data Analysis with Python": 1,
    },
    "learn SQL for free": {"SQL for Data Analysis": 3},
    "beginner data science course": {
        "IBM Data Science Professional Certificate": 3,
        "Data Analysis with Python": 1,
        "SQL for Data Analysis": 1,
    },
    "machine learning": {
        "Machine Learning Specialization (Andrew Ng)": 3,
        "IBM Data Science Professional Certificate": 1,
    },
    "cloud computing certification": {"AWS Certified Cloud Practitioner Essentials": 3},
    "cybersecurity for beginners": {"Google Cybersecurity Professional Certificate": 3},
    "free web design course with a certificate": {"Responsive Web Design Certification": 3},
    "React course": {
        "React - The Complete Guide": 3,
        "The Complete JavaScript Course 2024": 1,
        "JavaScript: The Advanced Concepts": 1,
    },
    "beginner Python without paying for a certificate": {
        "CS50's Introduction to Programming with Python": 3,
        "Python for Everybody Specialization": 2,
        "Real Python Advanced Python Tutorials": 1,
    },
    "cheap Python bootcamp with a certificate": {
        "100 Days of Code: The Complete Python Pro Bootcamp": 3,
        "Python for Everybody Specialization": 1,
        "IBM Data Science Professional Certificate": 1,
    },
}
NEGATIVE_QUERIES = ["quantum physics", "how to bake sourdough bread"]

VARIANTS = {
    "title": lambda c: c.title,
    "title+snippet": lambda c: f"{c.title} | {c.snippet}",
    "title+snippet+tags": lambda c: f"{c.title} | {c.snippet} | tags: {', '.join(c.keywords)}",
}


def jev_scores(query: str, texts: list[str], api_key: str) -> tuple[list[float], dict]:
    listing = "\n".join(f"[{i}] {text}" for i, text in enumerate(texts))
    questions = {
        f"c{i}": {
            "type": "score",
            "instructions": f"How well does course [{i}] fit the user query?",
            "criteria": RUBRIC,
        }
        for i in range(len(texts))
    }
    response = httpx.post(
        DECISIONS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": MODEL,
            "state": f"User query: {query}\n\nCandidate courses:\n{listing}",
            "questions": questions,
        },
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    return [body["answers"][f"c{i}"]["score"] for i in range(len(texts))], body["usage"]


def cosine_scores(query: str, courses: list[MockListing], embedder: HashingEmbedder) -> list[float]:
    topic = _fallback_parse_filters(query).topic or query
    query_vector = embedder.embed([topic])[0]
    return [
        sum(a * b for a, b in zip(query_vector, vector))
        for vector in embedder.embed([course_text(c.title, c.snippet, []) for c in courses])
    ]


def ranks(scores: list[float]) -> list[int]:
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    result = [0] * len(scores)
    for position, index in enumerate(order):
        result[index] = position + 1
    return result


def fuse(*score_lists: list[float]) -> list[float]:
    all_ranks = [ranks(scores) for scores in score_lists]
    return [sum(1 / (RRF_K + r[i]) for r in all_ranks) for i in range(len(score_lists[0]))]


def ndcg_at(k: int, order: list[int], grades: list[int]) -> float:
    def dcg(items: list[int]) -> float:
        return sum(grades[i] / math.log2(pos + 2) for pos, i in enumerate(items[:k]))

    ideal = dcg(sorted(range(len(grades)), key=lambda i: -grades[i]))
    return dcg(order) / ideal if ideal else 0.0


def reciprocal_rank(order: list[int], grades: list[int]) -> float:
    for position, index in enumerate(order):
        if grades[index] >= RELEVANT_GRADE:
            return 1 / (position + 1)
    return 0.0


def recall_at(k: int, order: list[int], grades: list[int]) -> float:
    relevant = {i for i, g in enumerate(grades) if g >= RELEVANT_GRADE}
    return len(relevant & set(order[:k])) / len(relevant) if relevant else 1.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is required")

    embedder = HashingEmbedder()
    titles = [c.title for c in CATALOG]
    assert all(t in titles for labels in LABELS.values() for t in labels), "label names a missing course"

    totals: dict[str, dict[str, list[float]]] = {}
    top_scores: dict[str, dict[str, list[float]]] = {}
    tokens = 0
    cost = 0.0
    calls = 0

    def record(method: str, metrics: dict[str, float]) -> None:
        bucket = totals.setdefault(method, {"ndcg@5": [], "mrr": [], "recall@3": []})
        for name, value in metrics.items():
            bucket[name].append(value)

    for query, labels in LABELS.items():
        grades = [labels.get(t, 0) for t in titles]
        cosine = cosine_scores(query, CATALOG, embedder)
        cosine_order = sorted(range(len(titles)), key=lambda i: -cosine[i])
        record("cosine (production default)", {
            "ndcg@5": ndcg_at(5, cosine_order, grades),
            "mrr": reciprocal_rank(cosine_order, grades),
            "recall@3": recall_at(3, cosine_order, grades),
        })
        for variant, render in VARIANTS.items():
            texts = [render(c) for c in CATALOG]
            for _ in range(args.runs):
                jev, usage = jev_scores(query, texts, api_key)
                tokens += usage["input_tokens"]
                cost += usage["cost"]
                calls += 1
                for method, scores in ((f"jev [{variant}]", jev), (f"cosine+jev fused [{variant}]", fuse(cosine, jev))):
                    order = sorted(range(len(titles)), key=lambda i: -scores[i])
                    record(method, {
                        "ndcg@5": ndcg_at(5, order, grades),
                        "mrr": reciprocal_rank(order, grades),
                        "recall@3": recall_at(3, order, grades),
                    })
                top_scores.setdefault(variant, {}).setdefault("relevant", []).append(max(jev))

    for query in NEGATIVE_QUERIES:
        for variant, render in VARIANTS.items():
            jev, usage = jev_scores(query, [render(c) for c in CATALOG], api_key)
            tokens += usage["input_tokens"]
            cost += usage["cost"]
            calls += 1
            top_scores.setdefault(variant, {}).setdefault("negative", []).append(max(jev))

    print(f"{len(LABELS)} labelled queries, {len(titles)} courses, {calls} JEV calls, {tokens} input tokens, ${cost:.5f}\n")
    print(f"{'method':46s} {'ndcg@5':>7s} {'mrr':>7s} {'recall@3':>9s}")
    for method, bucket in totals.items():
        print(f"{method:46s} {statistics.mean(bucket['ndcg@5']):7.3f} {statistics.mean(bucket['mrr']):7.3f} {statistics.mean(bucket['recall@3']):9.3f}")

    print("\nJEV top-1 score: on-topic queries vs queries with no relevant course (higher separation = usable abstention)")
    for variant, buckets in top_scores.items():
        relevant, negative = buckets["relevant"], buckets["negative"]
        print(f"  {variant:22s} on-topic min {min(relevant):.2f} mean {statistics.mean(relevant):.2f} | negative max {max(negative):.2f}")


if __name__ == "__main__":
    main()

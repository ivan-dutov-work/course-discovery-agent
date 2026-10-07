from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

HERE = Path(__file__).parent
AGES = [0.1, 0.5, 2, 6, 20, 30, 72, 150]

THREADS = {
    "python": (
        {"topic": "python", "level": "beginner", "max_price": 20},
        ["cheaper please", "only ones with exercises", "something shorter, under 10 hours",
         "I meant for data analysis in python", "can you show me the second one in more detail?"],
        ["I need a course on beekeeping", "best guitar lessons for adults", "thanks, that works",
         "Ich suche einen Kurs über Gartenbau", "what is a good course on negotiation?"],
    ),
    "ux": (
        {"topic": "ux design", "min_rating": 4.5},
        ["not so theoretical", "with a certificate", "only on Coursera", "fewer results, just the top three",
         "дешевше, будь ласка"],
        ["learn rust for embedded systems", "courses about personal finance", "something for my 8 year old",
         "thank you!", "how do I get started with sourdough?"],
    ),
    "ml": (
        {"topic": "machine learning", "level": "advanced"},
        ["easier, I overestimated myself", "ones that use pytorch", "newer than 2023 only",
         "drop the video-only ones", "same but in Spanish"],
        ["yoga for back pain", "intro to public speaking", "what about spanish for travel?",
         "ok", "tax preparation for freelancers"],
    ),
    "spanish": (
        {"topic": "spanish for travel", "max_price": 0},
        ["I can pay up to 30 dollars", "more speaking practice", "beginner only", "also show Portuguese",
         "any of them on YouTube?"],
        ["python for finance", "how to start running", "great, thanks a lot", "learn blender for animation",
         "cursos de cocina italiana"],
    ),
    "excel": (
        {"topic": "excel data analysis", "include_certificate": True},
        ["with power query", "shorter ones", "no certificate needed actually", "only recent, 2025 or later",
         "and what about the same for google sheets?"],
        ["how do I learn drawing portraits", "kubernetes for beginners", "that is all, thanks",
         "something on mindfulness", "marketing analytics with tableau"],
    ),
    "pm": (
        {"topic": "project management", "max_price": 0},
        ["I am fine with paid ones", "agile specifically", "ones that prepare for the PMP", "not Udemy",
         "make it intermediate"],
        ["learn to play chess", "react native tutorials", "thanks, will take a look",
         "electric vehicle maintenance", "what about creative writing?"],
    ),
    "rust": (
        {"topic": "rust", "level": "intermediate"},
        ["more about async", "with projects", "up to 15 hours", "skip the ones from 2021", "and for beginners too"],
        ["gardening in small spaces", "intro to philosophy", "cool", "ielts preparation",
         "how to learn go instead?"],
    ),
    "photo": (
        {"topic": "photography", "providers": ["udemy"]},
        ["landscape focus", "include other platforms too", "cheaper", "which of these covers lighting?",
         "only beginner friendly"],
        ["quantum computing basics", "stand up comedy", "perfect, thanks", "learn accounting",
         "aws certification prep"],
    ),
}


def build() -> list[dict]:
    rows = []
    for key, (parked, continues, news) in THREADS.items():
        for kind, messages in (("continue", continues), ("new_topic", news)):
            for n, message in enumerate(messages, 1):
                topic, filters = parked["topic"], {k: v for k, v in parked.items() if k != "topic"}
                rows.append(
                    {
                        "id": f"{key}-{kind}-{n}",
                        "parked": {
                            "topic": topic,
                            "filters": filters,
                            "age_hours": AGES[len(rows) % len(AGES)],
                        },
                        "message": message,
                        "intended": kind,
                    }
                )
    return rows


def assign_splits(rows: list[dict]) -> None:
    for kind in ("continue", "new_topic"):
        group = sorted(
            (r for r in rows if r["intended"] == kind),
            key=lambda r: hashlib.sha1(r["id"].encode()).hexdigest(),
        )
        for i, row in enumerate(group):
            share = i / len(group)
            row["split"] = "train" if share < 0.5 else "dev" if share < 0.75 else "test"


def main() -> None:
    rows = build()
    assign_splits(rows)
    rows.sort(key=lambda r: hashlib.sha1(("order" + r["id"]).encode()).hexdigest())
    for number, row in enumerate(rows, 1):
        row["id"] = f"pair-{number:02d}"
    owner = [
        {"id": r["id"], "split": r["split"], "parked": r["parked"], "message": r["message"], "label": None}
        for r in rows
    ]
    intended = [{"id": r["id"], "label": r["intended"]} for r in rows]
    header = (
        "# Label each pair: set `label` to `continue` or `new_topic`.\n"
        "#   continue   the message refines, adjusts or asks about the parked search\n"
        "#   new_topic  it asks about something else, or is not about the search at all\n"
        "# Age is not a rule: judge the message, with the age as context.\n"
    )
    (HERE / "thread_pairs.yaml").write_text(header + yaml.safe_dump(owner, sort_keys=False, allow_unicode=True, width=100))
    (HERE / "thread_pairs.intended.yaml").write_text(yaml.safe_dump(intended, sort_keys=False))
    counts = {}
    for r in rows:
        counts[(r["split"], r["intended"])] = counts.get((r["split"], r["intended"]), 0) + 1
    print(len(rows), "pairs", dict(sorted(counts.items())))


if __name__ == "__main__":
    main()

from __future__ import annotations

from dataclasses import dataclass

from course_discovery.conversation import Decision, ParkedThread

HOUR = 3600.0
DAY = 24 * HOUR

PYTHON = ParkedThread("python", {"level": "beginner", "max_price": 20.0}, 0.2 * HOUR)
PYTHON_YESTERDAY = ParkedThread("python", {"level": "beginner", "max_price": 20.0}, 30 * HOUR)
DESIGN = ParkedThread("ux design", {"min_rating": 4.5}, 3 * HOUR)


@dataclass(frozen=True)
class Row:
    name: str
    message: str
    parked: ParkedThread | None
    stub_score: float
    expected: Decision
    degraded: str | None = None
    signal_score: float = 0.02


ROWS = [
    Row("refinement cheaper", "cheaper please", PYTHON, 0.96, Decision.CONTINUE),
    Row("refinement level", "only intermediate level", PYTHON, 0.93, Decision.CONTINUE),
    Row("refinement about results", "what does the second one cover?", PYTHON, 0.88, Decision.CONTINUE),
    Row("unrelated query", "I need a course on beekeeping", PYTHON, 0.03, Decision.NEW_TOPIC),
    Row("unrelated query, design parked", "learn rust for embedded systems", DESIGN, 0.05, Decision.NEW_TOPIC),
    Row("same topic after a gap", "also show ones with certificates", PYTHON_YESTERDAY, 0.81, Decision.CONTINUE),
    Row("unrelated after a gap", "what about spanish for travel?", PYTHON_YESTERDAY, 0.02, Decision.NEW_TOPIC),
    Row("only thanks", "thanks!", PYTHON, 0.52, Decision.NO_SIGNAL, signal_score=0.96),
    Row("ok", "ok", PYTHON, 0.55, Decision.NO_SIGNAL, signal_score=0.91),
    Row("closing in another language", "дякую, цього досить", DESIGN, 0.4, Decision.NO_SIGNAL, signal_score=0.88),
    Row("short filter is not an acknowledgement", "cheaper", PYTHON, 0.96, Decision.CONTINUE, signal_score=0.12),
    Row("other language, refinement", "дешевше, будь ласка", PYTHON, 0.90, Decision.CONTINUE),
    Row("other language, unrelated", "Ich suche einen Kurs über Gartenbau", PYTHON, 0.04, Decision.NEW_TOPIC),
    Row("ambiguous short message", "and for kids?", DESIGN, 0.47, Decision.NEW_TOPIC, "middle_score"),
    Row("nothing parked", "python for data analysis", None, 0.0, Decision.NEW_TOPIC),
    Row("blank message", "   ", PYTHON, 0.99, Decision.NEW_TOPIC),
]

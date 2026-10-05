from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CASES_DIR = Path(__file__).parent / "cases"
SOURCES = {"seed", "review", "prod"}
STATUSES = {"capability", "regression"}


@dataclass(frozen=True)
class Case:
    id: str
    level: str
    source: str
    origin: str
    input: dict[str, Any]
    expect: dict[str, Any]
    graders: list[str]
    status: str = "capability"
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)


def _build(raw: dict, level: str, path: Path) -> Case:
    case = Case(
        id=raw["id"],
        level=raw.get("level", level),
        source=raw["source"],
        origin=raw["origin"],
        input=raw.get("input", {}),
        expect=raw.get("expect", {}),
        graders=list(raw["graders"]),
        status=raw.get("status", "capability"),
        tags=list(raw.get("tags", [])),
        requires=list(raw.get("requires", [])),
    )
    if case.source not in SOURCES:
        raise ValueError(f"{path}: {case.id}: source must be one of {sorted(SOURCES)}")
    if case.status not in STATUSES:
        raise ValueError(f"{path}: {case.id}: status must be one of {sorted(STATUSES)}")
    return case


def load_cases(level: str, root: Path = CASES_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted((root / level).glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text()) or []:
            cases.append(_build(raw, level, path))
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case ids in {level}")
    return cases

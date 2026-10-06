from __future__ import annotations

import json
from pathlib import Path

from course_discovery.domain.contract import STATE_SCHEMA_VERSION
from course_discovery.workflows.outer_graph import build_graph
from course_discovery.workflows.research_graph import build_research_graph

SNAPSHOT = Path(__file__).parent / "fixtures" / "state_contract.json"


def _shape(graph) -> dict:
    return {
        "channels": sorted(c for c in graph.channels if ":" not in c and not c.startswith("__")),
        "nodes": sorted(n for n in graph.nodes if not n.startswith("__")),
    }


def current() -> dict:
    return {
        "version": STATE_SCHEMA_VERSION,
        "outer": _shape(build_graph()),
        "research": _shape(build_research_graph()),
    }


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(current(), indent=2, sort_keys=True) + "\n")

from __future__ import annotations

import operator
import random
import sys
import threading
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

SEED = 7
PAYLOAD_CHARS = 400
SIZES = (10, 100, 1000)
FAIL_INDEX = 3


def payload(unit: int) -> str:
    rng = random.Random(SEED * 100003 + unit)
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(PAYLOAD_CHARS))


class Counter:
    def __init__(self, siblings: int = 0) -> None:
        self.executions: dict[int, int] = {}
        self.fail_once = FAIL_INDEX
        self.siblings = siblings
        self.finished = 0
        self.lock = threading.Lock()

    def run_unit(self, unit: int) -> str:
        with self.lock:
            self.executions[unit] = self.executions.get(unit, 0) + 1
            first = self.executions[unit] == 1
        if unit == self.fail_once and first:
            deadline = time.monotonic() + 30
            while self.finished < self.siblings and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)
            raise RuntimeError("injected failure")
        result = payload(unit)
        with self.lock:
            self.finished += 1
        return result


class BatchState(TypedDict, total=False):
    units: list[int]
    results: Annotated[list[str], operator.add]


class UnitState(TypedDict, total=False):
    unit: int
    result: str


def build_batch(counter: Counter, saver):
    def fan_out(state: BatchState):
        return [Send("work", {"unit": unit}) for unit in state["units"]]

    def work(state: dict) -> dict:
        return {"results": [counter.run_unit(state["unit"])]}

    builder = StateGraph(BatchState)
    builder.add_node("start", lambda state: {})
    builder.add_node("work", work)
    builder.add_edge(START, "start")
    builder.add_conditional_edges("start", fan_out, ["work"])
    builder.add_edge("work", END)
    return builder.compile(checkpointer=saver)


def build_unit(counter: Counter, saver):
    builder = StateGraph(UnitState)
    builder.add_node("work", lambda state: {"result": counter.run_unit(state["unit"])})
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    return builder.compile(checkpointer=saver)


def stored_bytes(saver: MemorySaver, thread_id: str) -> tuple[int, int, int]:
    sizes = []
    for item in saver.list({"configurable": {"thread_id": thread_id}}):
        size = len(saver.serde.dumps_typed(item.checkpoint["channel_values"])[1])
        size += sum(len(saver.serde.dumps_typed(value)[1]) for _, _, value in item.pending_writes or [])
        sizes.append(size)
    return (sizes[0] if sizes else 0), sum(sizes), len(sizes)


def run_batch(n: int, durability: str) -> dict:
    counter, saver = Counter(siblings=n - 1), MemorySaver()
    graph = build_batch(counter, saver)
    config = {"configurable": {"thread_id": "batch"}}
    try:
        graph.invoke({"units": list(range(n))}, config, durability=durability)
    except RuntimeError:
        pass
    after_failure = stored_bytes(saver, "batch")
    graph.invoke(None, config, durability=durability)
    reruns = sum(count - 1 for unit, count in counter.executions.items() if unit != FAIL_INDEX and count > 1)
    latest, total, checkpoints = stored_bytes(saver, "batch")
    return {
        "mode": f"one Send ({durability})",
        "n": n,
        "largest_checkpoint": max(after_failure[0], latest),
        "total_bytes": total,
        "checkpoints": checkpoints,
        "siblings_rerun": reruns,
    }


def run_units(n: int) -> dict:
    counter, saver = Counter(), MemorySaver()
    graph = build_unit(counter, saver)
    failed: list[int] = []
    for unit in range(n):
        try:
            graph.invoke({"unit": unit}, {"configurable": {"thread_id": f"unit-{unit}"}}, durability="sync")
        except RuntimeError:
            failed.append(unit)
    for unit in failed:
        graph.invoke(None, {"configurable": {"thread_id": f"unit-{unit}"}}, durability="sync")
    reruns = sum(count - 1 for unit, count in counter.executions.items() if unit != FAIL_INDEX and count > 1)
    sizes = [stored_bytes(saver, f"unit-{unit}") for unit in range(n)]
    return {
        "mode": "N runs",
        "n": n,
        "largest_checkpoint": max(s[0] for s in sizes),
        "total_bytes": sum(s[1] for s in sizes),
        "checkpoints": sum(s[2] for s in sizes),
        "siblings_rerun": reruns,
    }


def main() -> int:
    rows = []
    for n in SIZES:
        rows.append(run_batch(n, "sync"))
        rows.append(run_units(n))
    header = ("mode", "n", "largest_checkpoint", "total_bytes", "checkpoints", "siblings_rerun")
    print("stub workload: fixed-size text per unit, one injected failure at unit", FAIL_INDEX)
    print("sizes count channel values and task writes only; the failing unit waits until every other unit has finished")
    print(" | ".join(f"{h:>20}" for h in header))
    for row in rows:
        print(" | ".join(f"{row[h]:>20}" for h in header))
    return 0


if __name__ == "__main__":
    sys.exit(main())

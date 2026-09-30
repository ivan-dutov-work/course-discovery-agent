from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from course_discovery.domain.pii import Pii

SUBJECT = "subject"
RAW = "raw"


@dataclass(frozen=True)
class Sink:
    name: str
    kind: str
    accepts: frozenset[str] = frozenset()


def store(name: str, accepts: frozenset[str] = frozenset({SUBJECT})) -> Sink:
    return Sink(name, "store", accepts)


def external(name: str, accepts: frozenset[str] = frozenset()) -> Sink:
    return Sink(name, "external", accepts)


@dataclass(frozen=True)
class NodeFlow:
    reads: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()
    sinks: tuple[Sink, ...] = ()
    redacts: frozenset[str] = frozenset()
    declassifies: dict[str, str] = field(default_factory=dict)


def flow(
    reads: set[str] | None = None,
    writes: set[str] | None = None,
    sinks: tuple[Sink, ...] = (),
    redacts: set[str] | None = None,
    declassifies: dict[str, str] | None = None,
) -> NodeFlow:
    return NodeFlow(
        frozenset(reads or ()),
        frozenset(writes or ()),
        sinks,
        frozenset(redacts or ()),
        declassifies or {},
    )


@dataclass(frozen=True)
class Violation:
    rule: str
    node: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.node}: {self.detail}"


def pii_seeds(schema: type) -> dict[str, frozenset[str]]:
    seeds: dict[str, frozenset[str]] = {}
    for name, hint in get_type_hints(schema, include_extras=True).items():
        for meta in _annotations(hint):
            if isinstance(meta, Pii):
                seeds[name] = frozenset({SUBJECT} if meta.redacted else {SUBJECT, RAW})
    return seeds


def _annotations(hint: Any) -> tuple[Any, ...]:
    if get_origin(hint) is Annotated:
        return get_args(hint)[1:]
    return ()


Labels = dict[str, frozenset[str]]
Origins = dict[tuple[str, str], tuple[str, str] | None]


def _seen(spec: NodeFlow, labels: Labels, channel: str) -> frozenset[str]:
    channel_labels = labels.get(channel, frozenset())
    return channel_labels - {RAW} if channel in spec.redacts else channel_labels


def _inputs(spec: NodeFlow, labels: Labels) -> frozenset[str]:
    return frozenset().union(*(_seen(spec, labels, channel) for channel in spec.reads))


def _source(spec: NodeFlow, labels: Labels, label: str) -> str:
    return next(c for c in sorted(spec.reads) if label in _seen(spec, labels, c))


def propagate(
    flows: dict[str, NodeFlow], seeds: Labels
) -> tuple[Labels, Origins]:
    labels: Labels = dict(seeds)
    origins: Origins = {(channel, label): None for channel, found in seeds.items() for label in found}
    changed = True
    while changed:
        changed = False
        for node, spec in flows.items():
            incoming = _inputs(spec, labels)
            if not incoming:
                continue
            for channel in spec.writes - set(spec.declassifies):
                new = incoming - labels.get(channel, frozenset())
                if not new:
                    continue
                for label in new:
                    origins[(channel, label)] = (node, _source(spec, labels, label))
                labels[channel] = labels.get(channel, frozenset()) | new
                changed = True
    return labels, origins


def trace(channel: str, label: str, origins: Origins) -> str:
    hops: list[str] = []
    current: tuple[str, str] | None = (channel, label)
    while current is not None:
        hops.append(current[0])
        origin = origins.get(current)
        if origin is None:
            break
        node, source = origin
        hops.append(f"({node})")
        current = (source, label)
    return " <- ".join(hops)


def check_flows(
    flows: dict[str, NodeFlow],
    seeds: Labels,
    *,
    graph_nodes: set[str],
    channels: set[str],
    erasable_stores: set[str],
    unerasable_stores: set[str],
) -> list[Violation]:
    violations: list[Violation] = []
    for node in sorted(graph_nodes - set(flows)):
        violations.append(Violation("coverage", node, "node has no flow declaration"))

    for node, spec in flows.items():
        unknown = (spec.reads | spec.writes | spec.redacts | set(spec.declassifies)) - channels
        if unknown:
            violations.append(Violation("unknown-channel", node, ", ".join(sorted(unknown))))

    labels, origins = propagate(flows, seeds)

    for node, spec in flows.items():
        incoming = _inputs(spec, labels)
        for sink in spec.sinks:
            for label in sorted(incoming - sink.accepts):
                source = _source(spec, labels, label)
                violations.append(
                    Violation(
                        "sink-accepts",
                        node,
                        f"{label!r} data reaches {sink.kind} sink {sink.name!r}: "
                        f"{trace(source, label, origins)}",
                    )
                )
            if sink.kind != "store" or SUBJECT not in incoming:
                continue
            if sink.name in unerasable_stores or sink.name not in erasable_stores:
                violations.append(
                    Violation(
                        "erasable",
                        node,
                        f"subject data reaches store {sink.name!r}, which erasure does not cover",
                    )
                )
    return violations

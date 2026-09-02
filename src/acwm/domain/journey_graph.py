"""Deterministic compiler for immutable ACWM Journey graphs."""

from __future__ import annotations

import hashlib
import heapq
import json

from pydantic import Field

from .contracts import ImmutableModel
from .journey_definition import (
    JourneyDefinition,
    JourneyEdgeDefinition,
    LoopDefinition,
    LoopPolicyDefinition,
)


class JourneyGraphError(ValueError):
    """A Journey graph cannot produce an unambiguous execution plan."""


class CompiledJourneyGraph(ImmutableModel):
    journey_id: str
    journey_version: str
    topological_order: tuple[str, ...]
    entry_node_ids: tuple[str, ...]
    exit_node_ids: tuple[str, ...]
    edges: tuple[JourneyEdgeDefinition, ...]
    loops: tuple[CompiledLoopGraph, ...] = ()
    stage_input_artifact_contracts: dict[
        str, tuple[dict[str, object], ...]
    ] = Field(default_factory=dict, exclude_if=lambda value: not value)
    fingerprint: str


class CompiledLoopGraph(ImmutableModel):
    node_id: str
    topological_order: tuple[str, ...]
    entry_node_ids: tuple[str, ...]
    exit_node_ids: tuple[str, ...]
    edges: tuple[JourneyEdgeDefinition, ...]
    policy: LoopPolicyDefinition


def compile_journey_graph(definition: JourneyDefinition) -> CompiledJourneyGraph:
    """Validate and canonicalize one outer acyclic Journey graph."""

    graph = _compile_acyclic(
        node_ids={node.id for node in definition.graph_nodes},
        edges=definition.graph_edges,
        label="Journey graph",
    )
    loops = tuple(
        _compile_loop(node)
        for node in sorted(definition.graph_nodes, key=lambda item: item.id)
        if isinstance(node, LoopDefinition)
    )
    node_by_id = {node.id: node for node in definition.graph_nodes}
    stage_input_artifact_contracts = _stage_input_artifact_contracts(definition)
    canonical = {
        "journey_id": definition.id,
        "journey_version": definition.version,
        "nodes": [
            node_by_id[node_id].model_dump(mode="json")
            for node_id in sorted(node_by_id)
        ],
        "edges": [edge.model_dump(mode="json") for edge in graph.edges],
        "topological_order": graph.order,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return CompiledJourneyGraph(
        journey_id=definition.id,
        journey_version=definition.version,
        topological_order=graph.order,
        entry_node_ids=graph.entries,
        exit_node_ids=graph.exits,
        edges=graph.edges,
        loops=loops,
        stage_input_artifact_contracts=stage_input_artifact_contracts,
        fingerprint=hashlib.sha256(encoded).hexdigest(),
    )


def _stage_input_artifact_contracts(
    definition: JourneyDefinition,
) -> dict[str, tuple[dict[str, object], ...]]:
    from .journey_definition import StageDefinition

    result: dict[str, tuple[dict[str, object], ...]] = {}

    def add(stage: StageDefinition, path: str) -> None:
        if not stage.input_artifact_contracts:
            return
        result[path] = tuple(
            {
                **contract.payload(),
                "sha256": contract.content_sha256(),
            }
            for contract in sorted(
                stage.input_artifact_contracts,
                key=lambda item: (item.id, item.version),
            )
        )

    for node in definition.graph_nodes:
        if isinstance(node, StageDefinition):
            add(node, node.id)
        elif isinstance(node, LoopDefinition):
            for child in node.nodes:
                if isinstance(child, StageDefinition):
                    add(child, f"{node.id}/{child.id}")
    return dict(sorted(result.items()))


class _AcyclicGraph(ImmutableModel):
    order: tuple[str, ...]
    entries: tuple[str, ...]
    exits: tuple[str, ...]
    edges: tuple[JourneyEdgeDefinition, ...]


def _compile_loop(definition: LoopDefinition) -> CompiledLoopGraph:
    graph = _compile_acyclic(
        node_ids={node.id for node in definition.nodes},
        edges=definition.edges,
        label=f"Loop body {definition.id}",
    )
    return CompiledLoopGraph(
        node_id=definition.id,
        topological_order=graph.order,
        entry_node_ids=graph.entries,
        exit_node_ids=graph.exits,
        edges=graph.edges,
        policy=definition.policy,
    )


def _compile_acyclic(
    *,
    node_ids: set[str],
    edges: tuple[JourneyEdgeDefinition, ...],
    label: str,
) -> _AcyclicGraph:
    edges = tuple(
        sorted(
            edges,
            key=lambda edge: (edge.source, edge.target, edge.condition or ""),
        )
    )
    edge_keys = {(edge.source, edge.target, edge.condition) for edge in edges}
    if len(edge_keys) != len(edges):
        raise JourneyGraphError(f"{label} edges must be unique")

    incoming = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise JourneyGraphError(
                f"{label} edge references an unknown Node: {edge.source} -> {edge.target}"
            )
        if edge.source == edge.target:
            raise JourneyGraphError(f"{label} cannot contain a self edge")
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)

    entry_node_ids = tuple(sorted(node_id for node_id, count in incoming.items() if count == 0))
    exit_node_ids = tuple(sorted(node_id for node_id, targets in outgoing.items() if not targets))

    remaining = dict(incoming)
    ready = list(entry_node_ids)
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        node_id = heapq.heappop(ready)
        order.append(node_id)
        for target in sorted(outgoing[node_id]):
            remaining[target] -= 1
            if remaining[target] == 0:
                heapq.heappush(ready, target)
    if len(order) != len(node_ids):
        raise JourneyGraphError(
            f"{label} contains a cycle; use an explicit bounded Loop Node"
        )
    if not entry_node_ids or not exit_node_ids:
        raise JourneyGraphError(f"{label} must contain entry and exit Nodes")
    return _AcyclicGraph(
        order=tuple(order),
        entries=entry_node_ids,
        exits=exit_node_ids,
        edges=edges,
    )

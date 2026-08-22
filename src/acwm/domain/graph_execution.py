"""Pure event-reducer primitives for durable Journey Graph Runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from .contracts import ImmutableModel
from .journey_graph import CompiledJourneyGraph, CompiledLoopGraph


class GraphNodeStatus(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"


class GraphEdgeStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"


class GraphTransitionError(ValueError):
    pass


class LoopBodyNodeRun(ImmutableModel):
    node_id: str
    status: GraphNodeStatus
    attempt: int = 0


class LoopBodyEdgeRun(ImmutableModel):
    source: str
    target: str
    condition: str | None = None
    status: GraphEdgeStatus = GraphEdgeStatus.PENDING


class LoopIterationRun(ImmutableModel):
    number: int
    status: Literal["running", "completed"]
    exit_condition_met: bool | None = None
    nodes: tuple[LoopBodyNodeRun, ...] = ()
    edges: tuple[LoopBodyEdgeRun, ...] = ()

    @property
    def ready_node_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                node.node_id
                for node in self.nodes
                if node.status is GraphNodeStatus.READY
            )
        )


class GraphNodeRun(ImmutableModel):
    node_id: str
    status: GraphNodeStatus
    attempt: int = 0
    iterations: tuple[LoopIterationRun, ...] = ()


class GraphEdgeRun(ImmutableModel):
    source: str
    target: str
    condition: str | None = None
    status: GraphEdgeStatus = GraphEdgeStatus.PENDING


class GraphRun(ImmutableModel):
    id: str
    graph: CompiledJourneyGraph
    status: Literal[
        "running", "completed", "failed", "cancelled", "needs_attention"
    ] = "running"
    version: int = 1
    nodes: tuple[GraphNodeRun, ...]
    edges: tuple[GraphEdgeRun, ...]

    @property
    def ready_node_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(node.node_id for node in self.nodes if node.status is GraphNodeStatus.READY)
        )


def create_graph_run(run_id: str, graph: CompiledJourneyGraph) -> GraphRun:
    entries = set(graph.entry_node_ids)
    return GraphRun(
        id=run_id,
        graph=graph,
        nodes=tuple(
            GraphNodeRun(
                node_id=node_id,
                status=(
                    GraphNodeStatus.READY if node_id in entries else GraphNodeStatus.BLOCKED
                ),
            )
            for node_id in graph.topological_order
        ),
        edges=tuple(
            GraphEdgeRun(source=edge.source, target=edge.target, condition=edge.condition)
            for edge in graph.edges
        ),
    )


def start_graph_node(run: GraphRun, node_id: str) -> GraphRun:
    node = _node(run, node_id)
    if node.status is not GraphNodeStatus.READY:
        raise GraphTransitionError(f"Graph Node {node_id} is not ready")
    return _replace_node(
        run,
        node.model_copy(
            update={"status": GraphNodeStatus.RUNNING, "attempt": node.attempt + 1}
        ),
    )


def succeed_graph_node(
    run: GraphRun,
    node_id: str,
    *,
    activated_conditions: set[str] | frozenset[str] = frozenset(),
) -> GraphRun:
    node = _node(run, node_id)
    if node.status is not GraphNodeStatus.RUNNING:
        raise GraphTransitionError(f"Graph Node {node_id} is not running")
    return _finish_node_success(
        run,
        node.model_copy(update={"status": GraphNodeStatus.SUCCEEDED}),
        activated_conditions=activated_conditions,
    )


def start_loop_iteration(run: GraphRun, node_id: str) -> GraphRun:
    node = _node(run, node_id)
    loop = _loop(run, node_id)
    if node.status is not GraphNodeStatus.RUNNING:
        raise GraphTransitionError(f"Loop Node {node_id} is not running")
    if node.iterations and node.iterations[-1].status == "running":
        raise GraphTransitionError(f"Loop Node {node_id} already has a running iteration")
    if len(node.iterations) >= loop.policy.max_iterations:
        raise GraphTransitionError(f"Loop Node {node_id} exhausted its iteration bound")
    entries = set(loop.entry_node_ids)
    iteration = LoopIterationRun(
        number=len(node.iterations) + 1,
        status="running",
        nodes=tuple(
            LoopBodyNodeRun(
                node_id=body_node_id,
                status=(
                    GraphNodeStatus.READY
                    if body_node_id in entries
                    else GraphNodeStatus.BLOCKED
                ),
            )
            for body_node_id in loop.topological_order
        ),
        edges=tuple(
            LoopBodyEdgeRun(
                source=edge.source,
                target=edge.target,
                condition=edge.condition,
            )
            for edge in loop.edges
        ),
    )
    return _replace_node(
        run, node.model_copy(update={"iterations": (*node.iterations, iteration)})
    )


def complete_loop_iteration(
    run: GraphRun, node_id: str, *, exit_condition_met: bool
) -> GraphRun:
    node = _node(run, node_id)
    loop = _loop(run, node_id)
    if (
        node.status is not GraphNodeStatus.RUNNING
        or not node.iterations
        or node.iterations[-1].status != "running"
    ):
        raise GraphTransitionError(f"Loop Node {node_id} has no running iteration")
    if any(
        item.status not in {GraphNodeStatus.SUCCEEDED, GraphNodeStatus.SKIPPED}
        for item in node.iterations[-1].nodes
    ):
        raise GraphTransitionError(f"Loop Node {node_id} body is not completed")
    completed = node.iterations[-1].model_copy(
        update={"status": "completed", "exit_condition_met": exit_condition_met}
    )
    updated_node = node.model_copy(
        update={"iterations": (*node.iterations[:-1], completed)}
    )
    if exit_condition_met:
        return _finish_node_success(
            run, updated_node.model_copy(update={"status": GraphNodeStatus.SUCCEEDED})
        )
    if len(updated_node.iterations) < loop.policy.max_iterations:
        return _replace_node(run, updated_node)
    if loop.policy.on_exhausted == "continue":
        return _finish_node_success(
            run, updated_node.model_copy(update={"status": GraphNodeStatus.SUCCEEDED})
        )
    terminal_status = (
        GraphNodeStatus.NEEDS_ATTENTION
        if loop.policy.on_exhausted == "needs_attention"
        else GraphNodeStatus.FAILED
    )
    return run.model_copy(
        update={
            "nodes": _replaced_nodes(
                run, updated_node.model_copy(update={"status": terminal_status})
            ),
            "status": (
                "needs_attention"
                if terminal_status is GraphNodeStatus.NEEDS_ATTENTION
                else "failed"
            ),
            "version": run.version + 1,
        }
    )


def start_loop_body_node(
    run: GraphRun, loop_node_id: str, body_node_id: str
) -> GraphRun:
    node, iteration = _running_iteration(run, loop_node_id)
    body_node = _body_node(iteration, body_node_id)
    if body_node.status is not GraphNodeStatus.READY:
        raise GraphTransitionError(f"Loop body Node {body_node_id} is not ready")
    updated = body_node.model_copy(
        update={"status": GraphNodeStatus.RUNNING, "attempt": body_node.attempt + 1}
    )
    return _replace_iteration(
        run,
        node,
        iteration.model_copy(
            update={
                "nodes": tuple(
                    updated if item.node_id == body_node_id else item
                    for item in iteration.nodes
                )
            }
        ),
    )


def succeed_loop_body_node(
    run: GraphRun,
    loop_node_id: str,
    body_node_id: str,
    *,
    activated_conditions: set[str] | frozenset[str] = frozenset(),
) -> GraphRun:
    node, iteration = _running_iteration(run, loop_node_id)
    body_node = _body_node(iteration, body_node_id)
    if body_node.status is not GraphNodeStatus.RUNNING:
        raise GraphTransitionError(f"Loop body Node {body_node_id} is not running")
    succeeded = body_node.model_copy(update={"status": GraphNodeStatus.SUCCEEDED})
    nodes = tuple(
        succeeded if item.node_id == body_node_id else item for item in iteration.nodes
    )
    edges = tuple(
        edge.model_copy(
            update={
                "status": (
                    GraphEdgeStatus.ACTIVE
                    if edge.condition is None or edge.condition in activated_conditions
                    else GraphEdgeStatus.INACTIVE
                )
            }
        )
        if edge.source == body_node_id
        else edge
        for edge in iteration.edges
    )
    released, resolved_edges = _stabilize_loop(nodes, edges)
    return _replace_iteration(
        run,
        node,
        iteration.model_copy(update={"nodes": released, "edges": resolved_edges}),
    )


def _finish_node_success(
    run: GraphRun,
    succeeded_node: GraphNodeRun,
    *,
    activated_conditions: set[str] | frozenset[str] = frozenset(),
) -> GraphRun:
    updated_nodes = _replaced_nodes(run, succeeded_node)
    updated_edges = tuple(
        edge.model_copy(
            update={
                "status": (
                    GraphEdgeStatus.ACTIVE
                    if edge.condition is None or edge.condition in activated_conditions
                    else GraphEdgeStatus.INACTIVE
                )
            }
        )
        if edge.source == succeeded_node.node_id
        else edge
        for edge in run.edges
    )
    released, resolved_edges = _stabilize(updated_nodes, updated_edges)
    terminal = all(
        item.status in {GraphNodeStatus.SUCCEEDED, GraphNodeStatus.SKIPPED}
        for item in released
    )
    return run.model_copy(
        update={
            "nodes": released,
            "edges": resolved_edges,
            "status": "completed" if terminal else "running",
            "version": run.version + 1,
        }
    )


def _node(run: GraphRun, node_id: str) -> GraphNodeRun:
    try:
        return next(node for node in run.nodes if node.node_id == node_id)
    except StopIteration as error:
        raise KeyError(node_id) from error


def _replace_node(run: GraphRun, updated: GraphNodeRun) -> GraphRun:
    return run.model_copy(
        update={
            "nodes": _replaced_nodes(run, updated),
            "version": run.version + 1,
        }
    )


def _replaced_nodes(run: GraphRun, updated: GraphNodeRun) -> tuple[GraphNodeRun, ...]:
    return tuple(
        updated if node.node_id == updated.node_id else node for node in run.nodes
    )


def _running_iteration(
    run: GraphRun, loop_node_id: str
) -> tuple[GraphNodeRun, LoopIterationRun]:
    node = _node(run, loop_node_id)
    _loop(run, loop_node_id)
    if (
        node.status is not GraphNodeStatus.RUNNING
        or not node.iterations
        or node.iterations[-1].status != "running"
    ):
        raise GraphTransitionError(f"Loop Node {loop_node_id} has no running iteration")
    return node, node.iterations[-1]


def _body_node(iteration: LoopIterationRun, node_id: str) -> LoopBodyNodeRun:
    try:
        return next(node for node in iteration.nodes if node.node_id == node_id)
    except StopIteration as error:
        raise KeyError(node_id) from error


def _replace_iteration(
    run: GraphRun,
    loop_node: GraphNodeRun,
    updated: LoopIterationRun,
) -> GraphRun:
    return _replace_node(
        run,
        loop_node.model_copy(
            update={"iterations": (*loop_node.iterations[:-1], updated)}
        ),
    )


def _stabilize_loop(
    nodes: tuple[LoopBodyNodeRun, ...], edges: tuple[LoopBodyEdgeRun, ...]
) -> tuple[tuple[LoopBodyNodeRun, ...], tuple[LoopBodyEdgeRun, ...]]:
    current_nodes = nodes
    current_edges = edges
    while True:
        changed = False
        replacements: dict[str, LoopBodyNodeRun] = {}
        for node in current_nodes:
            if node.status is not GraphNodeStatus.BLOCKED:
                continue
            incoming = tuple(edge for edge in current_edges if edge.target == node.node_id)
            if not incoming or any(edge.status is GraphEdgeStatus.PENDING for edge in incoming):
                continue
            status = (
                GraphNodeStatus.READY
                if any(edge.status is GraphEdgeStatus.ACTIVE for edge in incoming)
                else GraphNodeStatus.SKIPPED
            )
            replacements[node.node_id] = node.model_copy(update={"status": status})
            changed = True
            if status is GraphNodeStatus.SKIPPED:
                current_edges = tuple(
                    edge.model_copy(update={"status": GraphEdgeStatus.INACTIVE})
                    if edge.source == node.node_id
                    else edge
                    for edge in current_edges
                )
        if replacements:
            current_nodes = tuple(
                replacements.get(node.node_id, node) for node in current_nodes
            )
        if not changed:
            return current_nodes, current_edges


def _stabilize(
    nodes: tuple[GraphNodeRun, ...], edges: tuple[GraphEdgeRun, ...]
) -> tuple[tuple[GraphNodeRun, ...], tuple[GraphEdgeRun, ...]]:
    current_nodes = nodes
    current_edges = edges
    while True:
        changed = False
        replacements: dict[str, GraphNodeRun] = {}
        for node in current_nodes:
            if node.status is not GraphNodeStatus.BLOCKED:
                continue
            incoming = tuple(edge for edge in current_edges if edge.target == node.node_id)
            if not incoming or any(edge.status is GraphEdgeStatus.PENDING for edge in incoming):
                continue
            status = (
                GraphNodeStatus.READY
                if any(edge.status is GraphEdgeStatus.ACTIVE for edge in incoming)
                else GraphNodeStatus.SKIPPED
            )
            replacements[node.node_id] = node.model_copy(update={"status": status})
            changed = True
            if status is GraphNodeStatus.SKIPPED:
                current_edges = tuple(
                    edge.model_copy(update={"status": GraphEdgeStatus.INACTIVE})
                    if edge.source == node.node_id
                    else edge
                    for edge in current_edges
                )
        if replacements:
            current_nodes = tuple(replacements.get(node.node_id, node) for node in current_nodes)
        if not changed:
            return current_nodes, current_edges


def _loop(run: GraphRun, node_id: str) -> CompiledLoopGraph:
    try:
        return next(loop for loop in run.graph.loops if loop.node_id == node_id)
    except StopIteration as error:
        raise GraphTransitionError(f"Graph Node {node_id} is not a Loop") from error

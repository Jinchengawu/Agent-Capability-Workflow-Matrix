from acwm.domain import (
    JourneyDefinition,
    JourneyEdgeDefinition,
    LoopDefinition,
    LoopPolicyDefinition,
    StageDefinition,
    compile_journey_graph,
    complete_loop_iteration,
    create_graph_run,
    start_graph_node,
    start_loop_iteration,
    succeed_graph_node,
)


def _parallel_graph():
    return compile_journey_graph(
        JourneyDefinition(
            id="parallel-review",
            version="4.0.0",
            nodes=tuple(
                StageDefinition(
                    id=node_id,
                    workflow_mode="agentscope.role-turn",
                    bindings={"actor": f"agent-{node_id}"},
                )
                for node_id in ("plan", "security", "architecture", "delivery")
            ),
            edges=(
                JourneyEdgeDefinition(source="plan", target="security"),
                JourneyEdgeDefinition(source="plan", target="architecture"),
                JourneyEdgeDefinition(source="security", target="delivery"),
                JourneyEdgeDefinition(source="architecture", target="delivery"),
            ),
        )
    )


def test_graph_run_releases_parallel_nodes_and_waits_at_join() -> None:
    run = create_graph_run("run-1", _parallel_graph())

    assert run.ready_node_ids == ("plan",)
    run = succeed_graph_node(start_graph_node(run, "plan"), "plan")
    assert run.ready_node_ids == ("architecture", "security")

    run = succeed_graph_node(start_graph_node(run, "architecture"), "architecture")
    assert run.ready_node_ids == ("security",)

    run = succeed_graph_node(start_graph_node(run, "security"), "security")
    assert run.ready_node_ids == ("delivery",)


def test_loop_iterations_are_audited_and_fail_at_the_configured_bound() -> None:
    graph = compile_journey_graph(
        JourneyDefinition(
            id="bounded-repair",
            version="4.0.0",
            nodes=(
                LoopDefinition(
                    id="repair",
                    nodes=(
                        StageDefinition(
                            id="fix",
                            workflow_mode="code-delivery",
                            bindings={"developer": "codex-backend"},
                        ),
                    ),
                    edges=(),
                    policy=LoopPolicyDefinition(
                        exit_condition="tests-passed",
                        max_iterations=2,
                        timeout_seconds=120,
                        on_exhausted="fail",
                    ),
                ),
            ),
            edges=(),
        )
    )
    run = start_graph_node(create_graph_run("run-loop", graph), "repair")

    run = start_loop_iteration(run, "repair")
    run = complete_loop_iteration(run, "repair", exit_condition_met=False)
    run = start_loop_iteration(run, "repair")
    run = complete_loop_iteration(run, "repair", exit_condition_met=False)

    repair = next(node for node in run.nodes if node.node_id == "repair")
    assert [iteration.number for iteration in repair.iterations] == [1, 2]
    assert [iteration.exit_condition_met for iteration in repair.iterations] == [False, False]
    assert repair.status == "failed"
    assert run.status == "failed"


def test_named_condition_selects_one_branch_and_skips_the_other() -> None:
    graph = compile_journey_graph(
        JourneyDefinition(
            id="conditional-release",
            version="4.0.0",
            nodes=tuple(
                StageDefinition(
                    id=node_id,
                    workflow_mode="agentscope.role-turn",
                    bindings={"actor": f"agent-{node_id}"},
                )
                for node_id in ("review", "publish", "archive")
            ),
            edges=(
                JourneyEdgeDefinition(
                    source="review", target="publish", condition="approved"
                ),
                JourneyEdgeDefinition(
                    source="review", target="archive", condition="rejected"
                ),
            ),
        )
    )
    run = start_graph_node(create_graph_run("run-condition", graph), "review")

    run = succeed_graph_node(run, "review", activated_conditions={"approved"})

    statuses = {node.node_id: node.status for node in run.nodes}
    assert statuses == {"review": "succeeded", "publish": "ready", "archive": "skipped"}
    assert {(edge.target, edge.status) for edge in run.edges} == {
        ("publish", "active"),
        ("archive", "inactive"),
    }

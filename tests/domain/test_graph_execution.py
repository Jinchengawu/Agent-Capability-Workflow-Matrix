from acwm.domain import (
    JourneyDefinition,
    JourneyEdgeDefinition,
    LoopDefinition,
    LoopPolicyDefinition,
    StageDefinition,
    cancel_graph_run,
    compile_journey_graph,
    complete_loop_iteration,
    create_graph_run,
    fail_graph_node,
    start_graph_node,
    start_loop_body_node,
    start_loop_iteration,
    succeed_graph_node,
    succeed_loop_body_node,
)


def test_running_node_failure_terminates_graph_and_preserves_audit_state() -> None:
    graph = _parallel_graph()
    run = start_graph_node(create_graph_run("run-failed", graph), "plan")

    failed = fail_graph_node(run, "plan")

    assert failed.status == "failed"
    assert failed.version == run.version + 1
    assert next(node for node in failed.nodes if node.node_id == "plan").status == "failed"
    assert next(node for node in failed.nodes if node.node_id == "delivery").status == "cancelled"


def test_cancel_graph_run_is_idempotent_and_cancels_non_terminal_nodes() -> None:
    graph = _parallel_graph()
    run = create_graph_run("run-cancelled", graph)

    cancelled = cancel_graph_run(run)

    assert cancelled.status == "cancelled"
    assert cancelled.version == run.version + 1
    assert {node.status for node in cancelled.nodes} == {"cancelled"}
    assert cancel_graph_run(cancelled) == cancelled


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
    run = succeed_loop_body_node(
        start_loop_body_node(run, "repair", "fix"), "repair", "fix"
    )
    run = complete_loop_iteration(run, "repair", exit_condition_met=False)
    run = start_loop_iteration(run, "repair")
    run = succeed_loop_body_node(
        start_loop_body_node(run, "repair", "fix"), "repair", "fix"
    )
    run = complete_loop_iteration(run, "repair", exit_condition_met=False)

    repair = next(node for node in run.nodes if node.node_id == "repair")
    assert [iteration.number for iteration in repair.iterations] == [1, 2]
    assert [iteration.exit_condition_met for iteration in repair.iterations] == [False, False]
    assert [iteration.nodes[0].status for iteration in repair.iterations] == [
        "succeeded",
        "succeeded",
    ]
    assert repair.status == "failed"
    assert run.status == "failed"


def test_loop_iteration_releases_and_audits_its_internal_dag() -> None:
    graph = compile_journey_graph(
        JourneyDefinition(
            id="review-loop",
            version="4.0.0",
            nodes=(
                LoopDefinition(
                    id="review",
                    nodes=tuple(
                        StageDefinition(
                            id=node_id,
                            workflow_mode="agentscope.role-turn",
                            bindings={"actor": f"agent-{node_id}"},
                        )
                        for node_id in ("draft", "security", "architecture", "merge")
                    ),
                    edges=(
                        JourneyEdgeDefinition(source="draft", target="security"),
                        JourneyEdgeDefinition(source="draft", target="architecture"),
                        JourneyEdgeDefinition(source="security", target="merge"),
                        JourneyEdgeDefinition(source="architecture", target="merge"),
                    ),
                    policy=LoopPolicyDefinition(
                        exit_condition="approved",
                        max_iterations=3,
                        timeout_seconds=120,
                        on_exhausted="needs_attention",
                    ),
                ),
            ),
            edges=(),
        )
    )
    run = start_loop_iteration(
        start_graph_node(create_graph_run("run-review", graph), "review"), "review"
    )
    review = next(node for node in run.nodes if node.node_id == "review")
    assert review.iterations[-1].ready_node_ids == ("draft",)

    run = succeed_loop_body_node(
        start_loop_body_node(run, "review", "draft"), "review", "draft"
    )
    review = next(node for node in run.nodes if node.node_id == "review")
    assert review.iterations[-1].ready_node_ids == ("architecture", "security")

    for node_id in ("architecture", "security", "merge"):
        run = succeed_loop_body_node(
            start_loop_body_node(run, "review", node_id), "review", node_id
        )
    run = complete_loop_iteration(run, "review", exit_condition_met=True)

    review = next(node for node in run.nodes if node.node_id == "review")
    assert review.status == "succeeded"
    assert run.status == "completed"
    assert {node.node_id: node.status for node in review.iterations[0].nodes} == {
        "draft": "succeeded",
        "security": "succeeded",
        "architecture": "succeeded",
        "merge": "succeeded",
    }


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

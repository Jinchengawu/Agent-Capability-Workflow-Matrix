import pytest

from acwm.domain import (
    JourneyDefinition,
    JourneyEdgeDefinition,
    JourneyGraphError,
    LoopDefinition,
    LoopPolicyDefinition,
    StageDefinition,
    compile_journey_graph,
)


def test_compile_journey_graph_orders_a_fork_and_join_deterministically() -> None:
    definition = JourneyDefinition(
        id="parallel-review",
        version="4.0.0",
        nodes=(
            StageDefinition(
                id="plan",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-pm"},
            ),
            StageDefinition(
                id="security-review",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-security"},
            ),
            StageDefinition(
                id="architecture-review",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-architect"},
            ),
            StageDefinition(
                id="delivery",
                workflow_mode="code-delivery",
                bindings={"developer": "codex-backend"},
            ),
        ),
        edges=(
            JourneyEdgeDefinition(source="plan", target="security-review"),
            JourneyEdgeDefinition(source="plan", target="architecture-review"),
            JourneyEdgeDefinition(source="security-review", target="delivery"),
            JourneyEdgeDefinition(source="architecture-review", target="delivery"),
        ),
    )

    compiled = compile_journey_graph(definition)

    assert compiled.entry_node_ids == ("plan",)
    assert compiled.exit_node_ids == ("delivery",)
    assert compiled.topological_order == (
        "plan",
        "architecture-review",
        "security-review",
        "delivery",
    )
    assert len(compiled.fingerprint) == 64


def test_compile_journey_graph_freezes_a_bounded_loop_body() -> None:
    definition = JourneyDefinition(
        id="repair-until-verified",
        version="4.0.0",
        nodes=(
            StageDefinition(
                id="plan",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-pm"},
            ),
            LoopDefinition(
                id="repair",
                nodes=(
                    StageDefinition(
                        id="review",
                        workflow_mode="agentscope.role-turn",
                        bindings={"actor": "hermes-reviewer"},
                    ),
                    StageDefinition(
                        id="fix",
                        workflow_mode="code-delivery",
                        bindings={"developer": "codex-backend"},
                    ),
                ),
                edges=(JourneyEdgeDefinition(source="review", target="fix"),),
                policy=LoopPolicyDefinition(
                    exit_condition="machine-tests-passed",
                    max_iterations=3,
                    timeout_seconds=300,
                    on_exhausted="fail",
                ),
            ),
            StageDefinition(
                id="publish",
                workflow_mode="code-delivery",
                bindings={"developer": "codex-backend"},
            ),
        ),
        edges=(
            JourneyEdgeDefinition(source="plan", target="repair"),
            JourneyEdgeDefinition(source="repair", target="publish"),
        ),
    )

    compiled = compile_journey_graph(definition)

    assert compiled.topological_order == ("plan", "repair", "publish")
    assert len(compiled.loops) == 1
    assert compiled.loops[0].node_id == "repair"
    assert compiled.loops[0].topological_order == ("review", "fix")
    assert compiled.loops[0].policy.max_iterations == 3
    assert compiled.loops[0].policy.exit_condition == "machine-tests-passed"


def test_compile_journey_graph_rejects_an_arbitrary_back_edge() -> None:
    definition = JourneyDefinition(
        id="invalid-cycle",
        version="4.0.0",
        nodes=(
            StageDefinition(
                id="one",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-pm"},
            ),
            StageDefinition(
                id="two",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-admin"},
            ),
        ),
        edges=(
            JourneyEdgeDefinition(source="one", target="two"),
            JourneyEdgeDefinition(source="two", target="one"),
        ),
    )

    with pytest.raises(JourneyGraphError, match="explicit bounded Loop Node"):
        compile_journey_graph(definition)

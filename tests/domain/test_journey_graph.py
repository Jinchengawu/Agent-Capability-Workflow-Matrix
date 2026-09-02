import hashlib
import json

import pytest
from pydantic import ValidationError

from acwm.domain import (
    ArtifactContract,
    ArtifactModality,
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


def test_legacy_journey_fingerprint_and_shape_do_not_drift() -> None:
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

    assert compiled.fingerprint == (
        "b22c80d4e4172a25e966cbbabac7ff0122fefb946828afa9eb1e26981cd8410d"
    )
    assert "stage_input_artifact_contracts" not in compiled.model_dump(mode="json")


def test_compile_journey_graph_freezes_stage_input_contracts_by_canonical_path() -> None:
    contract = ArtifactContract(
        id="knowledge-context-v1",
        version="1.0.0",
        schema_uri="schema://knowledge-context-v1@1.0.0",
        modalities=frozenset({ArtifactModality.STRUCTURED}),
    )
    definition = JourneyDefinition(
        id="knowledge-delivery",
        version="1.0.0",
        nodes=(
            StageDefinition(
                id="requirements",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-pm"},
                input_artifact_contracts=(contract,),
            ),
            LoopDefinition(
                id="frontend-repair",
                nodes=(
                    StageDefinition(
                        id="frontend",
                        workflow_mode="agentscope.workcell-team",
                        bindings={"main": "frontend-lead"},
                        input_artifact_contracts=(contract,),
                    ),
                ),
                edges=(),
                policy=LoopPolicyDefinition(
                    exit_condition="verified",
                    max_iterations=2,
                    timeout_seconds=300,
                ),
            ),
        ),
        edges=(JourneyEdgeDefinition(source="requirements", target="frontend-repair"),),
    )

    compiled = compile_journey_graph(definition)
    payload = contract.model_dump(mode="json")
    expected_sha = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    assert set(compiled.stage_input_artifact_contracts) == {
        "requirements",
        "frontend-repair/frontend",
    }
    assert compiled.stage_input_artifact_contracts["requirements"] == (
        {**payload, "sha256": expected_sha},
    )
    assert compiled.stage_input_artifact_contracts["frontend-repair/frontend"] == (
        {**payload, "sha256": expected_sha},
    )
    assert compiled.model_dump(mode="json")["stage_input_artifact_contracts"] == {
        "frontend-repair/frontend": [{**payload, "sha256": expected_sha}],
        "requirements": [{**payload, "sha256": expected_sha}],
    }


def test_stage_rejects_ambiguous_input_contract_versions() -> None:
    with pytest.raises(ValidationError, match="input Artifact Contract ids must be unique"):
        StageDefinition(
            id="requirements",
            workflow_mode="agentscope.role-turn",
            bindings={"actor": "hermes-pm"},
            input_artifact_contracts=(
                ArtifactContract(id="knowledge-context-v1", version="1.0.0"),
                ArtifactContract(id="knowledge-context-v1", version="2.0.0"),
            ),
        )


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

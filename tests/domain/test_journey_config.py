from pathlib import Path

from acwm.config import load_journeys


def test_journey_yaml_declares_ordered_nodes_and_gates(tmp_path: Path) -> None:
    path = tmp_path / "journeys.yaml"
    path.write_text(
        """
schema_version: "3"
journeys:
  - id: team-delivery
    version: 3.0.0
    steps:
      - kind: stage
        id: requirements
        workflow_mode: agentscope.role-turn
        bindings: {actor: hermes-pm}
      - kind: stage
        id: tasking
        workflow_mode: agentscope.role-turn
        bindings: {actor: hermes-admin}
      - kind: approval_gate
        id: approve-plan
        subject_kind: delivery-plan
      - kind: stage
        id: delivery
        workflow_mode: code-delivery
        bindings: {developer: codex-backend}
""".strip(),
        encoding="utf-8",
    )

    definition = load_journeys(path)["team-delivery"]

    assert [step.id for step in definition.steps] == [
        "requirements",
        "tasking",
        "approve-plan",
        "delivery",
    ]
    assert definition.steps[0].bindings == {"actor": "hermes-pm"}  # type: ignore[union-attr]
    assert definition.steps[1].bindings == {"actor": "hermes-admin"}  # type: ignore[union-attr]
    assert definition.steps[2].subject_kind == "delivery-plan"  # type: ignore[union-attr]
    assert definition.steps[-1].bindings == {"developer": "codex-backend"}  # type: ignore[union-attr]


def test_schema_v4_loads_a_journey_graph_with_a_bounded_loop(tmp_path: Path) -> None:
    path = tmp_path / "journeys.yaml"
    path.write_text(
        """
schema_version: "4"
journeys:
  - id: iterative-delivery
    version: 4.0.0
    nodes:
      - kind: stage
        id: plan
        workflow_mode: agentscope.role-turn
        bindings: {actor: hermes-pm}
      - kind: loop
        id: repair
        policy:
          exit_condition: machine-tests-passed
          max_iterations: 3
          timeout_seconds: 300
          on_exhausted: fail
        nodes:
          - kind: stage
            id: implement
            workflow_mode: code-delivery
            bindings: {developer: codex-backend}
        edges: []
      - kind: approval_gate
        id: approve
        subject_kind: candidate-change
    edges:
      - {source: plan, target: repair}
      - {source: repair, target: approve}
""".strip(),
        encoding="utf-8",
    )

    definition = load_journeys(path)["iterative-delivery"]

    assert [node.id for node in definition.nodes] == ["plan", "repair", "approve"]
    assert definition.edges[0].source == "plan"
    assert definition.nodes[1].policy.max_iterations == 3  # type: ignore[union-attr]

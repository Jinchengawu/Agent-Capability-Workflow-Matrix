from pathlib import Path

from acwm.config import load_journeys


def test_journey_yaml_declares_ordered_nodes_and_gates(tmp_path: Path) -> None:
    path = tmp_path / "journeys.yaml"
    path.write_text(
        """
schema_version: "2"
journeys:
  - id: mixed-code-delivery
    version: 2.0.0
    steps:
      - {kind: node, id: plan, capability_id: http-planner, workflow_mode: direct}
      - {kind: approval_gate, id: approve-plan}
      - kind: node
        id: deliver
        capability_id: hermes-developer
        workflow_mode: langgraph.code-delivery
""".strip(),
        encoding="utf-8",
    )

    definition = load_journeys(path)["mixed-code-delivery"]

    assert [step.id for step in definition.steps] == ["plan", "approve-plan", "deliver"]
    assert definition.steps[0].capability_id == "http-planner"  # type: ignore[union-attr]
    assert definition.steps[-1].workflow_mode == "langgraph.code-delivery"  # type: ignore[union-attr]

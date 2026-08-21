import sys
from pathlib import Path

from acwm.adapters.hermes_acp import HermesACPCapabilityAdapter
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.config import HermesACPConfig, load_capabilities
from acwm.domain import (
    AgentTurn,
    CapabilityEvent,
    CapabilityFeature,
    StageRunSpec,
    WorkflowRequirements,
)


async def test_acp_waits_for_output_and_reuses_session_for_stage_turns(tmp_path: Path) -> None:
    fake_agent = Path(__file__).parents[1] / "fixtures" / "fake_acp_agent.py"
    config = HermesACPConfig(command=(sys.executable, str(fake_agent)))
    adapter = HermesACPCapabilityAdapter(config)
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        f"""
schema_version: "3"
capabilities:
  - id: hermes-developer
    version: 1.0.0
    adapter:
      type: hermes.acp
      config:
        command: [{sys.executable!r}, {str(fake_agent)!r}]
""",
        encoding="utf-8",
    )
    events: list[CapabilityEvent] = []
    runtime = DefaultCapabilityRuntime(
        catalog=load_capabilities(path),
        adapters={"hermes-developer": adapter},
        event_sink=events.append,
    )
    resolved = runtime.resolve(
        "hermes-developer",
        WorkflowRequirements(
            mode_id="direct",
            mode_version="2.0",
            required=frozenset({CapabilityFeature.TEXT_FINAL}),
        ),
    )

    async with runtime.stage(
        StageRunSpec(
            journey_id="journey-1",
            stage_id="plan",
            attempt_id="attempt-1",
            workflow_mode="direct",
            capability=resolved,
            objective="test",
            workspace=str(tmp_path),
        )
    ) as exchange:
        first = await exchange.turn(AgentTurn(purpose="plan", instruction="first"))
        second = await exchange.turn(AgentTurn(purpose="review", instruction="second"))
    await runtime.close()

    assert f"cwd={tmp_path}" in first.text
    assert "count=1" in first.text
    assert "count=2" in second.text
    output_events = [event for event in events if event.type == "capability.output.delta"]
    assert len(output_events) == 2

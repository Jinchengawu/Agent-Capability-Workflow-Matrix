from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from acwm.application.runtime import DefaultCapabilityRuntime, WorkflowIncompatibleError
from acwm.config import load_capabilities
from acwm.domain import (
    AdapterManifest,
    AgentTurn,
    CapabilityEvent,
    CapabilityFeature,
    StageRunSpec,
    TurnResult,
    WorkflowRequirements,
)


class ManifestOnlyAdapter:
    manifest = AdapterManifest(
        adapter_type="http.sync",
        adapter_version="0.2.0",
        features=frozenset({CapabilityFeature.TEXT_FINAL}),
    )


class ScriptedAdapter(ManifestOnlyAdapter):
    @asynccontextmanager
    async def stage(self, spec: StageRunSpec, emit: Any) -> Any:
        class Exchange:
            async def turn(self, turn: AgentTurn) -> TurnResult:
                return TurnResult(text=f"answer:{turn.instruction}")

        yield Exchange()

    async def signal(self, command: Any) -> Any:
        raise AssertionError("no signal expected")

    async def close(self) -> None:
        return None


def test_runtime_resolves_compatible_capability_and_rejects_missing_features(
    tmp_path: Path,
) -> None:
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        """
schema_version: "2"
capabilities:
  - id: http-planner
    version: 1.0.0
    adapter:
      type: http.sync
      config: {endpoint: http://127.0.0.1:9000/v1/invoke}
""",
        encoding="utf-8",
    )
    catalog = load_capabilities(path)
    runtime = DefaultCapabilityRuntime(
        catalog=catalog,
        adapters={"http-planner": ManifestOnlyAdapter()},
        event_sink=None,
    )

    direct = runtime.resolve(
        "http-planner",
        WorkflowRequirements(
            mode_id="direct",
            mode_version="2.0",
            required=frozenset({CapabilityFeature.TEXT_FINAL}),
        ),
    )

    assert direct.adapter_type == "http.sync"
    assert direct.features == frozenset({CapabilityFeature.TEXT_FINAL})

    with pytest.raises(WorkflowIncompatibleError) as captured:
        runtime.resolve(
            "http-planner",
            WorkflowRequirements(
                mode_id="langgraph.code-delivery",
                mode_version="2.0",
                required=frozenset(
                    {
                        CapabilityFeature.TEXT_FINAL,
                        CapabilityFeature.CWD_BINDING,
                        CapabilityFeature.REMOTE_STOP,
                    }
                ),
            ),
        )

    assert captured.value.missing_features == frozenset(
        {CapabilityFeature.CWD_BINDING, CapabilityFeature.REMOTE_STOP}
    )


async def test_stage_context_publishes_ordered_terminal_events(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        """
schema_version: "2"
capabilities:
  - id: http-planner
    version: 1.0.0
    adapter:
      type: http.sync
      config: {endpoint: http://127.0.0.1:9000/v1/invoke}
""",
        encoding="utf-8",
    )
    catalog = load_capabilities(path)
    events: list[CapabilityEvent] = []
    runtime = DefaultCapabilityRuntime(
        catalog=catalog,
        adapters={"http-planner": ScriptedAdapter()},
        event_sink=events.append,
    )
    resolved = runtime.resolve(
        "http-planner",
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
            objective="draft a plan",
        )
    ) as exchange:
        result = await exchange.turn(AgentTurn(purpose="plan", instruction="do it"))

    assert result.text == "answer:do it"
    assert [event.type for event in events] == [
        "capability.run.started",
        "capability.turn.started",
        "capability.output.delta",
        "capability.turn.completed",
        "capability.run.completed",
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]

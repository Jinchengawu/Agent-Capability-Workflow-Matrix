from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from acwm.adapters.workflows import LangGraphCodeDeliveryAdapter
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.config import load_capabilities
from acwm.domain import (
    AdapterManifest,
    AgentTurn,
    CapabilityFeature,
    NodeRequest,
    StageRunSpec,
    TurnResult,
    VerificationCommand,
)


class RepairingAdapter:
    manifest = AdapterManifest(
        adapter_type="hermes.acp",
        adapter_version="0.2.0",
        features=frozenset(CapabilityFeature),
    )

    def __init__(self) -> None:
        self.purposes: list[str] = []

    @asynccontextmanager
    async def stage(self, spec: StageRunSpec, emit: Any) -> Any:
        workspace = Path(spec.workspace or "")
        owner = self

        class Exchange:
            async def turn(self, turn: AgentTurn) -> TurnResult:
                owner.purposes.append(turn.purpose)
                if turn.purpose == "implement":
                    (workspace / "value.txt").write_text("wrong", encoding="utf-8")
                    return TurnResult(text="first implementation")
                if turn.purpose == "repair":
                    (workspace / "value.txt").write_text("correct", encoding="utf-8")
                    return TurnResult(text="repaired implementation")
                passed = (workspace / "value.txt").read_text(encoding="utf-8") == "correct"
                return TurnResult(
                    text=(
                        '{"accepted": true, "summary": "ok"}'
                        if passed
                        else '{"accepted": false, "summary": "tests failed"}'
                    )
                )

        yield Exchange()

    async def signal(self, command: Any) -> Any:
        raise AssertionError("no signal expected")

    async def close(self) -> None:
        return None


def make_runtime(tmp_path: Path, adapter: RepairingAdapter) -> DefaultCapabilityRuntime:
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        """
schema_version: "2"
capabilities:
  - id: hermes-developer
    version: 1.0.0
    adapter:
      type: hermes.acp
      config:
        command: [hermes, acp]
""",
        encoding="utf-8",
    )
    return DefaultCapabilityRuntime(
        catalog=load_capabilities(path),
        adapters={"hermes-developer": adapter},
        event_sink=None,
    )


@pytest.mark.asyncio
async def test_langgraph_repairs_a_failed_verification_before_delivery(tmp_path: Path) -> None:
    capability_adapter = RepairingAdapter()
    runtime = make_runtime(tmp_path, capability_adapter)
    adapter = LangGraphCodeDeliveryAdapter(runtime, tmp_path / "checkpoints.sqlite")
    resolved = runtime.resolve("hermes-developer", adapter.mode.requirements)

    result = await adapter.execute(
        NodeRequest(
            attempt_id="attempt-1",
            journey_id="journey-1",
            stage_id="deliver",
            capability=resolved,
            workspace=str(tmp_path),
            objective="write the correct value",
            verification_commands=(
                VerificationCommand(
                    name="check",
                    argv=("python", "-c", "assert open('value.txt').read() == 'correct'"),
                    timeout_seconds=5,
                ),
            ),
        )
    )

    assert "repair" in capability_adapter.purposes
    assert result.evidence[0]["exit_code"] == 0
    assert (tmp_path / "value.txt").read_text(encoding="utf-8") == "correct"

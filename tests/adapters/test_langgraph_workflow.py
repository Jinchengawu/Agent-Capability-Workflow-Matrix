from pathlib import Path

import pytest

from acwm.adapters.workflows import LangGraphCodeDeliveryAdapter
from acwm.domain import NodeRequest, VerificationCommand
from acwm.ports import CapabilityInvocation, CapabilityTransport, TransportResult


class RepairingTransport(CapabilityTransport):
    def __init__(self) -> None:
        self.purposes: list[str] = []

    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult:
        self.purposes.append(invocation.purpose)
        if invocation.purpose == "implement":
            (invocation.cwd / "value.txt").write_text("wrong", encoding="utf-8")
            return TransportResult(output="first implementation")
        if invocation.purpose == "repair":
            (invocation.cwd / "value.txt").write_text("correct", encoding="utf-8")
            return TransportResult(output="repaired implementation")
        passed = (invocation.cwd / "value.txt").read_text(encoding="utf-8") == "correct"
        return TransportResult(
            output=(
                '{"accepted": true, "summary": "ok"}'
                if passed
                else '{"accepted": false, "summary": "tests failed"}'
            )
        )

    async def cancel(self, session_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_langgraph_repairs_a_failed_verification_before_delivery(tmp_path: Path) -> None:
    transport = RepairingTransport()
    adapter = LangGraphCodeDeliveryAdapter(transport, tmp_path / "checkpoints.sqlite")

    result = await adapter.execute(
        NodeRequest(
            attempt_id="attempt-1",
            journey_id="journey-1",
            stage_id="deliver",
            capability_id="hermes-developer",
            session_id="mode-scoped-session",
            cwd=str(tmp_path),
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

    assert "repair" in transport.purposes
    assert result.evidence[0]["exit_code"] == 0
    assert (tmp_path / "value.txt").read_text(encoding="utf-8") == "correct"

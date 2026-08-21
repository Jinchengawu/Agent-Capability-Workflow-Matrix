from pathlib import Path

import pytest
from pydantic import ValidationError

from acwm.domain import CapabilitySession, NodeRequest, VerificationCommand


def test_capability_session_is_scoped_to_journey_stage_and_mode() -> None:
    session = CapabilitySession(
        id="acwm:j1:deliver:langgraph",
        journey_id="j1",
        stage_id="deliver",
        workflow_mode="langgraph.code-delivery",
    )

    with pytest.raises(ValidationError):
        session.stage_id = "plan"  # type: ignore[misc]


def test_node_request_is_the_shared_workflow_input_contract(tmp_path: Path) -> None:
    request = NodeRequest(
        attempt_id="attempt-1",
        journey_id="journey-1",
        stage_id="deliver",
        capability_id="hermes-developer",
        session_id="acwm:journey-1:deliver:langgraph",
        cwd=str(tmp_path),
        objective="Implement the approved plan",
        verification_commands=(VerificationCommand(name="tests", argv=("pytest",)),),
    )

    assert Path(request.cwd) == tmp_path
    assert request.max_repairs == 2

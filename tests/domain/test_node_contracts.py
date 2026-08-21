from pathlib import Path

import pytest
from pydantic import ValidationError

from acwm.domain import (
    CapabilityFeature,
    NodeRequest,
    ResolvedCapability,
    VerificationCommand,
)


def resolved_capability() -> ResolvedCapability:
    return ResolvedCapability(
        capability_id="hermes-developer",
        capability_version="1.0.0",
        adapter_type="hermes.acp",
        adapter_version="0.2.0",
        features=frozenset(CapabilityFeature),
        required_features=frozenset({CapabilityFeature.TEXT_FINAL}),
        config_fingerprint="config",
        policy_version="1.0",
        policy_fingerprint="policy",
    )


def test_node_request_is_the_shared_workflow_input_contract(tmp_path: Path) -> None:
    request = NodeRequest(
        attempt_id="attempt-1",
        journey_id="journey-1",
        stage_id="deliver",
        capability=resolved_capability(),
        workspace=str(tmp_path),
        objective="Implement the approved plan",
        verification_commands=(VerificationCommand(name="tests", argv=("pytest",)),),
    )

    assert Path(request.workspace or "") == tmp_path
    assert request.max_repairs == 2

    with pytest.raises(ValidationError):
        request.stage_id = "plan"  # type: ignore[misc]

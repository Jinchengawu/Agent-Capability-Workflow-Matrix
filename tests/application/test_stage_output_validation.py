from typing import Any

import pytest

from acwm.application.workflow_runtime import (
    DefaultWorkflowRuntime,
    StageValidationError,
)
from acwm.domain import (
    CapabilityFeature,
    ResolvedCapability,
    StageDefinition,
    StageExecutionSpec,
    StageResult,
    StageValidationReport,
    WorkflowBindingSlot,
    WorkflowManifest,
    WorkflowRequirements,
)


class CapabilityResolver:
    def resolve(
        self, capability_id: str, requirements: WorkflowRequirements
    ) -> ResolvedCapability:
        return ResolvedCapability(
            capability_id=capability_id,
            capability_version="1.0.0",
            adapter_type="fake",
            adapter_version="1.0.0",
            features=requirements.required,
            required_features=requirements.required,
            config_fingerprint="config",
            policy_version="1.0",
            policy_fingerprint="policy",
        )

    def stage(self, spec: Any) -> Any:
        raise AssertionError("not used by this workflow")


class CandidateWorkflow:
    manifest = WorkflowManifest(
        mode_id="code-delivery",
        mode_version="1.0.0",
        adapter_type="deterministic",
        adapter_version="1.0.0",
        resumable=False,
        bindings={
            "developer": WorkflowBindingSlot(
                required_features=frozenset({CapabilityFeature.TEXT_FINAL})
            )
        },
    )

    async def execute(self, spec: Any, stage: Any, capability_runtime: Any) -> StageResult:
        return StageResult(status="succeeded", output="candidate created")


class RejectingValidator:
    async def validate(self, stage: Any, result: StageResult) -> StageValidationReport:
        return StageValidationReport(
            policy_id="candidate-security-v1",
            status="failed",
            summary="secret material detected",
        )


async def test_failed_product_validator_prevents_successful_stage_completion() -> None:
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        adapters={"code-delivery": CandidateWorkflow()},
        validators={"candidate-security-v1": RejectingValidator()},
    )
    resolved = runtime.resolve(
        StageDefinition(
            id="delivery",
            workflow_mode="code-delivery",
            bindings={"developer": "codex-backend"},
            output_validator="candidate-security-v1",
        )
    )

    with pytest.raises(StageValidationError, match="secret material detected"):
        await runtime.execute(
            resolved,
            StageExecutionSpec(
                journey_id="journey-1",
                attempt_id="attempt-1",
                objective="implement candidate",
            ),
        )

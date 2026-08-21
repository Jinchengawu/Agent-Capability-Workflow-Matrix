from pathlib import Path

from acwm.adapters.agentscope_role_turn import AgentScopeRoleTurnAdapter
from acwm.adapters.code_delivery import CodeDeliveryWorkflowAdapter
from acwm.adapters.codex_cli import CodexCLICapabilityAdapter
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.application.workflow_runtime import DefaultWorkflowRuntime
from acwm.config import load_capabilities, load_journeys
from acwm.domain import (
    AdapterManifest,
    CapabilityFeature,
    ResolvedStage,
    StageResult,
    StageValidationReport,
)


class ManifestOnlyAdapter:
    def __init__(self, manifest: AdapterManifest) -> None:
        self.manifest = manifest


class CandidateValidator:
    async def validate(
        self, stage: ResolvedStage, result: StageResult
    ) -> StageValidationReport:
        raise AssertionError("Journey resolution must not execute validators")


def test_agent_team_os_journey_resolves_agentscope_hermes_and_codex_matrix() -> None:
    root = Path(__file__).parents[2]
    catalog = load_capabilities(root / "config" / "capabilities.yaml")
    hermes_manifest = AdapterManifest(
        adapter_type="hermes.acp",
        adapter_version="0.3.0",
        features=frozenset(CapabilityFeature),
    )
    capability_runtime = DefaultCapabilityRuntime(
        catalog=catalog,
        adapters={
            "hermes-pm": ManifestOnlyAdapter(hermes_manifest),
            "hermes-project-admin": ManifestOnlyAdapter(hermes_manifest),
            "codex-backend": ManifestOnlyAdapter(CodexCLICapabilityAdapter.manifest),
        },
        event_sink=None,
    )
    role_turn = AgentScopeRoleTurnAdapter()
    code_delivery = CodeDeliveryWorkflowAdapter()
    workflow_runtime = DefaultWorkflowRuntime(
        capability_runtime=capability_runtime,
        adapters={
            role_turn.manifest.mode_id: role_turn,
            code_delivery.manifest.mode_id: code_delivery,
        },
        validators={"candidate-security-v1": CandidateValidator()},
    )
    definition = load_journeys(root / "config" / "agent-team-os.journey.yaml")[
        "agent-team-os-backend-delivery"
    ]

    resolved = workflow_runtime.resolve_journey(definition)

    assert resolved.order == (
        "requirements",
        "tasking",
        "approve-plan",
        "delivery",
        "approve-candidate",
    )
    assert [stage.workflow.adapter_type for stage in resolved.stages] == [
        "agentscope",
        "agentscope",
        "acwm.code-delivery",
    ]
    assert [node.capability.capability_id for stage in resolved.stages for node in stage.nodes] == [
        "hermes-pm",
        "hermes-project-admin",
        "codex-backend",
    ]
    assert [gate.subject_kind for gate in resolved.gates] == [
        "delivery-plan",
        "candidate-change",
    ]

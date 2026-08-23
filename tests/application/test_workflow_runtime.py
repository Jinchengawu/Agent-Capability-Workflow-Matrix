from contextlib import asynccontextmanager
from typing import Any

import pytest

from acwm.adapters.agentscope_role_turn import AgentScopeRoleTurnAdapter
from acwm.adapters.code_delivery import CodeDeliveryWorkflowAdapter
from acwm.application.workflow_runtime import (
    DefaultWorkflowRuntime,
    StaticProviderResolver,
    WorkflowBindingError,
)
from acwm.domain import (
    ApprovalGateDefinition,
    CapabilityFeature,
    CapabilityProviderManifest,
    JourneyDefinition,
    JourneyEdgeDefinition,
    LoopDefinition,
    LoopPolicyDefinition,
    ProviderCapability,
    ResolvedCapability,
    StageDefinition,
    StageExecutionSpec,
    StageResult,
    TurnResult,
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
            adapter_type="fake.agent",
            adapter_version="1.0.0",
            features=requirements.required,
            required_features=requirements.required,
            config_fingerprint=f"config:{capability_id}",
            policy_version="1.0",
            policy_fingerprint=f"policy:{capability_id}",
        )

    @asynccontextmanager
    async def stage(self, spec: Any) -> Any:
        class Exchange:
            async def turn(self, turn: Any) -> TurnResult:
                return TurnResult(text=f"completed:{turn.instruction}")

        yield Exchange()


class TextMessageCodec:
    def user(self, name: str, content: str) -> str:
        return content

    def assistant(self, name: str, content: str) -> str:
        return content

    def text(self, message: str) -> str:
        return message


class RoleTurnWorkflow:
    manifest = WorkflowManifest(
        mode_id="agentscope.role-turn",
        mode_version="1.0.0",
        adapter_type="agentscope",
        adapter_version="2.0.5",
        resumable=False,
        bindings={
            "actor": WorkflowBindingSlot(
                required_features=frozenset({CapabilityFeature.TEXT_FINAL})
            )
        },
    )

    async def execute(self, spec: Any, nodes: Any, capabilities: Any) -> Any:
        raise AssertionError("resolution must not execute the workflow")


def test_workflow_runtime_resolves_every_named_capability_binding() -> None:
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        adapters={"agentscope.role-turn": RoleTurnWorkflow()},
    )

    resolved = runtime.resolve(
        StageDefinition(
            id="requirements",
            workflow_mode="agentscope.role-turn",
            bindings={"actor": "hermes-pm"},
        )
    )

    assert resolved.stage_id == "requirements"
    assert resolved.workflow.mode_id == "agentscope.role-turn"
    assert resolved.workflow.adapter_version == "2.0.5"
    assert resolved.nodes[0].slot == "actor"
    assert resolved.nodes[0].capability.capability_id == "hermes-pm"


def test_workflow_runtime_freezes_provider_selected_by_stage_binding_site() -> None:
    provider = CapabilityProviderManifest.create(
        provider_id="pm-agent",
        provider_revision="2",
        capabilities=(ProviderCapability(id="hermes-pm", version="1.0.0"),),
        workflow_modes=("agentscope.role-turn",),
        required_features=frozenset({CapabilityFeature.TEXT_FINAL}),
    )
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        provider_resolver=StaticProviderResolver({"requirements.actor": provider}),
        adapters={"agentscope.role-turn": RoleTurnWorkflow()},
    )

    resolved = runtime.resolve(
        StageDefinition(
            id="requirements",
            workflow_mode="agentscope.role-turn",
            bindings={"actor": "hermes-pm"},
        )
    )

    binding = resolved.nodes[0].provider_binding
    assert binding is not None
    assert binding.provider.provider_id == "pm-agent"
    assert binding.site.reference == "requirements.actor"
    assert binding.verify()


def test_workflow_runtime_rejects_provider_without_requested_capability() -> None:
    incompatible = CapabilityProviderManifest.create(
        provider_id="designer",
        provider_revision="1",
        capabilities=(ProviderCapability(id="design.review", version="1.0.0"),),
        workflow_modes=("agentscope.role-turn",),
    )
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        provider_resolver=StaticProviderResolver({"requirements.actor": incompatible}),
        adapters={"agentscope.role-turn": RoleTurnWorkflow()},
    )

    with pytest.raises(WorkflowBindingError, match="does not provide hermes-pm"):
        runtime.resolve(
            StageDefinition(
                id="requirements",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-pm"},
            )
        )


def test_workflow_runtime_rejects_missing_provider_assignment_for_binding_site() -> None:
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        provider_resolver=StaticProviderResolver({}),
        adapters={"agentscope.role-turn": RoleTurnWorkflow()},
    )

    with pytest.raises(
        WorkflowBindingError,
        match=r"provider assignment missing for requirements\.actor",
    ):
        runtime.resolve(
            StageDefinition(
                id="requirements",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-pm"},
            )
        )


def test_workflow_runtime_rejects_missing_or_unknown_binding_slots() -> None:
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        adapters={"agentscope.role-turn": RoleTurnWorkflow()},
    )

    with pytest.raises(WorkflowBindingError, match="missing.*actor"):
        runtime.resolve(
            StageDefinition(
                id="requirements",
                workflow_mode="agentscope.role-turn",
                bindings={"reviewer": "hermes-reviewer"},
            )
        )


async def test_agentscope_role_turn_executes_one_stage_scoped_capability_turn() -> None:
    capability_runtime = CapabilityResolver()
    adapter = AgentScopeRoleTurnAdapter(message_codec=TextMessageCodec())
    runtime = DefaultWorkflowRuntime(
        capability_runtime=capability_runtime,
        adapters={adapter.manifest.mode_id: adapter},
    )
    resolved = runtime.resolve(
        StageDefinition(
            id="requirements",
            workflow_mode="agentscope.role-turn",
            bindings={"actor": "hermes-pm"},
        )
    )

    result = await runtime.execute(
        resolved,
        StageExecutionSpec(
            journey_id="journey-1",
            attempt_id="attempt-1",
            objective="Clarify the backend requirement",
        ),
    )

    assert result == StageResult(
        status="succeeded",
        output="completed:Clarify the backend requirement",
    )


def test_journey_resolution_freezes_all_stages_without_executing_them() -> None:
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        adapters={"agentscope.role-turn": RoleTurnWorkflow()},
    )

    resolved = runtime.resolve_journey(
        JourneyDefinition(
            id="planning",
            version="3.0.0",
            steps=(
                StageDefinition(
                    id="requirements",
                    workflow_mode="agentscope.role-turn",
                    bindings={"actor": "hermes-pm"},
                ),
                StageDefinition(
                    id="tasking",
                    workflow_mode="agentscope.role-turn",
                    bindings={"actor": "hermes-admin"},
                ),
                ApprovalGateDefinition(id="approve-plan", subject_kind="delivery-plan"),
            ),
        )
    )

    assert resolved.order == ("requirements", "tasking", "approve-plan")
    assert [stage.stage_id for stage in resolved.stages] == ["requirements", "tasking"]
    assert resolved.gates[0].subject_kind == "delivery-plan"


def test_journey_resolution_freezes_graph_and_loop_stages() -> None:
    runtime = DefaultWorkflowRuntime(
        capability_runtime=CapabilityResolver(),
        adapters={"agentscope.role-turn": RoleTurnWorkflow()},
    )

    resolved = runtime.resolve_journey(
        JourneyDefinition(
            id="review-loop",
            version="4.0.0",
            nodes=(
                StageDefinition(
                    id="plan",
                    workflow_mode="agentscope.role-turn",
                    bindings={"actor": "hermes-pm"},
                ),
                LoopDefinition(
                    id="review-until-approved",
                    nodes=(
                        StageDefinition(
                            id="review",
                            workflow_mode="agentscope.role-turn",
                            bindings={"actor": "hermes-reviewer"},
                        ),
                    ),
                    edges=(),
                    policy=LoopPolicyDefinition(
                        exit_condition="review-approved",
                        max_iterations=2,
                        timeout_seconds=120,
                        on_exhausted="needs_attention",
                    ),
                ),
            ),
            edges=(
                JourneyEdgeDefinition(source="plan", target="review-until-approved"),
            ),
        )
    )

    assert resolved.order == ("plan", "review-until-approved")
    assert resolved.entry_node_ids == ("plan",)
    assert len(resolved.graph_fingerprint) == 64
    assert resolved.stages[0].stage_id == "plan"
    assert resolved.loops[0].node_id == "review-until-approved"
    assert resolved.loops[0].stages[0].stage_id == "review"
    assert resolved.loops[0].policy.max_iterations == 2


async def test_code_delivery_delegates_one_autonomous_turn_to_codex_capability(
    tmp_path: Any,
) -> None:
    capability_runtime = CapabilityResolver()
    adapter = CodeDeliveryWorkflowAdapter()
    runtime = DefaultWorkflowRuntime(
        capability_runtime=capability_runtime,
        adapters={adapter.manifest.mode_id: adapter},
    )
    resolved = runtime.resolve(
        StageDefinition(
            id="delivery",
            workflow_mode="code-delivery",
            bindings={"developer": "codex-backend"},
        )
    )

    result = await runtime.execute(
        resolved,
        StageExecutionSpec(
            journey_id="journey-1",
            attempt_id="attempt-delivery",
            objective="Implement the approved task",
            workspace=str(tmp_path),
        ),
    )

    assert result.status == "succeeded"
    assert result.output == "completed:Implement the approved task"

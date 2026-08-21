"""Thin single-turn code delivery Workflow.

The Capability owns autonomous code execution.  Product-provided validators
own path, secret and deterministic-test policies after the turn completes.
"""

from __future__ import annotations

from typing import Any

from acwm.domain import (
    AgentTurn,
    CapabilityFeature,
    ResolvedStage,
    StageExecutionSpec,
    StageResult,
    StageRunSpec,
    WorkflowBindingSlot,
    WorkflowManifest,
)


class CodeDeliveryWorkflowAdapter:
    manifest = WorkflowManifest(
        mode_id="code-delivery",
        mode_version="1.0.0",
        adapter_type="acwm.code-delivery",
        adapter_version="1.0.0",
        resumable=False,
        bindings={
            "developer": WorkflowBindingSlot(
                required_features=frozenset(
                    {
                        CapabilityFeature.TEXT_FINAL,
                        CapabilityFeature.CWD_BINDING,
                        CapabilityFeature.REMOTE_STOP,
                    }
                ),
                optional_features=frozenset({CapabilityFeature.TOOL_EVENTS}),
            )
        },
    )

    async def execute(
        self,
        spec: StageExecutionSpec,
        stage: ResolvedStage,
        capability_runtime: Any,
    ) -> StageResult:
        if spec.workspace is None:
            raise ValueError("Code delivery requires a bound workspace")
        developer = next(node for node in stage.nodes if node.slot == "developer")
        instruction = spec.objective
        if spec.handoff is not None:
            instruction += "\n\nApproved handoff:\n" + spec.handoff.model_dump_json(indent=2)
        run_spec = StageRunSpec(
            journey_id=spec.journey_id,
            stage_id=stage.stage_id,
            attempt_id=spec.attempt_id,
            workflow_mode=stage.workflow.mode_id,
            capability=developer.capability,
            objective=spec.objective,
            workspace=spec.workspace,
            artifacts=spec.artifacts,
            handoff=spec.handoff,
        )
        async with capability_runtime.stage(run_spec) as exchange:
            turn = await exchange.turn(
                AgentTurn(purpose="implement", instruction=instruction)
            )
        return StageResult(
            status="succeeded",
            output=turn.text,
            metrics=turn.metrics,
        )


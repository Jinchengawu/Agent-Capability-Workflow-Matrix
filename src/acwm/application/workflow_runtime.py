"""Workflow-agnostic Stage compatibility resolution."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from acwm.domain import (
    ApprovalGateDefinition,
    JourneyDefinition,
    ResolvedCapability,
    ResolvedJourney,
    ResolvedNode,
    ResolvedStage,
    ResolvedWorkflow,
    StageDefinition,
    StageExecutionSpec,
    StageResult,
    StageValidationReport,
    WorkflowManifest,
    WorkflowRequirements,
)


class WorkflowRuntimeError(RuntimeError):
    code = "workflow_runtime_error"


class WorkflowNotFoundError(WorkflowRuntimeError):
    code = "workflow_not_found"


class WorkflowBindingError(WorkflowRuntimeError):
    code = "workflow_binding_invalid"


class StageValidationError(WorkflowRuntimeError):
    code = "stage_output_invalid"


class CapabilityResolver(Protocol):
    def resolve(
        self, capability_id: str, requirements: WorkflowRequirements
    ) -> ResolvedCapability: ...

    def stage(self, spec: Any) -> AbstractAsyncContextManager[Any]: ...


class WorkflowAdapter(Protocol):
    manifest: WorkflowManifest

    async def execute(
        self,
        spec: StageExecutionSpec,
        stage: ResolvedStage,
        capability_runtime: CapabilityResolver,
    ) -> StageResult: ...


class StageOutputValidator(Protocol):
    async def validate(
        self, stage: ResolvedStage, result: StageResult
    ) -> StageValidationReport: ...


class DefaultWorkflowRuntime:
    """Resolve a declarative Stage without observing Workflow internals."""

    def __init__(
        self,
        *,
        capability_runtime: CapabilityResolver,
        adapters: Mapping[str, WorkflowAdapter],
        validators: Mapping[str, StageOutputValidator] | None = None,
    ) -> None:
        self.capability_runtime = capability_runtime
        self.adapters = dict(adapters)
        self.validators = dict(validators or {})

    def resolve(self, stage: StageDefinition) -> ResolvedStage:
        adapter = self.adapters.get(stage.workflow_mode)
        if adapter is None:
            raise WorkflowNotFoundError(stage.workflow_mode)
        if stage.output_validator is not None and stage.output_validator not in self.validators:
            raise WorkflowBindingError(
                f"unknown Stage output validator: {stage.output_validator}"
            )
        manifest = adapter.manifest
        declared = set(stage.bindings)
        required = {name for name, slot in manifest.bindings.items() if slot.required}
        allowed = set(manifest.bindings)
        missing = sorted(required - declared)
        unknown = sorted(declared - allowed)
        if missing or unknown:
            details = []
            if missing:
                details.append("missing slots: " + ", ".join(missing))
            if unknown:
                details.append("unknown slots: " + ", ".join(unknown))
            raise WorkflowBindingError("; ".join(details))

        nodes = tuple(
            ResolvedNode(
                node_id=f"{stage.id}:{slot_name}",
                slot=slot_name,
                workflow_mode=manifest.mode_id,
                workflow_version=manifest.mode_version,
                capability=self.capability_runtime.resolve(
                    capability_id,
                    manifest.bindings[slot_name].requirements(
                        manifest.mode_id, manifest.mode_version
                    ),
                ),
            )
            for slot_name, capability_id in stage.bindings.items()
        )
        return ResolvedStage(
            stage_id=stage.id,
            workflow=ResolvedWorkflow.from_manifest(manifest),
            nodes=nodes,
            output_validator=stage.output_validator,
        )

    def resolve_journey(self, definition: JourneyDefinition) -> ResolvedJourney:
        return ResolvedJourney(
            journey_id=definition.id,
            journey_version=definition.version,
            order=tuple(step.id for step in definition.steps),
            stages=tuple(
                self.resolve(step)
                for step in definition.steps
                if isinstance(step, StageDefinition)
            ),
            gates=tuple(
                step
                for step in definition.steps
                if isinstance(step, ApprovalGateDefinition)
            ),
        )

    async def execute(
        self, stage: ResolvedStage, spec: StageExecutionSpec
    ) -> StageResult:
        adapter = self.adapters.get(stage.workflow.mode_id)
        if adapter is None:
            raise WorkflowNotFoundError(stage.workflow.mode_id)
        result = await adapter.execute(spec, stage, self.capability_runtime)
        if stage.output_validator is None:
            return result
        report = await self.validators[stage.output_validator].validate(stage, result)
        if report.status == "failed":
            raise StageValidationError(report.summary)
        return result.model_copy(update={"validation": report})

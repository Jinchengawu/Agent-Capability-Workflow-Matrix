"""Workflow-side manifests and immutable Stage resolution snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from .capability import ArtifactRequirement, CapabilityFeature, WorkflowRequirements
from .contracts import ArtifactRef, HandoffEnvelope, ImmutableModel
from .execution import ResolvedNode
from .journey_definition import (
    ApprovalGateDefinition,
    JourneyEdgeDefinition,
    LoopPolicyDefinition,
)


class WorkflowBindingSlot(ImmutableModel):
    required_features: frozenset[CapabilityFeature]
    optional_features: frozenset[CapabilityFeature] = frozenset()
    required: bool = True
    input_artifacts: tuple[ArtifactRequirement, ...] = ()
    output_artifacts: tuple[ArtifactRequirement, ...] = ()

    def requirements(self, mode_id: str, mode_version: str) -> WorkflowRequirements:
        return WorkflowRequirements(
            mode_id=mode_id,
            mode_version=mode_version,
            required=self.required_features,
            optional=self.optional_features,
            input_artifacts=self.input_artifacts,
            output_artifacts=self.output_artifacts,
        )


class WorkflowManifest(ImmutableModel):
    mode_id: str
    mode_version: str
    adapter_type: str
    adapter_version: str
    resumable: bool
    bindings: dict[str, WorkflowBindingSlot]


class ResolvedWorkflow(ImmutableModel):
    mode_id: str
    mode_version: str
    adapter_type: str
    adapter_version: str
    resumable: bool
    manifest_fingerprint: str
    resolution_schema_version: str = "2.0"

    @classmethod
    def from_manifest(cls, manifest: WorkflowManifest) -> ResolvedWorkflow:
        payload = manifest.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return cls(
            mode_id=manifest.mode_id,
            mode_version=manifest.mode_version,
            adapter_type=manifest.adapter_type,
            adapter_version=manifest.adapter_version,
            resumable=manifest.resumable,
            manifest_fingerprint=hashlib.sha256(encoded).hexdigest(),
        )


class ResolvedStage(ImmutableModel):
    stage_id: str
    workflow: ResolvedWorkflow
    nodes: tuple[ResolvedNode, ...]
    output_validator: str | None = None


class ResolvedLoop(ImmutableModel):
    node_id: str
    order: tuple[str, ...]
    entry_node_ids: tuple[str, ...]
    exit_node_ids: tuple[str, ...]
    edges: tuple[JourneyEdgeDefinition, ...]
    stages: tuple[ResolvedStage, ...]
    gates: tuple[ApprovalGateDefinition, ...]
    policy: LoopPolicyDefinition


class ResolvedJourney(ImmutableModel):
    journey_id: str
    journey_version: str
    order: tuple[str, ...]
    stages: tuple[ResolvedStage, ...]
    gates: tuple[ApprovalGateDefinition, ...]
    edges: tuple[JourneyEdgeDefinition, ...] = ()
    entry_node_ids: tuple[str, ...] = ()
    exit_node_ids: tuple[str, ...] = ()
    loops: tuple[ResolvedLoop, ...] = ()
    graph_fingerprint: str | None = None


class StageExecutionSpec(ImmutableModel):
    journey_id: str
    attempt_id: str
    objective: str
    workspace: str | None = None
    handoff: HandoffEnvelope | None = None
    artifacts: tuple[ArtifactRef, ...] = ()


class StageValidationReport(ImmutableModel):
    policy_id: str
    status: Literal["passed", "failed"]
    summary: str
    artifact: ArtifactRef | None = None


class StageResult(ImmutableModel):
    status: Literal["succeeded", "failed"]
    output: str
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)
    evidence: tuple[dict[str, Any], ...] = ()
    validation: StageValidationReport | None = None

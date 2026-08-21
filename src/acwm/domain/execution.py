"""Execution snapshots exposed by the ACWM control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from .contracts import ArtifactRef, HandoffEnvelope, ImmutableModel
from .state import AttemptStatus, GateStatus, JourneyStatus, StageStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkflowMode(ImmutableModel):
    id: str
    version: str
    description: str
    resumable: bool


class VerificationCommand(ImmutableModel):
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int = Field(default=120, ge=1, le=3600)


class RepositorySpec(ImmutableModel):
    path: str
    base_ref: str


class ResolvedNode(ImmutableModel):
    node_id: str
    capability_id: str
    capability_version: str
    workflow_mode: str
    workflow_version: str
    policy_version: str = "1.0"


class StageSnapshot(ImmutableModel):
    id: str
    status: StageStatus = StageStatus.QUEUED
    resolved_node: ResolvedNode
    current_attempt_id: str | None = None


class GateSnapshot(ImmutableModel):
    id: str
    status: GateStatus = GateStatus.PENDING
    revision: int = 0
    plan_hash: str | None = None


class AttemptSnapshot(ImmutableModel):
    id: str
    stage_id: str
    status: AttemptStatus
    session_id: str
    started_at: datetime
    finished_at: datetime | None = None
    retries_attempt_id: str | None = None
    resumes_attempt_id: str | None = None
    checkpoint_thread_id: str | None = None
    error: str | None = None


class PermissionSnapshot(ImmutableModel):
    id: str
    session_id: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    revision: int = 1
    request: dict[str, Any]


class JourneySnapshot(ImmutableModel):
    id: str
    definition_id: str
    capability_id: str
    objective: str
    repository: RepositorySpec
    base_sha: str | None = None
    worktree_path: str | None = None
    status: JourneyStatus = JourneyStatus.QUEUED
    revision: int = 0
    current_stage_id: str | None = None
    stages: tuple[StageSnapshot, ...]
    gates: tuple[GateSnapshot, ...]
    attempts: tuple[AttemptSnapshot, ...] = ()
    permissions: tuple[PermissionSnapshot, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    verification_commands: tuple[VerificationCommand, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExecutionEvent(ImmutableModel):
    schema_version: str = "1.0"
    event_id: int
    journey_id: str
    type: str
    timestamp: datetime
    entity_type: str
    entity_id: str
    payload: dict[str, Any]


class NodeRequest(ImmutableModel):
    attempt_id: str
    journey_id: str
    stage_id: str
    capability_id: str
    session_id: str
    cwd: str
    objective: str
    handoff: HandoffEnvelope | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    verification_commands: tuple[VerificationCommand, ...] = ()
    resume: bool = False
    max_repairs: int = Field(default=2, ge=0, le=10)


class NodeResult(ImmutableModel):
    status: Literal["succeeded", "failed"]
    output: str
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
    evidence: tuple[dict[str, Any], ...] = ()

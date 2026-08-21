"""Provider-neutral contracts exchanged through the Capability Runtime seam."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .capability import ResolvedCapability
from .contracts import ArtifactRef, HandoffEnvelope, ImmutableModel
from .execution import utc_now


class StageRunSpec(ImmutableModel):
    journey_id: str
    stage_id: str
    attempt_id: str
    workflow_mode: str
    capability: ResolvedCapability
    objective: str
    workspace: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    handoff: HandoffEnvelope | None = None


class AgentTurn(ImmutableModel):
    purpose: str
    instruction: str
    expected_output: Literal["text", "json"] = "text"


class TurnResult(ImmutableModel):
    text: str
    structured: dict[str, Any] | None = None
    metrics: dict[str, float] = Field(default_factory=dict)


class CapabilityEvent(ImmutableModel):
    schema_version: str = "1.0"
    journey_id: str
    stage_id: str
    attempt_id: str
    sequence: int = Field(ge=1)
    type: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)
    native_metadata: dict[str, Any] | None = None


class PermissionDecision(ImmutableModel):
    attempt_id: str
    request_id: str
    revision: int
    decision: Literal["approve", "reject"]


class StopRequested(ImmutableModel):
    attempt_id: str
    reason: str


class SignalReceipt(ImmutableModel):
    disposition: Literal[
        "accepted",
        "duplicate",
        "unsupported",
        "not_running",
        "unknown_permission",
        "already_terminal",
    ]

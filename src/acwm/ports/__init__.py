"""Infrastructure boundaries used by the ACWM application core."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from acwm.domain import (
    AgentTurn,
    NodeRequest,
    NodeResult,
    PermissionDecision,
    ResolvedCapability,
    SignalReceipt,
    StageRunSpec,
    StopRequested,
    TurnResult,
    WorkflowMode,
    WorkflowRequirements,
)


class CapabilityExchange(Protocol):
    async def turn(self, turn: AgentTurn) -> TurnResult: ...


class CapabilityRuntime(Protocol):
    def resolve(
        self, capability_id: str, requirements: WorkflowRequirements
    ) -> ResolvedCapability: ...

    def stage(self, spec: StageRunSpec) -> AbstractAsyncContextManager[CapabilityExchange]: ...

    async def signal(self, command: PermissionDecision | StopRequested) -> SignalReceipt: ...


# Temporary v0.1 compatibility shim while the application service is upgraded.
@dataclass(frozen=True, slots=True)
class CapabilityInvocation:
    capability_id: str
    session_id: str
    cwd: Path
    purpose: str
    prompt: str


@dataclass(frozen=True, slots=True)
class TransportResult:
    output: str


class CapabilityTransport(Protocol):
    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult: ...

    async def cancel(self, session_id: str) -> None: ...


class WorkflowAdapter(Protocol):
    mode: WorkflowMode

    async def execute(self, request: NodeRequest) -> NodeResult: ...

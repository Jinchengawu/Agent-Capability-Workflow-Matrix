"""Infrastructure boundaries used by the ACWM application core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from acwm.domain import NodeRequest, NodeResult, WorkflowMode


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

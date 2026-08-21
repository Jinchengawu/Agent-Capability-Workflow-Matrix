"""Synchronous request/response HTTP Agent Adapter."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from acwm.config import HttpSyncConfig
from acwm.domain import (
    AdapterManifest,
    AgentTurn,
    CapabilityFeature,
    SignalReceipt,
    StageRunSpec,
    TurnResult,
)


class CapabilityAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class _WireOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class _WireError(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: str
    message: str


class _WireResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    invocation_id: str
    status: Literal["succeeded", "failed"]
    output: _WireOutput
    metrics: dict[str, float] = Field(default_factory=dict)
    error: _WireError | None = None


class _HttpExchange:
    def __init__(self, owner: HttpSyncCapabilityAdapter, spec: StageRunSpec) -> None:
        self.owner = owner
        self.spec = spec
        self.turn_count = 0

    async def turn(self, turn: AgentTurn) -> TurnResult:
        self.turn_count += 1
        if self.turn_count > 1:
            raise CapabilityAdapterError(
                "invocation_rejected", "http.sync supports one turn per Stage"
            )
        invocation_id = f"{self.spec.attempt_id}:{turn.purpose}:{self.turn_count}"
        headers: dict[str, str] = {}
        if self.owner.config.bearer_token_env:
            token = os.environ.get(self.owner.config.bearer_token_env)
            if token is None:
                raise CapabilityAdapterError(
                    "required_environment_missing",
                    f"Missing environment variable: {self.owner.config.bearer_token_env}",
                )
            headers["Authorization"] = f"Bearer {token}"
        body = {
            "schema_version": "1.0",
            "invocation_id": invocation_id,
            "capability_id": self.spec.capability.capability_id,
            "purpose": turn.purpose,
            "instruction": turn.instruction,
            "context": {
                "journey_id": self.spec.journey_id,
                "stage_id": self.spec.stage_id,
                "attempt_id": self.spec.attempt_id,
                "objective": self.spec.objective,
            },
        }
        try:
            response = await self.owner.client.post(
                str(self.owner.config.endpoint),
                json=body,
                headers=headers,
                timeout=self.owner.config.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise CapabilityAdapterError(
                "invocation_timeout", "HTTP Agent invocation timed out", retryable=True
            ) from error
        except httpx.RequestError as error:
            raise CapabilityAdapterError(
                "transport_unavailable", "HTTP Agent is unavailable", retryable=True
            ) from error
        if response.status_code >= 500:
            raise CapabilityAdapterError(
                "transport_unavailable",
                f"HTTP Agent returned {response.status_code}",
                retryable=True,
            )
        if response.status_code >= 400:
            raise CapabilityAdapterError(
                "invocation_rejected", f"HTTP Agent returned {response.status_code}"
            )
        try:
            parsed = _WireResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise CapabilityAdapterError(
                "protocol_violation", "HTTP Agent returned an invalid response"
            ) from error
        if parsed.invocation_id != invocation_id:
            raise CapabilityAdapterError(
                "protocol_violation", "HTTP Agent returned a mismatched invocation_id"
            )
        if parsed.status == "failed":
            message = parsed.error.message if parsed.error else "HTTP Agent failed"
            raise CapabilityAdapterError("invocation_rejected", message)
        return TurnResult(text=parsed.output.text, metrics=parsed.metrics)


class HttpSyncCapabilityAdapter:
    manifest = AdapterManifest(
        adapter_type="http.sync",
        adapter_version="0.2.0",
        features=frozenset({CapabilityFeature.TEXT_FINAL}),
    )

    def __init__(self, config: HttpSyncConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.client = client or httpx.AsyncClient()

    @asynccontextmanager
    async def stage(self, spec: StageRunSpec, _emit: Any) -> Any:
        if spec.workspace is not None:
            raise CapabilityAdapterError(
                "invocation_rejected", "http.sync does not support workspace binding"
            )
        yield _HttpExchange(self, spec)

    async def signal(self, _command: Any) -> SignalReceipt:
        return SignalReceipt(disposition="unsupported")

    async def close(self) -> None:
        await self.client.aclose()

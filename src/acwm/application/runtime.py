"""Deep runtime module that resolves and executes provider-neutral Capabilities."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

from acwm.config import CapabilityCatalog
from acwm.domain import (
    AgentTurn,
    CapabilityEvent,
    CapabilityFeature,
    PermissionDecision,
    ResolvedCapability,
    SignalReceipt,
    StageRunSpec,
    StopRequested,
    TurnResult,
    WorkflowRequirements,
)


class CapabilityRuntimeError(RuntimeError):
    code = "capability_runtime_error"


class CapabilityNotFoundError(CapabilityRuntimeError):
    code = "capability_not_found"


class WorkflowIncompatibleError(CapabilityRuntimeError):
    code = "workflow_incompatible"

    def __init__(self, capability_id: str, missing_features: frozenset[CapabilityFeature]) -> None:
        self.capability_id = capability_id
        self.missing_features = missing_features
        super().__init__(
            f"Capability {capability_id} lacks required features: "
            + ", ".join(sorted(missing_features))
        )


class EventPublicationError(CapabilityRuntimeError):
    code = "event_publication_failed"


class DefaultCapabilityRuntime:
    def __init__(
        self,
        *,
        catalog: CapabilityCatalog,
        adapters: Mapping[str, Any],
        event_sink: Any,
    ) -> None:
        self.catalog = catalog
        self.adapters = dict(adapters)
        self.event_sink = event_sink
        self._active: dict[str, _StageExecution] = {}

    def resolve(self, capability_id: str, requirements: WorkflowRequirements) -> ResolvedCapability:
        descriptor = self.catalog.descriptors.get(capability_id)
        adapter = self.adapters.get(capability_id)
        if descriptor is None or adapter is None:
            raise CapabilityNotFoundError(capability_id)
        manifest = adapter.manifest
        missing = requirements.required - manifest.features
        if missing:
            raise WorkflowIncompatibleError(capability_id, frozenset(missing))
        config = self.catalog.adapter_configs[capability_id]
        return ResolvedCapability(
            capability_id=descriptor.id,
            capability_version=descriptor.version,
            adapter_type=manifest.adapter_type,
            adapter_version=manifest.adapter_version,
            features=manifest.features,
            required_features=requirements.required,
            config_fingerprint=self._fingerprint(config.model_dump(mode="json")),
            policy_version=descriptor.policy.version,
            policy_fingerprint=self._fingerprint(descriptor.policy.model_dump(mode="json")),
        )

    @asynccontextmanager
    async def stage(self, spec: StageRunSpec) -> AsyncIterator[_RuntimeExchange]:
        if spec.attempt_id in self._active:
            raise CapabilityRuntimeError(f"Attempt is already running: {spec.attempt_id}")
        adapter = self.adapters.get(spec.capability.capability_id)
        if adapter is None:
            raise CapabilityNotFoundError(spec.capability.capability_id)
        execution = _StageExecution(self, spec, adapter)
        self._active[spec.attempt_id] = execution
        try:
            async with execution.open() as exchange:
                yield exchange
        finally:
            self._active.pop(spec.attempt_id, None)

    async def signal(self, command: PermissionDecision | StopRequested) -> SignalReceipt:
        execution = self._active.get(command.attempt_id)
        if execution is None:
            return SignalReceipt(disposition="not_running")
        return cast(SignalReceipt, await execution.adapter.signal(command))

    async def close(self) -> None:
        for adapter in self.adapters.values():
            close = getattr(adapter, "close", None)
            if close is not None:
                await close()

    async def _publish(self, event: CapabilityEvent) -> None:
        if self.event_sink is None:
            return
        try:
            result = self.event_sink(event)
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            raise EventPublicationError("Capability event publication failed") from error

    @staticmethod
    def _fingerprint(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class _RuntimeExchange:
    def __init__(self, execution: _StageExecution, adapter_exchange: Any) -> None:
        self.execution = execution
        self.adapter_exchange = adapter_exchange

    async def turn(self, turn: AgentTurn) -> TurnResult:
        await self.execution.emit("capability.turn.started", {"purpose": turn.purpose})
        result = await self.adapter_exchange.turn(turn)
        if (
            result.text
            and CapabilityFeature.TEXT_STREAM not in self.execution.spec.capability.features
        ):
            await self.execution.emit(
                "capability.output.delta", {"purpose": turn.purpose, "text": result.text}
            )
        await self.execution.emit("capability.turn.completed", {"purpose": turn.purpose})
        return cast(TurnResult, result)


class _StageExecution:
    def __init__(self, runtime: DefaultCapabilityRuntime, spec: StageRunSpec, adapter: Any) -> None:
        self.runtime = runtime
        self.spec = spec
        self.adapter = adapter
        self.sequence = 0
        self.terminal = False

    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        native_metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.terminal:
            raise CapabilityRuntimeError("Capability event emitted after terminal event")
        if event_type in {
            "capability.run.completed",
            "capability.run.failed",
            "capability.run.cancelled",
        }:
            self.terminal = True
        self.sequence += 1
        await self.runtime._publish(
            CapabilityEvent(
                journey_id=self.spec.journey_id,
                stage_id=self.spec.stage_id,
                attempt_id=self.spec.attempt_id,
                sequence=self.sequence,
                type=event_type,
                payload=payload or {},
                native_metadata=self._sanitize_native(native_metadata),
            )
        )

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_RuntimeExchange]:
        await self.emit("capability.run.started")
        try:
            async with self.adapter.stage(self.spec, self.emit) as adapter_exchange:
                yield _RuntimeExchange(self, adapter_exchange)
        except EventPublicationError:
            try:
                await self.adapter.signal(
                    StopRequested(
                        attempt_id=self.spec.attempt_id,
                        reason="event_publication_failed",
                    )
                )
            except Exception:
                pass
            raise
        except BaseException as error:
            event_type = (
                "capability.run.cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "capability.run.failed"
            )
            await self.emit(event_type, {"error": str(error)})
            raise
        else:
            await self.emit("capability.run.completed")

    @staticmethod
    def _sanitize_native(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None

        def redact(item: Any) -> Any:
            if isinstance(item, dict):
                return {
                    key: (
                        "[REDACTED]"
                        if any(
                            marker in key.lower().replace("-", "_")
                            for marker in (
                                "secret",
                                "password",
                                "token",
                                "api_key",
                                "authorization",
                            )
                        )
                        else redact(child)
                    )
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [redact(child) for child in item]
            if isinstance(item, str):
                return item[:2048]
            return item

        return cast(dict[str, Any], redact(value))

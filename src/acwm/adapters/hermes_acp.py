"""Hermes Agent Adapter over its public ACP stdio protocol."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import acp
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    DeniedOutcome,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    TextContentBlock,
)

from acwm.config import HermesACPConfig
from acwm.domain import (
    AdapterManifest,
    AgentTurn,
    CapabilityFeature,
    CapabilityPolicy,
    PermissionDecision,
    SignalReceipt,
    StageRunSpec,
    StopRequested,
    TurnResult,
)

EventEmitter = Callable[[str, dict[str, Any] | None, dict[str, Any] | None], Awaitable[None]]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"secret|password|token|api.?key|authorization", key, re.I)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


@dataclass(slots=True)
class _TurnState:
    spec: StageRunSpec
    emit: EventEmitter
    outputs: list[str] = field(default_factory=list)
    output_seen: asyncio.Event = field(default_factory=asyncio.Event)


class _PermissionBroker:
    def __init__(self) -> None:
        self.pending: dict[str, tuple[str, asyncio.Future[bool]]] = {}

    async def ask(self, state: _TurnState, request: dict[str, Any]) -> bool:
        request_id = str(uuid4())
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = (state.spec.attempt_id, future)
        try:
            await state.emit(
                "capability.permission.required",
                {
                    "request_id": request_id,
                    "revision": 1,
                    "request": _redact(request),
                },
                None,
            )
            return bool(await future)
        finally:
            self.pending.pop(request_id, None)

    def decide(self, command: PermissionDecision) -> SignalReceipt:
        pending = self.pending.get(command.request_id)
        if pending is None or pending[0] != command.attempt_id:
            return SignalReceipt(disposition="unknown_permission")
        future = pending[1]
        if future.done():
            return SignalReceipt(disposition="duplicate")
        future.set_result(command.decision == "approve")
        return SignalReceipt(disposition="accepted")

    def cancel_all(self) -> None:
        for _, future in self.pending.values():
            if not future.done():
                future.cancel()


class _ACPClient:
    def __init__(self, owner: HermesACPCapabilityAdapter) -> None:
        self.owner = owner

    def on_connect(self, connection: Any) -> None:
        self.owner._connection = connection

    async def session_update(self, session_id: str, update: Any, **_kwargs: Any) -> None:
        state = self.owner._turn_by_session.get(session_id)
        if state is None:
            return
        if isinstance(update, AgentMessageChunk) and isinstance(update.content, TextContentBlock):
            text = update.content.text
            state.outputs.append(text)
            await state.emit("capability.output.delta", {"text": text}, None)
            state.output_seen.set()
            return
        class_name = type(update).__name__.lower()
        if "toolcall" in class_name:
            native_status = str(getattr(update, "status", "")).lower()
            if "fail" in native_status or "error" in native_status:
                phase = "failed"
            elif "complete" in native_status:
                phase = "completed"
            elif "progress" in native_status or "pending" in native_status:
                phase = "started"
            elif "update" in class_name:
                phase = "completed"
            else:
                phase = "started"
            await state.emit(
                f"capability.tool.{phase}",
                {
                    "call_id": str(getattr(update, "tool_call_id", "unknown")),
                    "name": str(getattr(update, "title", "tool")),
                },
                None,
            )

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: Any,
        **_kwargs: Any,
    ) -> RequestPermissionResponse:
        state = self.owner._turn_by_session.get(session_id)
        approved = False
        if state is not None:
            approved = self.owner._is_policy_allowed(state.spec, tool_call)
            if not approved:
                approved = await self.owner._permissions.ask(
                    state,
                    {
                        "title": getattr(tool_call, "title", "Permission required"),
                        "kind": getattr(tool_call, "kind", None),
                        "locations": [
                            item.model_dump(mode="json")
                            for item in (getattr(tool_call, "locations", None) or [])
                        ],
                        "raw_input": getattr(tool_call, "raw_input", None),
                    },
                )
        if approved:
            option = next((item for item in options if item.kind == "allow_once"), options[0])
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=option.option_id)
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def read_text_file(self, **_kwargs: Any) -> Any:
        raise NotImplementedError("Hermes ACP uses its own filesystem tools")

    async def write_text_file(self, **_kwargs: Any) -> Any:
        raise NotImplementedError("Hermes ACP uses its own filesystem tools")

    async def create_terminal(self, **_kwargs: Any) -> Any:
        raise NotImplementedError("Hermes ACP uses its own terminal tools")

    async def terminal_output(self, **_kwargs: Any) -> Any:
        raise NotImplementedError

    async def release_terminal(self, **_kwargs: Any) -> Any:
        raise NotImplementedError

    async def wait_for_terminal_exit(self, **_kwargs: Any) -> Any:
        raise NotImplementedError

    async def kill_terminal(self, **_kwargs: Any) -> Any:
        raise NotImplementedError


class _HermesExchange:
    def __init__(self, owner: HermesACPCapabilityAdapter, spec: StageRunSpec, emit: EventEmitter):
        self.owner = owner
        self.spec = spec
        self.emit = emit

    async def turn(self, turn: AgentTurn) -> TurnResult:
        return await self.owner._turn(self.spec, self.emit, turn)


class HermesACPCapabilityAdapter:
    manifest = AdapterManifest(
        adapter_type="hermes.acp",
        adapter_version="0.2.0",
        features=frozenset(CapabilityFeature),
    )

    def __init__(self, config: HermesACPConfig, policy: CapabilityPolicy | None = None) -> None:
        self.config = config
        self.policy = policy or CapabilityPolicy()
        self._permissions = _PermissionBroker()
        self._connection: Any = None
        self._process_context: AbstractAsyncContextManager[Any] | None = None
        self._process: Any = None
        self._start_lock = asyncio.Lock()
        self._actual_session_by_attempt: dict[str, str] = {}
        self._turn_by_session: dict[str, _TurnState] = {}
        self._lock_by_session: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def stage(self, spec: StageRunSpec, emit: EventEmitter) -> Any:
        if spec.workspace is None:
            raise RuntimeError("Hermes ACP requires workspace binding")
        try:
            yield _HermesExchange(self, spec, emit)
        finally:
            actual = self._actual_session_by_attempt.pop(spec.attempt_id, None)
            if actual:
                self._turn_by_session.pop(actual, None)
                self._lock_by_session.pop(actual, None)

    async def signal(self, command: PermissionDecision | StopRequested) -> SignalReceipt:
        if isinstance(command, PermissionDecision):
            return self._permissions.decide(command)
        actual = self._actual_session_by_attempt.get(command.attempt_id)
        if actual is None or self._connection is None:
            return SignalReceipt(disposition="not_running")
        await self._connection.cancel(session_id=actual)
        return SignalReceipt(disposition="accepted")

    async def close(self) -> None:
        self._permissions.cancel_all()
        if self._process_context is not None:
            context = self._process_context
            self._process_context = None
            self._connection = None
            await context.__aexit__(None, None, None)

    async def _turn(self, spec: StageRunSpec, emit: EventEmitter, turn: AgentTurn) -> TurnResult:
        connection = await self._ensure_started()
        actual = self._actual_session_by_attempt.get(spec.attempt_id)
        if actual is None:
            response = await connection.new_session(cwd=spec.workspace, mcp_servers=[])
            actual = response.session_id
            self._actual_session_by_attempt[spec.attempt_id] = actual
            self._lock_by_session[actual] = asyncio.Lock()
        async with self._lock_by_session[actual]:
            state = _TurnState(spec=spec, emit=emit)
            self._turn_by_session[actual] = state
            await connection.prompt(
                prompt=[acp.text_block(turn.instruction)],
                session_id=actual,
                message_id=str(uuid4()),
            )
            try:
                await asyncio.wait_for(state.output_seen.wait(), timeout=5)
            except TimeoutError as error:
                raise RuntimeError("Hermes ACP completed without an agent message") from error
            finally:
                self._turn_by_session.pop(actual, None)
            return TurnResult(text="".join(state.outputs).strip())

    async def _ensure_started(self) -> Any:
        if self._is_running():
            return self._connection
        async with self._start_lock:
            if self._is_running():
                return self._connection
            if not self.config.command:
                raise RuntimeError("Hermes ACP command is empty")
            env = os.environ.copy()
            for target, source in self.config.env.items():
                if source not in os.environ:
                    raise RuntimeError(f"Required environment variable is missing: {source}")
                env[target] = os.environ[source]
            context = acp.spawn_agent_process(
                cast(acp.Client, _ACPClient(self)),
                self.config.command[0],
                *self.config.command[1:],
                env=env,
                use_unstable_protocol=True,
            )
            connection, process = await context.__aenter__()
            self._process_context = context
            self._connection = connection
            self._process = process
            await connection.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_info=Implementation(name="acwm", title="ACWM", version="0.2.0"),
            )
            return connection

    def _is_running(self) -> bool:
        return (
            self._connection is not None
            and self._process is not None
            and self._process.returncode is None
        )

    def _is_policy_allowed(self, spec: StageRunSpec, tool_call: Any) -> bool:
        kind = getattr(tool_call, "kind", None)
        if kind in {"read", "search", "think", "fetch"}:
            return True
        if kind in {"edit", "delete", "move"} and self.policy.workspace_edits == "allow":
            root = Path(cast(str, spec.workspace)).resolve()
            locations = getattr(tool_call, "locations", None) or []
            if not locations:
                return False
            for location in locations:
                raw_path = getattr(location, "path", None)
                if not raw_path:
                    return False
                candidate = Path(raw_path)
                if not candidate.is_absolute():
                    candidate = root / candidate
                try:
                    candidate.resolve().relative_to(root)
                except ValueError:
                    return False
            return True
        if kind == "execute":
            raw = getattr(tool_call, "raw_input", None)
            command = raw.get("command", "") if isinstance(raw, dict) else str(raw or "")
            return any(
                command == allowed or command.startswith(f"{allowed} ")
                for allowed in self.policy.command_allowlist
            )
        return False

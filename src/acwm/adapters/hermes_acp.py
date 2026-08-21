"""Hermes Agent Capability transport over the public ACP stdio protocol."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
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

from acwm.domain import CapabilityDescriptor
from acwm.ports import CapabilityInvocation, CapabilityTransport, TransportResult

PermissionHandler = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"secret|password|token|api.?key", key, re.I)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class _PermissionBroker:
    def __init__(self) -> None:
        self.handler: PermissionHandler | None = None
        self.pending: dict[str, asyncio.Future[bool]] = {}

    def set_handler(self, handler: PermissionHandler) -> None:
        self.handler = handler

    async def ask(self, session_id: str, request: dict[str, Any]) -> bool:
        if self.handler is None:
            return False
        request_id = str(uuid4())
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.handler(session_id, request_id, _redact(request))
            return bool(await future)
        finally:
            self.pending.pop(request_id, None)

    def resolve(self, request_id: str, approved: bool) -> bool:
        future = self.pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True


class _ACPClient:
    def __init__(self, owner: HermesACPTransportAdapter) -> None:
        self.owner = owner

    def on_connect(self, conn: Any) -> None:
        self.owner._connection = conn

    async def session_update(self, session_id: str, update: Any, **_kwargs: Any) -> None:
        if isinstance(update, AgentMessageChunk) and isinstance(update.content, TextContentBlock):
            self.owner._outputs.setdefault(session_id, []).append(update.content.text)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: Any,
        **_kwargs: Any,
    ) -> RequestPermissionResponse:
        invocation = self.owner._invocation_by_actual_session.get(session_id)
        approved = False
        if invocation is not None:
            approved = self.owner._is_policy_allowed(invocation, tool_call)
            if not approved:
                approved = await self.owner._permissions.ask(
                    invocation.session_id,
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


class HermesACPTransportAdapter(CapabilityTransport):
    """Maintains one Hermes ACP process and mode-scoped sessions."""

    def __init__(self, descriptor: CapabilityDescriptor) -> None:
        self.descriptor = descriptor
        self._permissions = _PermissionBroker()
        self._connection: Any = None
        self._process_context: AbstractAsyncContextManager[Any] | None = None
        self._process: Any = None
        self._start_lock = asyncio.Lock()
        self._actual_session_by_logical: dict[str, str] = {}
        self._invocation_by_actual_session: dict[str, CapabilityInvocation] = {}
        self._outputs: dict[str, list[str]] = {}

    def set_permission_handler(self, handler: PermissionHandler) -> None:
        self._permissions.set_handler(handler)

    def resolve_permission(self, request_id: str, approved: bool) -> bool:
        return self._permissions.resolve(request_id, approved)

    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult:
        connection = await self._ensure_started()
        actual_session = self._actual_session_by_logical.get(invocation.session_id)
        if actual_session is None:
            response = await connection.new_session(cwd=str(invocation.cwd), mcp_servers=[])
            actual_session = response.session_id
            self._actual_session_by_logical[invocation.session_id] = actual_session
        self._invocation_by_actual_session[actual_session] = invocation
        self._outputs[actual_session] = []
        await connection.prompt(
            prompt=[acp.text_block(invocation.prompt)],
            session_id=actual_session,
            message_id=str(uuid4()),
        )
        output = "".join(self._outputs.get(actual_session, [])).strip()
        if not output:
            raise RuntimeError("Hermes ACP completed without an agent message")
        return TransportResult(output=output)

    async def cancel(self, session_id: str) -> None:
        actual = self._actual_session_by_logical.get(session_id)
        if actual and self._connection is not None:
            await self._connection.cancel(session_id=actual)

    async def close(self) -> None:
        if self._process_context is not None:
            context = self._process_context
            self._process_context = None
            self._connection = None
            await context.__aexit__(None, None, None)

    async def _ensure_started(self) -> Any:
        if (
            self._connection is not None
            and self._process is not None
            and self._process.returncode is None
        ):
            return self._connection
        async with self._start_lock:
            if (
                self._connection is not None
                and self._process is not None
                and self._process.returncode is None
            ):
                return self._connection
            command = self.descriptor.transport.command
            if not command:
                raise RuntimeError("Hermes ACP command is empty")
            env = os.environ.copy()
            for target, source in self.descriptor.transport.env.items():
                if source not in os.environ:
                    raise RuntimeError(f"Required environment variable is missing: {source}")
                env[target] = os.environ[source]
            context = acp.spawn_agent_process(
                cast(acp.Client, _ACPClient(self)),
                command[0],
                *command[1:],
                env=env,
                use_unstable_protocol=True,
            )
            connection, process = await context.__aenter__()
            self._process_context = context
            self._connection = connection
            self._process = process
            await connection.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_info=Implementation(name="acwm", title="ACWM", version="0.1.0"),
            )
            return connection

    def _is_policy_allowed(self, invocation: CapabilityInvocation, tool_call: Any) -> bool:
        kind = getattr(tool_call, "kind", None)
        if kind in {"read", "search", "think", "fetch"}:
            return True
        if (
            kind in {"edit", "delete", "move"}
            and self.descriptor.permissions.workspace_edits == "allow"
        ):
            locations = getattr(tool_call, "locations", None) or []
            if not locations:
                return False
            root = invocation.cwd.resolve()
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
                for allowed in self.descriptor.permissions.command_allowlist
            )
        return False

"""Controlled Codex CLI Capability Adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from acwm.config import CodexCLIConfig
from acwm.domain import (
    AdapterManifest,
    AgentTurn,
    CapabilityFeature,
    SignalReceipt,
    StageRunSpec,
    StopRequested,
    TurnResult,
)

EventEmitter = Callable[[str, dict[str, Any] | None, dict[str, Any] | None], Awaitable[None]]


class CodexCLIError(RuntimeError):
    pass


class _CodexExchange:
    def __init__(
        self,
        owner: CodexCLICapabilityAdapter,
        spec: StageRunSpec,
        emit: EventEmitter,
    ) -> None:
        self.owner = owner
        self.spec = spec
        self.emit = emit
        self.turn_count = 0

    async def turn(self, turn: AgentTurn) -> TurnResult:
        self.turn_count += 1
        if self.turn_count > 1:
            raise CodexCLIError("codex.cli supports one autonomous turn per Stage Attempt")
        return await self.owner._turn(self.spec, self.emit, turn)


class CodexCLICapabilityAdapter:
    manifest = AdapterManifest(
        adapter_type="codex.cli",
        adapter_version="1.0.0",
        features=frozenset(
            {
                CapabilityFeature.TEXT_FINAL,
                CapabilityFeature.CWD_BINDING,
                CapabilityFeature.REMOTE_STOP,
                CapabilityFeature.TOOL_EVENTS,
            }
        ),
    )

    def __init__(self, config: CodexCLIConfig) -> None:
        self.config = config
        self._active: dict[str, asyncio.subprocess.Process] = {}

    @asynccontextmanager
    async def stage(self, spec: StageRunSpec, emit: EventEmitter) -> Any:
        if spec.workspace is None:
            raise CodexCLIError("Codex CLI requires a bound workspace")
        yield _CodexExchange(self, spec, emit)

    async def signal(self, command: Any) -> SignalReceipt:
        if not isinstance(command, StopRequested):
            return SignalReceipt(disposition="unsupported")
        process = self._active.get(command.attempt_id)
        if process is None:
            return SignalReceipt(disposition="not_running")
        process.terminate()
        return SignalReceipt(disposition="accepted")

    async def close(self) -> None:
        processes = tuple(self._active.values())
        for process in processes:
            if process.returncode is None:
                process.terminate()
        if processes:
            await asyncio.gather(*(process.wait() for process in processes))

    async def _turn(
        self, spec: StageRunSpec, emit: EventEmitter, turn: AgentTurn
    ) -> TurnResult:
        command = (
            *self.config.command,
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            self.config.sandbox,
            "-C",
            str(spec.workspace),
            "-",
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=spec.workspace,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active[spec.attempt_id] = process
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(turn.instruction.encode()),
                timeout=self.config.timeout_seconds,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise CodexCLIError("Codex CLI invocation timed out") from error
        finally:
            self._active.pop(spec.attempt_id, None)
        if process.returncode != 0:
            detail = stderr.decode(errors="replace")[-4000:]
            raise CodexCLIError(
                f"Codex CLI exited with {process.returncode}: {detail}"
            )

        final_messages: list[str] = []
        for raw_line in stdout.decode(errors="replace").splitlines():
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") if isinstance(event, dict) else None
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", ""))
            event_type = str(event.get("type", ""))
            if item_type == "agent_message" and event_type == "item.completed":
                final_messages.append(str(item.get("text", "")))
            elif item_type:
                phase = "completed" if event_type == "item.completed" else "started"
                await emit(
                    f"capability.tool.{phase}",
                    {"name": item_type, "item_id": str(item.get("id", ""))},
                    None,
                )
        output = "\n".join(message for message in final_messages if message).strip()
        if not output:
            raise CodexCLIError("Codex CLI completed without a final agent message")
        return TurnResult(text=output)


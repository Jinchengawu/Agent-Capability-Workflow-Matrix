from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import acp
from acp.schema import Implementation, InitializeResponse, NewSessionResponse, PromptResponse


class FakeAgent:
    def __init__(self) -> None:
        self.client: Any = None
        self.sessions: dict[str, dict[str, Any]] = {}

    def on_connect(self, conn: Any) -> None:
        self.client = conn

    async def initialize(self, protocol_version: int, **_kwargs: Any) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(name="fake-acp", version="1.0.0"),
        )

    async def new_session(self, cwd: str, **_kwargs: Any) -> NewSessionResponse:
        session_id = str(uuid4())
        self.sessions[session_id] = {"cwd": cwd, "count": 0}
        return NewSessionResponse(session_id=session_id)

    async def prompt(self, prompt: list[Any], session_id: str, **_kwargs: Any) -> PromptResponse:
        session = self.sessions[session_id]
        session["count"] += 1
        text = prompt[0].text
        await self.client.session_update(
            session_id=session_id,
            update=acp.update_agent_message_text(
                f"cwd={session['cwd']} count={session['count']} prompt={text}"
            ),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **_kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


if __name__ == "__main__":
    asyncio.run(acp.run_agent(FakeAgent(), use_unstable_protocol=True))

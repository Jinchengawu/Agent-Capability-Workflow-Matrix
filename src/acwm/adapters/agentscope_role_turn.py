"""AgentScope message boundary for a single Stage-scoped role turn."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol

from acwm.domain import (
    AgentTurn,
    CapabilityFeature,
    ResolvedStage,
    StageExecutionSpec,
    StageResult,
    StageRunSpec,
    WorkflowBindingSlot,
    WorkflowManifest,
)


class MessageCodec(Protocol):
    def user(self, name: str, content: str) -> Any: ...

    def assistant(self, name: str, content: str) -> Any: ...

    def text(self, message: Any) -> str: ...


class AgentScopeMessageCodec:
    """Lazy AgentScope 2.x codec so Core remains dependency-free."""

    def __init__(self) -> None:
        try:
            messages = import_module("agentscope.message")
        except ImportError as error:
            raise RuntimeError(
                "AgentScope adapter requires the 'agentscope' optional dependency"
            ) from error
        self._assistant_message = messages.AssistantMsg
        self._user_message = messages.UserMsg

    def user(self, name: str, content: str) -> Any:
        return self._user_message(name, content)

    def assistant(self, name: str, content: str) -> Any:
        return self._assistant_message(name, content)

    def text(self, message: Any) -> str:
        return "\n".join(
            str(block.text)
            for block in message.content
            if getattr(block, "type", None) == "text"
        )


class AgentScopeRoleTurnAdapter:
    """Use AgentScope messages while ACWM retains only the Stage result."""

    manifest = WorkflowManifest(
        mode_id="agentscope.role-turn",
        mode_version="1.0.0",
        adapter_type="agentscope",
        adapter_version="2.0.6",
        resumable=False,
        bindings={
            "actor": WorkflowBindingSlot(
                required_features=frozenset({CapabilityFeature.TEXT_FINAL})
            )
        },
    )

    def __init__(self, message_codec: MessageCodec | None = None) -> None:
        self.messages = message_codec or AgentScopeMessageCodec()

    async def execute(
        self,
        spec: StageExecutionSpec,
        stage: ResolvedStage,
        capability_runtime: Any,
    ) -> StageResult:
        actor = next(node for node in stage.nodes if node.slot == "actor")
        incoming = self.messages.user("user", spec.objective)
        instruction = self.messages.text(incoming)
        if spec.handoff is not None:
            instruction += "\n\nHandoff:\n" + spec.handoff.model_dump_json(indent=2)
        run_spec = StageRunSpec(
            journey_id=spec.journey_id,
            stage_id=stage.stage_id,
            attempt_id=spec.attempt_id,
            workflow_mode=stage.workflow.mode_id,
            capability=actor.capability,
            objective=spec.objective,
            workspace=spec.workspace,
            artifacts=spec.artifacts,
            handoff=spec.handoff,
        )
        async with capability_runtime.stage(run_spec) as exchange:
            turn = await exchange.turn(
                AgentTurn(purpose="role_turn", instruction=instruction)
            )
        outgoing = self.messages.assistant(actor.capability.capability_id, turn.text)
        return StageResult(
            status="succeeded",
            output=self.messages.text(outgoing),
            metrics=turn.metrics,
        )

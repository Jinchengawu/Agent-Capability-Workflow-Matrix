"""Workflow semantics built only on the provider-neutral Capability Runtime."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from acwm.domain import (
    AgentTurn,
    CapabilityFeature,
    NodeRequest,
    NodeResult,
    StageRunSpec,
    WorkflowMode,
)
from acwm.ports import CapabilityRuntime


class WorkflowExecutionError(RuntimeError):
    pass


def _stage_spec(request: NodeRequest, workflow_mode: str) -> StageRunSpec:
    workspace = (
        request.workspace if CapabilityFeature.CWD_BINDING in request.capability.features else None
    )
    return StageRunSpec(
        journey_id=request.journey_id,
        stage_id=request.stage_id,
        attempt_id=request.attempt_id,
        workflow_mode=workflow_mode,
        capability=request.capability,
        objective=request.objective,
        workspace=workspace,
    )


class DirectWorkflowAdapter:
    mode = WorkflowMode(
        id="direct",
        version="2.0",
        description="Single autonomous capability turn",
        resumable=False,
        required_features=frozenset({CapabilityFeature.TEXT_FINAL}),
    )

    def __init__(self, runtime: CapabilityRuntime) -> None:
        self.runtime = runtime

    async def execute(self, request: NodeRequest) -> NodeResult:
        async with self.runtime.stage(_stage_spec(request, self.mode.id)) as exchange:
            result = await exchange.turn(
                AgentTurn(
                    purpose="plan",
                    instruction=(
                        "Create a concrete implementation plan for the objective below. "
                        "Do not edit files in this stage.\n\n"
                        f"Objective: {request.objective}"
                    ),
                )
            )
        return NodeResult(status="succeeded", output=result.text, metrics=result.metrics)


class CodeState(TypedDict, total=False):
    objective: str
    implementation_output: str
    verification: list[dict[str, Any]]
    review: dict[str, Any]
    repair_count: int


class LangGraphCodeDeliveryAdapter:
    mode = WorkflowMode(
        id="langgraph.code-delivery",
        version="2.0",
        description="Implement, verify, review, and repair loop",
        resumable=True,
        required_features=frozenset(
            {
                CapabilityFeature.TEXT_FINAL,
                CapabilityFeature.MULTI_TURN,
                CapabilityFeature.CWD_BINDING,
                CapabilityFeature.REMOTE_STOP,
            }
        ),
        optional_features=frozenset(
            {
                CapabilityFeature.TEXT_STREAM,
                CapabilityFeature.PERMISSION,
                CapabilityFeature.TOOL_EVENTS,
            }
        ),
    )

    def __init__(self, runtime: CapabilityRuntime, checkpoint_path: Path) -> None:
        self.runtime = runtime
        self.checkpoint_path = checkpoint_path
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    async def execute(self, request: NodeRequest) -> NodeResult:
        if request.workspace is None:
            raise WorkflowExecutionError("Code delivery requires a workspace")
        cwd = Path(request.workspace)

        async with self.runtime.stage(_stage_spec(request, self.mode.id)) as exchange:

            async def invoke_agent(purpose: str, prompt: str) -> str:
                result = await exchange.turn(AgentTurn(purpose=purpose, instruction=prompt))
                return result.text

            async def implement(state: CodeState) -> dict[str, Any]:
                output = await invoke_agent(
                    "implement",
                    "Implement this objective in the current repository.\n"
                    f"{request.objective}\n\nHandoff:\n"
                    f"{request.handoff.model_dump_json(indent=2) if request.handoff else '{}'}",
                )
                return {"implementation_output": output, "repair_count": 0}

            async def verify(_state: CodeState) -> dict[str, Any]:
                evidence: list[dict[str, Any]] = []
                for command in request.verification_commands:
                    runtime_argv = (
                        (sys.executable, *command.argv[1:])
                        if command.argv[0] == "python"
                        else command.argv
                    )
                    process = None
                    try:
                        process = await asyncio.create_subprocess_exec(
                            *runtime_argv,
                            cwd=cwd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(), timeout=command.timeout_seconds
                        )
                        evidence.append(
                            {
                                "name": command.name,
                                "argv": list(command.argv),
                                "exit_code": process.returncode,
                                "stdout": stdout.decode(errors="replace")[-8000:],
                                "stderr": stderr.decode(errors="replace")[-8000:],
                            }
                        )
                    except TimeoutError:
                        if process is not None:
                            process.kill()
                            await process.wait()
                        evidence.append(
                            {
                                "name": command.name,
                                "argv": list(command.argv),
                                "exit_code": None,
                                "error": "timeout",
                            }
                        )
                return {"verification": evidence}

            async def review(state: CodeState) -> dict[str, Any]:
                raw = await invoke_agent(
                    "review",
                    "Review the implementation and verification evidence. Return only JSON with "
                    "keys accepted (boolean) and summary (string).\n\nEvidence:\n"
                    + json.dumps(state.get("verification", []), ensure_ascii=False),
                )
                try:
                    parsed = json.loads(raw)
                    accepted = bool(parsed.get("accepted"))
                    summary = str(parsed.get("summary", ""))
                except (json.JSONDecodeError, AttributeError):
                    accepted = False
                    summary = "Review did not return the required JSON contract"
                tests_passed = all(
                    item.get("exit_code") == 0 for item in state.get("verification", [])
                )
                return {"review": {"accepted": accepted and tests_passed, "summary": summary}}

            async def repair(state: CodeState) -> dict[str, Any]:
                output = await invoke_agent(
                    "repair",
                    "Repair the implementation using this verification and review evidence:\n"
                    + json.dumps(
                        {
                            "verification": state.get("verification"),
                            "review": state.get("review"),
                        },
                        ensure_ascii=False,
                    ),
                )
                return {
                    "implementation_output": output,
                    "repair_count": int(state.get("repair_count", 0)) + 1,
                }

            def route(state: CodeState) -> Literal["done", "repair", "exhausted"]:
                if state.get("review", {}).get("accepted"):
                    return "done"
                if int(state.get("repair_count", 0)) >= request.max_repairs:
                    return "exhausted"
                return "repair"

            async def exhausted(_state: CodeState) -> dict[str, Any]:
                return {}

            builder = StateGraph(CodeState)
            builder.add_node("implement", implement)
            builder.add_node("verify", verify)  # type: ignore[arg-type]
            builder.add_node("review", review)
            builder.add_node("repair", repair)
            builder.add_node("exhausted", exhausted)  # type: ignore[arg-type]
            builder.add_edge(START, "implement")
            builder.add_edge("implement", "verify")
            builder.add_edge("verify", "review")
            builder.add_conditional_edges(
                "review", route, {"done": END, "repair": "repair", "exhausted": "exhausted"}
            )
            builder.add_edge("repair", "verify")
            builder.add_edge("exhausted", END)

            async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as checkpointer:
                graph = builder.compile(checkpointer=checkpointer)
                state = await graph.ainvoke(
                    None if request.resume else {"objective": request.objective},
                    {
                        "configurable": {
                            "thread_id": request.checkpoint_thread_id or request.attempt_id
                        }
                    },
                )

        if not state.get("review", {}).get("accepted"):
            raise WorkflowExecutionError("Code delivery exhausted its repair budget")
        return NodeResult(
            status="succeeded",
            output=str(state.get("implementation_output", "")),
            evidence=tuple(state.get("verification", [])),
        )


WORKFLOW_MODES = (DirectWorkflowAdapter.mode, LangGraphCodeDeliveryAdapter.mode)

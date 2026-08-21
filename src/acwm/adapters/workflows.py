"""Direct and LangGraph workflow semantics over a reusable capability transport."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from acwm.domain import NodeRequest, NodeResult, WorkflowMode
from acwm.ports import CapabilityInvocation, CapabilityTransport


class WorkflowExecutionError(RuntimeError):
    pass


class DirectWorkflowAdapter:
    mode = WorkflowMode(
        id="direct",
        version="1.0",
        description="Single autonomous capability invocation",
        resumable=False,
    )

    def __init__(self, transport: CapabilityTransport) -> None:
        self.transport = transport

    async def execute(self, request: NodeRequest) -> NodeResult:
        result = await self.transport.invoke(
            CapabilityInvocation(
                capability_id=request.capability_id,
                session_id=request.session_id,
                cwd=Path(request.cwd),
                purpose="plan",
                prompt=(
                    "Create a concrete implementation plan for the objective below. "
                    "Inspect the repository, but do not edit files in this stage.\n\n"
                    f"Objective: {request.objective}"
                ),
            )
        )
        return NodeResult(status="succeeded", output=result.output)


class CodeState(TypedDict, total=False):
    objective: str
    implementation_output: str
    verification: list[dict[str, Any]]
    review: dict[str, Any]
    repair_count: int


class LangGraphCodeDeliveryAdapter:
    mode = WorkflowMode(
        id="langgraph.code-delivery",
        version="1.0",
        description="Implement, verify, review, and repair loop",
        resumable=True,
    )

    def __init__(self, transport: CapabilityTransport, checkpoint_path: Path) -> None:
        self.transport = transport
        self.checkpoint_path = checkpoint_path
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    async def execute(self, request: NodeRequest) -> NodeResult:
        cwd = Path(request.cwd)

        async def invoke_agent(purpose: str, prompt: str) -> str:
            result = await self.transport.invoke(
                CapabilityInvocation(
                    capability_id=request.capability_id,
                    session_id=request.session_id,
                    cwd=cwd,
                    purpose=purpose,
                    prompt=prompt,
                )
            )
            return result.output

        async def implement(state: CodeState) -> dict[str, Any]:
            prompt = (
                "Implement this objective in the current repository.\n"
                f"{request.objective}\n\nHandoff:\n"
                f"{request.handoff.model_dump_json(indent=2) if request.handoff else '{}'}"
            )
            output = await invoke_agent(
                "implement",
                prompt,
            )
            return {"implementation_output": output, "repair_count": 0}

        async def verify(_state: CodeState) -> dict[str, Any]:
            evidence: list[dict[str, Any]] = []
            for command in request.verification_commands:
                try:
                    process = await asyncio.create_subprocess_exec(
                        *command.argv,
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
            tests_passed = all(item.get("exit_code") == 0 for item in state.get("verification", []))
            return {"review": {"accepted": accepted and tests_passed, "summary": summary}}

        async def repair(state: CodeState) -> dict[str, Any]:
            count = int(state.get("repair_count", 0)) + 1
            output = await invoke_agent(
                "repair",
                "Repair the implementation using this verification and review evidence:\n"
                + json.dumps(
                    {"verification": state.get("verification"), "review": state.get("review")},
                    ensure_ascii=False,
                ),
            )
            return {"implementation_output": output, "repair_count": count}

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
                {"configurable": {"thread_id": request.attempt_id}},
            )
        if not state.get("review", {}).get("accepted"):
            raise WorkflowExecutionError("Code delivery exhausted its repair budget")
        return NodeResult(
            status="succeeded",
            output=str(state.get("implementation_output", "")),
            evidence=tuple(state.get("verification", [])),
        )


WORKFLOW_MODES = (DirectWorkflowAdapter.mode, LangGraphCodeDeliveryAdapter.mode)

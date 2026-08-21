import json
from pathlib import Path

import httpx
import pytest

from acwm.adapters.http_sync import HttpSyncCapabilityAdapter
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.config import HttpSyncConfig, load_capabilities
from acwm.domain import (
    AgentTurn,
    CapabilityFeature,
    StageRunSpec,
    WorkflowRequirements,
)


async def test_http_agent_uses_versioned_wire_contract_and_bearer_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        body = seen["body"]
        assert isinstance(body, dict)
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "invocation_id": body["invocation_id"],
                "status": "succeeded",
                "output": {"text": "remote plan"},
                "metrics": {"latency_ms": 12.0},
                "error": None,
            },
        )

    monkeypatch.setenv("HTTP_AGENT_TOKEN", "secret-token")
    config = HttpSyncConfig(
        endpoint="https://agent.example/v1/invoke",
        timeout_seconds=30,
        bearer_token_env="HTTP_AGENT_TOKEN",
    )
    adapter = HttpSyncCapabilityAdapter(
        config, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    config_path = tmp_path / "capabilities.yaml"
    config_path.write_text(
        """
schema_version: "3"
capabilities:
  - id: http-planner
    version: 1.0.0
    adapter:
      type: http.sync
      config: {endpoint: https://agent.example/v1/invoke, bearer_token_env: HTTP_AGENT_TOKEN}
""",
        encoding="utf-8",
    )
    runtime = DefaultCapabilityRuntime(
        catalog=load_capabilities(config_path),
        adapters={"http-planner": adapter},
        event_sink=None,
    )
    resolved = runtime.resolve(
        "http-planner",
        WorkflowRequirements(
            mode_id="direct",
            mode_version="2.0",
            required=frozenset({CapabilityFeature.TEXT_FINAL}),
        ),
    )

    async with runtime.stage(
        StageRunSpec(
            journey_id="journey-1",
            stage_id="plan",
            attempt_id="attempt-1",
            workflow_mode="direct",
            capability=resolved,
            objective="plan the change",
        )
    ) as exchange:
        result = await exchange.turn(AgentTurn(purpose="plan", instruction="make a plan"))
    await runtime.close()

    assert result.text == "remote plan"
    assert seen["authorization"] == "Bearer secret-token"
    assert seen["body"] == {
        "schema_version": "1.0",
        "invocation_id": "attempt-1:plan:1",
        "capability_id": "http-planner",
        "purpose": "plan",
        "instruction": "make a plan",
        "context": {
            "journey_id": "journey-1",
            "stage_id": "plan",
            "attempt_id": "attempt-1",
            "objective": "plan the change",
        },
    }

import sys
from pathlib import Path

from acwm.adapters.codex_cli import CodexCLICapabilityAdapter
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.config import CodexCLIConfig, load_capabilities
from acwm.domain import (
    AgentTurn,
    CapabilityFeature,
    StageRunSpec,
    WorkflowRequirements,
)


async def test_codex_cli_runs_in_bound_workspace_and_returns_final_message(
    tmp_path: Path,
) -> None:
    fake_cli = tmp_path / "fake_codex.py"
    fake_cli.write_text(
        """
import json, os, sys
prompt = sys.stdin.read()
print(json.dumps({"type": "item.completed", "item": {
    "type": "agent_message", "text": f"cwd={os.getcwd()};prompt={prompt}"
}}))
""".strip(),
        encoding="utf-8",
    )
    config = CodexCLIConfig(command=(sys.executable, str(fake_cli)), timeout_seconds=10)
    adapter = CodexCLICapabilityAdapter(config)
    config_path = tmp_path / "capabilities.yaml"
    config_path.write_text(
        f"""
schema_version: "3"
capabilities:
  - id: codex-backend
    version: 1.0.0
    adapter:
      type: codex.cli
      config:
        command: [{sys.executable!r}, {str(fake_cli)!r}]
        timeout_seconds: 10
""".strip(),
        encoding="utf-8",
    )
    runtime = DefaultCapabilityRuntime(
        catalog=load_capabilities(config_path),
        adapters={"codex-backend": adapter},
        event_sink=None,
    )
    resolved = runtime.resolve(
        "codex-backend",
        WorkflowRequirements(
            mode_id="code-delivery",
            mode_version="1.0.0",
            required=frozenset(
                {
                    CapabilityFeature.TEXT_FINAL,
                    CapabilityFeature.CWD_BINDING,
                    CapabilityFeature.REMOTE_STOP,
                }
            ),
        ),
    )

    async with runtime.stage(
        StageRunSpec(
            journey_id="journey-1",
            stage_id="delivery",
            attempt_id="attempt-1",
            workflow_mode="code-delivery",
            capability=resolved,
            objective="implement change",
            workspace=str(tmp_path),
        )
    ) as exchange:
        result = await exchange.turn(
            AgentTurn(purpose="implement", instruction="implement the approved task")
        )
    await runtime.close()

    assert f"cwd={tmp_path}" in result.text
    assert "prompt=implement the approved task" in result.text

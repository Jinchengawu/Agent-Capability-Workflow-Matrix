import os
from pathlib import Path

import pytest

from acwm.adapters import HermesACPCapabilityAdapter
from acwm.adapters.workflows import DirectWorkflowAdapter
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.config import HermesACPConfig, load_capabilities
from acwm.domain import AgentTurn, StageRunSpec


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ACWM_REAL_HERMES") != "1",
    reason="set ACWM_REAL_HERMES=1 and configure Hermes model credentials",
)
async def test_real_hermes_acp_smoke(tmp_path: Path) -> None:
    config_path = tmp_path / "capabilities.yaml"
    config_path.write_text(
        """
schema_version: "2"
capabilities:
  - id: hermes-developer
    version: 1.0.0
    adapter:
      type: hermes.acp
      config: {command: [hermes, acp]}
""",
        encoding="utf-8",
    )
    catalog = load_capabilities(config_path)
    adapter = HermesACPCapabilityAdapter(HermesACPConfig())
    runtime = DefaultCapabilityRuntime(
        catalog=catalog, adapters={"hermes-developer": adapter}, event_sink=None
    )
    resolved = runtime.resolve("hermes-developer", DirectWorkflowAdapter.mode.requirements)
    try:
        async with runtime.stage(
            StageRunSpec(
                journey_id="smoke",
                stage_id="plan",
                attempt_id="attempt",
                workflow_mode="direct",
                capability=resolved,
                objective="smoke",
                workspace=str(tmp_path),
            )
        ) as exchange:
            result = await exchange.turn(
                AgentTurn(purpose="plan", instruction="Reply READY. Do not use tools.")
            )
    finally:
        await runtime.close()

    assert result.text.strip()

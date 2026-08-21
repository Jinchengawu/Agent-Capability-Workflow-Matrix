from pathlib import Path

import pytest

from acwm.config import ConfigurationError, load_capabilities


def test_schema_v2_loads_provider_neutral_capabilities(tmp_path: Path) -> None:
    config = tmp_path / "capabilities.yaml"
    config.write_text(
        """
schema_version: "2"
capabilities:
  - id: hermes-developer
    version: 1.0.0
    labels: [developer, coding]
    adapter:
      type: hermes.acp
      config:
        command: [hermes, acp]
        env: {HERMES_API_KEY: HERMES_API_KEY}
    policy:
      workspace_edits: allow
      command_allowlist: [uv run pytest]
  - id: http-planner
    version: 1.0.0
    labels: [planner]
    adapter:
      type: http.sync
      config:
        endpoint: http://127.0.0.1:9000/v1/invoke
        timeout_seconds: 30
        bearer_token_env: HTTP_AGENT_TOKEN
""".strip(),
        encoding="utf-8",
    )

    catalog = load_capabilities(config)

    assert catalog.descriptors["hermes-developer"].adapter_type == "hermes.acp"
    assert catalog.descriptors["http-planner"].adapter_type == "http.sync"
    assert catalog.adapter_configs["http-planner"].type == "http.sync"


def test_legacy_capability_schema_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "capabilities.yaml"
    config.write_text(
        "capabilities: [{id: old, version: 1.0.0, transport: {type: hermes_acp}}]",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="schema_version"):
        load_capabilities(config)

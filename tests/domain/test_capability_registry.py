from pathlib import Path

import pytest

from acwm.config import ConfigurationError, load_capabilities


def test_capability_yaml_uses_environment_references_without_persisting_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_TOKEN", "super-secret-value")
    config = tmp_path / "capabilities.yaml"
    config.write_text(
        """
capabilities:
  - id: hermes-developer
    version: 1.0.0
    labels: [developer, coding]
    transport:
      type: hermes_acp
      command: [hermes, acp]
      profile: developer
      env:
        API_TOKEN: HERMES_TOKEN
    permissions:
      workspace_edits: allow
      command_allowlist: [git status, pytest]
""".strip(),
        encoding="utf-8",
    )

    descriptor = load_capabilities(config)["hermes-developer"]

    assert descriptor.transport.env == {"API_TOKEN": "HERMES_TOKEN"}
    assert "super-secret-value" not in descriptor.model_dump_json()

    config.write_text(config.read_text().replace("HERMES_TOKEN", "super-secret-value"))
    with pytest.raises(ConfigurationError, match="environment variable name"):
        load_capabilities(config)

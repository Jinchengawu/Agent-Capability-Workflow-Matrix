from pathlib import Path

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from acwm.adapters import LegacyDataDirError, SQLiteStore
from acwm.api import AppSettings, create_app
from acwm.application.runtime import DefaultCapabilityRuntime
from acwm.config import CapabilityCatalog, load_capabilities
from acwm.domain import (
    AdapterManifest,
    CapabilityFeature,
    JourneyDefinition,
    NodeStepDefinition,
)


class FinalTextOnlyAdapter:
    manifest = AdapterManifest(
        adapter_type="http.sync",
        adapter_version="0.2.0",
        features=frozenset({CapabilityFeature.TEXT_FINAL}),
    )

    async def close(self) -> None:
        return None


def http_catalog(tmp_path: Path) -> CapabilityCatalog:
    config = tmp_path / "capabilities.yaml"
    config.write_text(
        """
schema_version: "2"
capabilities:
  - id: http-planner
    version: 1.0.0
    labels: [planner]
    adapter:
      type: http.sync
      config:
        endpoint: http://127.0.0.1:9000/v1/invoke
        bearer_token_env: PRIVATE_HTTP_TOKEN
""",
        encoding="utf-8",
    )
    return load_capabilities(config)


def test_http_capability_is_rejected_for_langgraph_before_journey_creation(
    tmp_path: Path,
) -> None:
    catalog = http_catalog(tmp_path)
    runtime = DefaultCapabilityRuntime(
        catalog=catalog,
        adapters={"http-planner": FinalTextOnlyAdapter()},
        event_sink=None,
    )
    definition = JourneyDefinition(
        id="invalid-http-delivery",
        version="2.0.0",
        steps=(
            NodeStepDefinition(
                id="deliver",
                capability_id="http-planner",
                workflow_mode="langgraph.code-delivery",
            ),
        ),
    )
    data_dir = tmp_path / "data"
    app = create_app(
        AppSettings(data_dir=data_dir),
        runtime=runtime,
        catalog=catalog,
        journey_definitions={definition.id: definition},
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/journeys",
            headers={"Idempotency-Key": "incompatible"},
            json={
                "definition_id": definition.id,
                "objective": "must not start",
                "repository": {"path": str(tmp_path), "base_ref": "HEAD"},
                "verification_commands": [],
            },
        )
        capabilities = client.get("/v1/capabilities").json()

    assert response.status_code == 422
    assert response.json()["code"] == "workflow_incompatible"
    assert "workspace.cwd_binding" in response.json()["details"]["missing_features"]
    assert not any((data_dir / "workspaces").iterdir())
    assert "endpoint" not in capabilities[0]
    assert "PRIVATE_HTTP_TOKEN" not in str(capabilities)


@pytest.mark.asyncio
async def test_v02_rejects_legacy_sqlite_data_directory(tmp_path: Path) -> None:
    database = tmp_path / "acwm.sqlite"
    async with aiosqlite.connect(database) as connection:
        await connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        await connection.execute("INSERT INTO schema_version(version) VALUES(2)")
        await connection.commit()

    with pytest.raises(LegacyDataDirError) as captured:
        await SQLiteStore(database).initialize()

    assert captured.value.code == "legacy_data_dir_unsupported"

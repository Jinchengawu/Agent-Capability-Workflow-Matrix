from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acwm.api import AppSettings, create_app


def test_non_loopback_binding_requires_api_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ACWM_API_KEY"):
        create_app(AppSettings(data_dir=tmp_path, host="0.0.0.0"))


def test_bearer_token_protects_the_entire_api(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, host="0.0.0.0", api_key="test-token"))
    with TestClient(app) as client:
        denied = client.get("/health")
        allowed = client.get("/health", headers={"Authorization": "Bearer test-token"})

    assert denied.status_code == 401
    assert denied.json()["code"] == "unauthorized"
    assert allowed.status_code == 200


def test_request_validation_uses_the_common_problem_shape(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/v1/journeys",
            headers={"Idempotency-Key": "invalid-body"},
            json={"objective": "missing required fields"},
        )

    assert response.status_code == 422
    assert set(response.json()) == {"code", "message", "details", "trace_id"}
    assert response.json()["code"] == "validation_error"


def test_verification_command_must_match_capability_allowlist(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        response = client.post(
            "/v1/journeys",
            headers={"Idempotency-Key": "unlisted-command"},
            json={
                "definition_id": "code-delivery-v1",
                "capability_id": "hermes-developer",
                "objective": "unsafe verification",
                "repository": {"path": str(tmp_path), "base_ref": "HEAD"},
                "verification_commands": [{"name": "not-allowed", "argv": ["git", "push"]}],
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_journey"
    assert "allowlist" in response.json()["message"]

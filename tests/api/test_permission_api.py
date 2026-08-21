from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from acwm.api import AppSettings, create_app
from acwm.ports import CapabilityInvocation, CapabilityTransport, TransportResult


class PermissionRequestingTransport(CapabilityTransport):
    def __init__(self) -> None:
        self.handler: Any = None
        self.future: asyncio.Future[bool] | None = None

    def set_permission_handler(self, handler: Any) -> None:
        self.handler = handler

    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult:
        self.future = asyncio.get_running_loop().create_future()
        await self.handler(
            invocation.session_id,
            "permission-1",
            {"kind": "execute", "title": "Run an unlisted command"},
        )
        if not await self.future:
            raise RuntimeError("permission denied")
        return TransportResult(output="# Plan\nApproved command may run later.")

    def resolve_permission(self, request_id: str, approved: bool) -> bool:
        if request_id != "permission-1" or self.future is None or self.future.done():
            return False
        self.future.set_result(approved)
        return True

    async def cancel(self, session_id: str) -> None:
        return None


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _wait(client: TestClient, journey_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"/v1/journeys/{journey_id}").json()
        if body["status"] == status:
            return body
        time.sleep(0.02)
    raise AssertionError(f"Journey did not reach {status}: {body}")


def test_acp_permission_is_persisted_and_resolved_through_the_api(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "acwm@example.test")
    _git(repository, "config", "user.name", "ACWM Test")
    (repository / "README.md").write_text("fixture", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "fixture")

    app = create_app(
        AppSettings(data_dir=tmp_path / "data"), transport=PermissionRequestingTransport()
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/journeys",
            headers={"Idempotency-Key": "permission-create"},
            json={
                "definition_id": "code-delivery-v1",
                "objective": "Plan a change",
                "repository": {"path": str(repository), "base_ref": "HEAD"},
                "verification_commands": [],
            },
        )
        journey_id = response.json()["journey_id"]
        waiting = _wait(client, journey_id, "awaiting_permission")
        permission = waiting["permissions"][0]
        decision = client.post(
            f"/v1/journeys/{journey_id}/permissions/{permission['id']}/decisions",
            headers={"Idempotency-Key": "permission-approve"},
            json={"decision": "approve", "expected_revision": permission["revision"]},
        )
        assert decision.status_code == 202
        approved = _wait(client, journey_id, "awaiting_approval")

    assert approved["permissions"][0]["status"] == "approved"

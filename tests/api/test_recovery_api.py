from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from acwm.api import AppSettings, create_app
from acwm.ports import CapabilityInvocation, CapabilityTransport, TransportResult


class InterruptibleTransport(CapabilityTransport):
    def __init__(self) -> None:
        self.implement_started = threading.Event()

    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult:
        if invocation.purpose == "plan":
            return TransportResult(output="# Plan\nChange the value and verify it.")
        if invocation.purpose == "implement":
            self.implement_started.set()
            await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def cancel(self, session_id: str) -> None:
        return None


class CompletingTransport(CapabilityTransport):
    def __init__(self) -> None:
        self.purposes: list[str] = []

    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult:
        self.purposes.append(invocation.purpose)
        if invocation.purpose in {"implement", "repair"}:
            (invocation.cwd / "value.txt").write_text("new", encoding="utf-8")
            return TransportResult(output="implemented")
        if invocation.purpose == "review":
            return TransportResult(output='{"accepted": true, "summary": "ok"}')
        raise AssertionError(f"Completed plan stage was replayed: {invocation.purpose}")

    async def cancel(self, session_id: str) -> None:
        return None


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _poll(client: TestClient, journey_id: str, status: str) -> dict[str, object]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        body = client.get(f"/v1/journeys/{journey_id}").json()
        if body["status"] == status:
            return body
        time.sleep(0.025)
    raise AssertionError(f"Journey did not reach {status}")


def test_restart_marks_attempt_interrupted_and_resume_creates_a_new_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "acwm@example.test")
    _git(repository, "config", "user.name", "ACWM Test")
    (repository / "value.txt").write_text("old", encoding="utf-8")
    _git(repository, "add", "value.txt")
    _git(repository, "commit", "-m", "fixture")
    source_head = _git(repository, "rev-parse", "HEAD")
    data_dir = tmp_path / "data"
    request = {
        "definition_id": "code-delivery-v1",
        "capability_id": "hermes-developer",
        "objective": "Change value.txt to new",
        "repository": {"path": str(repository), "base_ref": source_head},
        "verification_commands": [
            {
                "name": "value-check",
                "argv": ["python", "-c", "assert open('value.txt').read() == 'new'"],
                "timeout_seconds": 5,
            }
        ],
    }

    interrupted_transport = InterruptibleTransport()
    with TestClient(
        create_app(AppSettings(data_dir=data_dir), transport=interrupted_transport)
    ) as client:
        created = client.post(
            "/v1/journeys", json=request, headers={"Idempotency-Key": "create-recovery"}
        ).json()
        journey_id = created["journey_id"]
        waiting = _poll(client, journey_id, "awaiting_approval")
        gate = waiting["gates"][0]  # type: ignore[index]
        response = client.post(
            f"/v1/journeys/{journey_id}/gates/{gate['id']}/decisions",  # type: ignore[index]
            headers={"Idempotency-Key": "approve-recovery"},
            json={
                "decision": "approve",
                "expected_revision": gate["revision"],  # type: ignore[index]
                "plan_hash": gate["plan_hash"],  # type: ignore[index]
            },
        )
        assert response.status_code == 202
        assert interrupted_transport.implement_started.wait(timeout=5)
        running = _poll(client, journey_id, "running")
        old_attempt = next(
            item
            for item in running["attempts"]
            if item["stage_id"] == "deliver"  # type: ignore[union-attr]
        )

    completing = CompletingTransport()
    with TestClient(create_app(AppSettings(data_dir=data_dir), transport=completing)) as client:
        attention = _poll(client, journey_id, "needs_attention")
        interrupted = next(
            item
            for item in attention["attempts"]
            if item["id"] == old_attempt["id"]  # type: ignore[union-attr]
        )
        assert interrupted["status"] == "interrupted"
        resumed = client.post(
            f"/v1/journeys/{journey_id}/attempts/{old_attempt['id']}/resume",
            headers={"Idempotency-Key": "resume-recovery"},
        )
        assert resumed.status_code == 202
        completed = _poll(client, journey_id, "completed")

    new_attempt = completed["attempts"][-1]  # type: ignore[index]
    assert new_attempt["id"] != old_attempt["id"]
    assert new_attempt["resumes_attempt_id"] == old_attempt["id"]
    assert "plan" not in completing.purposes

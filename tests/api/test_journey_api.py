from __future__ import annotations

import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from acwm.api import AppSettings, create_app
from acwm.ports import CapabilityInvocation, CapabilityTransport, TransportResult


class ScriptedHermesTransport(CapabilityTransport):
    def __init__(self) -> None:
        self.sessions: list[tuple[str, Path]] = []

    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult:
        self.sessions.append((invocation.session_id, invocation.cwd))
        if invocation.purpose == "plan":
            return TransportResult(output="# Plan\nChange value.txt to `new` and run verification.")
        if invocation.purpose in {"implement", "repair"}:
            (invocation.cwd / "value.txt").write_text("new", encoding="utf-8")
            return TransportResult(output="Implemented the requested change.")
        return TransportResult(output='{"accepted": true, "summary": "verification passed"}')

    async def cancel(self, session_id: str) -> None:
        return None


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _wait_for(client: TestClient, journey_id: str, status: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/v1/journeys/{journey_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] == status:
            return body
        time.sleep(0.02)
    raise AssertionError(f"Journey did not reach {status}")


def test_api_runs_direct_approval_and_langgraph_in_an_isolated_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "acwm@example.test")
    _git(repository, "config", "user.name", "ACWM Test")
    (repository / "value.txt").write_text("old", encoding="utf-8")
    _git(repository, "add", "value.txt")
    _git(repository, "commit", "-m", "fixture")
    source_head = _git(repository, "rev-parse", "HEAD")

    transport = ScriptedHermesTransport()
    app = create_app(AppSettings(data_dir=tmp_path / "data"), transport=transport)
    request = {
        "definition_id": "code-delivery-v1",
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

    with TestClient(app) as client:
        created = client.post("/v1/journeys", json=request, headers={"Idempotency-Key": "create-1"})
        assert created.status_code == 202
        journey_id = created.json()["journey_id"]

        duplicate = client.post(
            "/v1/journeys", json=request, headers={"Idempotency-Key": "create-1"}
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["journey_id"] == journey_id

        conflict_request = {**request, "objective": "A different objective"}
        conflict = client.post(
            "/v1/journeys",
            json=conflict_request,
            headers={"Idempotency-Key": "create-1"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"

        waiting = _wait_for(client, journey_id, "awaiting_approval")
        gate = waiting["gates"][0]  # type: ignore[index]
        approved = client.post(
            f"/v1/journeys/{journey_id}/gates/{gate['id']}/decisions",  # type: ignore[index]
            headers={"Idempotency-Key": "approve-1"},
            json={
                "decision": "approve",
                "expected_revision": gate["revision"],  # type: ignore[index]
                "plan_hash": gate["plan_hash"],  # type: ignore[index]
            },
        )
        assert approved.status_code == 202
        completed = _wait_for(client, journey_id, "completed")

        with client.stream("GET", f"/v1/journeys/{journey_id}/events") as stream:
            assert stream.status_code == 200
            lines = list(stream.iter_lines())
        event_ids = [int(line.removeprefix("id: ")) for line in lines if line.startswith("id: ")]
        event_types = [line.removeprefix("event: ") for line in lines if line.startswith("event: ")]
        assert event_ids == sorted(event_ids)
        assert {"journey.created", "gate.opened", "journey.completed"} <= set(event_types)

        replay_after = event_ids[-2]
        with client.stream(
            "GET",
            f"/v1/journeys/{journey_id}/events",
            headers={"Last-Event-ID": str(replay_after)},
        ) as replay:
            replay_lines = list(replay.iter_lines())
        replay_ids = [
            int(line.removeprefix("id: ")) for line in replay_lines if line.startswith("id: ")
        ]
        assert replay_ids and all(event_id > replay_after for event_id in replay_ids)

    assert (repository / "value.txt").read_text(encoding="utf-8") == "old"
    assert _git(repository, "rev-parse", "HEAD") == source_head
    assert len({session_id for session_id, _ in transport.sessions}) == 2
    assert all(cwd != repository for _, cwd in transport.sessions)
    artifact_kinds = {item["kind"] for item in completed["artifacts"]}  # type: ignore[index]
    assert {"implementation_plan", "patch", "test_evidence", "artifact_manifest"} <= artifact_kinds


def test_cancel_is_idempotent(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "acwm@example.test")
    _git(repository, "config", "user.name", "ACWM Test")
    (repository / "value.txt").write_text("old", encoding="utf-8")
    _git(repository, "add", "value.txt")
    _git(repository, "commit", "-m", "fixture")

    app = create_app(AppSettings(data_dir=tmp_path / "data"), transport=ScriptedHermesTransport())
    with TestClient(app) as client:
        created = client.post(
            "/v1/journeys",
            headers={"Idempotency-Key": "create-cancel"},
            json={
                "definition_id": "code-delivery-v1",
                "objective": "Plan only",
                "repository": {"path": str(repository), "base_ref": "HEAD"},
                "verification_commands": [],
            },
        )
        journey_id = created.json()["journey_id"]
        _wait_for(client, journey_id, "awaiting_approval")

        first = client.post(
            f"/v1/journeys/{journey_id}/cancel",
            headers={"Idempotency-Key": "cancel-1"},
        )
        duplicate = client.post(
            f"/v1/journeys/{journey_id}/cancel",
            headers={"Idempotency-Key": "cancel-1"},
        )

        assert first.status_code == duplicate.status_code == 202
        assert first.json() == duplicate.json()
        event_rows = client.portal.call(  # type: ignore[union-attr]
            client.app.state.service.store.events, journey_id
        )
        assert [event.type for event in event_rows].count("journey.cancelled") == 1

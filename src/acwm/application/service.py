"""Journey application service and durable orchestration boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from acwm.adapters.artifacts import ArtifactStore
from acwm.adapters.git_workspace import GitWorkspaceManager, ManagedWorkspace
from acwm.adapters.sqlite_store import SQLiteStore
from acwm.adapters.workflows import (
    DirectWorkflowAdapter,
    LangGraphCodeDeliveryAdapter,
)
from acwm.application.runtime import EventPublicationError
from acwm.config import CapabilityCatalog
from acwm.domain import (
    AttemptSnapshot,
    AttemptStatus,
    CapabilityEvent,
    GateSnapshot,
    GateStatus,
    GateSubject,
    HandoffEnvelope,
    JourneyDefinition,
    JourneySnapshot,
    JourneyStatus,
    NodeRequest,
    NodeStepDefinition,
    PermissionDecision,
    PermissionSnapshot,
    RepositorySpec,
    ResolvedNode,
    StageSnapshot,
    StageStatus,
    StopRequested,
    VerificationCommand,
    utc_now,
)


class JourneyNotFoundError(KeyError):
    pass


class StaleDecisionError(ValueError):
    pass


class JourneyService:
    def __init__(
        self,
        *,
        data_dir: Path,
        runtime: Any,
        catalog: CapabilityCatalog,
        definitions: dict[str, JourneyDefinition],
    ) -> None:
        self.data_dir = data_dir
        self.store = SQLiteStore(data_dir / "acwm.sqlite")
        self.artifacts = ArtifactStore(data_dir / "artifacts")
        self.workspaces = GitWorkspaceManager(data_dir / "workspaces")
        self.runtime = runtime
        self.catalog = catalog
        self.capabilities = catalog.descriptors
        self.definitions = definitions
        self.direct = DirectWorkflowAdapter(runtime)
        self.code_delivery = LangGraphCodeDeliveryAdapter(runtime, data_dir / "langgraph.sqlite")
        self._tasks: set[asyncio.Task[None]] = set()
        runtime.event_sink = self.handle_capability_event

    async def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        await self.store.initialize()
        await self._reconcile_startup()

    async def shutdown(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.runtime.close()

    def _schedule(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def create_journey(
        self,
        *,
        definition_id: str,
        objective: str,
        repository: RepositorySpec,
        verification_commands: tuple[VerificationCommand, ...],
    ) -> JourneySnapshot:
        definition = self.definitions.get(definition_id)
        if definition is None:
            raise ValueError("Unknown Journey definition")
        resolved_steps: list[tuple[NodeStepDefinition, Any, Any]] = []
        for step in definition.steps:
            if not isinstance(step, NodeStepDefinition):
                continue
            workflow = self._workflow(step.workflow_mode)
            resolved_steps.append(
                (
                    step,
                    workflow,
                    self.runtime.resolve(step.capability_id, workflow.mode.requirements),
                )
            )
        for command in verification_commands:
            rendered = " ".join(command.argv)
            delivery = next(
                (
                    entry
                    for entry in resolved_steps
                    if entry[0].workflow_mode == self.code_delivery.mode.id
                ),
                None,
            )
            if delivery is None:
                raise ValueError("Journey has no code-delivery Stage")
            policy = self.capabilities[delivery[0].capability_id].policy
            command_allowed = any(
                rendered == allowed or rendered.startswith(f"{allowed} ")
                for allowed in policy.command_allowlist
            )
            if not command_allowed:
                raise ValueError(
                    f"verification command is not in the Capability allowlist: {rendered}"
                )
        journey_id = str(uuid4())
        snapshot = JourneySnapshot(
            id=journey_id,
            definition_id=definition_id,
            objective=objective,
            repository=repository,
            verification_commands=verification_commands,
            stages=tuple(
                StageSnapshot(
                    id=step.id,
                    resolved_node=ResolvedNode(
                        node_id=step.id,
                        workflow_mode=step.workflow_mode,
                        workflow_version=workflow.mode.version,
                        capability=resolved,
                    ),
                )
                for step, workflow, resolved in resolved_steps
            ),
            gates=tuple(
                GateSnapshot(id=step.id, subject_kind=step.subject_kind)
                for step in definition.steps
                if not isinstance(step, NodeStepDefinition)
            ),
        )
        await self.store.save(snapshot, "journey.created")
        self._schedule(self._run_plan(journey_id))
        return snapshot

    async def get(self, journey_id: str) -> JourneySnapshot:
        snapshot = await self.store.get(journey_id)
        if snapshot is None:
            raise JourneyNotFoundError(journey_id)
        return snapshot

    async def decide_gate(
        self,
        journey_id: str,
        gate_id: str,
        *,
        decision: Literal["approve", "reject"],
        expected_revision: int,
        plan_hash: str,
    ) -> JourneySnapshot:
        snapshot = await self.get(journey_id)
        gate = next((item for item in snapshot.gates if item.id == gate_id), None)
        if gate is None:
            raise JourneyNotFoundError(gate_id)
        if (
            gate.status is not GateStatus.OPEN
            or gate.revision != expected_revision
            or gate.plan_hash != plan_hash
        ):
            raise StaleDecisionError("Gate revision or plan hash is stale")
        decided = gate.model_copy(
            update={"status": GateStatus.APPROVED if decision == "approve" else GateStatus.REJECTED}
        )
        gates = tuple(decided if item.id == gate_id else item for item in snapshot.gates)
        status = JourneyStatus.RUNNING if decision == "approve" else JourneyStatus.CANCELLED
        updated = snapshot.model_copy(
            update={
                "gates": gates,
                "status": status,
                "revision": snapshot.revision + 1,
                "updated_at": utc_now(),
            }
        )
        await self.store.save(
            updated,
            f"gate.{decision}d" if decision == "approve" else "gate.rejected",
            entity_type="gate",
            entity_id=gate_id,
            payload={"revision": expected_revision, "plan_hash": plan_hash},
        )
        if decision == "approve":
            self._schedule(self._run_delivery(journey_id))
        return updated

    async def cancel(self, journey_id: str) -> JourneySnapshot:
        snapshot = await self.get(journey_id)
        if snapshot.status is JourneyStatus.CANCELLED:
            return snapshot
        cancelled_attempt_ids: set[str] = set()
        unconfirmed_attempt_ids: set[str] = set()
        for attempt in reversed(snapshot.attempts):
            if attempt.status is AttemptStatus.RUNNING:
                receipt = await self.runtime.signal(
                    StopRequested(attempt_id=attempt.id, reason="journey_cancelled")
                )
                cancelled_attempt_ids.add(attempt.id)
                if receipt.disposition == "unsupported":
                    unconfirmed_attempt_ids.add(attempt.id)
        attempts = tuple(
            item.model_copy(
                update={
                    "status": (
                        AttemptStatus.CANCELLING
                        if item.id in unconfirmed_attempt_ids
                        else AttemptStatus.CANCELLED
                    ),
                    "finished_at": None if item.id in unconfirmed_attempt_ids else utc_now(),
                }
            )
            if item.id in cancelled_attempt_ids
            else item
            for item in snapshot.attempts
        )
        cancelled_stage_ids = {
            item.stage_id for item in snapshot.attempts if item.id in cancelled_attempt_ids
        }
        stages = tuple(
            item.model_copy(update={"status": StageStatus.CANCELLED})
            if item.id in cancelled_stage_ids
            else item
            for item in snapshot.stages
        )
        gates = tuple(
            item.model_copy(update={"status": GateStatus.CANCELLED})
            if item.status in {GateStatus.PENDING, GateStatus.OPEN}
            else item
            for item in snapshot.gates
        )
        updated = snapshot.model_copy(
            update={
                "status": (
                    JourneyStatus.CANCELLING if unconfirmed_attempt_ids else JourneyStatus.CANCELLED
                ),
                "attempts": attempts,
                "stages": stages,
                "gates": gates,
                "updated_at": utc_now(),
            }
        )
        await self.store.save(
            updated,
            "journey.cancel_requested" if unconfirmed_attempt_ids else "journey.cancelled",
            payload=(
                {"warning": "remote_termination_unconfirmed"} if unconfirmed_attempt_ids else None
            ),
        )
        return updated

    async def resume_attempt(self, journey_id: str, attempt_id: str) -> JourneySnapshot:
        snapshot = await self.get(journey_id)
        prior = next((item for item in snapshot.attempts if item.id == attempt_id), None)
        if (
            prior is None
            or prior.status is not AttemptStatus.INTERRUPTED
            or prior.stage_id != "deliver"
            or not prior.checkpoint_thread_id
        ):
            raise StaleDecisionError("Attempt has no resumable LangGraph checkpoint")
        updated = snapshot.model_copy(
            update={"status": JourneyStatus.RUNNING, "updated_at": utc_now()}
        )
        await self.store.save(
            updated, "attempt.resume_requested", entity_type="attempt", entity_id=attempt_id
        )
        self._schedule(self._run_delivery(journey_id, prior_attempt=prior, resume=True))
        return updated

    async def retry_stage(self, journey_id: str, stage_id: str) -> JourneySnapshot:
        snapshot = await self.get(journey_id)
        stage = next((item for item in snapshot.stages if item.id == stage_id), None)
        if stage is None:
            raise JourneyNotFoundError(stage_id)
        if stage.status not in {StageStatus.FAILED, StageStatus.INTERRUPTED}:
            raise StaleDecisionError("Stage is not failed or interrupted")
        prior = next(
            (item for item in reversed(snapshot.attempts) if item.stage_id == stage_id), None
        )
        updated = snapshot.model_copy(
            update={"status": JourneyStatus.RUNNING, "updated_at": utc_now()}
        )
        await self.store.save(
            updated, "stage.retry_requested", entity_type="stage", entity_id=stage_id
        )
        if stage_id == "plan":
            self._schedule(self._run_plan(journey_id, prior_attempt=prior))
        elif stage_id == "deliver":
            self._schedule(self._run_delivery(journey_id, prior_attempt=prior, resume=False))
        else:
            raise StaleDecisionError("Unknown retryable stage")
        return updated

    async def decide_permission(
        self,
        journey_id: str,
        request_id: str,
        *,
        decision: Literal["approve", "reject"],
        expected_revision: int,
    ) -> JourneySnapshot:
        snapshot = await self.get(journey_id)
        permission = next((item for item in snapshot.permissions if item.id == request_id), None)
        if permission is None:
            raise JourneyNotFoundError(request_id)
        if permission.status != "pending" or permission.revision != expected_revision:
            raise StaleDecisionError("Permission request revision is stale")
        decided_status = "approved" if decision == "approve" else "rejected"
        updated_permission = permission.model_copy(update={"status": decided_status})
        permissions = tuple(
            updated_permission if item.id == request_id else item for item in snapshot.permissions
        )
        snapshot = snapshot.model_copy(
            update={
                "permissions": permissions,
                "status": JourneyStatus.RUNNING,
                "revision": snapshot.revision + 1,
                "updated_at": utc_now(),
            }
        )
        await self.store.save(
            snapshot,
            f"permission.{decision}d",
            entity_type="permission",
            entity_id=request_id,
        )
        receipt = await self.runtime.signal(
            PermissionDecision(
                attempt_id=permission.attempt_id,
                request_id=request_id,
                revision=expected_revision,
                decision=decision,
            )
        )
        if receipt.disposition != "accepted":
            orphaned = snapshot.model_copy(
                update={"status": JourneyStatus.NEEDS_ATTENTION, "updated_at": utc_now()}
            )
            await self.store.save(
                orphaned,
                "permission.orphaned",
                entity_type="permission",
                entity_id=request_id,
            )
            raise StaleDecisionError("ACP permission request is no longer live")
        return snapshot

    async def handle_capability_event(self, event: CapabilityEvent) -> None:
        journey_id = event.journey_id
        if event.type != "capability.permission.required":
            await self.store.append_event(
                journey_id,
                event.type,
                entity_type="attempt",
                entity_id=event.attempt_id,
                payload={"sequence": event.sequence, **event.payload},
            )
            return
        snapshot = await self.get(journey_id)
        request_id = str(event.payload["request_id"])
        permission = PermissionSnapshot(
            id=request_id,
            attempt_id=event.attempt_id,
            revision=int(event.payload.get("revision", 1)),
            request=dict(event.payload.get("request", {})),
        )
        snapshot = snapshot.model_copy(
            update={
                "permissions": (*snapshot.permissions, permission),
                "status": JourneyStatus.AWAITING_PERMISSION,
                "revision": snapshot.revision + 1,
                "updated_at": utc_now(),
            }
        )
        await self.store.save(
            snapshot,
            "permission.required",
            entity_type="permission",
            entity_id=request_id,
            payload={"revision": permission.revision, "request": permission.request},
        )

    def _workflow(self, workflow_mode: str) -> Any:
        if workflow_mode == self.direct.mode.id:
            return self.direct
        if workflow_mode == self.code_delivery.mode.id:
            return self.code_delivery
        raise ValueError(f"Unknown Workflow mode: {workflow_mode}")

    async def _run_plan(
        self, journey_id: str, prior_attempt: AttemptSnapshot | None = None
    ) -> None:
        try:
            snapshot = await self.get(journey_id)
            workspace = await asyncio.to_thread(
                self.workspaces.create,
                journey_id,
                Path(snapshot.repository.path),
                snapshot.repository.base_ref,
            )
            attempt = self._attempt(
                "plan",
                self._stage(snapshot, "plan").resolved_node.capability.capability_id,
                resumable=False,
                retries_attempt_id=prior_attempt.id if prior_attempt else None,
            )
            snapshot = self._start_stage(snapshot, "plan", attempt).model_copy(
                update={
                    "base_sha": workspace.base_sha,
                    "worktree_path": str(workspace.path),
                    "status": JourneyStatus.RUNNING,
                }
            )
            await self.store.save(snapshot, "stage.started", entity_type="stage", entity_id="plan")
            result = await self.direct.execute(
                NodeRequest(
                    attempt_id=attempt.id,
                    journey_id=snapshot.id,
                    stage_id="plan",
                    capability=self._stage(snapshot, "plan").resolved_node.capability,
                    workspace=str(workspace.path),
                    objective=snapshot.objective,
                )
            )
            snapshot = await self.get(journey_id)
            if snapshot.status in {JourneyStatus.CANCELLED, JourneyStatus.CANCELLING}:
                if snapshot.status is JourneyStatus.CANCELLING:
                    await self._finalize_cancellation(snapshot)
                return
            plan = self.artifacts.put(
                "implementation_plan", "text/markdown", result.output.encode()
            )
            finished = attempt.model_copy(
                update={"status": AttemptStatus.SUCCEEDED, "finished_at": utc_now()}
            )
            snapshot = self._finish_stage(snapshot, "plan", finished)
            gate = snapshot.gates[0].model_copy(
                update={
                    "status": GateStatus.OPEN,
                    "revision": snapshot.revision + 1,
                    "subject": GateSubject(
                        kind=snapshot.gates[0].subject_kind,
                        artifact_id=plan.artifact_id,
                        sha256=plan.sha256,
                    ),
                    "plan_hash": plan.sha256,
                }
            )
            snapshot = snapshot.model_copy(
                update={
                    "status": JourneyStatus.AWAITING_APPROVAL,
                    "gates": (gate,),
                    "artifacts": (*snapshot.artifacts, plan),
                    "revision": snapshot.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            await self.store.save(
                snapshot,
                "gate.opened",
                entity_type="gate",
                entity_id=gate.id,
                payload={
                    "revision": gate.revision,
                    "subject_kind": gate.subject_kind,
                    "subject_hash": gate.plan_hash,
                },
            )
        except Exception as error:
            await self._record_failure(journey_id, "plan", error)

    async def _run_delivery(
        self,
        journey_id: str,
        prior_attempt: AttemptSnapshot | None = None,
        resume: bool = False,
    ) -> None:
        try:
            snapshot = await self.get(journey_id)
            if not snapshot.worktree_path or not snapshot.base_sha:
                raise RuntimeError("Journey has no managed workspace")
            workspace = ManagedWorkspace(
                path=Path(snapshot.worktree_path),
                base_sha=snapshot.base_sha,
                branch=f"acwm/{snapshot.id}",
            )
            attempt = self._attempt(
                "deliver",
                self._stage(snapshot, "deliver").resolved_node.capability.capability_id,
                resumable=True,
                retries_attempt_id=prior_attempt.id if prior_attempt and not resume else None,
                resumes_attempt_id=prior_attempt.id if prior_attempt and resume else None,
                checkpoint_thread_id=(
                    prior_attempt.checkpoint_thread_id if prior_attempt and resume else None
                ),
            )
            snapshot = self._start_stage(snapshot, "deliver", attempt).model_copy(
                update={"status": JourneyStatus.RUNNING}
            )
            await self.store.save(
                snapshot, "stage.started", entity_type="stage", entity_id="deliver"
            )
            plan = next(item for item in snapshot.artifacts if item.kind == "implementation_plan")
            plan_text = self.artifacts.read(plan).decode()
            plan_attempt = next(item for item in snapshot.attempts if item.stage_id == "plan")
            handoff = HandoffEnvelope.create(
                objective=snapshot.objective,
                summary=plan_text,
                decisions=(),
                constraints=("Only edit the managed worktree", "Do not push or merge"),
                facts=(f"Base SHA: {snapshot.base_sha}",),
                open_items=(),
                source_journey_id=snapshot.id,
                source_stage_id="plan",
                source_attempt_id=plan_attempt.id,
                artifacts=(plan,),
            )
            handoff_artifact = self.artifacts.put(
                "handoff", "application/json", handoff.model_dump_json().encode()
            )
            result = await self.code_delivery.execute(
                NodeRequest(
                    attempt_id=attempt.id,
                    checkpoint_thread_id=attempt.checkpoint_thread_id,
                    journey_id=snapshot.id,
                    stage_id="deliver",
                    capability=self._stage(snapshot, "deliver").resolved_node.capability,
                    workspace=str(workspace.path),
                    objective=snapshot.objective,
                    handoff=handoff,
                    artifacts=(plan,),
                    verification_commands=snapshot.verification_commands,
                    resume=resume,
                )
            )
            snapshot = await self.get(journey_id)
            if snapshot.status in {JourneyStatus.CANCELLED, JourneyStatus.CANCELLING}:
                if snapshot.status is JourneyStatus.CANCELLING:
                    await self._finalize_cancellation(snapshot)
                return
            patch = self.artifacts.put("patch", "text/x-diff", self.workspaces.patch(workspace))
            evidence = self.artifacts.put(
                "test_evidence",
                "application/json",
                json.dumps(result.evidence, indent=2, ensure_ascii=False).encode(),
            )
            summary = self.artifacts.put(
                "delivery_summary", "text/markdown", result.output.encode()
            )
            current_artifacts = (
                *snapshot.artifacts,
                handoff_artifact,
                patch,
                evidence,
                summary,
            )
            manifest_body = {
                "journey_id": snapshot.id,
                "workspace": self.workspaces.manifest(workspace),
                "artifacts": [item.model_dump(mode="json") for item in current_artifacts],
            }
            manifest = self.artifacts.put(
                "artifact_manifest",
                "application/json",
                json.dumps(manifest_body, indent=2, sort_keys=True).encode(),
            )
            finished = attempt.model_copy(
                update={"status": AttemptStatus.SUCCEEDED, "finished_at": utc_now()}
            )
            snapshot = self._finish_stage(snapshot, "deliver", finished).model_copy(
                update={
                    "status": JourneyStatus.COMPLETED,
                    "artifacts": (*current_artifacts, manifest),
                    "revision": snapshot.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            await self.store.save(snapshot, "journey.completed")
        except Exception as error:
            await self._record_failure(journey_id, "deliver", error)

    def _attempt(
        self,
        stage_id: str,
        capability_id: str,
        resumable: bool,
        *,
        retries_attempt_id: str | None = None,
        resumes_attempt_id: str | None = None,
        checkpoint_thread_id: str | None = None,
    ) -> AttemptSnapshot:
        attempt_id = str(uuid4())
        return AttemptSnapshot(
            id=attempt_id,
            stage_id=stage_id,
            status=AttemptStatus.RUNNING,
            capability_id=capability_id,
            checkpoint_thread_id=(checkpoint_thread_id or attempt_id) if resumable else None,
            retries_attempt_id=retries_attempt_id,
            resumes_attempt_id=resumes_attempt_id,
            started_at=utc_now(),
        )

    @staticmethod
    def _stage(snapshot: JourneySnapshot, stage_id: str) -> StageSnapshot:
        return next(item for item in snapshot.stages if item.id == stage_id)

    @staticmethod
    def _start_stage(
        snapshot: JourneySnapshot, stage_id: str, attempt: AttemptSnapshot
    ) -> JourneySnapshot:
        stages = tuple(
            stage.model_copy(
                update={"status": StageStatus.RUNNING, "current_attempt_id": attempt.id}
            )
            if stage.id == stage_id
            else stage
            for stage in snapshot.stages
        )
        return snapshot.model_copy(
            update={
                "stages": stages,
                "attempts": (*snapshot.attempts, attempt),
                "current_stage_id": stage_id,
                "updated_at": utc_now(),
            }
        )

    @staticmethod
    def _finish_stage(
        snapshot: JourneySnapshot, stage_id: str, attempt: AttemptSnapshot
    ) -> JourneySnapshot:
        stages = tuple(
            stage.model_copy(update={"status": StageStatus.SUCCEEDED})
            if stage.id == stage_id
            else stage
            for stage in snapshot.stages
        )
        attempts = tuple(attempt if item.id == attempt.id else item for item in snapshot.attempts)
        return snapshot.model_copy(update={"stages": stages, "attempts": attempts})

    async def _record_failure(self, journey_id: str, stage_id: str, error: Exception) -> None:
        snapshot = await self.get(journey_id)
        if snapshot.status is JourneyStatus.CANCELLED:
            return
        if snapshot.status is JourneyStatus.CANCELLING:
            await self._finalize_cancellation(snapshot)
            return
        if isinstance(error, EventPublicationError):
            attention = snapshot.model_copy(
                update={"status": JourneyStatus.NEEDS_ATTENTION, "updated_at": utc_now()}
            )
            await self.store.save(
                attention,
                "journey.needs_attention",
                payload={"code": error.code},
            )
            return
        attempts = tuple(
            item.model_copy(
                update={
                    "status": AttemptStatus.FAILED,
                    "finished_at": utc_now(),
                    "error": str(error),
                }
            )
            if item.stage_id == stage_id and item.status is AttemptStatus.RUNNING
            else item
            for item in snapshot.attempts
        )
        stages = tuple(
            item.model_copy(update={"status": StageStatus.FAILED}) if item.id == stage_id else item
            for item in snapshot.stages
        )
        snapshot = snapshot.model_copy(
            update={
                "status": JourneyStatus.FAILED,
                "attempts": attempts,
                "stages": stages,
                "updated_at": utc_now(),
            }
        )
        await self.store.save(
            snapshot,
            "stage.failed",
            entity_type="stage",
            entity_id=stage_id,
            payload={"error": str(error)},
        )

    async def _finalize_cancellation(self, snapshot: JourneySnapshot) -> None:
        attempts = tuple(
            item.model_copy(update={"status": AttemptStatus.CANCELLED, "finished_at": utc_now()})
            if item.status is AttemptStatus.CANCELLING
            else item
            for item in snapshot.attempts
        )
        finalized = snapshot.model_copy(
            update={
                "status": JourneyStatus.CANCELLED,
                "attempts": attempts,
                "updated_at": utc_now(),
            }
        )
        await self.store.save(
            finalized,
            "journey.cancelled",
            payload={"warning": "remote_termination_unconfirmed"},
        )

    async def _reconcile_startup(self) -> None:
        for snapshot in await self.store.list_snapshots():
            if snapshot.status is JourneyStatus.QUEUED:
                self._schedule(self._run_plan(snapshot.id))
                continue
            running = [item for item in snapshot.attempts if item.status is AttemptStatus.RUNNING]
            if not running:
                continue
            running_ids = {item.id for item in running}
            attempts = tuple(
                item.model_copy(
                    update={
                        "status": AttemptStatus.INTERRUPTED,
                        "finished_at": utc_now(),
                        "error": "ACWM process restarted while the attempt was running",
                    }
                )
                if item.id in running_ids
                else item
                for item in snapshot.attempts
            )
            stage_ids = {item.stage_id for item in running}
            stages = tuple(
                item.model_copy(update={"status": StageStatus.INTERRUPTED})
                if item.id in stage_ids
                else item
                for item in snapshot.stages
            )
            reconciled = snapshot.model_copy(
                update={
                    "status": JourneyStatus.NEEDS_ATTENTION,
                    "attempts": attempts,
                    "stages": stages,
                    "updated_at": utc_now(),
                }
            )
            await self.store.save(reconciled, "journey.interrupted")

"""FastAPI control plane for ACWM Journeys."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from acwm.adapters import IdempotencyConflictError
from acwm.adapters.workflows import WORKFLOW_MODES
from acwm.application import JourneyNotFoundError, JourneyService, StaleDecisionError
from acwm.domain import (
    ApprovalGateDefinition,
    CapabilityDescriptor,
    HermesACPTransport,
    JourneyDefinition,
    JourneyStatus,
    NodeStepDefinition,
    PermissionPolicy,
    RepositorySpec,
    VerificationCommand,
)
from acwm.ports import CapabilityInvocation, CapabilityTransport, TransportResult


class AppSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    data_dir: Path
    host: str = "127.0.0.1"
    api_key: str | None = None


class CreateJourneyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition_id: str
    capability_id: str
    objective: str
    repository: RepositorySpec
    verification_commands: tuple[VerificationCommand, ...]


class GateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    expected_revision: int
    plan_hash: str


class PermissionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    expected_revision: int


class _UnavailableTransport(CapabilityTransport):
    async def invoke(self, invocation: CapabilityInvocation) -> TransportResult:
        raise RuntimeError(f"No CapabilityTransport was configured for {invocation.capability_id}")

    async def cancel(self, session_id: str) -> None:
        return None


def _problem(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "details": details,
            "trace_id": str(uuid4()),
        },
    )


def create_app(
    settings: AppSettings,
    *,
    transport: CapabilityTransport | None = None,
    capabilities: dict[str, CapabilityDescriptor] | None = None,
    journey_definitions: dict[str, JourneyDefinition] | None = None,
) -> FastAPI:
    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.api_key:
        raise ValueError("ACWM_API_KEY is required for non-loopback binding")
    registry = capabilities or {
        "hermes-developer": CapabilityDescriptor(
            id="hermes-developer",
            version="1.0.0",
            labels=("developer", "coding"),
            transport=HermesACPTransport(),
            permissions=PermissionPolicy(
                workspace_edits="allow",
                command_allowlist=("git status", "pytest", "python -c"),
            ),
        )
    }
    definitions = journey_definitions or {
        "code-delivery-v1": JourneyDefinition(
            id="code-delivery-v1",
            version="1.0.0",
            steps=(
                NodeStepDefinition(id="plan", workflow_mode="direct"),
                ApprovalGateDefinition(id="approve-plan"),
                NodeStepDefinition(id="deliver", workflow_mode="langgraph.code-delivery"),
            ),
        )
    }
    service = JourneyService(
        data_dir=settings.data_dir,
        transport=transport or _UnavailableTransport(),
        capabilities=registry,
        definitions=definitions,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await service.initialize()
        try:
            yield
        finally:
            await service.shutdown()

    app = FastAPI(title="Agent Capability–Workflow Matrix", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            {"location": item["loc"], "message": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return _problem(422, "validation_error", "Request validation failed", details)

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next: Any) -> Any:
        if settings.api_key:
            expected = f"Bearer {settings.api_key}"
            if request.headers.get("authorization") != expected:
                return _problem(401, "unauthorized", "A valid Bearer token is required")
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/capabilities")
    async def list_capabilities() -> list[dict[str, Any]]:
        return [descriptor.model_dump(mode="json") for descriptor in registry.values()]

    @app.get("/v1/workflow-modes")
    async def list_workflow_modes() -> list[dict[str, Any]]:
        return [mode.model_dump(mode="json") for mode in WORKFLOW_MODES]

    @app.post("/v1/journeys")
    async def create_journey(
        body: CreateJourneyRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            return _problem(422, "idempotency_key_required", "Idempotency-Key is required")
        encoded = body.model_dump(mode="json")
        try:
            remembered = await service.store.idempotent_response(idempotency_key, encoded)
            if remembered:
                return JSONResponse(status_code=remembered[0], content=remembered[1])
            snapshot = await service.create_journey(
                definition_id=body.definition_id,
                capability_id=body.capability_id,
                objective=body.objective,
                repository=body.repository,
                verification_commands=body.verification_commands,
            )
            response = {"journey_id": snapshot.id, "status": snapshot.status.value}
            await service.store.remember_response(idempotency_key, encoded, 202, response)
            return JSONResponse(status_code=202, content=response)
        except IdempotencyConflictError as error:
            return _problem(409, "idempotency_conflict", str(error))
        except ValueError as error:
            return _problem(422, "invalid_journey", str(error))

    @app.get("/v1/journeys/{journey_id}")
    async def get_journey(journey_id: str) -> JSONResponse:
        try:
            snapshot = await service.get(journey_id)
        except JourneyNotFoundError:
            return _problem(404, "journey_not_found", "Journey was not found")
        return JSONResponse(content=jsonable_encoder(snapshot))

    @app.post("/v1/journeys/{journey_id}/gates/{gate_id}/decisions")
    async def decide_gate(
        journey_id: str,
        gate_id: str,
        body: GateDecisionRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            return _problem(422, "idempotency_key_required", "Idempotency-Key is required")
        encoded = {"journey_id": journey_id, "gate_id": gate_id, **body.model_dump(mode="json")}
        try:
            remembered = await service.store.idempotent_response(idempotency_key, encoded)
            if remembered:
                return JSONResponse(status_code=remembered[0], content=remembered[1])
            snapshot = await service.decide_gate(
                journey_id,
                gate_id,
                decision=body.decision,
                expected_revision=body.expected_revision,
                plan_hash=body.plan_hash,
            )
            response = {"journey_id": snapshot.id, "status": snapshot.status.value}
            await service.store.remember_response(idempotency_key, encoded, 202, response)
            return JSONResponse(status_code=202, content=response)
        except IdempotencyConflictError as error:
            return _problem(409, "idempotency_conflict", str(error))
        except StaleDecisionError as error:
            return _problem(409, "stale_decision", str(error))
        except JourneyNotFoundError:
            return _problem(404, "resource_not_found", "Journey or gate was not found")

    @app.post("/v1/journeys/{journey_id}/cancel")
    async def cancel_journey(
        journey_id: str,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            return _problem(422, "idempotency_key_required", "Idempotency-Key is required")
        encoded = {"journey_id": journey_id, "operation": "cancel"}
        try:
            remembered = await service.store.idempotent_response(idempotency_key, encoded)
            if remembered:
                return JSONResponse(status_code=remembered[0], content=remembered[1])
            snapshot = await service.cancel(journey_id)
            response = {"journey_id": snapshot.id, "status": snapshot.status.value}
            await service.store.remember_response(idempotency_key, encoded, 202, response)
            return JSONResponse(status_code=202, content=response)
        except IdempotencyConflictError as error:
            return _problem(409, "idempotency_conflict", str(error))
        except JourneyNotFoundError:
            return _problem(404, "journey_not_found", "Journey was not found")

    @app.get("/v1/journeys/{journey_id}/events", response_model=None)
    async def journey_events(
        request: Request, journey_id: str
    ) -> EventSourceResponse | JSONResponse:
        try:
            await service.get(journey_id)
        except JourneyNotFoundError:
            return _problem(404, "journey_not_found", "Journey was not found")
        try:
            cursor = int(request.headers.get("last-event-id", "0"))
        except ValueError:
            cursor = 0

        async def stream() -> AsyncIterator[dict[str, str]]:
            nonlocal cursor
            while True:
                events = await service.store.events(journey_id, cursor)
                for event in events:
                    cursor = event.event_id
                    yield {
                        "id": str(event.event_id),
                        "event": event.type,
                        "data": event.model_dump_json(),
                    }
                snapshot = await service.get(journey_id)
                if (
                    snapshot.status
                    in {
                        JourneyStatus.COMPLETED,
                        JourneyStatus.FAILED,
                        JourneyStatus.CANCELLED,
                    }
                    and not events
                ):
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.25)

        return EventSourceResponse(stream(), ping=15)

    @app.post("/v1/journeys/{journey_id}/permissions/{request_id}/decisions")
    async def decide_permission(
        journey_id: str,
        request_id: str,
        body: PermissionDecisionRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            return _problem(422, "idempotency_key_required", "Idempotency-Key is required")
        encoded = {
            "journey_id": journey_id,
            "request_id": request_id,
            **body.model_dump(mode="json"),
        }
        try:
            remembered = await service.store.idempotent_response(idempotency_key, encoded)
            if remembered:
                return JSONResponse(status_code=remembered[0], content=remembered[1])
            snapshot = await service.decide_permission(
                journey_id,
                request_id,
                decision=body.decision,
                expected_revision=body.expected_revision,
            )
            response = {"journey_id": snapshot.id, "status": snapshot.status.value}
            await service.store.remember_response(idempotency_key, encoded, 202, response)
            return JSONResponse(status_code=202, content=response)
        except IdempotencyConflictError as error:
            return _problem(409, "idempotency_conflict", str(error))
        except StaleDecisionError as error:
            return _problem(409, "stale_decision", str(error))
        except JourneyNotFoundError:
            return _problem(404, "permission_not_found", "Permission request was not found")

    @app.post("/v1/journeys/{journey_id}/attempts/{attempt_id}/resume")
    async def resume_attempt(
        journey_id: str,
        attempt_id: str,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            return _problem(422, "idempotency_key_required", "Idempotency-Key is required")
        encoded = {"journey_id": journey_id, "attempt_id": attempt_id, "operation": "resume"}
        try:
            remembered = await service.store.idempotent_response(idempotency_key, encoded)
            if remembered:
                return JSONResponse(status_code=remembered[0], content=remembered[1])
            snapshot = await service.resume_attempt(journey_id, attempt_id)
            response = {"journey_id": snapshot.id, "status": snapshot.status.value}
            await service.store.remember_response(idempotency_key, encoded, 202, response)
            return JSONResponse(status_code=202, content=response)
        except IdempotencyConflictError as error:
            return _problem(409, "idempotency_conflict", str(error))
        except StaleDecisionError as error:
            return _problem(409, "attempt_not_resumable", str(error))
        except JourneyNotFoundError:
            return _problem(404, "journey_not_found", "Journey was not found")

    @app.post("/v1/journeys/{journey_id}/stages/{stage_id}/retries")
    async def retry_stage(
        journey_id: str,
        stage_id: str,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            return _problem(422, "idempotency_key_required", "Idempotency-Key is required")
        encoded = {"journey_id": journey_id, "stage_id": stage_id, "operation": "retry"}
        try:
            remembered = await service.store.idempotent_response(idempotency_key, encoded)
            if remembered:
                return JSONResponse(status_code=remembered[0], content=remembered[1])
            snapshot = await service.retry_stage(journey_id, stage_id)
            response = {"journey_id": snapshot.id, "status": snapshot.status.value}
            await service.store.remember_response(idempotency_key, encoded, 202, response)
            return JSONResponse(status_code=202, content=response)
        except IdempotencyConflictError as error:
            return _problem(409, "idempotency_conflict", str(error))
        except StaleDecisionError as error:
            return _problem(409, "stage_not_retryable", str(error))
        except JourneyNotFoundError:
            return _problem(404, "resource_not_found", "Journey or stage was not found")

    return app

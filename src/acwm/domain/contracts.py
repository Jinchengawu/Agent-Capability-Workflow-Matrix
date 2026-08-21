"""Versioned, immutable contracts exchanged between ACWM stages."""

from __future__ import annotations

import hashlib
import json
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ArtifactRef(ImmutableModel):
    artifact_id: str
    kind: str
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    uri: str


class HandoffEnvelope(ImmutableModel):
    schema_version: str
    objective: str
    summary: str
    decisions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    open_items: tuple[str, ...] = ()
    source_journey_id: str
    source_stage_id: str
    source_attempt_id: str
    artifacts: tuple[ArtifactRef, ...] = ()
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    CURRENT_VERSION: ClassVar[str] = "1.0"

    @classmethod
    def create(cls, **values: Any) -> HandoffEnvelope:
        values = {"schema_version": cls.CURRENT_VERSION, **values}
        values["sha256"] = cls.compute_hash(values)
        return cls.model_validate(values)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"sha256"})

    @staticmethod
    def compute_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=lambda value: value.model_dump(mode="json"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def verify(self) -> bool:
        return self.sha256 == self.compute_hash(self.payload())

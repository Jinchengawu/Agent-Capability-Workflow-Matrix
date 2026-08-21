"""Thin-control-plane Journey definitions.

A Journey orders Workflow Stages and global Gates.  A Stage owns one Workflow
Mode and binds one or more named Capability Nodes into that mode.  Workflow
internals (messages, checkpoints and agent topology) deliberately stay out of
this contract.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import ImmutableModel


class StageDefinition(ImmutableModel):
    kind: Literal["stage"] = "stage"
    id: str
    workflow_mode: str
    bindings: dict[str, str]
    output_validator: str | None = None

    @model_validator(mode="after")
    def has_named_capability_bindings(self) -> "StageDefinition":
        if not self.bindings:
            raise ValueError("Stage must bind at least one Capability")
        if any(
            not slot.strip() or not capability_id.strip()
            for slot, capability_id in self.bindings.items()
        ):
            raise ValueError("Stage binding names and Capability ids must not be blank")
        return self

    @property
    def capability_id(self) -> str:
        """Single-binding projection used by the v0.2 reference Journey."""
        if len(self.bindings) != 1:
            raise ValueError("Stage has more than one Capability binding")
        return next(iter(self.bindings.values()))


class ApprovalGateDefinition(ImmutableModel):
    kind: Literal["approval_gate"] = "approval_gate"
    id: str
    subject_kind: str = "artifact"


JourneyStepDefinition = Annotated[
    StageDefinition | ApprovalGateDefinition, Field(discriminator="kind")
]


class JourneyDefinition(ImmutableModel):
    id: str
    version: str
    steps: tuple[JourneyStepDefinition, ...]

    @model_validator(mode="after")
    def valid_ordered_journey(self) -> "JourneyDefinition":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Journey step ids must be unique")
        if not self.steps or not isinstance(self.steps[0], StageDefinition):
            raise ValueError("Journey must begin with a Stage")
        return self


# Transitional import alias for v0.2 application code while the orchestration
# service is migrated to StageDefinition in the next vertical slice.
NodeStepDefinition = StageDefinition

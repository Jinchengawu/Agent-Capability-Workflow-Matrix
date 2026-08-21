"""Declarative, deliberately limited Journey definitions."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import ImmutableModel


class NodeStepDefinition(ImmutableModel):
    kind: Literal["node"] = "node"
    id: str
    capability_id: str
    workflow_mode: Literal["direct", "langgraph.code-delivery"]


class ApprovalGateDefinition(ImmutableModel):
    kind: Literal["approval_gate"] = "approval_gate"
    id: str


JourneyStepDefinition = Annotated[
    NodeStepDefinition | ApprovalGateDefinition, Field(discriminator="kind")
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
        if not self.steps or not isinstance(self.steps[0], NodeStepDefinition):
            raise ValueError("Journey must begin with a node")
        return self

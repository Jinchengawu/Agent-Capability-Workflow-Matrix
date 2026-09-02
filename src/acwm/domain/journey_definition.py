"""Thin-control-plane Journey graph definitions.

A Journey orders Workflow Stages and global Gates.  A Stage owns one Workflow
Mode and binds one or more named Capability Nodes into that mode.  Workflow
internals (messages, checkpoints and agent topology) deliberately stay out of
this contract.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import ImmutableModel
from .provider import ArtifactContract


class StageDefinition(ImmutableModel):
    kind: Literal["stage"] = "stage"
    id: str
    workflow_mode: str
    bindings: dict[str, str]
    output_validator: str | None = None
    input_artifact_contracts: tuple[ArtifactContract, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def has_named_capability_bindings(self) -> "StageDefinition":
        if not self.bindings:
            raise ValueError("Stage must bind at least one Capability")
        if any(
            not slot.strip() or not capability_id.strip()
            for slot, capability_id in self.bindings.items()
        ):
            raise ValueError("Stage binding names and Capability ids must not be blank")
        contract_ids = [contract.id for contract in self.input_artifact_contracts]
        if len(contract_ids) != len(set(contract_ids)):
            raise ValueError("Stage input Artifact Contract ids must be unique")
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


class JourneyEdgeDefinition(ImmutableModel):
    source: str
    target: str
    condition: str | None = None


class LoopPolicyDefinition(ImmutableModel):
    exit_condition: str = Field(min_length=1)
    max_iterations: int = Field(ge=1, le=100)
    timeout_seconds: int = Field(ge=1, le=86_400)
    on_exhausted: Literal["fail", "continue", "needs_attention"] = "fail"


class LoopDefinition(ImmutableModel):
    kind: Literal["loop"] = "loop"
    id: str
    nodes: tuple[JourneyStepDefinition, ...]
    edges: tuple[JourneyEdgeDefinition, ...]
    policy: LoopPolicyDefinition

    @model_validator(mode="after")
    def has_non_empty_body(self) -> "LoopDefinition":
        if not self.nodes:
            raise ValueError("Loop body must contain at least one Node")
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Loop body Node ids must be unique")
        return self


JourneyNodeDefinition = Annotated[
    StageDefinition | ApprovalGateDefinition | LoopDefinition,
    Field(discriminator="kind"),
]


class JourneyDefinition(ImmutableModel):
    id: str
    version: str
    nodes: tuple[JourneyNodeDefinition, ...] = ()
    edges: tuple[JourneyEdgeDefinition, ...] = ()
    steps: tuple[JourneyStepDefinition, ...] = ()

    @model_validator(mode="after")
    def valid_journey_representation(self) -> "JourneyDefinition":
        if self.nodes and self.steps:
            raise ValueError("Journey must use either graph nodes or legacy steps, not both")
        graph_nodes = self.nodes or self.steps
        if not graph_nodes:
            raise ValueError("Journey must contain at least one Node")
        ids = [node.id for node in graph_nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Journey Node ids must be unique")
        if self.steps and not isinstance(self.steps[0], StageDefinition):
            raise ValueError("Legacy Journey must begin with a Stage")
        if self.steps and self.edges:
            raise ValueError("Legacy Journey steps cannot declare graph edges")
        return self

    @property
    def graph_nodes(self) -> tuple[JourneyNodeDefinition, ...]:
        return self.nodes or self.steps

    @property
    def graph_edges(self) -> tuple[JourneyEdgeDefinition, ...]:
        if self.nodes:
            return self.edges
        return tuple(
            JourneyEdgeDefinition(source=source.id, target=target.id)
            for source, target in zip(self.steps, self.steps[1:], strict=False)
        )


# Transitional import alias for v0.2 application code while the orchestration
# service is migrated to StageDefinition in the next vertical slice.
NodeStepDefinition = StageDefinition

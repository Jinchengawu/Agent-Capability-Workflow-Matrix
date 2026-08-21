"""Capability axis declarations."""

from typing import Literal

from pydantic import Field, field_validator

from .contracts import ImmutableModel


class HermesACPTransport(ImmutableModel):
    type: Literal["hermes_acp"] = "hermes_acp"
    command: tuple[str, ...] = ("hermes", "acp")
    profile: str | None = None
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("env")
    @classmethod
    def environment_values_are_names(cls, value: dict[str, str]) -> dict[str, str]:
        import re

        for name in value.values():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                raise ValueError("environment values must be an environment variable name")
        return value


class PermissionPolicy(ImmutableModel):
    workspace_edits: Literal["allow", "ask", "deny"] = "ask"
    command_allowlist: tuple[str, ...] = ()


class CapabilityDescriptor(ImmutableModel):
    id: str
    version: str
    labels: tuple[str, ...] = ()
    transport: HermesACPTransport
    permissions: PermissionPolicy = PermissionPolicy()


class CapabilitySession(ImmutableModel):
    """A logical Agent session that never crosses a Stage or Workflow Mode boundary."""

    id: str
    journey_id: str
    stage_id: str
    workflow_mode: str

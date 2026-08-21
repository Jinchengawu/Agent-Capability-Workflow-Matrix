"""Provider-neutral Capability declarations and compatibility vocabulary."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from .contracts import ImmutableModel


class CapabilityFeature(StrEnum):
    TEXT_FINAL = "io.text.final"
    TEXT_STREAM = "io.text.stream"
    MULTI_TURN = "interaction.multi_turn"
    CWD_BINDING = "workspace.cwd_binding"
    PERMISSION = "control.permission"
    REMOTE_STOP = "control.remote_stop"
    TOOL_EVENTS = "events.tool"


class CapabilityPolicy(ImmutableModel):
    version: str = "1.0"
    workspace_edits: Literal["allow", "ask", "deny"] = "ask"
    command_allowlist: tuple[str, ...] = ()


class CapabilityDescriptor(ImmutableModel):
    id: str
    version: str
    labels: tuple[str, ...] = ()
    adapter_type: str
    policy: CapabilityPolicy = CapabilityPolicy()


class WorkflowRequirements(ImmutableModel):
    mode_id: str
    mode_version: str
    required: frozenset[CapabilityFeature]
    optional: frozenset[CapabilityFeature] = frozenset()


class AdapterManifest(ImmutableModel):
    adapter_type: str
    adapter_version: str
    features: frozenset[CapabilityFeature]


class ResolvedCapability(ImmutableModel):
    capability_id: str
    capability_version: str
    adapter_type: str
    adapter_version: str
    features: frozenset[CapabilityFeature]
    required_features: frozenset[CapabilityFeature]
    config_fingerprint: str
    policy_version: str
    policy_fingerprint: str
    resolution_schema_version: str = "1.0"

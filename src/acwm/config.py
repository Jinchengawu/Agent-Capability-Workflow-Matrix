"""Schema-v2 configuration with provider-specific data outside the domain module."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator

from acwm.domain.capability import CapabilityDescriptor, CapabilityPolicy
from acwm.domain.journey_definition import JourneyDefinition


class ConfigurationError(ValueError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HermesACPConfig(_StrictModel):
    command: tuple[str, ...] = ("hermes", "acp")
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("env")
    @classmethod
    def env_values_are_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) for name in value.values()):
            raise ValueError("environment values must be an environment variable name")
        return value


class HttpSyncConfig(_StrictModel):
    endpoint: HttpUrl
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    bearer_token_env: str | None = None

    @field_validator("bearer_token_env")
    @classmethod
    def token_reference_is_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
            raise ValueError("bearer_token_env must be an environment variable name")
        return value


class CodexCLIConfig(_StrictModel):
    command: tuple[str, ...] = ("codex",)
    timeout_seconds: int = Field(default=180, ge=1, le=3600)
    sandbox: Literal["read-only", "workspace-write"] = "workspace-write"


class HermesAdapterSpec(_StrictModel):
    type: Literal["hermes.acp"]
    config: HermesACPConfig = HermesACPConfig()


class HttpAdapterSpec(_StrictModel):
    type: Literal["http.sync"]
    config: HttpSyncConfig


class CodexAdapterSpec(_StrictModel):
    type: Literal["codex.cli"]
    config: CodexCLIConfig = CodexCLIConfig()


AdapterSpec = Annotated[
    HermesAdapterSpec | HttpAdapterSpec | CodexAdapterSpec,
    Field(discriminator="type"),
]


class CapabilityEntry(_StrictModel):
    id: str
    version: str
    labels: tuple[str, ...] = ()
    adapter: AdapterSpec
    policy: CapabilityPolicy = CapabilityPolicy()


class CapabilityFile(_StrictModel):
    schema_version: Literal["3"]
    capabilities: tuple[CapabilityEntry, ...]


class JourneyFile(_StrictModel):
    schema_version: Literal["3"]
    journeys: tuple[JourneyDefinition, ...]


@dataclass(frozen=True, slots=True)
class CapabilityCatalog:
    descriptors: dict[str, CapabilityDescriptor]
    adapter_configs: dict[str, AdapterSpec]


def load_capabilities(path: Path) -> CapabilityCatalog:
    try:
        parsed = CapabilityFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(str(error)) from error
    if len({entry.id for entry in parsed.capabilities}) != len(parsed.capabilities):
        raise ConfigurationError("capability ids must be unique")
    return CapabilityCatalog(
        descriptors={
            entry.id: CapabilityDescriptor(
                id=entry.id,
                version=entry.version,
                labels=entry.labels,
                adapter_type=entry.adapter.type,
                policy=entry.policy,
            )
            for entry in parsed.capabilities
        },
        adapter_configs={entry.id: entry.adapter for entry in parsed.capabilities},
    )


def load_journeys(path: Path) -> dict[str, JourneyDefinition]:
    try:
        parsed = JourneyFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(str(error)) from error
    result = {definition.id: definition for definition in parsed.journeys}
    if len(result) != len(parsed.journeys):
        raise ConfigurationError("Journey ids must be unique")
    return result

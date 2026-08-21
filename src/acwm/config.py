"""Versioned YAML configuration loaders."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from .domain.capability import CapabilityDescriptor
from .domain.journey_definition import JourneyDefinition


class ConfigurationError(ValueError):
    pass


class _CapabilityFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: tuple[CapabilityDescriptor, ...]


class _JourneyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    journeys: tuple[JourneyDefinition, ...]


def load_capabilities(path: Path) -> dict[str, CapabilityDescriptor]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = _CapabilityFile.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(str(error)) from error
    result = {descriptor.id: descriptor for descriptor in parsed.capabilities}
    if len(result) != len(parsed.capabilities):
        raise ConfigurationError("capability ids must be unique")
    return result


def load_journeys(path: Path) -> dict[str, JourneyDefinition]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = _JourneyFile.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(str(error)) from error
    result = {definition.id: definition for definition in parsed.journeys}
    if len(result) != len(parsed.journeys):
        raise ConfigurationError("Journey ids must be unique")
    return result

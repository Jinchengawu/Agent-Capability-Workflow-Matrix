"""Provider-neutral Capability and Artifact declarations."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, field_serializer, model_validator

from .capability import (
    ArtifactRequirement,
    CapabilityFeature,
    ResolvedCapability,
    WorkflowRequirements,
)
from .contracts import ImmutableModel


class ArtifactModality(StrEnum):
    TEXT = "text"
    STRUCTURED = "structured"
    FILE = "file"
    RESOURCE = "resource"
    IMAGE = "image"
    AUDIO = "audio"


class TextContentPart(ImmutableModel):
    modality: Literal[ArtifactModality.TEXT] = ArtifactModality.TEXT
    text: str


class StructuredContentPart(ImmutableModel):
    modality: Literal[ArtifactModality.STRUCTURED] = ArtifactModality.STRUCTURED
    data: dict[str, Any] | list[Any]


class FileContentPart(ImmutableModel):
    modality: Literal[ArtifactModality.FILE] = ArtifactModality.FILE
    uri: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResourceContentPart(ImmutableModel):
    modality: Literal[ArtifactModality.RESOURCE] = ArtifactModality.RESOURCE
    uri: str = Field(min_length=1)
    media_type: str | None = None


class ImageContentPart(ImmutableModel):
    modality: Literal[ArtifactModality.IMAGE] = ArtifactModality.IMAGE
    uri: str = Field(min_length=1)
    media_type: str = Field(pattern=r"^image/")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AudioContentPart(ImmutableModel):
    modality: Literal[ArtifactModality.AUDIO] = ArtifactModality.AUDIO
    uri: str = Field(min_length=1)
    media_type: str = Field(pattern=r"^audio/")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


ContentPart = Annotated[
    TextContentPart
    | StructuredContentPart
    | FileContentPart
    | ResourceContentPart
    | ImageContentPart
    | AudioContentPart,
    Field(discriminator="modality"),
]


class ArtifactContract(ImmutableModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    schema_uri: str | None = None
    modalities: frozenset[ArtifactModality] = frozenset({ArtifactModality.TEXT})
    integrity: str = "sha256-required"
    provenance: str = "required"
    verification: str = "schema"

    @field_serializer("modalities")
    def serialize_modalities(
        self, value: frozenset[ArtifactModality]
    ) -> tuple[str, ...]:
        return tuple(sorted(item.value for item in value))

    def payload(self) -> dict[str, Any]:
        """Return the canonical contract body without an addressing hash."""

        return self.model_dump(mode="json")

    def content_sha256(self) -> str:
        """Address this exact schema/modality contract independently of a Provider."""

        canonical = json.dumps(
            self.payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class ProviderCapability(ImmutableModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class CapabilityProviderManifest(ImmutableModel):
    provider_id: str = Field(min_length=1)
    provider_revision: str = Field(min_length=1)
    capabilities: tuple[ProviderCapability, ...]
    workflow_modes: tuple[str, ...]
    required_features: frozenset[CapabilityFeature] = frozenset()
    optional_features: frozenset[CapabilityFeature] = frozenset()
    input_contracts: tuple[ArtifactContract, ...] = ()
    output_contracts: tuple[ArtifactContract, ...] = ()
    permission_requirements: tuple[str, ...] = ()
    manifest_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_serializer("required_features", "optional_features")
    def serialize_features(
        self, value: frozenset[CapabilityFeature]
    ) -> tuple[str, ...]:
        return tuple(sorted(item.value for item in value))

    @model_validator(mode="after")
    def has_unambiguous_declarations(self) -> CapabilityProviderManifest:
        capability_ids = [item.id for item in self.capabilities]
        if not capability_ids:
            raise ValueError("provider must declare at least one capability")
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability ids must be unique")
        if not self.workflow_modes:
            raise ValueError("provider must declare at least one workflow mode")
        if self.required_features & self.optional_features:
            raise ValueError("required and optional features must be disjoint")
        return self

    @classmethod
    def create(cls, **values: Any) -> CapabilityProviderManifest:
        candidate = cls.model_validate(
            {**values, "manifest_fingerprint": "0" * 64}
        )
        return candidate.model_copy(
            update={"manifest_fingerprint": cls.compute_fingerprint(candidate.payload())}
        )

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_fingerprint"})

    @staticmethod
    def compute_fingerprint(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=lambda value: value.model_dump(mode="json"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def verify(self) -> bool:
        return self.manifest_fingerprint == self.compute_fingerprint(self.payload())


class ProviderBindingSite(ImmutableModel):
    node_path: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    slot: str = Field(min_length=1)

    @property
    def reference(self) -> str:
        return f"{self.node_path}.{self.slot}"


class ResolvedProviderBinding(ImmutableModel):
    site: ProviderBindingSite
    capability: ResolvedCapability
    provider: CapabilityProviderManifest
    workflow_mode: str
    workflow_version: str
    input_artifact_requirements: tuple[ArtifactRequirement, ...] = ()
    output_artifact_requirements: tuple[ArtifactRequirement, ...] = ()
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolution_schema_version: str = "1.0"

    @model_validator(mode="after")
    def is_compatible(self) -> ResolvedProviderBinding:
        if not self.provider.verify():
            raise ValueError("provider manifest fingerprint is invalid")
        provided = {item.id: item.version for item in self.provider.capabilities}
        actual_version = provided.get(self.capability.capability_id)
        if actual_version is None:
            raise ValueError(
                f"provider {self.provider.provider_id} does not provide "
                f"{self.capability.capability_id}"
            )
        if actual_version != self.capability.capability_version:
            raise ValueError(
                f"provider capability version {actual_version} does not match "
                f"resolved version {self.capability.capability_version}"
            )
        if self.workflow_mode not in self.provider.workflow_modes:
            raise ValueError(
                f"provider {self.provider.provider_id} does not support "
                f"{self.workflow_mode}"
            )
        missing = self.provider.required_features - self.capability.features
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"provider requires unavailable features: {names}")
        self._validate_artifacts(
            "input", self.provider.input_contracts, self.input_artifact_requirements
        )
        self._validate_artifacts(
            "output", self.provider.output_contracts, self.output_artifact_requirements
        )
        return self

    @staticmethod
    def _validate_artifacts(
        direction: str,
        provided_contracts: tuple[ArtifactContract, ...],
        requirements: tuple[ArtifactRequirement, ...],
    ) -> None:
        provided = {(item.id, item.version): item for item in provided_contracts}
        for requirement in requirements:
            contract = provided.get((requirement.id, requirement.version))
            if contract is None:
                raise ValueError(
                    f"provider missing {direction} artifact contract {requirement.id}"
                )
            offered = {item.value for item in contract.modalities}
            if requirement.modalities and not requirement.modalities <= offered:
                raise ValueError(
                    f"provider artifact contract {requirement.id} has incompatible modalities"
                )

    @classmethod
    def create(
        cls,
        *,
        site: ProviderBindingSite,
        capability: ResolvedCapability,
        provider: CapabilityProviderManifest,
        requirements: WorkflowRequirements,
    ) -> ResolvedProviderBinding:
        candidate = cls.model_validate(
            {
                "site": site,
                "capability": capability,
                "provider": provider,
                "workflow_mode": requirements.mode_id,
                "workflow_version": requirements.mode_version,
                "input_artifact_requirements": requirements.input_artifacts,
                "output_artifact_requirements": requirements.output_artifacts,
                "binding_fingerprint": "0" * 64,
            }
        )
        return candidate.model_copy(
            update={"binding_fingerprint": candidate.compute_binding_fingerprint()}
        )

    def compute_binding_fingerprint(self) -> str:
        payload = {
            "site": self.site.model_dump(mode="json"),
            "capability_id": self.capability.capability_id,
            "capability_version": self.capability.capability_version,
            "adapter_type": self.capability.adapter_type,
            "adapter_version": self.capability.adapter_version,
            "features": sorted(item.value for item in self.capability.features),
            "required_features": sorted(
                item.value for item in self.capability.required_features
            ),
            "config_fingerprint": self.capability.config_fingerprint,
            "policy_version": self.capability.policy_version,
            "policy_fingerprint": self.capability.policy_fingerprint,
            "provider_fingerprint": self.provider.manifest_fingerprint,
            "workflow_mode": self.workflow_mode,
            "workflow_version": self.workflow_version,
            "input_artifact_requirements": [
                item.model_dump(mode="json")
                for item in self.input_artifact_requirements
            ],
            "output_artifact_requirements": [
                item.model_dump(mode="json")
                for item in self.output_artifact_requirements
            ],
            "resolution_schema_version": self.resolution_schema_version,
        }
        return CapabilityProviderManifest.compute_fingerprint(payload)

    def verify(self) -> bool:
        return (
            self.provider.verify()
            and self.binding_fingerprint == self.compute_binding_fingerprint()
        )

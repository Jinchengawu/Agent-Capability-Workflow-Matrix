import pytest
from pydantic import TypeAdapter, ValidationError

from acwm.domain import (
    ArtifactContract,
    ArtifactModality,
    ArtifactRequirement,
    CapabilityFeature,
    CapabilityProviderManifest,
    ContentPart,
    ProviderBindingSite,
    ProviderCapability,
    ResolvedCapability,
    ResolvedProviderBinding,
    WorkflowRequirements,
)


def test_provider_manifest_is_content_addressed_and_multimodal() -> None:
    design_input = ArtifactContract(
        id="design.brief",
        version="1.0",
        schema_uri="schema://design-brief@1",
        modalities=frozenset({ArtifactModality.STRUCTURED, ArtifactModality.IMAGE}),
        integrity="sha256-required",
        provenance="required",
        verification="schema",
    )
    implementation = ArtifactContract(
        id="candidate.change",
        version="1.0",
        schema_uri="schema://candidate-change@1",
        modalities=frozenset({ArtifactModality.FILE, ArtifactModality.STRUCTURED}),
        integrity="sha256-required",
        provenance="required",
        verification="machine",
    )

    manifest = CapabilityProviderManifest.create(
        provider_id="frontend-engineer",
        provider_revision="3",
        capabilities=(
            ProviderCapability(id="frontend.implementation", version="1.0"),
        ),
        workflow_modes=("code-delivery",),
        required_features=frozenset({CapabilityFeature.CWD_BINDING}),
        optional_features=frozenset({CapabilityFeature.TOOL_EVENTS}),
        input_contracts=(design_input,),
        output_contracts=(implementation,),
        permission_requirements=("workspace.write",),
    )

    assert manifest.verify()
    assert len(manifest.manifest_fingerprint) == 64
    assert manifest.output_contracts[0].modalities == frozenset(
        {ArtifactModality.FILE, ArtifactModality.STRUCTURED}
    )

    restored = CapabilityProviderManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    assert restored.verify()


def test_provider_manifest_rejects_duplicate_capabilities() -> None:
    with pytest.raises(ValidationError, match="capability ids must be unique"):
        CapabilityProviderManifest.create(
            provider_id="duplicate-provider",
            provider_revision="1",
            capabilities=(
                ProviderCapability(id="analysis", version="1"),
                ProviderCapability(id="analysis", version="2"),
            ),
            workflow_modes=("agentscope.role-turn",),
        )


def test_content_part_rejects_payload_that_does_not_match_modality() -> None:
    adapter = TypeAdapter(ContentPart)

    structured = adapter.validate_python(
        {"modality": "structured", "data": {"acceptance_ids": ["AC-1"]}}
    )
    assert structured.data == {"acceptance_ids": ["AC-1"]}

    with pytest.raises(ValidationError):
        adapter.validate_python({"modality": "image", "text": "not an image"})


def test_provider_binding_rejects_missing_workflow_artifact_contract() -> None:
    provider = CapabilityProviderManifest.create(
        provider_id="pm-agent",
        provider_revision="1",
        capabilities=(ProviderCapability(id="planning", version="1.0"),),
        workflow_modes=("agentscope.role-turn",),
        output_contracts=(
            ArtifactContract(
                id="requirement",
                version="1.0",
                modalities=frozenset({ArtifactModality.TEXT}),
            ),
        ),
    )
    capability = ResolvedCapability(
        capability_id="planning",
        capability_version="1.0",
        adapter_type="hermes.acp",
        adapter_version="1",
        features=frozenset({CapabilityFeature.TEXT_FINAL}),
        required_features=frozenset({CapabilityFeature.TEXT_FINAL}),
        config_fingerprint="config",
        policy_version="1",
        policy_fingerprint="policy",
    )

    with pytest.raises(ValidationError, match="missing output artifact contract task"):
        ResolvedProviderBinding.create(
            site=ProviderBindingSite(
                node_path="requirements", stage_id="requirements", slot="actor"
            ),
            capability=capability,
            provider=provider,
            requirements=WorkflowRequirements(
                mode_id="agentscope.role-turn",
                mode_version="1",
                required=frozenset({CapabilityFeature.TEXT_FINAL}),
                output_artifacts=(
                    ArtifactRequirement(
                        id="task", version="1.0", modalities=frozenset()
                    ),
                ),
            ),
        )

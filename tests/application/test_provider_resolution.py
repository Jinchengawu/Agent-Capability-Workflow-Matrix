import pytest

from acwm.application import (
    DefaultProviderResolver,
    ProviderResolutionError,
    enumerate_provider_binding_sites,
)
from acwm.domain import (
    CapabilityFeature,
    CapabilityProviderManifest,
    JourneyDefinition,
    LoopDefinition,
    LoopPolicyDefinition,
    ProviderBindingSite,
    ProviderCapability,
    ResolvedCapability,
    StageDefinition,
    WorkflowRequirements,
)


def _capability() -> ResolvedCapability:
    return ResolvedCapability(
        capability_id="frontend.implementation",
        capability_version="1.2.0",
        adapter_type="codex.cli",
        adapter_version="1.0.0",
        features=frozenset(
            {CapabilityFeature.TEXT_FINAL, CapabilityFeature.CWD_BINDING}
        ),
        required_features=frozenset({CapabilityFeature.TEXT_FINAL}),
        config_fingerprint="c" * 64,
        policy_version="1",
        policy_fingerprint="p" * 64,
    )


def _provider() -> CapabilityProviderManifest:
    return CapabilityProviderManifest.create(
        provider_id="codex-frontend",
        provider_revision="1",
        capabilities=(
            ProviderCapability(id="frontend.implementation", version="1.2.0"),
        ),
        workflow_modes=("agentscope.role-turn",),
        permission_requirements=("workspace:write",),
    )


def test_default_provider_resolver_returns_structured_binding() -> None:
    site = ProviderBindingSite(node_path="requirements", stage_id="requirements", slot="actor")
    resolver = DefaultProviderResolver(
        {site.reference: _provider()},
        version_constraints={site.reference: ">=1,<2"},
        granted_permissions={site.reference: frozenset({"workspace:write"})},
    )

    binding = resolver.resolve(
        site,
        _capability(),
        WorkflowRequirements(
            mode_id="agentscope.role-turn",
            mode_version="1.0.0",
            required=frozenset({CapabilityFeature.TEXT_FINAL}),
        ),
    )

    assert binding.verify()
    assert binding.site.reference == "requirements.actor"


def test_default_provider_resolver_reports_stable_permission_error() -> None:
    site = ProviderBindingSite(node_path="requirements", stage_id="requirements", slot="actor")
    resolver = DefaultProviderResolver({site.reference: _provider()})

    with pytest.raises(ProviderResolutionError) as captured:
        resolver.resolve(
            site,
            _capability(),
            WorkflowRequirements(
                mode_id="agentscope.role-turn",
                mode_version="1.0.0",
                required=frozenset({CapabilityFeature.TEXT_FINAL}),
            ),
        )

    assert captured.value.code == "PROVIDER_PERMISSION_MISSING"


def test_binding_site_enumerator_includes_loop_path() -> None:
    definition = JourneyDefinition(
        id="delivery",
        version="1",
        nodes=(
            StageDefinition(
                id="requirements",
                workflow_mode="agentscope.role-turn",
                bindings={"actor": "hermes-pm"},
            ),
            LoopDefinition(
                id="code-repair",
                nodes=(
                    StageDefinition(
                        id="code-repair-work",
                        workflow_mode="code-delivery",
                        bindings={"developer": "codex-backend"},
                    ),
                ),
                edges=(),
                policy=LoopPolicyDefinition(
                    exit_condition="tests-passed",
                    max_iterations=3,
                    timeout_seconds=60,
                ),
            ),
        ),
    )

    sites = enumerate_provider_binding_sites(definition)

    assert [site.reference for site in sites] == [
        "requirements.actor",
        "code-repair/code-repair-work.developer",
    ]

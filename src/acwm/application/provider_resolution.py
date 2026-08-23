"""Structured Provider resolution and binding-site discovery."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from acwm.domain import (
    CapabilityProviderManifest,
    JourneyDefinition,
    LoopDefinition,
    ProviderBindingSite,
    ResolvedCapability,
    ResolvedProviderBinding,
    StageDefinition,
    WorkflowRequirements,
)
from acwm.domain.contracts import ImmutableModel


class ProviderResolutionError(ValueError):
    """Fail-closed Provider resolution error with a stable product code."""

    def __init__(self, code: str, detail: str, repair: str) -> None:
        self.code = code
        self.detail = detail
        self.repair = repair
        super().__init__(detail)


class ProviderResolutionIssue(ImmutableModel):
    code: str
    detail: str
    repair: str


class ProviderResolutionRequest(ImmutableModel):
    site: ProviderBindingSite
    capability: ResolvedCapability
    requirements: WorkflowRequirements
    provider: CapabilityProviderManifest
    capability_version_constraint: str | None = None
    granted_permissions: frozenset[str] = frozenset()


class ProviderResolutionReport(ImmutableModel):
    status: Literal["passed", "failed"]
    site_reference: str
    binding: ResolvedProviderBinding | None = None
    issues: tuple[ProviderResolutionIssue, ...] = ()


class DefaultProviderResolver:
    """Resolve one immutable Provider per Stage binding site.

    Deployment selection remains a product concern. This service owns the
    provider-neutral compatibility decision and its stable failure vocabulary.
    """

    def __init__(
        self,
        assignments: Mapping[str, CapabilityProviderManifest],
        *,
        version_constraints: Mapping[str, str] | None = None,
        granted_permissions: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self.assignments = dict(assignments)
        self.version_constraints = dict(version_constraints or {})
        self.granted_permissions = dict(granted_permissions or {})

    def resolve(
        self,
        site: ProviderBindingSite,
        capability: ResolvedCapability,
        requirements: WorkflowRequirements,
    ) -> ResolvedProviderBinding:
        provider = self.assignments.get(site.reference)
        if provider is None:
            raise ProviderResolutionError(
                "PROVIDER_ASSIGNMENT_MISSING",
                f"provider assignment missing for {site.reference}",
                "Assign a qualified Provider to the Stage binding site.",
            )
        report = self.inspect(
            ProviderResolutionRequest(
                site=site,
                capability=capability,
                requirements=requirements,
                provider=provider,
                capability_version_constraint=self.version_constraints.get(site.reference),
                granted_permissions=self.granted_permissions.get(
                    site.reference, frozenset()
                ),
            )
        )
        if report.binding is None:
            issue = report.issues[0]
            raise ProviderResolutionError(issue.code, issue.detail, issue.repair)
        return report.binding

    def inspect(self, request: ProviderResolutionRequest) -> ProviderResolutionReport:
        issue = self._preflight(request)
        if issue is not None:
            return ProviderResolutionReport(
                status="failed",
                site_reference=request.site.reference,
                issues=(issue,),
            )
        try:
            binding = ResolvedProviderBinding.create(
                site=request.site,
                capability=request.capability,
                provider=request.provider,
                requirements=request.requirements,
            )
        except ValueError as error:
            return ProviderResolutionReport(
                status="failed",
                site_reference=request.site.reference,
                issues=(
                    ProviderResolutionIssue(
                        code="PROVIDER_INCOMPATIBLE",
                        detail=str(error),
                        repair=(
                            "Choose a Provider whose Capability, Workflow, Feature "
                            "and Artifact contracts match."
                        ),
                    ),
                ),
            )
        return ProviderResolutionReport(
            status="passed",
            site_reference=request.site.reference,
            binding=binding,
        )

    @staticmethod
    def _preflight(
        request: ProviderResolutionRequest,
    ) -> ProviderResolutionIssue | None:
        constraint = request.capability_version_constraint
        if constraint and not _version_satisfies(
            request.capability.capability_version, constraint
        ):
            return ProviderResolutionIssue(
                code="CAPABILITY_VERSION_INCOMPATIBLE",
                detail=(
                    f"capability version {request.capability.capability_version} "
                    f"does not satisfy {constraint}"
                ),
                repair=(
                    "Publish or select a Provider matching the Agent Profile "
                    "version constraint."
                ),
            )
        missing = set(request.provider.permission_requirements) - set(
            request.granted_permissions
        )
        if missing:
            return ProviderResolutionIssue(
                code="PROVIDER_PERMISSION_MISSING",
                detail="provider requires unavailable permissions: "
                + ", ".join(sorted(missing)),
                repair="Grant only the required permissions or select a less privileged Provider.",
            )
        return None


def enumerate_provider_binding_sites(
    definition: JourneyDefinition,
) -> tuple[ProviderBindingSite, ...]:
    """Return canonical Stage path + Slot sites, including bounded LOOP bodies."""

    sites: list[ProviderBindingSite] = []
    for node in definition.graph_nodes:
        if isinstance(node, StageDefinition):
            sites.extend(_stage_sites(node, node.id))
        elif isinstance(node, LoopDefinition):
            for child in node.nodes:
                if isinstance(child, StageDefinition):
                    sites.extend(_stage_sites(child, f"{node.id}/{child.id}"))
    return tuple(sites)


def _stage_sites(
    stage: StageDefinition, node_path: str
) -> tuple[ProviderBindingSite, ...]:
    return tuple(
        ProviderBindingSite(node_path=node_path, stage_id=stage.id, slot=slot)
        for slot in stage.bindings
    )


def _version_satisfies(version: str, constraint: str) -> bool:
    actual = _version_tuple(version)
    for clause in (item.strip() for item in constraint.split(",")):
        if not clause:
            continue
        match = re.fullmatch(r"(>=|<=|==|>|<)?\s*([0-9]+(?:\.[0-9]+){0,2})", clause)
        if match is None:
            return False
        operator = match.group(1) or "=="
        expected = _version_tuple(match.group(2))
        comparisons = {
            "==": actual == expected,
            ">=": actual >= expected,
            "<=": actual <= expected,
            ">": actual > expected,
            "<": actual < expected,
        }
        if not comparisons[operator]:
            return False
    return True


def _version_tuple(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0]
    parts = core.split(".")
    if not parts or len(parts) > 3 or any(not part.isdigit() for part in parts):
        return (-1, -1, -1)
    padded = (*parts, "0", "0")
    return (int(padded[0]), int(padded[1]), int(padded[2]))

"""Framework-independent ACWM application use cases.

The v0.2 reference Journey service remains available through lazy attributes,
but importing :mod:`acwm.application` no longer imports server or adapter
dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .provider_resolution import (
    DefaultProviderResolver,
    ProviderResolutionError,
    ProviderResolutionIssue,
    ProviderResolutionReport,
    ProviderResolutionRequest,
    enumerate_provider_binding_sites,
)
from .workflow_runtime import (
    DefaultWorkflowRuntime,
    WorkflowBindingError,
    WorkflowNotFoundError,
    WorkflowRuntimeError,
)

__all__ = [
    "DefaultWorkflowRuntime",
    "DefaultProviderResolver",
    "JourneyNotFoundError",
    "JourneyService",
    "ProviderResolutionError",
    "ProviderResolutionIssue",
    "ProviderResolutionReport",
    "ProviderResolutionRequest",
    "StaleDecisionError",
    "WorkflowBindingError",
    "WorkflowNotFoundError",
    "WorkflowRuntimeError",
    "enumerate_provider_binding_sites",
]


def __getattr__(name: str) -> Any:
    if name in {"JourneyNotFoundError", "JourneyService", "StaleDecisionError"}:
        return getattr(import_module("acwm.application.service"), name)
    raise AttributeError(name)

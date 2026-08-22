"""Optional reference adapters, loaded only when explicitly requested."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AgentScopeRoleTurnAdapter": "acwm.adapters.agentscope_role_turn",
    "ArtifactStore": "acwm.adapters.artifacts",
    "CodexCLICapabilityAdapter": "acwm.adapters.codex_cli",
    "CodeDeliveryWorkflowAdapter": "acwm.adapters.code_delivery",
    "GitWorkspaceManager": "acwm.adapters.git_workspace",
    "GraphRunVersionConflict": "acwm.adapters.sqlite_store",
    "HermesACPCapabilityAdapter": "acwm.adapters.hermes_acp",
    "HttpSyncCapabilityAdapter": "acwm.adapters.http_sync",
    "IdempotencyConflictError": "acwm.adapters.sqlite_store",
    "LegacyDataDirError": "acwm.adapters.sqlite_store",
    "ManagedWorkspace": "acwm.adapters.git_workspace",
    "SQLiteStore": "acwm.adapters.sqlite_store",
    "WorkspaceError": "acwm.adapters.git_workspace",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)

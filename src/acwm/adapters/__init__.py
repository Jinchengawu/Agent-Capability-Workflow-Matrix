"""Reference infrastructure adapters."""

from .artifacts import ArtifactStore
from .git_workspace import GitWorkspaceManager, ManagedWorkspace, WorkspaceError
from .hermes_acp import HermesACPCapabilityAdapter
from .http_sync import HttpSyncCapabilityAdapter
from .sqlite_store import IdempotencyConflictError, LegacyDataDirError, SQLiteStore

__all__ = [
    "ArtifactStore",
    "GitWorkspaceManager",
    "HermesACPCapabilityAdapter",
    "HttpSyncCapabilityAdapter",
    "IdempotencyConflictError",
    "LegacyDataDirError",
    "ManagedWorkspace",
    "SQLiteStore",
    "WorkspaceError",
]

"""Isolated, single-writer Git worktrees."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ManagedWorkspace:
    path: Path
    base_sha: str
    branch: str


class GitWorkspaceManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, journey_id: str, repository: Path, base_ref: str) -> ManagedWorkspace:
        repository = repository.resolve(strict=True)
        if not (repository / ".git").exists():
            raise WorkspaceError("repository must be a Git working tree")
        base_sha = self._git(repository, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        path = (self.root / journey_id).resolve()
        if self.root not in path.parents:
            raise WorkspaceError("managed worktree escaped ACWM_DATA_DIR")
        branch = f"acwm/{journey_id}"
        if path.exists():
            existing = self._git(path, "rev-parse", "HEAD")
            if existing != base_sha and not self._git(path, "status", "--porcelain"):
                raise WorkspaceError("existing worktree does not match the recorded base SHA")
            return ManagedWorkspace(path=path, base_sha=base_sha, branch=branch)
        self._git(repository, "worktree", "add", "-b", branch, str(path), base_sha)
        return ManagedWorkspace(path=path, base_sha=base_sha, branch=branch)

    def patch(self, workspace: ManagedWorkspace) -> bytes:
        tracked = subprocess.check_output(
            ["git", "diff", "--binary", workspace.base_sha, "--"], cwd=workspace.path
        )
        untracked_output = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=workspace.path,
        )
        untracked_patches: list[bytes] = []
        for raw_path in untracked_output.split(b"\0"):
            if not raw_path:
                continue
            relative_path = raw_path.decode()
            candidate = (workspace.path / relative_path).resolve()
            try:
                candidate.relative_to(workspace.path.resolve())
            except ValueError as error:
                raise WorkspaceError("untracked file escaped the managed worktree") from error
            result = subprocess.run(
                ["git", "diff", "--no-index", "--binary", "--", "/dev/null", relative_path],
                cwd=workspace.path,
                capture_output=True,
                check=False,
            )
            if result.returncode not in {0, 1}:
                raise WorkspaceError(result.stderr.decode(errors="replace").strip())
            untracked_patches.append(result.stdout)
        return tracked + b"".join(untracked_patches)

    def manifest(self, workspace: ManagedWorkspace) -> dict[str, object]:
        status = self._git(workspace.path, "status", "--porcelain=v1")
        return {
            "base_sha": workspace.base_sha,
            "branch": workspace.branch,
            "worktree": str(workspace.path),
            "status": status.splitlines() if status else [],
        }

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT
            ).strip()
        except subprocess.CalledProcessError as error:
            raise WorkspaceError(error.output.strip()) from error

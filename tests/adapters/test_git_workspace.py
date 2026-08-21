import subprocess
from pathlib import Path

from acwm.adapters import GitWorkspaceManager


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def test_patch_contains_untracked_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "acwm@example.test")
    _git(source, "config", "user.name", "ACWM Test")
    (source / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-m", "fixture")

    manager = GitWorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("journey-1", source, "HEAD")
    (workspace.path / "new.txt").write_text("new content\n", encoding="utf-8")

    patch = manager.patch(workspace).decode()

    assert "new.txt" in patch
    assert "+new content" in patch

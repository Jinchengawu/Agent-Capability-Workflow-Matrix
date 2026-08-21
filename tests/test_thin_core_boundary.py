import subprocess
import sys


def test_core_and_application_import_without_framework_or_server_dependencies() -> None:
    script = r'''
import importlib.abc
import sys

blocked = {"acp", "agentscope", "fastapi", "httpx", "langgraph", "sse_starlette", "uvicorn"}

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".", 1)[0] in blocked:
            raise ModuleNotFoundError(f"blocked optional dependency: {fullname}")
        return None

sys.meta_path.insert(0, Blocker())
import acwm.domain
import acwm.application
from acwm.application.workflow_runtime import DefaultWorkflowRuntime
assert DefaultWorkflowRuntime
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

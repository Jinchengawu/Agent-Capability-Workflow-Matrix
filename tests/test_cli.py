import subprocess
import sys


def test_cli_exposes_single_worker_serve_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "acwm.cli", "serve", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--capabilities" in result.stdout
    assert "--journeys" in result.stdout
    assert "--port" in result.stdout

"""The console script and ``python -m control_plane`` must stay interchangeable.

The module form is what users fall back to when the interpreter's scripts
directory is not on PATH, so it is a supported entrypoint, not a convenience.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "control_plane", *args],
        capture_output=True,
        text=True,
    )


def test_module_form_is_runnable() -> None:
    proc = _run("--help")
    assert proc.returncode == 0, proc.stderr
    assert "serve" in proc.stdout


def test_module_form_names_itself_in_usage() -> None:
    """Hints must be copy-pasteable for whoever cannot reach the console script."""
    proc = _run("--help")
    assert "usage: python -m control_plane" in proc.stdout


def test_module_form_hints_use_the_module_form() -> None:
    proc = _run("ui")
    assert "python -m control_plane serve" in proc.stdout
    assert "control-plane serve" not in proc.stdout


@pytest.mark.parametrize(
    "argv0,expected",
    [("control-plane", "control-plane"), ("__main__.py", "python -m control_plane")],
)
def test_prog_follows_invocation(
    argv0: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from control_plane import cli

    monkeypatch.setattr(sys, "argv", [argv0])
    assert cli._prog() == expected

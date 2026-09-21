"""Shared helpers for running the CLI in-process and as a subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest

from lecture2notes import _out


@pytest.fixture(autouse=True)
def reset_output_flags():
    """Keep the module-level output flags from leaking between tests."""
    _out.reset()
    yield
    _out.reset()


def run_cli(
    args: Sequence[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    encoding: str = "utf-8",
) -> subprocess.CompletedProcess:
    """Run `python -m lecture2notes <args>` in a child process."""
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-m", "lecture2notes", *args],
        cwd=str(cwd) if cwd else None,
        env=child_env,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
    )


def minimal_path(extra: Optional[Path] = None) -> str:
    """A PATH that still lets Python start but contains no pipeline binaries."""
    parts: List[str] = [str(Path(sys.executable).parent)]
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        parts.append(str(Path(system_root) / "System32"))
    if extra is not None:
        parts.insert(0, str(extra))
    return os.pathsep.join(parts)


def listdir_set(path: Path) -> set:
    return {p.name for p in path.iterdir()}

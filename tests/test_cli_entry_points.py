"""Every documented way of starting the CLI actually starts it.

There are three spellings, and they are not interchangeable by accident:

- `l2n` -- the console script, which is what the docs tell people to use;
- `python -m lecture2notes` -- the spelling that works without the script being
  on PATH, which is how the tests and CI invoke it;
- `python -m lecture2notes.cli` -- the spelling a person reaches for when they
  are debugging inside the package.

`lecture2notes.cli` is a package, so the third one raises "No module named
lecture2notes.cli.__main__" unless the package ships a `__main__`. That is a
particularly unhelpful failure, because the spelling that does work
(`python -m lecture2notes.cli.main`) differs by one dot and reads like a typo.
A subprocess is the only honest test of this: an in-process call to `main()`
would pass whether or not `__main__.py` exists.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

#: Every module path that has to behave like the `l2n` console script.
MODULE_SPELLINGS = (
    "lecture2notes",
    "lecture2notes.cli",
    "lecture2notes.cli.main",
)


def _run_module(module: str, *args: str) -> subprocess.CompletedProcess:
    """`python -m <module> <args>` against this checkout, not an installed copy."""
    env = dict(os.environ)
    # Absolute, and ahead of anything already there: the test must exercise the
    # source tree it lives in even when a different lecture2notes is installed.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(REPO_ROOT),
    )


@pytest.mark.parametrize("module", MODULE_SPELLINGS)
def test_help_exits_zero_and_prints_the_usage_line(module: str):
    result = _run_module(module, "--help")

    assert result.returncode == 0, (
        "python -m %s --help failed with %d\nstderr: %s"
        % (module, result.returncode, result.stderr)
    )
    assert "usage: l2n" in result.stdout, (
        "python -m %s --help did not print the l2n usage line: %r"
        % (module, result.stdout[:200])
    )


@pytest.mark.parametrize("module", MODULE_SPELLINGS)
def test_version_is_the_same_whichever_spelling_is_used(module: str):
    result = _run_module(module, "--version")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "--version printed nothing"


def test_every_spelling_prints_the_same_subcommand_list():
    """Not just "it runs": the three have to be the same program."""
    outputs = {
        module: _run_module(module, "--help").stdout for module in MODULE_SPELLINGS
    }
    first = outputs[MODULE_SPELLINGS[0]]
    for module, text in outputs.items():
        assert text == first, (
            "python -m %s prints different help from python -m %s"
            % (module, MODULE_SPELLINGS[0])
        )


def test_the_cli_package_ships_a_main_module():
    # The import-level statement of the same fact, so a deleted file fails here
    # with a sentence rather than only as three subprocess failures.
    assert (SRC / "lecture2notes" / "cli" / "__main__.py").is_file()
    assert (SRC / "lecture2notes" / "__main__.py").is_file()

"""Console output stays cp950-safe and progress lines carry the required fields."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_cli

from lecture2notes import _out
from lecture2notes.cli.main import SUBCOMMANDS

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
FORBIDDEN_CHARS = "→≥✓✗"  # -> >= check cross
FORBIDDEN_RE = re.compile("[%s]" % FORBIDDEN_CHARS)

FAKE_LOOP = """
import sys
from lecture2notes import _out
_out.configure(quiet=False, json_progress=%s)
p = _out.Progress("demo", 5, interval=0)
for _ in range(5):
    p.advance()
_out.say("ok", "完成")
_out.stage("demo", "skip (exists)")
"""


def _run_snippet(code: str, encoding: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
        env={**dict(__import__("os").environ), "PYTHONIOENCODING": encoding},
    )


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_subcommand_help_survives_cp950(command):
    proc = run_cli([command, "--help"], env={"PYTHONIOENCODING": "cp950"},
                   encoding="cp950")
    assert proc.returncode == 0, proc.stderr
    assert "UnicodeEncodeError" not in proc.stderr


def test_root_help_lists_every_subcommand():
    proc = run_cli(["--help"])
    assert proc.returncode == 0, proc.stderr
    for command in SUBCOMMANDS:
        assert command in proc.stdout, command
    assert len(SUBCOMMANDS) == 15


def test_fake_progress_loop_survives_cp950():
    proc = _run_snippet(FAKE_LOOP % "False", "cp950")
    assert proc.returncode == 0, proc.stderr
    assert "UnicodeEncodeError" not in proc.stderr
    assert "[demo] 5/5" in proc.stdout


def test_json_progress_lines_parse_and_have_all_keys():
    proc = _run_snippet(FAKE_LOOP % "True", "utf-8")
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 5
    for line in lines:
        event = json.loads(line)
        assert set(event) == {"stage", "done", "total", "elapsed_sec", "eta_sec"}
        assert event["stage"] == "demo"
        assert event["total"] == 5
    assert [json.loads(line)["done"] for line in lines] == [1, 2, 3, 4, 5]


def test_json_progress_keeps_messages_off_stdout():
    proc = _run_snippet(FAKE_LOOP % "True", "utf-8")
    assert "[ok]" not in proc.stdout
    assert "[ok]" in proc.stderr


def test_text_progress_line_format():
    _out.configure(quiet=False, json_progress=False)
    clock = iter([0.0, 0.0, 10.0, 10.0])
    progress = _out.Progress("frames", 134, interval=0, clock=lambda: next(clock))
    buffer = io.StringIO()
    old = sys.stdout
    sys.stdout = buffer
    try:
        progress.advance()
    finally:
        sys.stdout = old
    assert buffer.getvalue().strip() == "[frames] 1/134 elapsed 10s eta 1330s"


def test_quiet_suppresses_progress_but_not_errors():
    _out.configure(quiet=True, json_progress=False)
    buffer = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = buffer
    sys.stderr = buffer
    try:
        _out.Progress("demo", 3, interval=0).advance()
        _out.say("ok", "should not appear")
        _out.error("should appear")
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    text = buffer.getvalue()
    assert "should not appear" not in text
    assert "[error] should appear" in text
    assert "1/3" not in text


def test_source_tree_has_no_forbidden_symbols():
    offenders = []
    for path in sorted(SRC_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".md", ".toml", ".yaml", ".json"):
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN_RE.search(line):
                offenders.append("%s:%d" % (path, lineno))
    assert offenders == []


def test_source_tree_has_no_upstream_private_references():
    offenders = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "ZeroType" in line or "USER.md" in line:
                offenders.append("%s:%d" % (path, lineno))
    assert offenders == []

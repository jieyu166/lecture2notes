"""A malformed overlay file is an error, not a shrug.

The failure this guards against is specific: a config loader that catches a
parse error and falls back to the defaults hands the user the package's answers
under their own settings' name. They then spend an afternoon wondering why the
file they are looking at does nothing. So a broken file names itself and its
line, exits 2, and contributes nothing -- not even the keys it got right before
the syntax error.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import run_cli
from lecture2notes import exit_codes
from lecture2notes.cli.main import main
from lecture2notes.profiles import layers, loader

FIXTURE_DOC = Path(__file__).parent / "fixtures" / "note_doc.json"

# A valid table for six lines, then a key with no value on line 7. The first
# six lines matter: they are what a "fall back to the defaults" loader would
# have applied, and the test below proves this one applies none of it.
BROKEN_ON_LINE_7 = """# an overlay that starts out fine
# and then does not

pbf = true

[note]
style =
"""

BROKEN_JSON_ON_LINE_4 = """{
  "deterministic": {
    "a": "b",
    "c": ,
  }
}
"""

BROKEN_FRONTMATTER_ON_LINE_5 = """# a frontmatter template
---
title: "{{title}}"
date: {{date}}
this line has no colon
---
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(work)
    return work


def project_overlay(work: Path, name: str, text: str) -> Path:
    directory = work / layers.OVERLAY_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the scenario from the spec: broken TOML on line 7
# --------------------------------------------------------------------------
def test_broken_toml_prints_the_path_and_line_7_and_exits_2(workspace: Path, capsys):
    path = project_overlay(workspace, layers.OUTPUTS_FILE, BROKEN_ON_LINE_7)

    assert main(["profile", "show"]) == exit_codes.ERROR

    out = capsys.readouterr().out
    assert str(path) in out
    assert "line 7" in out


def test_no_part_of_the_broken_file_is_applied(workspace: Path, capsys):
    # Line 4 of the file sets `pbf = true`, which is the opposite of the
    # builtin answer. Nothing may be printed under it.
    project_overlay(workspace, layers.OUTPUTS_FILE, BROKEN_ON_LINE_7)

    assert main(["profile", "show"]) == exit_codes.ERROR
    out = capsys.readouterr().out
    assert "pbf = " not in out
    assert "note.style" not in out


def test_the_resolver_raises_rather_than_returning_half_a_configuration(
    workspace: Path,
):
    project_overlay(workspace, layers.OUTPUTS_FILE, BROKEN_ON_LINE_7)

    with pytest.raises(loader.OverlayError) as excinfo:
        loader.resolve()
    assert excinfo.value.line == 7
    assert excinfo.value.path.name == layers.OUTPUTS_FILE


def test_the_error_message_is_ascii_marked_and_survives_cp950(workspace: Path):
    project_overlay(workspace, layers.OUTPUTS_FILE, BROKEN_ON_LINE_7)

    proc = run_cli(
        ["profile", "show"],
        cwd=workspace,
        env={
            "PYTHONIOENCODING": "cp950",
            "HOME": str(workspace.parent / "home"),
            "USERPROFILE": str(workspace.parent / "home"),
        },
        encoding="cp950",
    )
    assert proc.returncode == exit_codes.ERROR
    assert "line 7" in proc.stdout
    assert "[error]" in proc.stdout
    for banned in ("→", "≥", "✓", "✗"):
        assert banned not in proc.stdout


# --------------------------------------------------------------------------
# the other two parsers report the same way
# --------------------------------------------------------------------------
def test_broken_corrections_json_names_its_line(workspace: Path, capsys):
    path = project_overlay(
        workspace, layers.CORRECTIONS_FILE, BROKEN_JSON_ON_LINE_4
    )

    assert main(["profile", "show"]) == exit_codes.ERROR
    out = capsys.readouterr().out
    assert str(path) in out
    assert "line 4" in out


def test_broken_frontmatter_template_names_its_line(workspace: Path, capsys):
    path = project_overlay(
        workspace, layers.FRONTMATTER_TEMPLATE, BROKEN_FRONTMATTER_ON_LINE_5
    )

    assert main(["profile", "show"]) == exit_codes.ERROR
    out = capsys.readouterr().out
    assert str(path) in out
    assert "line 5" in out


@pytest.mark.parametrize(
    "text, line",
    [
        ("---\ntitle: a\n", 1),                      # fence never closed
        ("---\n  nested: a\n---\n", 2),              # not flat
        ("---\ntitle: a\n---\ntrailing\n", 4),       # after the closing fence
        ("stray text\n---\ntitle: a\n---\n", 1),     # before the opening fence
        ("---\n: a\n---\n", 2),                      # empty field name
    ],
)
def test_frontmatter_template_shapes_that_are_refused(
    workspace: Path, text: str, line: int
):
    path = project_overlay(workspace, layers.FRONTMATTER_TEMPLATE, text)
    with pytest.raises(loader.OverlayError) as excinfo:
        loader.resolve()
    assert excinfo.value.path == path
    assert excinfo.value.line == line


def test_the_shipped_templates_pass_their_own_validator():
    for profile in layers.builtin_profiles():
        path = layers.profile_dir(profile) / layers.FRONTMATTER_TEMPLATE
        if not path.is_file():
            continue
        layers.check_frontmatter_template(
            path.read_text(encoding="utf-8"), path
        )


# --------------------------------------------------------------------------
# every subcommand that resolves configuration, not just `profile show`
# --------------------------------------------------------------------------
def test_render_fails_the_same_way(workspace: Path, capsys):
    doc = workspace / "talk.json"
    shutil.copyfile(FIXTURE_DOC, doc)
    path = project_overlay(workspace, layers.OUTPUTS_FILE, BROKEN_ON_LINE_7)

    assert main(["render", str(doc)]) == exit_codes.ERROR
    out = capsys.readouterr().out
    assert str(path) in out
    assert "line 7" in out
    # And the stage wrote nothing on its way out.
    assert not (workspace / "talk.v4.md").exists()


def test_a_user_overlay_breaks_the_command_too(workspace: Path, capsys):
    home_overlay = workspace.parent / "home" / layers.OVERLAY_DIR
    home_overlay.mkdir(parents=True)
    path = home_overlay / layers.OUTPUTS_FILE
    path.write_text(BROKEN_ON_LINE_7, encoding="utf-8")

    assert main(["profile", "show"]) == exit_codes.ERROR
    assert str(path) in capsys.readouterr().out


def test_a_valid_overlay_beside_a_broken_one_does_not_rescue_it(
    workspace: Path, capsys
):
    project_overlay(workspace, layers.PRIVACY_FILE, "patterns = []\n")
    project_overlay(workspace, layers.OUTPUTS_FILE, BROKEN_ON_LINE_7)

    assert main(["profile", "show"]) == exit_codes.ERROR
    assert "privacy.patterns" not in capsys.readouterr().out


# --------------------------------------------------------------------------
# a well-formed overlay still works, so the guard is not just "always fail"
# --------------------------------------------------------------------------
def test_a_valid_overlay_is_applied(workspace: Path, capsys):
    project_overlay(workspace, layers.OUTPUTS_FILE, '[note]\nstyle = "faithful"\n')

    assert main(["profile", "show", "--json"]) == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["note.style"] == {"value": "faithful", "source": "project"}

"""`l2n install-skill --check` catches an edited or missing target.

The check recomputes both hashes from the files on disk. Reading the recorded
``source_sha256`` back out of ``.installed.json`` would be cheaper and would
report ``[ok]`` for exactly the directory the check exists to catch, so a test
here edits a target *without* touching its record and insists on drift.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path

import pytest

from lecture2notes import install as install_mod
from lecture2notes.cli import main as cli_main

TARGETS = {
    "claude": Path(".claude") / "skills" / "lecture2notes",
    "codex": Path(".agents") / "skills" / "lecture2notes",
    "opencode": Path(".config") / "opencode" / "skills" / "lecture2notes",
}


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _run(argv, capsys):
    code = cli_main(argv)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


@pytest.fixture()
def installed(fake_home, capsys):
    assert cli_main(["install-skill", "--all"]) == 0
    capsys.readouterr()
    return fake_home


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------
def test_clean_install_checks_ok_and_exits_0(installed, capsys):
    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 0
    for name, relative in TARGETS.items():
        assert "[ok] %s" % (installed / relative) in out, name
    assert "[drift]" not in out


def test_bare_check_means_all_three(installed, capsys):
    code, out = _run(["install-skill", "--check"], capsys)
    assert code == 0
    assert out.count("[ok]") == 3


def test_check_a_single_target(installed, capsys):
    code, out = _run(["install-skill", "--check", "--target", "codex"], capsys)
    assert code == 0
    assert out.count("[ok]") == 1
    assert str(installed / TARGETS["codex"]) in out


# --------------------------------------------------------------------------
# an edited target
# --------------------------------------------------------------------------
def test_edited_skill_md_is_reported_as_drift(installed, capsys):
    skill_md = installed / TARGETS["claude"] / "SKILL.md"
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1] + "  (edited by hand)"
    skill_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 2
    assert "[drift] %s" % (installed / TARGETS["claude"]) in out
    assert "SKILL.md: differs" in out
    # Only the claude target drifted.
    assert out.count("[ok]") == 2


def test_stale_installed_json_does_not_hide_drift(installed, capsys):
    """Editing a target without rewriting its record must still be caught."""
    target = installed / TARGETS["codex"]
    record_before = json.loads((target / ".installed.json").read_text(encoding="utf-8"))
    (target / "references" / "segmentation.md").write_text(
        "gutted\n", encoding="utf-8"
    )
    record_after = json.loads((target / ".installed.json").read_text(encoding="utf-8"))
    assert record_before == record_after, "the record was not meant to change"

    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 2
    assert "references/segmentation.md: differs" in out


def test_deleted_reference_file_is_named(installed, capsys):
    (installed / TARGETS["claude"] / "references" / "note-writing.md").unlink()

    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 2
    assert "references/note-writing.md: missing" in out


def test_drift_lists_one_line_per_differing_file(installed, capsys):
    target = installed / TARGETS["opencode"]
    (target / "SKILL.md").write_text("changed\n", encoding="utf-8")
    (target / "references" / "transcription.md").write_text("changed\n", encoding="utf-8")

    code, out = _run(["install-skill", "--check", "--target", "opencode"], capsys)
    assert code == 2
    detail = [line.strip() for line in out.splitlines() if line.startswith("  ")]
    assert "SKILL.md: differs" in detail
    assert "references/transcription.md: differs" in detail
    assert len(detail) == 2, detail


def test_an_extra_file_in_the_target_is_not_drift(installed, capsys):
    """The check is about the packaged files, not about the user's own notes."""
    (installed / TARGETS["claude"] / "my-notes.md").write_text("mine\n", encoding="utf-8")
    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 0, out


# --------------------------------------------------------------------------
# a missing target
# --------------------------------------------------------------------------
def test_missing_target_prints_not_installed(installed, capsys):
    import shutil

    shutil.rmtree(installed / TARGETS["opencode"])

    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 2
    assert "[drift] %s: not installed" % (installed / TARGETS["opencode"]) in out
    assert out.count("[ok]") == 2


def test_never_installed_home_reports_three_not_installed(fake_home, capsys):
    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 2
    assert out.count("not installed") == 3
    assert "[ok]" not in out


def test_directory_without_skill_md_counts_as_not_installed(installed, capsys):
    (installed / TARGETS["codex"] / "SKILL.md").unlink()
    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 2
    assert "%s: not installed" % (installed / TARGETS["codex"]) in out


# --------------------------------------------------------------------------
# exit codes and reinstall
# --------------------------------------------------------------------------
def test_reinstall_clears_the_drift(installed, capsys):
    (installed / TARGETS["claude"] / "SKILL.md").write_text("changed\n", encoding="utf-8")
    assert _run(["install-skill", "--check", "--all"], capsys)[0] == 2

    assert _run(["install-skill", "--all"], capsys)[0] == 0
    code, out = _run(["install-skill", "--check", "--all"], capsys)
    assert code == 0, out


def test_check_output_is_ascii_only(installed, capsys):
    (installed / TARGETS["claude"] / "SKILL.md").write_text("changed\n", encoding="utf-8")
    _code, out = _run(["install-skill", "--check", "--all"], capsys)
    non_ascii = {ch for ch in out if ord(ch) > 127}
    # Paths come from the temp dir and are ASCII here; the markers the check
    # itself prints must never need more than cp950 can encode.
    assert not non_ascii, sorted(non_ascii)


def test_check_helper_returns_lines_and_a_flag(installed):
    lines, all_ok = install_mod.check(all_targets=True)
    assert all_ok is True
    assert len(lines) == 3
    assert all(line.startswith("[ok] ") for line in lines)

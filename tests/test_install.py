"""`l2n install-skill` puts the skill in all three agent folders, non-destructively.

Every test here runs against a temporary home directory. Nothing in this file
may write to the real ``~/.claude``, ``~/.agents`` or ``~/.config/opencode``:
those hold the user's own installed skills, and a test that clobbers them
fails in a way pytest cannot report.
"""

from __future__ import annotations

import json
import pathlib
import re
from pathlib import Path

import pytest

from lecture2notes import __version__
from lecture2notes import install as install_mod
from lecture2notes.cli import main as cli_main

REFERENCE_FILES = (
    "transcription.md",
    "segmentation.md",
    "frames-and-notes.md",
    "note-writing.md",
    "outputs-and-batch.md",
    "profiles-and-overlay.md",
)

EXPECTED_RELATIVE = {
    "claude": Path(".claude") / "skills" / "lecture2notes",
    "codex": Path(".agents") / "skills" / "lecture2notes",
    "opencode": Path(".config") / "opencode" / "skills" / "lecture2notes",
}


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """A throwaway home directory that ``Path.home()`` resolves to."""
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


# --------------------------------------------------------------------------
# target resolution
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(EXPECTED_RELATIVE))
def test_target_dir_matches_the_documented_path(name, fake_home):
    assert install_mod.target_dir(name) == fake_home / EXPECTED_RELATIVE[name]


def test_target_dirs_use_the_lecture2notes_name(fake_home):
    for name in install_mod.TARGET_NAMES:
        assert install_mod.target_dir(name).name == "lecture2notes"


def test_no_target_selected_is_an_error(fake_home, capsys):
    code, out = _run(["install-skill"], capsys)
    assert code == 2
    assert "--target" in out


def test_dest_with_all_is_refused(fake_home, tmp_path, capsys):
    code, out = _run(["install-skill", "--all", "--dest", str(tmp_path / "d")], capsys)
    assert code == 2
    assert "--dest" in out


# --------------------------------------------------------------------------
# installing
# --------------------------------------------------------------------------
def test_install_all_populates_three_targets(fake_home, capsys):
    code, out = _run(["install-skill", "--all"], capsys)
    assert code == 0

    for name in install_mod.TARGET_NAMES:
        target = fake_home / EXPECTED_RELATIVE[name]
        assert (target / "SKILL.md").is_file(), "%s has no SKILL.md" % name
        for filename in REFERENCE_FILES:
            assert (target / "references" / filename).is_file(), (
                "%s is missing references/%s" % (name, filename)
            )
        assert (target / ".installed.json").is_file()
        assert str(target) in out, "the path of the %s target was not printed" % name


def test_install_all_prints_exactly_three_paths(fake_home, capsys):
    _code, out = _run(["install-skill", "--all"], capsys)
    printed = [
        line.strip()
        for line in out.splitlines()
        if line.strip().endswith("lecture2notes")
    ]
    assert len(printed) == 3, "expected three target paths, got %r" % printed


def test_install_single_target_leaves_the_others_alone(fake_home, capsys):
    code, _out = _run(["install-skill", "--target", "codex"], capsys)
    assert code == 0
    assert (fake_home / EXPECTED_RELATIVE["codex"] / "SKILL.md").is_file()
    assert not (fake_home / EXPECTED_RELATIVE["claude"]).exists()
    assert not (fake_home / EXPECTED_RELATIVE["opencode"]).exists()


def test_dest_overrides_the_target_directory(fake_home, tmp_path, capsys):
    dest = tmp_path / "elsewhere" / "lecture2notes"
    code, out = _run(["install-skill", "--dest", str(dest)], capsys)
    assert code == 0
    assert (dest / "SKILL.md").is_file()
    assert str(dest) in out
    assert not (fake_home / EXPECTED_RELATIVE["claude"]).exists()


def test_install_copies_files_and_never_symlinks(fake_home, capsys):
    _run(["install-skill", "--all"], capsys)
    for name in install_mod.TARGET_NAMES:
        target = fake_home / EXPECTED_RELATIVE[name]
        for path in target.rglob("*"):
            assert not path.is_symlink(), "%s is a symlink" % path


def test_installed_copy_matches_the_source_byte_for_byte(fake_home, capsys):
    _run(["install-skill", "--target", "claude"], capsys)
    src = install_mod.source_dir()
    target = fake_home / EXPECTED_RELATIVE["claude"]
    for name in install_mod.iter_source_files(src):
        assert (target / name).read_bytes() == (src / name).read_bytes(), name


def test_reinstall_is_idempotent(fake_home, capsys):
    _run(["install-skill", "--all"], capsys)
    first = sorted(
        p.relative_to(fake_home).as_posix()
        for p in (fake_home / EXPECTED_RELATIVE["claude"]).rglob("*")
    )
    code, _out = _run(["install-skill", "--all"], capsys)
    assert code == 0
    second = sorted(
        p.relative_to(fake_home).as_posix()
        for p in (fake_home / EXPECTED_RELATIVE["claude"]).rglob("*")
    )
    assert first == second


# --------------------------------------------------------------------------
# the install record
# --------------------------------------------------------------------------
def test_installed_json_carries_the_four_required_fields(fake_home, capsys):
    _run(["install-skill", "--all"], capsys)
    for name in install_mod.TARGET_NAMES:
        record = json.loads(
            (fake_home / EXPECTED_RELATIVE[name] / ".installed.json").read_text(
                encoding="utf-8"
            )
        )
        assert set(record) == {
            "version",
            "source_sha256",
            "installed_at",
            "target",
        }, record
        assert record["version"] == __version__
        assert record["target"] == name
        assert re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"])
        # ISO 8601, UTC, second resolution.
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", record["installed_at"]
        ), record["installed_at"]


def test_installed_json_records_the_source_hash(fake_home, capsys):
    _run(["install-skill", "--target", "claude"], capsys)
    record = json.loads(
        (fake_home / EXPECTED_RELATIVE["claude"] / ".installed.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["source_sha256"] == install_mod.source_hash()


def test_source_hash_is_order_independent_of_the_filesystem(fake_home):
    # Concatenation order is the sorted relative path, not rglob order.
    src = install_mod.source_dir()
    names = install_mod.iter_source_files(src)
    assert names == sorted(names)
    assert install_mod.content_hash(src, names) == install_mod.source_hash(src)


def test_installed_json_is_not_itself_hashed(fake_home, capsys):
    _run(["install-skill", "--target", "claude"], capsys)
    target = fake_home / EXPECTED_RELATIVE["claude"]
    assert ".installed.json" not in install_mod.iter_source_files(target)


# --------------------------------------------------------------------------
# overlay preservation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("filename", install_mod.OVERLAY_FILENAMES)
def test_existing_overlay_file_is_left_untouched(filename, fake_home, capsys):
    target = fake_home / EXPECTED_RELATIVE["codex"]
    target.mkdir(parents=True)
    overlay = target / filename
    payload = b'{"mine": true}\n'
    overlay.write_bytes(payload)

    code, _out = _run(["install-skill", "--all"], capsys)
    assert code == 0
    assert overlay.read_bytes() == payload, "%s was overwritten" % filename


def test_overlay_preserved_even_when_the_skill_ships_that_name(
    fake_home, tmp_path, capsys
):
    """A future skill/ could ship an overlay filename; the target still wins."""
    src = tmp_path / "skill-src"
    (src / "references").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: lecture2notes\n---\n", encoding="utf-8")
    (src / "corrections.json").write_text('{"from": "package"}\n', encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    mine = b'{"from": "user"}\n'
    (dest / "corrections.json").write_bytes(mine)

    _path, skipped = install_mod.install_one(dest, "custom", src=src)
    assert skipped == ["corrections.json"]
    assert (dest / "corrections.json").read_bytes() == mine


def test_overlay_file_absent_in_target_is_installed_normally(tmp_path):
    src = tmp_path / "skill-src"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: lecture2notes\n---\n", encoding="utf-8")
    (src / "outputs.toml").write_text("[note]\nstyle = 'concise'\n", encoding="utf-8")

    dest = tmp_path / "dest"
    _path, skipped = install_mod.install_one(dest, "custom", src=src)
    assert skipped == []
    assert (dest / "outputs.toml").is_file()


def test_install_does_not_delete_unrelated_files_in_the_target(fake_home, capsys):
    target = fake_home / EXPECTED_RELATIVE["claude"]
    target.mkdir(parents=True)
    stray = target / "my-notes.md"
    stray.write_text("keep me\n", encoding="utf-8")

    _run(["install-skill", "--all"], capsys)
    assert stray.read_text(encoding="utf-8") == "keep me\n"


# --------------------------------------------------------------------------
# the repo-root wrapper
# --------------------------------------------------------------------------
def test_repo_root_install_py_shares_the_implementation(fake_home, tmp_path):
    import importlib.util

    repo_root = Path(install_mod.__file__).resolve().parents[2]
    script = repo_root / "install.py"
    assert script.is_file(), "repo-root install.py is missing"

    spec = importlib.util.spec_from_file_location("_l2n_install_script", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    dest = tmp_path / "via-script"
    assert module.main(["--dest", str(dest)]) == 0
    assert (dest / "SKILL.md").is_file()
    assert (dest / "references" / "note-writing.md").is_file()

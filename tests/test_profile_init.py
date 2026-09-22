"""`l2n profile init` writes the overlay example and never overwrites yours.

Before 0.2.1 the only documented way to get an overlay was a `cp` out of a
repository clone, which a `pip install` user does not have. The command closes
that gap, and it has exactly one dangerous failure mode: clobbering the five
files that *are* the user's configuration. Half of what follows is about that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.cli.main import main
from lecture2notes.profiles import bootstrap, layers, loader

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "overlay-minimal"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway home, so no run of this suite can touch a real one."""
    fake = tmp_path / "home"
    fake.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.chdir(work)
    return fake


# --------------------------------------------------------------------------
# what it writes
# --------------------------------------------------------------------------
def test_it_writes_the_five_overlay_files_plus_the_readme(home: Path):
    target, written, kept = bootstrap.init_overlay()

    assert target == home / layers.OVERLAY_DIR
    assert kept == []
    assert set(written) == set(layers.OVERLAY_FILES) | {bootstrap.EXAMPLE_README}
    for name in written:
        assert (target / name).read_bytes() == (EXAMPLE / name).read_bytes()


def test_the_default_destination_is_the_user_overlay_layer(home: Path):
    """Whatever it writes has to land in the layer the loader actually reads."""
    bootstrap.init_overlay()

    assert layers.user_dir() == home / layers.OVERLAY_DIR
    assert loader.note_style() == "faithful"


def test_dest_overrides_the_home_directory(home: Path, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere" / "nested"

    target, written, _kept = bootstrap.init_overlay(dest=str(elsewhere))

    assert target == elsewhere
    assert written, "nothing written"
    assert not (home / layers.OVERLAY_DIR).exists()


# --------------------------------------------------------------------------
# what it refuses to touch
# --------------------------------------------------------------------------
def test_an_existing_file_is_kept_byte_for_byte(home: Path):
    target = home / layers.OVERLAY_DIR
    target.mkdir()
    mine = target / layers.OUTPUTS_FILE
    mine.write_text('[note]\nstyle = "concise"\n', encoding="utf-8")
    before = mine.read_bytes()

    _target, written, kept = bootstrap.init_overlay()

    assert kept == [layers.OUTPUTS_FILE]
    assert layers.OUTPUTS_FILE not in written
    assert mine.read_bytes() == before


def test_running_it_twice_changes_nothing_the_second_time(home: Path):
    bootstrap.init_overlay()
    edited = home / layers.OVERLAY_DIR / layers.NOTE_TEMPLATE
    edited.write_text("# mine\n", encoding="utf-8")

    _target, written, kept = bootstrap.init_overlay()

    assert written == []
    assert set(kept) == set(layers.OVERLAY_FILES) | {bootstrap.EXAMPLE_README}
    assert edited.read_text(encoding="utf-8") == "# mine\n"


def test_a_missing_file_is_restored_without_disturbing_the_others(home: Path):
    bootstrap.init_overlay()
    target = home / layers.OVERLAY_DIR
    (target / layers.NOTE_TEMPLATE).write_text("# mine\n", encoding="utf-8")
    (target / layers.PRIVACY_FILE).unlink()

    _target, written, kept = bootstrap.init_overlay()

    assert written == [layers.PRIVACY_FILE]
    assert (target / layers.NOTE_TEMPLATE).read_text(encoding="utf-8") == "# mine\n"


# --------------------------------------------------------------------------
# through the CLI
# --------------------------------------------------------------------------
def test_the_cli_writes_and_says_so(home: Path, capsys):
    assert main(["profile", "init"]) == exit_codes.OK

    out = capsys.readouterr().out
    assert "[error]" not in out
    assert str(home / layers.OVERLAY_DIR) in out
    for name in layers.OVERLAY_FILES:
        assert "wrote %s" % name in out


def test_the_cli_reports_what_it_kept(home: Path, capsys):
    main(["profile", "init"])
    capsys.readouterr()

    assert main(["profile", "init"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "kept existing file: %s" % layers.OUTPUTS_FILE in out
    assert "wrote" not in out


def test_the_cli_dest_flag_reaches_the_bootstrap(home: Path, tmp_path: Path, capsys):
    elsewhere = tmp_path / "cli-dest"

    assert main(["profile", "init", "--dest", str(elsewhere)]) == exit_codes.OK

    assert (elsewhere / layers.OUTPUTS_FILE).is_file()
    assert str(elsewhere) in capsys.readouterr().out


def test_profile_show_still_works_after_init(home: Path, capsys):
    main(["profile", "init"])
    capsys.readouterr()

    assert main(["profile", "show"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "[error]" not in out
    assert "note.style" in out


def test_an_unknown_action_is_still_refused(home: Path, capsys):
    assert main(["profile", "nonsense"]) == exit_codes.ERROR
    assert "unknown profile action" in capsys.readouterr().out


def test_the_readme_documents_the_command():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "l2n profile init" in text

"""Layer precedence: cli > project > user > profile > builtin.

The point of these tests is not that a merge function merges. It is that the
five layers keep their order under the specific pressures that break layered
config in practice: a key set in two layers at once, a key set in only one, a
file that one layer ships and another does not, and a template that must never
be stitched together out of two files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes.cli.main import main
from lecture2notes.profiles import layers, loader


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway home directory that ``Path.home()`` really returns.

    Windows resolves ``~`` through ``USERPROFILE`` and POSIX through ``HOME``,
    so both are repointed: a test that set only one would pass on the machine
    it was written on and quietly read the developer's real overlay on the
    other.
    """
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    assert Path.home() == fake
    return fake


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway working directory, so ``./.lecture2notes/`` is ours."""
    work = tmp_path / "course"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def overlay(base: Path) -> Path:
    directory = base / layers.OVERLAY_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# precedence
# --------------------------------------------------------------------------
def test_project_overlay_beats_user_overlay(home: Path, project: Path):
    write(overlay(home), layers.OUTPUTS_FILE, '[note]\nstyle = "faithful"\n')
    write(overlay(project), layers.OUTPUTS_FILE, '[note]\nstyle = "concise"\n')

    resolved = loader.resolve()
    assert resolved.settings()["note.style"].value == "concise"
    assert resolved.settings()["note.style"].source == "project"
    assert loader.note_style() == "concise"


def test_user_overlay_beats_the_profile_when_the_project_is_silent(
    home: Path, project: Path
):
    write(overlay(home), layers.OUTPUTS_FILE, '[note]\nstyle = "faithful"\n')

    resolved = loader.resolve()
    assert resolved.settings()["note.style"].value == "faithful"
    assert resolved.settings()["note.style"].source == "user"


def test_cli_beats_every_file(home: Path, project: Path):
    write(overlay(project), layers.OUTPUTS_FILE, '[note]\nstyle = "concise"\n')

    with loader.cli_layer(note__style="faithful"):
        resolved = loader.resolve()
    assert resolved.settings()["note.style"].value == "faithful"
    assert resolved.settings()["note.style"].source == "cli"


def test_cli_profile_beats_the_profile_the_caller_named(home: Path, project: Path):
    with loader.cli_layer(profile="radiology"):
        resolved = loader.resolve("generic")
    assert resolved.profile == "radiology"
    assert resolved.profile_source == "cli"


def test_the_cli_layer_does_not_survive_its_own_command(home: Path, project: Path):
    with loader.cli_layer(profile="radiology"):
        pass
    assert loader.resolve().profile == layers.BUILTIN_PROFILE


def test_overlay_can_select_the_profile(home: Path, project: Path):
    write(overlay(home), layers.OUTPUTS_FILE, 'profile = "radiology"\n')

    resolved = loader.resolve()
    assert resolved.profile == "radiology"
    assert resolved.profile_source == "user"
    # The selector key is reported once, as `profile`, not twice.
    assert "profile" in resolved.settings()
    assert [k for k in resolved.settings() if k == "profile"] == ["profile"]


# --------------------------------------------------------------------------
# a layer that lacks a file contributes nothing
# --------------------------------------------------------------------------
def test_a_layer_without_the_file_contributes_nothing(home: Path, project: Path):
    # The project overlay exists but ships only privacy.toml. It must not wipe
    # out the note style the user overlay sets.
    write(overlay(home), layers.OUTPUTS_FILE, '[note]\nstyle = "faithful"\n')
    write(overlay(project), layers.PRIVACY_FILE, "patterns = []\n")

    resolved = loader.resolve()
    assert resolved.settings()["note.style"].value == "faithful"
    assert resolved.settings()["note.style"].source == "user"


def test_an_unset_key_still_comes_from_the_builtin_layer(home: Path, project: Path):
    write(overlay(project), layers.OUTPUTS_FILE, '[note]\nstyle = "faithful"\n')

    settings = loader.resolve().settings()
    assert settings["note.style"].source == "project"
    # pbf is not mentioned by the project overlay at all.
    assert settings["pbf"].source == "builtin"
    assert settings["pbf"].value is False


def test_privacy_patterns_are_replaced_whole_not_appended(home: Path, project: Path):
    write(overlay(project), layers.PRIVACY_FILE, "patterns = ['^ONLY-THIS$']\n")

    assert loader.privacy_patterns() == ["^ONLY-THIS$"]


# --------------------------------------------------------------------------
# templates replace, tables merge
# --------------------------------------------------------------------------
def test_a_template_replaces_the_lower_layer_whole(home: Path, project: Path):
    write(
        overlay(project),
        layers.FRONTMATTER_TEMPLATE,
        "---\nonly: \"{{title}}\"\n---\n",
    )

    text = loader.frontmatter_template()
    assert "only:" in text
    # Nothing of the builtin four-field template survives.
    assert "tags:" not in text
    assert "source:" not in text


def test_the_note_template_comes_from_the_highest_layer_that_ships_one(
    home: Path, project: Path
):
    write(overlay(home), layers.NOTE_TEMPLATE, "USER TEMPLATE\n")
    assert loader.note_template().strip() == "USER TEMPLATE"

    write(overlay(project), layers.NOTE_TEMPLATE, "PROJECT TEMPLATE\n")
    assert loader.note_template().strip() == "PROJECT TEMPLATE"


# --------------------------------------------------------------------------
# corrections merge key by key
# --------------------------------------------------------------------------
def test_corrections_merge_and_the_higher_layer_wins(home: Path, project: Path):
    write(
        overlay(home),
        layers.CORRECTIONS_FILE,
        json.dumps(
            {
                "_sources": {"deterministic": "user-overlay"},
                "deterministic": {"口拍的": "Copilot", "馬克當": "Markdown"},
            },
            ensure_ascii=False,
        ),
    )
    write(
        overlay(project),
        layers.CORRECTIONS_FILE,
        json.dumps(
            {"deterministic": {"馬克當": "markdown"}},
            ensure_ascii=False,
        ),
    )

    merged = loader.corrections()["deterministic"]
    # Both layers' pairs are active...
    assert merged["口拍的"] == "Copilot"
    # ...and the higher layer wins the key they share.
    assert merged["馬克當"] == "markdown"

    sources = loader.resolve().sources
    assert sources["corrections.deterministic.口拍的"] == "user"
    assert sources["corrections.deterministic.馬克當"] == "project"


def test_correction_entries_carry_the_declared_source_label(home: Path, project: Path):
    write(
        overlay(home),
        layers.CORRECTIONS_FILE,
        json.dumps(
            {
                "_sources": {"deterministic": "my-own-table"},
                "deterministic": {"口拍的": "Copilot"},
            },
            ensure_ascii=False,
        ),
    )

    entry = [e for e in loader.correction_entries() if e["heard"] == "口拍的"][0]
    assert entry["correct"] == "Copilot"
    assert entry["source"] == "my-own-table"
    assert entry["layer"] == "user"


def test_metadata_keys_are_not_reported_as_settings(home: Path, project: Path):
    write(
        overlay(project),
        layers.CORRECTIONS_FILE,
        json.dumps({"_about": "notes to self", "deterministic": {"a": "b"}}),
    )

    keys = list(loader.resolve().settings())
    assert "corrections._about" not in keys
    assert "corrections.deterministic.a" in keys


# --------------------------------------------------------------------------
# the layer stack itself
# --------------------------------------------------------------------------
def test_layer_stack_order_and_the_deduplicated_builtin(home: Path, project: Path):
    names = [name for name, _ in layers.layer_stack(layers.BUILTIN_PROFILE)]
    assert names == ["project", "user", "builtin"]

    names = [name for name, _ in layers.layer_stack("radiology")]
    assert names == ["project", "user", "profile", "builtin"]


def test_home_and_cwd_can_be_injected_instead_of_patched(tmp_path: Path):
    other_home = tmp_path / "elsewhere"
    write(overlay(other_home), layers.OUTPUTS_FILE, '[note]\nstyle = "faithful"\n')

    resolved = loader.resolve(home=other_home, cwd=tmp_path / "no-such-project")
    assert resolved.settings()["note.style"].value == "faithful"
    assert resolved.settings()["note.style"].source == "user"


# --------------------------------------------------------------------------
# the layers are visible through the CLI, not just the API
# --------------------------------------------------------------------------
def test_profile_show_reports_the_winning_layer(home: Path, project: Path, capsys):
    write(overlay(home), layers.OUTPUTS_FILE, '[note]\nstyle = "faithful"\n')
    write(overlay(project), layers.OUTPUTS_FILE, '[note]\nstyle = "concise"\n')

    assert main(["profile", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["note.style"] == {"value": "concise", "source": "project"}

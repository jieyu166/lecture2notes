"""`l2n profile show`: what is in force, and which layer put it there.

This command is how a person answers "why did it do that" without reading five
directories, so the tests hold it to the two things that make it trustworthy:
every key carries a source layer, and the JSON form says exactly what the human
form says.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_cli
from lecture2notes import exit_codes
from lecture2notes.cli.main import main
from lecture2notes.profiles import layers


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


def show_json(capsys, *args: str) -> dict:
    assert main(["profile", "show", "--json", *args]) == exit_codes.OK
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------
# --json
# --------------------------------------------------------------------------
def test_json_parses_and_note_style_has_value_and_source(workspace: Path, capsys):
    payload = show_json(capsys)
    assert "note.style" in payload
    assert set(payload["note.style"]) == {"value", "source"}
    assert payload["note.style"]["value"] == "concise"
    assert payload["note.style"]["source"] == "builtin"


def test_every_key_carries_a_known_source_layer(workspace: Path, capsys):
    payload = show_json(capsys)
    assert payload
    for key, entry in payload.items():
        assert set(entry) == {"value", "source"}, key
        assert entry["source"] in layers.LAYER_NAMES, (key, entry["source"])


def test_json_covers_every_overlayable_file(workspace: Path, capsys):
    payload = show_json(capsys)
    keys = list(payload)
    assert "profile" in keys
    assert "pbf" in keys                                  # outputs.toml
    assert "privacy.patterns" in keys                     # privacy.toml
    assert "template." + layers.FRONTMATTER_TEMPLATE in keys
    assert "template." + layers.NOTE_TEMPLATE in keys
    assert any(k.startswith("corrections.") for k in keys)  # corrections.json


def test_the_cli_layer_shows_up_as_cli(workspace: Path, capsys):
    payload = show_json(capsys, "--profile", "radiology", "--style", "faithful")
    assert payload["profile"] == {"value": "radiology", "source": "cli"}
    assert payload["note.style"] == {"value": "faithful", "source": "cli"}


def test_selecting_radiology_reports_its_template_from_the_profile_layer(
    workspace: Path, capsys
):
    payload = show_json(capsys, "--profile", "radiology")
    assert payload["template." + layers.NOTE_TEMPLATE]["source"] == "profile"
    # radiology ships no frontmatter template, so that one is still builtin.
    assert payload["template." + layers.FRONTMATTER_TEMPLATE]["source"] == "builtin"


# --------------------------------------------------------------------------
# the human form
# --------------------------------------------------------------------------
def test_human_form_prints_key_value_and_source(workspace: Path, capsys):
    assert main(["profile", "show"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "note.style = concise  (builtin)" in out
    assert "pbf = false  (builtin)" in out
    assert "hub = true  (builtin)" in out


def test_human_form_and_json_form_agree(workspace: Path, capsys):
    assert main(["profile", "show"]) == exit_codes.OK
    human = capsys.readouterr().out
    payload = show_json(capsys)
    for key, entry in payload.items():
        assert "(%s)" % entry["source"] in human
        assert key in human


def test_console_markers_stay_ascii(workspace: Path, capsys):
    assert main(["profile", "show"]) == exit_codes.OK
    out = capsys.readouterr().out
    for banned in ("→", "≥", "✓", "✗"):
        assert banned not in out


# --------------------------------------------------------------------------
# misuse
# --------------------------------------------------------------------------
def test_an_unknown_action_is_a_usage_error(workspace: Path, capsys):
    assert main(["profile", "explode"]) == exit_codes.ERROR
    assert "unknown profile action" in capsys.readouterr().out


# --------------------------------------------------------------------------
# a narrow console must not corrupt the payload
# --------------------------------------------------------------------------
def test_json_still_parses_on_a_cp950_console(tmp_path: Path):
    """The tables are full of Traditional Chinese; the payload must survive.

    A cp950 console cannot encode every character in a correction key, and the
    encoding-safe writer replaces what it cannot encode. So the JSON form is
    ASCII-escaped: the escapes go through cp950 untouched and the output still
    parses on the machine this product targets.
    """
    proc = run_cli(
        ["profile", "show", "--json"],
        cwd=tmp_path,
        env={"PYTHONIOENCODING": "cp950", "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        encoding="cp950",
    )
    assert proc.returncode == exit_codes.OK, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["note.style"]["value"] == "concise"
    # And the Chinese survived the round trip through the escapes.
    key = "corrections.deterministic.程式馬"  # 程式馬 -> 程式碼
    assert payload[key]["value"] == "程式碼"

"""`--lang` is mandatory for transcribe and run, and the gate fires before any work."""

from __future__ import annotations

import pytest

from conftest import listdir_set, run_cli

from lecture2notes.cli.main import LANG_CHOICES, LANG_REQUIRED_MESSAGE

MESSAGE = "--lang is required (zh|en|ja|auto)"


@pytest.mark.parametrize("command", ["transcribe", "run"])
def test_missing_lang_exits_2_and_creates_no_files(tmp_path, command):
    work = tmp_path / command
    work.mkdir()
    (work / "video.mp4").write_bytes(b"not a real video")
    before = listdir_set(work)

    proc = run_cli([command, "video.mp4"], cwd=work)
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 2, combined
    assert MESSAGE in combined
    assert listdir_set(work) == before


@pytest.mark.parametrize("command", ["transcribe", "run"])
def test_missing_lang_still_reported_under_quiet(tmp_path, command):
    proc = run_cli([command, "video.mp4", "--quiet"], cwd=tmp_path)
    assert proc.returncode == 2
    assert MESSAGE in (proc.stdout + proc.stderr)


def test_message_constant_matches_the_contract():
    assert LANG_REQUIRED_MESSAGE == MESSAGE
    assert LANG_CHOICES == ("zh", "en", "ja", "auto")


@pytest.mark.parametrize("command", ["transcribe", "run"])
def test_invalid_lang_is_rejected_by_choices(tmp_path, command):
    proc = run_cli([command, "video.mp4", "--lang", "de"], cwd=tmp_path)
    assert proc.returncode == 2
    assert "invalid choice" in (proc.stdout + proc.stderr)


@pytest.mark.parametrize("lang", list(LANG_CHOICES))
def test_valid_lang_passes_the_gate(tmp_path, lang):
    proc = run_cli(["transcribe", "video.mp4", "--lang", lang], cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert MESSAGE not in combined
    # 3 = ffmpeg absent on this machine, 4 = stage body not written yet.
    assert proc.returncode in (3, 4), combined

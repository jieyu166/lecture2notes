"""Missing external dependencies exit 3, say what is missing, and write nothing."""

from __future__ import annotations

import shutil

import pytest

from conftest import listdir_set, minimal_path, run_cli

from lecture2notes import _deps
from lecture2notes.cli.main import main as cli_entry


def test_frames_without_ffmpeg_exits_3_and_writes_nothing(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "video.mp4").write_bytes(b"not a real video")
    before = listdir_set(work)

    path = minimal_path()
    if shutil.which("ffmpeg", path=path):
        pytest.skip("ffmpeg is present even on the stripped PATH")

    proc = run_cli(["frames", "video.mp4"], cwd=work, env={"PATH": path})
    combined = proc.stdout + proc.stderr

    assert proc.returncode == 3, combined
    assert "missing dependency: ffmpeg" in combined
    assert "install:" in combined
    assert listdir_set(work) == before


def test_ocr_without_rapidocr_exits_3(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise _deps.MissingDependency("rapidocr", _deps.INSTALL_HINTS["rapidocr"])

    monkeypatch.setattr(_deps, "require_rapidocr", boom)
    monkeypatch.setitem(_deps._CHECKS, "rapidocr", boom)
    monkeypatch.chdir(tmp_path)
    # A target that exists, because 16.1 moved the dependency check behind
    # target resolution: a path that is not there is exit 2 and rapidocr is
    # never asked for, which is the whole point of the reordering.
    (tmp_path / "talk.json").write_text("{}", encoding="utf-8")
    before = listdir_set(tmp_path)

    code = cli_entry(["ocr", "talk.json"])

    assert code == 3
    assert listdir_set(tmp_path) == before


def test_missing_ct2_model_directory_is_reported():
    with pytest.raises(_deps.MissingDependency) as excinfo:
        _deps.require("ct2_model", "no/such/model/dir")
    assert excinfo.value.name == "ct2_model"
    assert "convert-model" in excinfo.value.how


def test_every_install_hint_is_ascii():
    for name, hint in _deps.INSTALL_HINTS.items():
        assert hint.isascii(), name

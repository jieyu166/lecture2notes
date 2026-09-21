"""An unreadable duration is reported, not turned into an empty capture.

Three failure paths used to be silent. ``scene.get_duration`` returned 0.0
whenever ffprobe printed something it could not parse, ``plan_seconds`` turned
that 0.0 into an empty interval plan, and ``l2n frames`` then reported
"capture produced no frames" -- true, but with the actual cause three layers
away and never printed. ``curator.measure_frame_quality`` swallowed its ffmpeg
exception the same way.

No ffmpeg or ffprobe binary is needed here: every one of those boundaries is
faked, because what is under test is what the code says when the tool fails,
not the tool.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.cli.main import main as run_main
from lecture2notes.frames import capture as capture_mod
from lecture2notes.frames import curator as curator_mod
from lecture2notes.frames import scene as scene_mod


class _Completed:
    """Just enough of ``subprocess.CompletedProcess`` for these calls."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"not a real video")
    return path


def _fake_ffprobe(monkeypatch, stdout: str, stderr: str = "") -> None:
    monkeypatch.setattr(scene_mod._deps, "require_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(
        scene_mod.subprocess,
        "run",
        lambda *a, **k: _Completed(stdout=stdout, stderr=stderr),
    )


# -- scene.get_duration ----------------------------------------------------
def test_unparseable_duration_still_returns_zero(video: Path, monkeypatch, capsys):
    _fake_ffprobe(monkeypatch, stdout="N/A\n")

    assert scene_mod.get_duration(video) == 0.0


def test_unparseable_duration_says_what_ffprobe_printed(video: Path, monkeypatch, capsys):
    _fake_ffprobe(monkeypatch, stdout="N/A\n")

    scene_mod.get_duration(video)

    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "talk.mp4" in out
    # The raw output is the whole point: without it the warning says no more
    # than the exit code already did.
    assert "N/A" in out


def test_empty_ffprobe_output_is_named_rather_than_shown_blank(
    video: Path, monkeypatch, capsys
):
    _fake_ffprobe(monkeypatch, stdout="   \n", stderr="talk.mp4: Invalid data found")

    scene_mod.get_duration(video)

    out = capsys.readouterr().out
    assert "(empty stdout)" in out
    assert "Invalid data found" in out


def test_a_parseable_duration_warns_about_nothing(video: Path, monkeypatch, capsys):
    _fake_ffprobe(monkeypatch, stdout="1823.4\n")

    assert scene_mod.get_duration(video) == pytest.approx(1823.4)
    assert "[warn]" not in capsys.readouterr().out


def test_the_preview_is_bounded(video: Path, monkeypatch, capsys):
    _fake_ffprobe(monkeypatch, stdout="x" * 5000)

    scene_mod.get_duration(video)

    out = capsys.readouterr().out
    assert "x" * scene_mod.DURATION_PREVIEW in out
    assert "x" * (scene_mod.DURATION_PREVIEW + 1) not in out


# -- plan_seconds ----------------------------------------------------------
def test_interval_planning_refuses_an_unknown_duration(video: Path, monkeypatch):
    monkeypatch.setattr(capture_mod.scene_mode, "get_duration", lambda path: 0.0)

    with pytest.raises(capture_mod.DurationUnknown):
        capture_mod.plan_seconds(video, mode="interval", every=45.0)


def test_interval_planning_refuses_a_negative_duration(video: Path, monkeypatch):
    monkeypatch.setattr(capture_mod.scene_mode, "get_duration", lambda path: -1.0)

    with pytest.raises(capture_mod.DurationUnknown):
        capture_mod.plan_seconds(video, mode="interval", every=45.0)


def test_interval_planning_still_works_with_a_real_duration(video: Path, monkeypatch):
    monkeypatch.setattr(capture_mod.scene_mode, "get_duration", lambda path: 100.0)

    seconds = capture_mod.plan_seconds(video, mode="interval", every=45.0)

    assert seconds, "a 100-second video must yield sample times"


def test_scene_mode_never_consults_the_duration(video: Path, monkeypatch):
    # Only interval mode divides the timeline, so a duration ffprobe cannot
    # read must not block the detector-driven mode.
    def _boom(path):  # pragma: no cover - must not be reached
        raise AssertionError("scene mode asked for the duration")

    monkeypatch.setattr(capture_mod.scene_mode, "get_duration", _boom)
    monkeypatch.setattr(
        capture_mod.scene_mode,
        "detect_scenes",
        lambda *a, **k: [{"timestamp_sec": 0.0}, {"timestamp_sec": 12.0}],
    )

    planned = capture_mod.plan_seconds(video, mode="scene")

    assert planned == [0.0, 12.0 + scene_mod.SEEK_PADDING]


# -- the CLI exit code -----------------------------------------------------
def _cli_without_binaries(monkeypatch) -> None:
    monkeypatch.setattr(capture_mod._deps, "require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(capture_mod._deps, "require_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr("lecture2notes._deps.require", lambda name, *a, **k: name)
    monkeypatch.setattr(capture_mod.scene_mode, "get_duration", lambda path: 0.0)


def test_frames_exits_two_and_says_why_on_an_unknown_duration(
    video: Path, monkeypatch, capsys
):
    _cli_without_binaries(monkeypatch)

    code = run_main(["frames", str(video), "--mode", "interval"])

    assert code == exit_codes.ERROR
    out = capsys.readouterr().out
    assert "[error]" in out
    assert "duration" in out


def test_the_failed_run_writes_no_frames_folder(video: Path, monkeypatch, capsys):
    _cli_without_binaries(monkeypatch)

    run_main(["frames", str(video), "--mode", "interval"])

    assert not (video.parent / "frames").exists()
    assert not list(video.parent.glob("*.frames.json"))


# -- curator.measure_frame_quality ----------------------------------------
def test_an_unmeasurable_frame_warns_with_the_reason(tmp_path: Path, monkeypatch, capsys):
    frame = tmp_path / "talk-0012.png"
    frame.write_bytes(b"not a real png")
    monkeypatch.setattr(curator_mod._deps, "require_ffmpeg", lambda: "ffmpeg")

    def _explode(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)

    monkeypatch.setattr(curator_mod.subprocess, "run", _explode)

    result = curator_mod.measure_frame_quality(frame)

    # Unchanged behaviour: still a candidate, still marked unreadable.
    assert result["readable"] is False
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "talk-0012.png" in out
    assert "ffmpeg" in out


def test_a_measurable_frame_warns_about_nothing(tmp_path: Path, monkeypatch, capsys):
    frame = tmp_path / "talk-0012.png"
    frame.write_bytes(b"not a real png")
    monkeypatch.setattr(curator_mod._deps, "require_ffmpeg", lambda: "ffmpeg")
    size = curator_mod.QUALITY_SIZE

    class _Raw:
        returncode = 0
        stdout = bytes(size * size)

    monkeypatch.setattr(curator_mod.subprocess, "run", lambda *a, **k: _Raw())

    curator_mod.measure_frame_quality(frame)

    assert "[warn]" not in capsys.readouterr().out

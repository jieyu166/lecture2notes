"""Task 5.1 -- two capture modes, one manifest format.

The fixture is a 30-second video built in ``tmp_path`` from six flat-colour
slides. Nothing is committed: no real lecture recording may enter the
repository, and a capture test whose fixture is a download is a test that stops
running the first time the network is down.

ffmpeg is a hard requirement of the stage under test, so its absence is not a
failure of this code. The module skips itself and says why.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_MISSING = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
if _MISSING:
    _REASON = (
        "test_frames.py skipped: %s not on PATH, so the synthetic fixture video "
        "cannot be built" % " and ".join(_MISSING)
    )
    print("[skip] %s" % _REASON)
    pytest.skip(_REASON, allow_module_level=True)

pytest.importorskip("PIL", reason="Pillow builds the synthetic slides")

from fixtures import synthetic  # noqa: E402

from lecture2notes.frames import capture, scene  # noqa: E402
from lecture2notes.frames.manifest import read_manifest  # noqa: E402

FIXTURE_SECONDS = 30
MANIFEST_KEYS = ("timestamp_sec", "frame", "sha256")


@pytest.fixture(scope="module")
def lecture_video(tmp_path_factory) -> Path:
    """One 30-second video per module run; the modes do not mutate it."""
    folder = tmp_path_factory.mktemp("capture")
    return synthetic.build_lecture_video(folder)


@pytest.fixture()
def workdir(lecture_video: Path, tmp_path: Path) -> Path:
    """A private copy of the video, so each test writes into its own directory."""
    destination = tmp_path / lecture_video.name
    shutil.copy2(lecture_video, destination)
    return destination


def _assert_manifest_shape(manifest_path: Path, base_dir: Path) -> list:
    rows = read_manifest(manifest_path)
    assert rows, "manifest is empty"
    for row in rows:
        for key in MANIFEST_KEYS:
            assert key in row, "manifest row is missing %s: %r" % (key, row)
        assert isinstance(row["timestamp_sec"], float)
        assert len(row["sha256"]) == 64
        assert row["frame"].startswith("frames/"), row["frame"]
        assert (base_dir / row["frame"]).is_file()
    return rows


def test_interval_mode_writes_frames_and_a_manifest(workdir: Path):
    result = capture.capture(workdir, mode="interval", every=5, width=320)

    assert result.mode == "interval"
    # 29.9 seconds at 5-second steps: 0, 5, 10, 15, 20, 25.
    assert result.evaluated == 6
    assert result.manifest_path.name == "lecture.frames.json"
    rows = _assert_manifest_shape(result.manifest_path, workdir.parent)
    assert len(rows) == result.kept
    # Six visually distinct slides: none of them is a duplicate of the last.
    assert result.dropped == 0
    assert [row["timestamp_sec"] for row in rows] == [0.0, 5.0, 10.0, 15.0, 20.0, 25.0]


def test_frame_filenames_are_stem_and_mmss(workdir: Path):
    result = capture.capture(workdir, mode="interval", every=5, width=320)

    names = sorted(path.name for path in result.frames_dir.iterdir())
    assert names == [
        "lecture-0000.png", "lecture-0005.png", "lecture-0010.png",
        "lecture-0015.png", "lecture-0020.png", "lecture-0025.png",
    ]


def test_scene_mode_writes_the_same_manifest_format(workdir: Path):
    result = capture.capture(workdir, mode="scene", width=320)

    assert result.mode == "scene"
    rows = _assert_manifest_shape(result.manifest_path, workdir.parent)
    # Six hard cuts in 30 seconds; the detector must find more than the title
    # card, otherwise the mode is not doing anything.
    assert len(rows) >= 2
    assert [row["timestamp_sec"] for row in rows] == sorted(
        row["timestamp_sec"] for row in rows
    )


def test_both_modes_produce_interchangeable_manifests(workdir: Path):
    interval_rows = read_manifest(
        capture.capture(workdir, mode="interval", every=5, width=320).manifest_path
    )
    scene_rows = read_manifest(
        capture.capture(workdir, mode="scene", width=320).manifest_path
    )
    assert {key for row in interval_rows for key in row} == {
        key for row in scene_rows for key in row
    }


def test_scene_mode_falls_back_to_ffmpeg_without_scenedetect(
    workdir: Path, monkeypatch, capsys
):
    # None in sys.modules makes `from scenedetect import ...` raise ImportError
    # without uninstalling anything or touching the import machinery.
    monkeypatch.setitem(sys.modules, "scenedetect", None)

    result = capture.capture(workdir, mode="scene", width=320)

    printed = capsys.readouterr()
    combined = printed.out + printed.err
    assert scene.FALLBACK_MESSAGE in combined
    assert scene.FALLBACK_MESSAGE == (
        "[frames] scenedetect not installed, using ffmpeg scene filter"
    )
    _assert_manifest_shape(result.manifest_path, workdir.parent)


def test_fallback_is_announced_even_under_quiet(workdir: Path, monkeypatch, capsys):
    """A silent downgrade is the failure mode this message exists to prevent."""
    from lecture2notes import _out

    monkeypatch.setitem(sys.modules, "scenedetect", None)
    _out.configure(quiet=True)
    try:
        capture.capture(workdir, mode="scene", width=320)
    finally:
        _out.reset()

    printed = capsys.readouterr()
    assert scene.FALLBACK_MESSAGE in printed.out + printed.err


def test_stage_writes_candidates_under_staging(workdir: Path):
    result = capture.capture(workdir, mode="interval", every=5, width=320, stage=True)

    assert result.staged is True
    assert result.frames_dir == workdir.parent / "staging" / "frames"
    assert not (workdir.parent / "frames").exists()
    for row in read_manifest(result.manifest_path):
        assert row["frame"].startswith("staging/frames/")


def test_progress_is_reported_while_capturing(workdir: Path, capsys):
    capture.capture(workdir, mode="interval", every=5, width=320, progress_interval=0)

    printed = capsys.readouterr().out
    assert "[frames] 1/6" in printed
    assert "[frames] 6/6" in printed
    # cp950 cannot encode these, so they must never be printed.
    assert not any(marker in printed for marker in ("→", "≥", "✓", "✗"))


def test_an_unknown_mode_is_refused(workdir: Path):
    with pytest.raises(ValueError, match="unknown capture mode"):
        capture.capture(workdir, mode="keyframe")


def test_same_second_detections_do_not_collide():
    """Two cuts inside one second must not both claim ``<stem>-MMSS.png``."""
    planned = capture.plan_frames("talk", [12.1, 12.6, 40.0])

    assert [name for _second, name in planned] == ["talk-0012.png", "talk-0040.png"]

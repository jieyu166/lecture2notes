"""Task 5.1 -- two capture modes, one manifest format.

The fixture is a 30-second video built in ``tmp_path`` from six flat-colour
slides. Nothing is committed: no real lecture recording may enter the
repository, and a capture test whose fixture is a download is a test that stops
running the first time the network is down.

ffmpeg is a hard requirement of the stage under test, so its absence is not a
failure of this code. The module skips itself and says why.
"""

from __future__ import annotations

import io
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

from lecture2notes import _out  # noqa: E402
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


# --------------------------------------------------------------------------
# 15.5 -- a scan that says nothing for two minutes looks like a hang
# --------------------------------------------------------------------------
class FakeFfmpeg:
    """Stands in for a scanning ffmpeg: a `-progress` stream and a showinfo dump."""

    #: What ffmpeg writes to the progress pipe, one block per reporting point.
    BLOCKS = [
        "frame=%d\nfps=25\nout_time=00:00:%02d.000000\nprogress=continue\n"
        % (second * 25, second)
        for second in (5, 10, 15, 20)
    ]
    SHOWINFO = (
        "[Parsed_showinfo_1 @ 0000] n:0 pts_time:4.400 pos:1\n"
        "[Parsed_showinfo_1 @ 0000] n:1 pts_time:12.800 pos:2\n"
    )

    def __init__(self, command, stdout=None, stderr=None, **kwargs):
        self.command = list(command)
        self.stdout = io.StringIO("".join(self.BLOCKS) + "progress=end\n")
        if stderr is not None:
            stderr.write(self.SHOWINFO)
        self.returncode = 0

    def wait(self):
        return self.returncode


def test_run_scan_reports_how_far_through_the_media_it_is(capsys):
    """The 110 second field scan printed nothing for 108 of them."""
    _out.reset()

    stderr_text = scene.run_scan(
        ["ffmpeg", "-progress", "pipe:1"], total_sec=20, interval=0,
        popen=FakeFfmpeg,
    )

    printed = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("[frames] ")
    ]
    assert len(printed) > 1, printed
    assert "[frames] 5/20" in printed[0]
    assert printed[-1].startswith("[frames] 20/20")
    assert "pts_time:4.400" in stderr_text


def test_run_scan_lines_stay_ascii(capsys):
    _out.reset()
    scene.run_scan(
        ["ffmpeg"], total_sec=20, interval=0, popen=FakeFfmpeg
    )

    printed = capsys.readouterr().out
    assert not any(marker in printed for marker in "\u2192\u2265\u2713\u2717")


def test_run_scan_reports_elapsed_and_eta(capsys):
    _out.reset()
    scene.run_scan(["ffmpeg"], total_sec=20, interval=0, popen=FakeFfmpeg)

    first = capsys.readouterr().out.splitlines()[0]
    assert "elapsed" in first and "eta" in first


def test_detect_ffmpeg_asks_for_the_progress_stream_and_still_finds_the_cuts(
    monkeypatch, capsys
):
    _out.reset()
    captured = {}

    class Recording(FakeFfmpeg):
        def __init__(self, command, **kwargs):
            captured["command"] = list(command)
            super().__init__(command, **kwargs)

    monkeypatch.setattr(scene._deps, "require_ffmpeg", lambda: "ffmpeg")

    marks = scene.detect_ffmpeg(
        Path("talk.mp4"), total_sec=20, progress_interval=0, popen=Recording
    )

    assert "-progress" in captured["command"]
    assert "pipe:1" in captured["command"]
    seconds = [entry["timestamp_sec"] for entry in marks]
    assert seconds == [0.0, 4.4, 12.8]
    assert "[frames] 20/20" in capsys.readouterr().out


def test_quiet_silences_the_scan_progress(capsys):
    _out.configure(quiet=True, json_progress=False)
    try:
        scene.run_scan(["ffmpeg"], total_sec=20, interval=0, popen=FakeFfmpeg)
    finally:
        _out.reset()

    assert capsys.readouterr().out == ""


def test_timing_warnings_flag_start_offset_and_variable_frame_rate():
    from lecture2notes.frames import scene

    found = scene.timing_warnings(
        {"video_start": 87.5, "format_start": 0.0, "nominal_fps": 30.0, "average_fps": 24.2}
    )
    assert len(found) == 2 and "87.50" in found[0] and "variable frame rate" in found[1]
    assert scene.timing_warnings(
        {"video_start": 0.0, "format_start": 0.0, "nominal_fps": 30.0, "average_fps": 29.97}
    ) == []
    assert scene.timing_warnings({}) == []


def test_scene_times_follow_the_container_clock_when_the_video_starts_late(tmp_path):
    import shutil
    import subprocess

    pytest.importorskip("scenedetect")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg not installed")
    from lecture2notes.frames import scene

    video, audio, late = tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "late.mp4"
    run = lambda *args: subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)
    run("-f", "lavfi", "-i", "color=c=red:s=160x90:d=4:r=25", "-f", "lavfi", "-i",
        "color=c=blue:s=160x90:d=4:r=25", "-filter_complex",
        "[0][1]concat=n=2:v=1:a=0,format=yuv420p[v]", "-map", "[v]", "-c:v", "libx264", str(video))
    run("-f", "lavfi", "-i", "sine=f=440:d=9", "-c:a", "aac", str(audio))
    run("-itsoffset", "2.0", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
        "-c", "copy", str(late))

    times = [m["timestamp_sec"] for m in scene.detect_scenes(late, detector="content")]
    assert times[0] == 0.0
    assert any(abs(t - 6.0) < 0.2 for t in times), times  # blue at 4 s of video = 6 s of file
    assert not any(abs(t - 4.0) < 0.2 for t in times), times


# -- a video that changes size --------------------------------------------------
def _concat_sizes(tmp_path: Path, first: str, seconds: float) -> Path:
    """``first`` for ``seconds``, then 160x80 blue with a white box from 2 s on."""
    import subprocess

    run = lambda *args: subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)
    a, b, out = tmp_path / "a.ts", tmp_path / "b.ts", tmp_path / "mixed.mp4"
    run("-f", "lavfi", "-i", "color=c=red:s=%s:d=%s:r=25" % (first, seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(a))
    run("-f", "lavfi", "-i",
        "color=c=blue:s=160x80:d=4:r=25,drawbox=w=80:h=40:c=white@1:t=fill:enable='gte(t,2)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(b))
    run("-i", "concat:%s|%s" % (a, b), "-c", "copy", str(out))
    return out


def test_a_size_change_at_the_start_is_seen_by_the_probe(tmp_path):
    """Field file: the first two frames were 2468x1280, the stream and the rest 2468x1186."""
    video = _concat_sizes(tmp_path, "160x96", 0.08)
    changed = scene.size_change(scene.frame_sizes(video))
    assert changed and "160x96" in changed and "160x80" in changed
    plain = _concat_sizes(tmp_path, "160x80", 0.08)
    assert scene.size_change(scene.frame_sizes(plain)) is None


def test_size_change_reads_the_stream_and_the_frames():
    assert scene.size_change({"stream": (4, 3), "frames": [(4, 3), (4, 3)]}) is None
    assert scene.size_change({"stream": (4, 2), "frames": [(4, 3), (4, 2)]}) == "4x2 -> 4x3"
    assert scene.size_change({"stream": None, "frames": []}) is None


def _spy_copy(monkeypatch):
    calls = []
    real = scene.normalized_copy

    def spy(path, workdir, sizes=None):
        calls.append(path)
        return real(path, workdir, sizes)

    monkeypatch.setattr(scene, "normalized_copy", spy)
    return calls


def test_a_size_change_is_scanned_from_a_fixed_size_copy(tmp_path, monkeypatch, capsys):
    """Same detector and threshold as any other file, so no cut is lost to a
    coarser fallback (the ffmpeg scene filter missed 36 of 76 cuts on the field file)."""
    pytest.importorskip("scenedetect")
    video = _concat_sizes(tmp_path, "160x96", 0.08)
    calls = _spy_copy(monkeypatch)
    times = [m["timestamp_sec"] for m in scene.detect_scenes(video)]
    assert calls == [video]
    assert "changes size (160x96 -> 160x80)" in capsys.readouterr().out
    assert times[0] == 0.0
    assert any(abs(t - 2.08) < 0.2 for t in times), times


def test_scenedetects_size_errors_also_switch_to_the_copy(tmp_path, monkeypatch, capsys):
    """A change past the probed opening: PySceneDetect logs, skips and finds nothing."""
    pytest.importorskip("scenedetect")
    import logging

    import scenedetect

    video = _concat_sizes(tmp_path, "160x80", 0.08)
    real = scenedetect.SceneManager.detect_scenes

    def noisy(self, *args, **kwargs):
        logging.getLogger("pyscenedetect").error(
            "ERROR: Frame at 00:00:00.067 has incorrect size and cannot be processed"
        )
        return real(self, *args, **kwargs)

    monkeypatch.setattr(scenedetect.SceneManager, "detect_scenes", noisy)
    calls = _spy_copy(monkeypatch)
    times = [m["timestamp_sec"] for m in scene.detect_scenes(video)]
    assert calls == [video]
    assert "1 frames of another size" in capsys.readouterr().out
    assert any(abs(t - 2.08) < 0.2 for t in times), times
    assert not any(isinstance(h, scene._SizeErrors) for h in logging.getLogger("pyscenedetect").handlers)


def test_the_copy_has_one_size_and_a_constant_rate(tmp_path):
    video = _concat_sizes(tmp_path, "160x96", 0.08)
    source = scene.frame_sizes(video)
    copy = scene.normalized_copy(video, tmp_path, source)
    sizes = scene.frame_sizes(copy, count=200)
    assert scene.size_change(sizes) is None
    width, height = source["stream"]  # the declared aspect, whatever the frames say
    assert sizes["stream"] == (scene.NORMALIZED_WIDTH, scene.NORMALIZED_WIDTH * height // width)
    timing = scene.stream_timing(copy)
    assert timing["nominal_fps"] == timing["average_fps"] == scene.NORMALIZED_FPS


def test_a_failed_copy_falls_back_to_the_ffmpeg_scene_filter(tmp_path, monkeypatch, capsys):
    pytest.importorskip("scenedetect")
    video = _concat_sizes(tmp_path, "160x96", 0.08)
    monkeypatch.setattr(scene, "normalized_copy", lambda *a, **k: None)
    times = [m["timestamp_sec"] for m in scene.detect_scenes(video)]
    assert "ffmpeg scene filter" in capsys.readouterr().out
    assert any(abs(t - 2.08) < 0.2 for t in times), times


def test_the_copy_keeps_the_container_shift_of_a_late_video(tmp_path):
    """Video starting 2 s into the container and changing size: the box at
    2.08 s of video is 4.08 s of file, counted once, not twice."""
    import subprocess

    pytest.importorskip("scenedetect")
    video = _concat_sizes(tmp_path, "160x96", 0.08)
    audio, late = tmp_path / "a.m4a", tmp_path / "late.mp4"
    run = lambda *args: subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)
    run("-f", "lavfi", "-i", "sine=f=440:d=8", "-c:a", "aac", str(audio))
    run("-itsoffset", "2.0", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
        "-c", "copy", str(late))
    times = [m["timestamp_sec"] for m in scene.detect_scenes(late)]
    assert any(abs(t - 4.08) < 0.2 for t in times), times
    assert not any(abs(t - 6.08) < 0.2 for t in times), times


def test_a_gap_in_the_video_timestamps_is_kept_on_every_path(tmp_path, monkeypatch):
    """A live recording that lost its video for 30 s (audio continuous): the cut
    after the gap stays at its own timestamp, not 30 s early or late."""
    import subprocess

    pytest.importorskip("scenedetect")
    video = tmp_path / "gap.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=160x90:d=55:r=25,"
        "drawbox=w=80:h=45:c=white@1:t=fill:enable='between(t,5,40)'",
        "-f", "lavfi", "-i", "sine=d=55",
        "-vf", "select='not(between(t,10,40))'", "-fps_mode", "passthrough",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video),
    ], check=True)
    near = lambda times, t: any(abs(x - t) < 0.2 for x in times)

    direct = [m["timestamp_sec"] for m in scene.detect_scenes(video)]
    by_filter = [m["timestamp_sec"] for m in scene.detect_ffmpeg(video)]
    monkeypatch.setattr(scene, "frame_sizes", lambda p, count=30: {"stream": (1, 1), "frames": [(2, 2)]})
    by_copy = [m["timestamp_sec"] for m in scene.detect_scenes(video)]
    for times in (direct, by_filter, by_copy):
        assert near(times, 5.0) and near(times, 40.0), times
        assert not near(times, 10.0), times

"""`l2n calibrate-subs`: fix an official subtitle's clock without touching its text.

An official subtitle track is far better on terminology than any ASR run, so
re-transcribing a three-hour recording to fix its timing would trade good text
for bad. A few short ASR probes are used purely as a clock instead.

Nothing here runs ffmpeg, loads a model or touches the network. Audio extraction
is a separate function and is replaced with a stub; the probe ASR arrives through
the Engine interface as a fake that replays the official text at a known offset,
which is what makes the expected offsets exact rather than approximate.

Every subtitle string is synthetic placeholder text generated in the test.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from lecture2notes import _deps, exit_codes
from lecture2notes.cli.main import main as run_main
from lecture2notes.engines import audio as audio_mod
from lecture2notes.engines import calibrate
from lecture2notes.engines.base import Cue, Engine, EngineMeta, parse_srt, write_srt

DURATION = 12000.0
#: Where the default layout puts the three probes in a 12000 second recording.
DEFAULT_POSITIONS = [300.0, 6000.0, 11040.0]
#: Offsets inside each probe window at which an official cue sits.
LOCAL_OFFSETS = (10.0, 30.0, 50.0)


def placeholder(index: int) -> str:
    """Unique, punctuation-free, long enough to be located by substring match."""
    return "佔位文字第%02d句用於時間校正測試" % index


def build_official(offsets):
    """Official cues whose clock is ahead of reality by ``offsets[i]`` per probe.

    The official time of a cue is the true time plus that probe's offset, which
    is exactly the quantity calibration has to recover.
    """
    cues = []
    index = 0
    for position, offset in zip(DEFAULT_POSITIONS, offsets):
        for local in LOCAL_OFFSETS:
            index += 1
            true_start = position + local
            cues.append(
                Cue(
                    start=true_start + offset,
                    end=true_start + offset + 4.0,
                    text=placeholder(index),
                )
            )
    return cues


class ReplayEngine(Engine):
    """Returns, for each probe window, the true-time cues of that window.

    Cue times are relative to the window start, which is what a real engine
    handed a 90 second clip produces.
    """

    meta = EngineMeta(
        name="replay", local=True, needs_gpu=False,
        has_timestamps=True, default_model="none",
    )

    def __init__(self, windows=None, **options):
        super().__init__(**options)
        self.windows = windows or {}
        self.seen = []

    def check(self) -> None:
        return None

    def transcribe(self, audio, lang):
        self.seen.append((Path(audio).name, lang))
        return list(self.windows.get(Path(audio).name, []))


def replay_engine(offsets, positions=None, drop=()):
    """Build an engine that replays the true speech behind ``build_official``.

    ``drop`` names probe indices that produce nothing, standing in for a window
    of music or silence that the matcher cannot use.
    """
    positions = positions or DEFAULT_POSITIONS
    windows = {}
    index = 0
    for probe, position in enumerate(positions):
        cues = []
        for local in LOCAL_OFFSETS:
            index += 1
            if probe in drop:
                continue
            cues.append(Cue(start=local, end=local + 4.0, text=placeholder(index)))
        windows["probe%02d.wav" % probe] = cues
    return ReplayEngine(windows)


def replay_factory(offsets, drop=()):
    """A registrable engine class (the registry reads `meta` off the factory)."""

    class Registered(ReplayEngine):
        meta = EngineMeta(
            name="replay", local=True, needs_gpu=False,
            has_timestamps=True, default_model="none",
        )

        def __init__(self, **options):
            built = replay_engine(offsets, drop=drop)
            super().__init__(windows=built.windows, **options)

    return Registered


def stub_extract(video, destination, **kwargs):
    """Stand-in for ffmpeg: writes a byte so the path exists, nothing more."""
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_bytes(b"RIFF")
    return Path(destination)


@pytest.fixture()
def workspace(tmp_path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"not a real video")
    return tmp_path, video


def run(tmp_path, video, offsets, subtitle_name="lecture.vtt", drop=(), **kwargs):
    subs = tmp_path / subtitle_name
    write_srt(subs, build_official(offsets))
    return calibrate.run_calibration(
        video, subs, "zh", replay_engine(offsets, drop=drop),
        duration_sec=DURATION, workdir=tmp_path / "probes",
        extract=stub_extract, **kwargs
    )


# -- probe placement -------------------------------------------------------
def test_the_default_layout_is_300_the_midpoint_and_92_percent():
    assert calibrate.default_probe_positions(DURATION) == DEFAULT_POSITIONS


def test_probe_windows_are_ninety_seconds():
    assert calibrate.PROBE_WINDOW_SEC == 90.0


def test_a_probes_count_spreads_evenly_across_the_same_span():
    positions = calibrate.parse_probe_spec(5, DURATION)
    assert len(positions) == 5
    assert positions[0] == 300.0
    assert positions[-1] == pytest.approx(DURATION * 0.92)


def test_explicit_probe_positions_are_taken_literally():
    assert calibrate.parse_probe_spec("300,1800,4200", DURATION) == [300.0, 1800.0, 4200.0]


def test_a_probe_window_never_runs_past_the_end_of_the_recording():
    positions = calibrate.parse_probe_spec("100,590,1000", 600.0)
    assert all(p + calibrate.PROBE_WINDOW_SEC <= 600.0 for p in positions)


def test_positions_that_collapse_onto_each_other_are_deduplicated():
    """Two identical windows would look like two agreeing probes."""
    assert calibrate.clamp_positions([500.0, 500.0, 520.0], 600.0) == [500.0, 510.0]


def test_a_nonsense_probes_argument_is_refused():
    with pytest.raises(calibrate.CalibrationError):
        calibrate.parse_probe_spec("many", DURATION)


# -- constant offset -------------------------------------------------------
CONSTANT = (1.44, 0.80, 0.96)


def test_constant_offset_is_not_flagged_as_drift(workspace):
    tmp_path, video = workspace
    result = run(tmp_path, video, CONSTANT)
    medians = [p["median_offset"] for p in result["report"]["probes"]]
    assert medians == [1.44, 0.8, 0.96]
    assert result["report"]["drift"] is False
    assert result["report"]["span"] == pytest.approx(0.64, abs=1e-6)


def test_each_endpoint_is_shifted_by_the_offset_at_its_own_time(workspace):
    tmp_path, video = workspace
    subs = tmp_path / "one.vtt"
    # Placed well past the offset: a cue at t=1 would clamp to zero, which is
    # correct behaviour but tests the clamp rather than the shift.
    write_srt(subs, build_official(CONSTANT) + [Cue(100.0, 103.0, placeholder(99))])
    result = calibrate.run_calibration(
        video, subs, "zh", replay_engine(CONSTANT), duration_sec=DURATION,
        workdir=tmp_path / "probes", extract=stub_extract,
    )
    a = result["report"]["fit"]["a"]
    b = result["report"]["fit"]["b"]
    shifted = result["cues"][-1]
    assert shifted.start == pytest.approx(100.0 - (a + b * 100.0), abs=1e-6)
    assert shifted.end == pytest.approx(103.0 - (a + b * 103.0), abs=1e-6)
    # Each endpoint used the offset at its own time, so the cue was stretched,
    # not just slid: the durations differ by b times the original duration.
    assert shifted.duration() == pytest.approx(3.0 * (1 - b), abs=1e-6)


# -- drift -----------------------------------------------------------------
DRIFTING = (3.14, 2.23, 0.86)


def test_drift_is_flagged_and_the_span_is_two_point_two_eight(workspace):
    tmp_path, video = workspace
    result = run(tmp_path, video, DRIFTING)
    report = result["report"]
    assert report["drift"] is True
    assert report["span"] == pytest.approx(2.28, abs=1e-6)
    assert report["fit"]["b"] < 0


def test_the_drift_threshold_is_one_point_five_seconds():
    assert calibrate.DRIFT_THRESHOLD == 1.5
    assert calibrate.has_drift([0.0, 1.49]) is False
    assert calibrate.has_drift([0.0, 1.5]) is True


def test_the_printed_report_names_the_drift_and_the_fit(workspace, capsys):
    tmp_path, video = workspace
    run(tmp_path, video, DRIFTING)
    printed = capsys.readouterr().out
    assert "drift" in printed
    assert "offset(t) =" in printed
    assert "span 2.280s" in printed
    for banned in ("→", "≥", "✓", "✗"):
        assert banned not in printed


# -- the fit itself --------------------------------------------------------
def test_the_synthetic_three_point_fit():
    """Fixed input, fixed expected coefficients: the contract's own example."""
    a, b = calibrate.fit_linear([(300.0, 3.0), (5700.0, 2.0), (10500.0, 1.0)])
    assert a == pytest.approx(3.06, abs=0.05)
    assert b == pytest.approx(-0.000196, abs=0.000005)


def test_format_fit_is_ascii_and_readable():
    line = calibrate.format_fit(3.06, -0.000196)
    line.encode("ascii")
    assert line.startswith("offset(t) = 3.0600 -")


# -- the text is never modified -------------------------------------------
def test_the_calibrated_and_official_files_carry_identical_text(workspace):
    tmp_path, video = workspace
    result = run(tmp_path, video, DRIFTING)
    calibrated = parse_srt(result["files"]["srt"])
    official = parse_srt(result["files"]["official"])
    assert "".join(c.text for c in calibrated) == "".join(c.text for c in official)
    assert [c.start for c in calibrated] != [c.start for c in official]


def test_calibrating_an_srt_in_place_keeps_the_original_in_official(workspace):
    """`<stem>.srt` may be the input file; the untouched copy is written first."""
    tmp_path, video = workspace
    result = run(tmp_path, video, DRIFTING, subtitle_name="lecture.srt")
    assert result["files"]["srt"] == tmp_path / "lecture.srt"
    official = parse_srt(tmp_path / "lecture.official.srt")
    assert [round(c.start, 3) for c in official] == [
        round(c.start, 3) for c in build_official(DRIFTING)
    ]


def test_the_three_outputs_are_named_as_documented(workspace):
    tmp_path, video = workspace
    files = run(tmp_path, video, CONSTANT)["files"]
    assert files["srt"].name == "lecture.srt"
    assert files["official"].name == "lecture.official.srt"
    assert files["report"].name == "lecture.offset.json"


def test_the_report_file_has_the_documented_shape(workspace):
    tmp_path, video = workspace
    files = run(tmp_path, video, DRIFTING)["files"]
    payload = json.loads(files["report"].read_text(encoding="utf-8"))
    assert set(payload) >= {"probes", "fit", "drift"}
    assert set(payload["probes"][0]) == {"at_sec", "n_points", "median_offset",
                                         "min", "max"}
    assert set(payload["fit"]) == {"a", "b"}
    assert not files["report"].read_bytes().startswith(b"\xef\xbb\xbf")


# -- timing floors ---------------------------------------------------------
def test_a_cue_that_would_become_zero_length_is_widened_to_the_floor(workspace):
    tmp_path, video = workspace
    subs = tmp_path / "floor.vtt"
    write_srt(subs, build_official(CONSTANT) + [Cue(50.0, 50.0, placeholder(98))])
    result = calibrate.run_calibration(
        video, subs, "zh", replay_engine(CONSTANT), duration_sec=DURATION,
        workdir=tmp_path / "probes", extract=stub_extract,
    )
    assert result["cues"][-1].duration() == pytest.approx(0.3, abs=1e-6)


def test_a_shift_below_zero_is_clamped(workspace):
    tmp_path, video = workspace
    subs = tmp_path / "clamp.vtt"
    write_srt(subs, build_official(CONSTANT) + [Cue(0.2, 2.0, placeholder(97))])
    result = calibrate.run_calibration(
        video, subs, "zh", replay_engine(CONSTANT), duration_sec=DURATION,
        workdir=tmp_path / "probes", extract=stub_extract,
    )
    assert result["cues"][-1].start == 0.0


# -- too few reliable probes ----------------------------------------------
def test_fewer_than_three_reliable_probes_writes_nothing(workspace):
    tmp_path, video = workspace
    before = {p.name for p in tmp_path.iterdir()}
    with pytest.raises(calibrate.CalibrationError) as excinfo:
        run(tmp_path, video, DRIFTING, drop=(1,))
    assert "reliable probes" in str(excinfo.value)
    after = {p.name for p in tmp_path.iterdir()} - {"lecture.vtt", "probes"}
    assert after == before - {"lecture.vtt"}
    assert not (tmp_path / "lecture.offset.json").exists()
    assert not (tmp_path / "lecture.official.srt").exists()


def test_a_failed_run_with_no_workdir_leaves_no_scratch_directory_anywhere(
    workspace, monkeypatch
):
    """The default probe workdir lives in the system temp area, and a failed
    run removes it again: no ``.l2n-probes`` next to the subtitle, and no
    ``l2n-probes-*`` directory left behind in the temp area either.
    """
    tmp_path, video = workspace
    subs = tmp_path / "lecture.vtt"
    write_srt(subs, build_official(DRIFTING))

    created: list = []
    real_mkdtemp = tempfile.mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr(calibrate.tempfile, "mkdtemp", spy_mkdtemp)

    with pytest.raises(calibrate.CalibrationError):
        calibrate.run_calibration(
            video, subs, "zh", replay_engine(DRIFTING, drop=(1,)),
            duration_sec=DURATION, extract=stub_extract,
        )

    assert created, "expected the default workdir to be created via tempfile.mkdtemp"
    assert created[0].name.startswith("l2n-probes-")
    assert not created[0].exists()
    assert not (tmp_path / ".l2n-probes").exists()


def test_a_caller_supplied_workdir_is_left_in_place(workspace):
    """A ``workdir`` the caller passed in is theirs; calibration never deletes it."""
    tmp_path, video = workspace
    subs = tmp_path / "lecture.vtt"
    write_srt(subs, build_official(CONSTANT))
    workdir = tmp_path / "my-scratch"

    calibrate.run_calibration(
        video, subs, "zh", replay_engine(CONSTANT), duration_sec=DURATION,
        workdir=workdir, extract=stub_extract,
    )

    assert workdir.is_dir()
    assert any(workdir.iterdir())


def test_an_unparseable_subtitle_file_is_refused(workspace):
    tmp_path, video = workspace
    subs = tmp_path / "empty.vtt"
    subs.write_text("WEBVTT\n\n", encoding="utf-8", newline="\n")
    with pytest.raises(calibrate.CalibrationError) as excinfo:
        calibrate.run_calibration(
            video, subs, "zh", replay_engine(CONSTANT), duration_sec=DURATION,
            workdir=tmp_path / "probes", extract=stub_extract,
        )
    assert "no cues" in str(excinfo.value)


def test_a_recording_too_short_for_three_windows_is_refused(workspace):
    tmp_path, video = workspace
    subs = tmp_path / "short.vtt"
    write_srt(subs, build_official(CONSTANT))
    with pytest.raises(calibrate.CalibrationError) as excinfo:
        calibrate.run_calibration(
            video, subs, "zh", replay_engine(CONSTANT), duration_sec=95.0,
            workdir=tmp_path / "probes", extract=stub_extract,
        )
    assert "probe positions" in str(excinfo.value)


# -- the ffmpeg seam -------------------------------------------------------
def test_cut_probes_asks_for_one_window_per_position(tmp_path):
    calls = []

    def recorder(video, destination, **kwargs):
        calls.append((kwargs.get("start_sec"), kwargs.get("duration_sec")))
        Path(destination).write_bytes(b"RIFF")
        return Path(destination)

    windows = calibrate.cut_probes(
        tmp_path / "v.mp4", [300.0, 600.0], tmp_path / "w", 90.0, recorder
    )
    assert calls == [(300.0, 90.0), (600.0, 90.0)]
    assert [at for at, _ in windows] == [300.0, 600.0]


def test_the_extract_argv_seeks_before_the_input():
    """-ss before -i is the fast seek; a probe does not need frame accuracy."""
    argv = audio_mod.build_extract_command(
        "ffmpeg", Path("v.mp4"), Path("out.wav"), start_sec=300.0, duration_sec=90.0
    )
    assert argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-t") + 1] == "90.000"
    assert argv[argv.index("-ar") + 1] == "16000"
    assert argv[argv.index("-ac") + 1] == "1"


# -- through the CLI -------------------------------------------------------
@pytest.fixture()
def cli_ready(monkeypatch):
    monkeypatch.setattr(_deps, "require", lambda name, *a, **k: "fake-" + name)
    monkeypatch.setattr(audio_mod, "extract_audio", stub_extract)


def test_cli_writes_the_three_files_and_exits_0(tmp_path, cli_ready, monkeypatch):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    subs = tmp_path / "lecture.vtt"
    write_srt(subs, build_official(CONSTANT))
    monkeypatch.setattr(audio_mod, "media_duration", lambda *a, **k: DURATION)
    from lecture2notes.engines import registry

    registry.register(replay_factory(CONSTANT))
    try:
        code = run_main([
            "calibrate-subs", str(video), str(subs), "--lang", "zh",
            "--engine", "replay",
        ])
    finally:
        registry.unregister("replay")
    assert code == exit_codes.OK
    assert (tmp_path / "lecture.srt").is_file()
    assert (tmp_path / "lecture.official.srt").is_file()
    assert (tmp_path / "lecture.offset.json").is_file()


def test_cli_exits_2_and_writes_nothing_when_probes_are_unreliable(
    tmp_path, cli_ready, monkeypatch, capsys
):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    subs = tmp_path / "lecture.vtt"
    write_srt(subs, build_official(DRIFTING))
    monkeypatch.setattr(audio_mod, "media_duration", lambda *a, **k: DURATION)
    from lecture2notes.engines import registry

    registry.register(replay_factory(DRIFTING, drop=(0, 1)))
    try:
        code = run_main([
            "calibrate-subs", str(video), str(subs), "--lang", "zh",
            "--engine", "replay",
        ])
    finally:
        registry.unregister("replay")
    assert code == exit_codes.ERROR
    assert "reliable probes" in capsys.readouterr().out
    assert not (tmp_path / "lecture.srt").exists()
    assert not (tmp_path / "lecture.official.srt").exists()
    assert not (tmp_path / "lecture.offset.json").exists()


def test_cli_requires_a_language(tmp_path, cli_ready, capsys):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"x")
    subs = tmp_path / "lecture.vtt"
    subs.write_text("", encoding="utf-8")
    assert run_main(["calibrate-subs", str(video), str(subs)]) == exit_codes.ERROR
    assert "--lang is required" in capsys.readouterr().out


def test_cli_reports_a_missing_input_file(tmp_path, cli_ready, capsys):
    assert run_main([
        "calibrate-subs", str(tmp_path / "nope.mp4"), str(tmp_path / "nope.vtt"),
        "--lang", "zh",
    ]) == exit_codes.ERROR
    assert "no such file" in capsys.readouterr().out

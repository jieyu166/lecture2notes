"""The `--allow-cloud` gate: a non-local engine cannot run by accident.

The package ships no cloud engine, so this gate is only ever exercised by a
third-party plugin. That is exactly why it needs its own tests: nothing in the
shipped set would notice if the gate quietly stopped working.

The fake engine below is registered and unregistered around each test so the
registry never keeps a non-local entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture2notes import _deps, _out, exit_codes
from lecture2notes.cli.main import main as run_main
from lecture2notes.engines import audio as audio_mod
from lecture2notes.engines import registry
from lecture2notes.engines.base import Cue, Engine, EngineMeta

CLOUD_NAME = "fake_cloud"


class FakeCloudEngine(Engine):
    """A plugin that would upload the audio. Never actually does anything."""

    meta = EngineMeta(
        name=CLOUD_NAME,
        local=False,
        needs_gpu=False,
        has_timestamps=True,
        default_model="remote-v1",
        extra="test double for the cloud gate",
    )
    constructed = 0

    def __init__(self, **options):
        super().__init__(**options)
        type(self).constructed += 1

    def check(self) -> None:
        return None

    def transcribe(self, audio, lang):
        return [Cue(0.0, 2.0, "佔位文字一"), Cue(2.0, 4.0, "佔位文字二")]


@pytest.fixture()
def cloud_engine():
    FakeCloudEngine.constructed = 0
    registry.register(FakeCloudEngine)
    try:
        yield FakeCloudEngine
    finally:
        registry.unregister(CLOUD_NAME)


@pytest.fixture()
def no_ffmpeg(monkeypatch, tmp_path):
    """Neither the gate nor the run may depend on ffmpeg being installed."""
    monkeypatch.setattr(_deps, "require", lambda name, *a, **k: "fake-" + name)

    def fake_extract(source, destination, **kwargs):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"RIFF")
        return Path(destination)

    monkeypatch.setattr(audio_mod, "extract_audio", fake_extract)


def make_video(tmp_path: Path) -> Path:
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"not a real video")
    return video


# -- the registry level ----------------------------------------------------
def test_the_package_itself_ships_no_cloud_engine():
    assert [name for name in registry.names() if not registry.meta_for(name).local] == []


def test_create_refuses_a_non_local_engine_without_consent(cloud_engine):
    with pytest.raises(registry.CloudEngineBlocked) as excinfo:
        registry.create(CLOUD_NAME)
    assert excinfo.value.name == CLOUD_NAME
    assert cloud_engine.constructed == 0, "the engine was built before the gate ran"


def test_create_allows_a_non_local_engine_with_consent(cloud_engine):
    engine = registry.create(CLOUD_NAME, allow_cloud=True)
    assert engine.name == CLOUD_NAME
    assert cloud_engine.constructed == 1


def test_the_gate_message_names_the_rule_not_just_the_flag(cloud_engine):
    message = str(registry.CloudEngineBlocked(CLOUD_NAME))
    assert "--allow-cloud" in message
    assert "privacy rule" in message
    assert "local" in message
    message.encode("ascii")  # a cp950 console must be able to print it


def test_a_local_engine_needs_no_flag():
    assert registry.create("faster_whisper").meta.local is True


def test_probe_still_reports_a_cloud_engine_without_consent(cloud_engine):
    """Seeing that a plugin exists is not the same as agreeing to run it."""
    status = registry.probe(CLOUD_NAME)
    assert status.satisfied is True


def test_the_listing_marks_a_cloud_engine_as_not_local(cloud_engine):
    row = next(r for r in registry.engine_lines() if r.startswith(CLOUD_NAME))
    assert row.split()[:2] == [CLOUD_NAME, "no"]


# -- the CLI level ---------------------------------------------------------
def test_cli_exits_2_and_explains_the_privacy_rule(tmp_path, capsys, cloud_engine, no_ffmpeg):
    video = make_video(tmp_path)
    code = run_main(["transcribe", str(video), "--lang", "zh", "--engine", CLOUD_NAME])
    captured = capsys.readouterr().out
    assert code == exit_codes.ERROR
    assert "--allow-cloud" in captured
    assert "privacy rule" in captured


def test_cli_writes_no_file_when_the_gate_fires(tmp_path, cloud_engine, no_ffmpeg):
    video = make_video(tmp_path)
    before = {p.name for p in tmp_path.iterdir()}
    run_main(["transcribe", str(video), "--lang", "zh", "--engine", CLOUD_NAME])
    assert {p.name for p in tmp_path.iterdir()} == before


def test_cli_runs_the_cloud_engine_once_the_flag_is_given(
    tmp_path, cloud_engine, no_ffmpeg
):
    video = make_video(tmp_path)
    code = run_main([
        "transcribe", str(video), "--lang", "zh",
        "--engine", CLOUD_NAME, "--allow-cloud",
    ])
    assert code == exit_codes.OK
    srt = tmp_path / "lecture.srt"
    assert srt.is_file()
    assert "佔位文字一" in srt.read_text(encoding="utf-8")


def test_an_unknown_engine_exits_2_and_lists_the_known_ones(tmp_path, capsys, no_ffmpeg):
    video = make_video(tmp_path)
    code = run_main(["transcribe", str(video), "--lang", "zh", "--engine", "nope"])
    captured = capsys.readouterr().out
    assert code == exit_codes.ERROR
    assert "nope" in captured
    assert "breeze_ct2" in captured


def test_the_gate_fires_before_the_language_gate_is_satisfied(tmp_path, capsys, cloud_engine):
    """--lang missing is still reported first; the gate never rescues a bad call."""
    _out.reset()
    code = run_main(["transcribe", str(make_video(tmp_path)), "--engine", CLOUD_NAME])
    assert code == exit_codes.ERROR
    assert "--lang is required" in capsys.readouterr().out

"""`l2n run`: the stages in order, skipping what is already there, stopping on error.

Two properties are pinned, and they are the ones that decide whether the command
is usable on a real lecture.

The first is idempotence. A thirty-minute recording is transcribed once and then
rendered, viewed and re-checked many times, so re-running has to be the normal
case rather than the dangerous one. The proof is size and mtime: run twice, and
require every file to be byte-for-byte and timestamp-for-timestamp what it was.
"No new files appeared" would not catch a stage that rewrote its output with
identical bytes, which is exactly the failure that would make `--force`
meaningless.

The second is that a check error stops the run. Building a note on top of a
broken document is worse than failing: the note looks finished. So the test
injects a failure into one stage's check and requires the later stages to have
left no trace at all.

No real engine and no real model: a fake backend registered for the duration of
the test returns fixed cues. The video is synthetic and carries a silent audio
track, because the transcribe stage extracts audio before it reaches the engine
and ffmpeg refuses a file with no audio stream at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import tree_state

from lecture2notes import _deps, _out, exit_codes
from lecture2notes.acceptance import check
from lecture2notes.cli.main import main as cli_entry
from lecture2notes.engines import registry
from lecture2notes.engines.base import Cue, Engine, EngineMeta
from lecture2notes.frames import ocr as ocr_mod

pytest.importorskip("PIL", reason="Pillow paints the synthetic frames")

FAKE_ENGINE = "fake_run"

#: Three cues spread over the fixture's thirty seconds. The text shares nothing
#: with the note, so the transcript-paste rule has nothing to find.
FAKE_CUES = (
    (0.0, 9.0, "wexfgjorqomzubrekathivlanpydsuc"),
    (10.0, 19.0, "gnufirovlukthemzarodniqpescybwa"),
    (20.0, 29.0, "usgwoxarnifdrazmelphitouqvenkyb"),
)


class FakeEngine(Engine):
    """A backend that transcribes nothing and returns the same cues every time."""

    meta = EngineMeta(
        name=FAKE_ENGINE, local=True, needs_gpu=False,
        has_timestamps=True, default_model="none",
    )

    def check(self) -> None:
        return None

    def transcribe(self, audio, lang):
        return [Cue(start=start, end=end, text=text) for start, end, text in FAKE_CUES]


@pytest.fixture()
def fake_engine():
    registry.register(FakeEngine)
    yield FAKE_ENGINE
    registry.unregister(FAKE_ENGINE)


@pytest.fixture()
def ready(synthetic_lecture, fake_engine, monkeypatch):
    """A lecture with its document and frames, but nothing `run` would produce.

    The canonical JSON stays: scaffold is the LLM stage and `run` refuses to
    invent it. The frames the document references stay too, so removing the
    manifest makes the frames stage do real work without breaking the document.

    OCR is stubbed out rather than run. RapidOCR loads several hundred megabytes
    of ONNX weights, and these tests are about stage order and idempotence --
    paying for a model to recognise nothing on a flat-colour PNG would make the
    suite depend on an optional extra for no assertion in return.
    """
    if synthetic_lecture.video is None:
        pytest.skip("ffmpeg is not available, so there is no recording to run on")
    synthetic_lecture.subtitle.unlink()
    synthetic_lecture.manifest.unlink()
    synthetic_lecture.note.unlink()

    def stub_ocr(target, **kwargs):
        cache = ocr_mod.cache_path_for(Path(target))
        cache.write_text("{}", encoding="utf-8", newline="\n")
        _out.stage("ocr", "stubbed")
        return SimpleNamespace(total=0, recognised=[], cache_path=cache)

    real_require = _deps.require
    monkeypatch.setattr(ocr_mod, "run_ocr", stub_ocr)
    monkeypatch.setattr(
        _deps, "require",
        lambda name, *a, **k: None if name == "rapidocr" else real_require(name, *a, **k),
    )
    return synthetic_lecture


def _run(lecture, *extra):
    return cli_entry(
        ["run", str(lecture.video), "--lang", "zh", "--engine", FAKE_ENGINE, *extra]
    )


# -- the stages run, in order ------------------------------------------------
# `run` ends on WARN rather than OK from task 15.10 onwards. Every mechanical
# stage succeeded; what it hands over is a *skeleton*, and R10 says so once per
# segment. A run that reported 0 would be claiming the note is written, which is
# the exact confusion this branch exists to remove. An R10 warning never stops
# the run -- only a check error does.
def test_a_first_run_produces_every_mechanical_output(ready, capsys):
    code = _run(ready, "--mode", "interval", "--every", "5")
    printed = capsys.readouterr().out

    assert code == exit_codes.WARN, printed
    assert "warn R10 segment 1" in printed
    assert ready.subtitle.is_file()
    assert ready.manifest.is_file()
    assert ready.note.is_file()
    assert check.stage_targets(ready.document)["transcribe"].is_file()
    for stage in ("transcribe", "frames", "ocr", "scaffold", "render", "viewer"):
        assert "[%s]" % stage in printed, stage
    assert printed.index("[transcribe]") < printed.index("[frames]")
    assert printed.index("[frames]") < printed.index("[scaffold]")
    assert printed.index("[scaffold]") < printed.index("[render]")
    assert printed.index("[render]") < printed.index("[viewer]")


def test_scaffold_says_it_is_the_model_s_stage_rather_than_doing_it(ready, capsys):
    _run(ready, "--mode", "interval", "--every", "5")

    assert "[scaffold] skip (LLM stage; see skill)" in capsys.readouterr().out


# -- the scenario: a second run is a no-op -----------------------------------
def test_a_second_run_skips_every_stage_and_changes_no_file(ready, capsys):
    assert _run(ready, "--mode", "interval", "--every", "5") == exit_codes.WARN
    capsys.readouterr()
    before = tree_state(ready.root)

    code = _run(ready, "--mode", "interval", "--every", "5")
    printed = capsys.readouterr().out

    assert code == exit_codes.WARN
    assert tree_state(ready.root) == before
    for stage in ("transcribe", "frames", "ocr", "render", "viewer"):
        assert "[%s] skip (exists)" % stage in printed, stage


def test_force_redoes_the_stages_a_second_run_would_skip(ready, capsys):
    _run(ready, "--mode", "interval", "--every", "5")
    capsys.readouterr()
    before = tree_state(ready.root)

    code = _run(ready, "--mode", "interval", "--every", "5", "--force")
    printed = capsys.readouterr().out

    assert code == exit_codes.WARN, printed
    assert "skip (exists)" not in printed
    assert tree_state(ready.root) != before


# -- the scenario: a check error stops the run -------------------------------
def test_an_error_in_an_early_check_stops_every_later_stage(
    ready, monkeypatch, capsys
):
    from lecture2notes.cli import main as _unused  # noqa: F401 - import for clarity

    def broken(path):
        report = check.StageReport("frames", Path(path).name)
        report.add("error", "sha256", "frames/x.png", "manifest ab.. actual cd..")
        return report

    monkeypatch.setattr(check, "check_frames", broken)

    code = _run(ready, "--mode", "interval", "--every", "5")
    printed = capsys.readouterr().out

    assert code == exit_codes.ERROR
    assert "[frames] error: sha256 frames/x.png: manifest ab.. actual cd.." in printed
    assert "[render]" not in printed
    assert "[viewer]" not in printed
    assert not ready.note.exists()
    assert not (ready.root / ("%s.viewer.html" % ready.stem)).exists()


def test_a_warning_only_check_lets_the_run_finish_with_exit_one(
    ready, monkeypatch, capsys
):
    real = check.check_transcribe_stage

    def warned(path):
        report = real(path)
        report.add("warn", "loop", Path(path).name, "a repeated cue")
        return report

    monkeypatch.setattr(check, "check_transcribe_stage", warned)

    code = _run(ready, "--mode", "interval", "--every", "5")
    printed = capsys.readouterr().out

    assert code == exit_codes.WARN
    assert "warn loop" in printed
    assert "[viewer] ok" in printed
    assert ready.note.is_file()


def test_a_missing_document_stops_the_run_at_scaffold(ready, capsys):
    ready.document.unlink()

    code = _run(ready, "--mode", "interval", "--every", "5")
    printed = capsys.readouterr().out

    assert code == exit_codes.ERROR
    assert "[scaffold] error:" in printed
    assert "lecture2notes skill" in printed
    assert "[render]" not in printed


def test_a_broken_document_stops_the_run_before_the_note_is_written(ready, capsys):
    """A legacy document is the clearest case: the frames stage cannot repair it.

    Damaging a segment's frame would not do, because the frames stage merges its
    manifest back into the document and would quietly put the frame back --
    correct behaviour, but it means a frame is the wrong thing to break here.
    """
    document = json.loads(ready.document.read_text(encoding="utf-8"))
    document.pop("schema_version")
    ready.document.write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )

    code = _run(ready, "--mode", "interval", "--every", "5")
    printed = capsys.readouterr().out

    assert code == exit_codes.ERROR
    assert "[scaffold] error:" in printed
    assert not ready.note.exists()


# -- the gates that fire before any work -------------------------------------
def test_run_refuses_a_video_that_is_not_there(tmp_path, capsys):
    before = tree_state(tmp_path)

    code = cli_entry(["run", str(tmp_path / "absent.mp4"), "--lang", "zh"])

    assert code == exit_codes.ERROR
    assert "no such file" in capsys.readouterr().out
    assert tree_state(tmp_path) == before


def test_the_chapter_file_is_written_only_when_the_profile_enables_it(
    ready, monkeypatch, capsys
):
    from lecture2notes.outputs import pbf as pbf_mod

    _run(ready, "--mode", "interval", "--every", "5")
    chapter = ready.root / ("%s.pbf" % ready.stem)
    assert not chapter.exists()

    monkeypatch.setattr(pbf_mod, "is_enabled", lambda config: True)
    code = _run(ready, "--mode", "interval", "--every", "5")

    assert code == exit_codes.WARN
    assert chapter.is_file()
    assert "[pbf] ok" in capsys.readouterr().out

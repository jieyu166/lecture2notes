"""`l2n ocr --skip-frames`: participant panels and chat never reach the JSON.

A trial run found real participant names in ``frame_ocr``: the recording showed
the meeting's participant panel for a few minutes and OCR read every name on
it. Frames like that are excluded before OCR, by path glob or by time range,
and text an earlier run cached for them is not merged either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="Pillow writes the frames being OCRed")

from test_ocr import CountingEngine, build_lecture  # noqa: E402

from lecture2notes import _deps, exit_codes  # noqa: E402
from lecture2notes.cli.main import main as cli_entry  # noqa: E402
from lecture2notes.frames import ocr as ocr_mod  # noqa: E402
from lecture2notes.schema.io import read_json  # noqa: E402


@pytest.fixture()
def no_s2t(monkeypatch):
    monkeypatch.setattr(ocr_mod, "load_s2t", lambda enabled=True: None)


def test_a_glob_skips_the_frame_and_its_text(tmp_path: Path, no_s2t):
    document = build_lecture(tmp_path, frame_count=3)
    engine = CountingEngine()
    result = ocr_mod.run_ocr(
        document, engine_factory=lambda: engine, skip_frames=["frames/talk-0010.png"]
    )

    assert result.skipped == ["frames/talk-0010.png"]
    assert not any("talk-0010" in call for call in engine.calls)
    merged = read_json(document)
    assert merged["segments"][1]["frame_ocr"] == []
    assert merged["segments"][0]["frame_ocr"] != []


def test_a_time_range_skips_by_the_time_in_the_file_name(tmp_path: Path, no_s2t):
    document = build_lecture(tmp_path, frame_count=3)  # frames at 0, 10, 20 s
    engine = CountingEngine()
    result = ocr_mod.run_ocr(
        document, engine_factory=lambda: engine, skip_frames=["00:00:10-00:00:20"]
    )

    assert sorted(result.skipped) == ["frames/talk-0010.png", "frames/talk-0020.png"]
    assert len(engine.calls) == 1


def test_a_skipped_frame_loses_text_an_earlier_run_cached(tmp_path: Path, no_s2t):
    document = build_lecture(tmp_path, frame_count=2)
    ocr_mod.run_ocr(document, engine_factory=CountingEngine)
    assert read_json(document)["segments"][1]["frame_ocr"]

    ocr_mod.run_ocr(document, engine_factory=CountingEngine, skip_frames=["*-0010.png"])

    assert read_json(document)["segments"][1]["frame_ocr"] == []


@pytest.mark.parametrize("pattern, frame, skipped", [
    ("frames/talk-00*.png", "frames/talk-0010.png", True),
    ("talk-0010.png", "frames/talk-0010.png", True),
    ("52:00-58:30", "frames/talk-5300.png", True),
    ("3120-3510", "frames/talk-5300.png", True),
    ("00:52:00-00:58:30", "frames/talk-5900.png", False),
    ("other-*.png", "frames/talk-0010.png", False),
])
def test_the_matcher(pattern, frame, skipped):
    assert ocr_mod.SkipFrames([pattern]).matches(frame) is skipped


def test_a_backwards_range_is_a_usage_error():
    with pytest.raises(ocr_mod.OcrTargetError):
        ocr_mod.SkipFrames(["00:10:00-00:05:00"])


def test_the_cli_accepts_the_flag_more_than_once(tmp_path: Path, monkeypatch, no_s2t):
    document = build_lecture(tmp_path, frame_count=3)
    monkeypatch.setattr(_deps, "require", lambda name: None)
    monkeypatch.setattr(ocr_mod, "load_engine", CountingEngine)

    code = cli_entry([
        "ocr", str(document),
        "--skip-frames", "frames/talk-0000.png",
        "--skip-frames", "00:00:20-00:00:25",
    ])

    assert code == exit_codes.OK
    segments = read_json(document)["segments"]
    assert [bool(s["frame_ocr"]) for s in segments] == [False, True, False]

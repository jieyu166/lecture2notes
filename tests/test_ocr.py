"""Task 5.5 -- OCR is cached and attached to frames.

RapidOCR loads an ONNX runtime and several hundred megabytes of weights. Paying
that on every run to re-read frames that have not changed is the difference
between a cached re-run taking a second and taking half a minute, so the cache
is not an optimisation to be verified loosely: the second run must not call the
engine at all, and the test asserts on the call count rather than on the timing.

No real OCR engine is used here. A counting fake stands in, so the test needs no
model, no GPU and no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="Pillow writes the frames being OCRed")

from fixtures import synthetic  # noqa: E402

from lecture2notes import _deps  # noqa: E402
from lecture2notes.cli.main import main as cli_entry  # noqa: E402
from lecture2notes.frames import ocr as ocr_mod  # noqa: E402
from lecture2notes.schema.io import read_json  # noqa: E402


class CountingEngine:
    """Stands in for RapidOCR: records every call, returns fixed text."""

    def __init__(self, text: str = "SLIDE TEXT") -> None:
        self.text = text
        self.calls: list = []

    def __call__(self, path: str):
        self.calls.append(str(path))
        # RapidOCR returns (result, elapsed); each result row is box/text/score.
        return ([[None, self.text, 0.98]], 0.01)


@pytest.fixture()
def no_s2t(monkeypatch):
    """OpenCC would rewrite the fake text; it is not what this file tests."""
    monkeypatch.setattr(ocr_mod, "load_s2t", lambda enabled=True: None)


def build_lecture(tmp_path: Path, frame_count: int = 3) -> Path:
    """A document with one segment per frame, and the frames on disk."""
    frames_dir = tmp_path / "frames"
    segments = []
    for index in range(frame_count):
        name = "talk-%04d.png" % (index * 10)
        synthetic.gray_image(frames_dir / name, 40 + index * 20, size=(64, 36))
        segments.append({
            "index": index + 1,
            "start_sec": float(index * 10),
            "end_sec": float((index + 1) * 10),
            "title": "segment %d" % (index + 1),
            "frame": "frames/%s" % name,
            "frames": ["frames/%s" % name],
        })
    document = tmp_path / "talk.json"
    document.write_text(
        json.dumps({"schema_version": "2.0", "segments": segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return document


# --------------------------------------------------------------------------
# the acceptance case
# --------------------------------------------------------------------------
def test_the_second_run_is_a_full_cache_hit_and_never_calls_the_engine(
    tmp_path: Path, capsys, no_s2t
):
    document = build_lecture(tmp_path, frame_count=3)
    engine = CountingEngine()

    first = ocr_mod.run_ocr(document, engine_factory=lambda: engine)
    capsys.readouterr()

    second_engine = CountingEngine()
    second = ocr_mod.run_ocr(document, engine_factory=lambda: second_engine)
    printed = capsys.readouterr().out

    assert first.engine_calls == 3
    assert len(engine.calls) == 3
    assert second.engine_calls == 0
    assert second_engine.calls == []
    assert "[ocr] 3 frames, 0 need OCR (3 cached)" in printed


def test_the_cache_report_wording_matches_the_contract():
    assert ocr_mod.cache_report(102, 0) == "102 frames, 0 need OCR (102 cached)"
    assert ocr_mod.cache_report(102, 5) == "102 frames, 5 need OCR (97 cached)"


def test_the_cache_file_is_keyed_by_frame_with_a_size_and_mtime_fingerprint(
    tmp_path: Path, no_s2t
):
    document = build_lecture(tmp_path, frame_count=2)

    ocr_mod.run_ocr(document, engine_factory=CountingEngine)

    cache = read_json(tmp_path / "talk.frames_ocr.json")
    assert sorted(cache) == ["frames/talk-0000.png", "frames/talk-0010.png"]
    for frame, entry in cache.items():
        stat = (tmp_path / frame).stat()
        assert entry["fingerprint"] == "%d:%d" % (stat.st_size, int(stat.st_mtime))
        assert entry["text"] == "SLIDE TEXT"


def test_a_changed_frame_invalidates_only_its_own_entry(tmp_path: Path, no_s2t):
    document = build_lecture(tmp_path, frame_count=3)
    ocr_mod.run_ocr(document, engine_factory=CountingEngine)

    changed = tmp_path / "frames" / "talk-0010.png"
    synthetic.gray_image(changed, 5, size=(64, 36))
    import os
    os.utime(changed, (changed.stat().st_atime + 60, changed.stat().st_mtime + 60))

    engine = CountingEngine("NEW TEXT")
    result = ocr_mod.run_ocr(document, engine_factory=lambda: engine)

    assert result.engine_calls == 1
    assert engine.calls == [str(changed)]


def test_force_re_ocrs_everything(tmp_path: Path, no_s2t):
    document = build_lecture(tmp_path, frame_count=3)
    ocr_mod.run_ocr(document, engine_factory=CountingEngine)

    engine = CountingEngine()
    result = ocr_mod.run_ocr(document, engine_factory=lambda: engine, force=True)

    assert result.engine_calls == 3


# --------------------------------------------------------------------------
# what lands in the document
# --------------------------------------------------------------------------
def test_frame_ocr_is_written_into_every_segment(tmp_path: Path, no_s2t):
    document = build_lecture(tmp_path, frame_count=2)

    ocr_mod.run_ocr(document, engine_factory=CountingEngine)

    data = read_json(document)
    assert data["segments"][0]["frame_ocr"] == [
        {"frame": "frames/talk-0000.png", "text": "SLIDE TEXT"}
    ]
    assert data["segments"][1]["frame_ocr"] == [
        {"frame": "frames/talk-0010.png", "text": "SLIDE TEXT"}
    ]
    assert data["ocr_meta"]["frames_with_text"] == 2
    assert data["schema_version"] == "2.0"


def test_an_empty_ocr_result_still_leaves_a_valid_v2_document(tmp_path: Path):
    """Flat-colour slides yield no text; ``frame_ocr`` must stay an empty list."""
    from lecture2notes.acceptance.check import check_json
    from lecture2notes.schema.io import write_json_atomic

    fixture = Path(__file__).parent / "fixtures" / "lecture_v2_valid.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))

    merged = ocr_mod.merge_ocr_into_segments(document, {})

    for segment in merged["segments"]:
        assert segment["frame_ocr"] == []
        names = list(segment.get("frames") or [])
        if segment.get("frame"):
            names.append(segment["frame"])
        for name in names:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"placeholder frame")
    path = write_json_atomic(tmp_path / "sample-talk.json", merged)

    report = check_json(path)
    assert report.errors == []
    assert merged["ocr_meta"]["segments_with_text"] == 0


def test_meeting_toolbar_lines_are_stripped(tmp_path: Path, no_s2t):
    document = build_lecture(tmp_path, frame_count=1)

    class ToolbarEngine(CountingEngine):
        def __call__(self, path: str):
            self.calls.append(str(path))
            return (
                [
                    [None, "靜音", 0.99],
                    [None, "Lung-RADS 4B", 0.97],
                    [None, "mute", 0.95],
                ],
                0.01,
            )

    ocr_mod.run_ocr(document, engine_factory=ToolbarEngine)

    data = read_json(document)
    assert data["segments"][0]["frame_ocr"][0]["text"] == "Lung-RADS 4B"


def test_a_frames_folder_resolves_to_the_lecture_beside_it(tmp_path: Path, no_s2t):
    """Task 15.2. The folder is named `frames`; the lecture is not.

    Deriving the stem from the folder name wrote `frames.frames_ocr.json`, a
    cache no later stage ever opens, and left `talk.json` without a single
    character of OCR text. A field run did exactly this and only noticed at the
    note-writing stage.
    """
    document = build_lecture(tmp_path, frame_count=2)

    result = ocr_mod.run_ocr(tmp_path / "frames", engine_factory=CountingEngine)

    assert result.total == 2
    assert result.document == document
    assert (tmp_path / "talk.frames_ocr.json").is_file()
    assert not (tmp_path / "frames.frames_ocr.json").exists()
    assert read_json(document)["segments"][0]["frame_ocr"]


def test_a_frames_folder_beside_a_manifest_alone_still_resolves(tmp_path: Path, no_s2t):
    """`<stem>.frames.json` names the stem even before the document exists."""
    build_lecture(tmp_path, frame_count=1)
    (tmp_path / "talk.json").rename(tmp_path / "talk.frames.json")

    result = ocr_mod.run_ocr(tmp_path / "frames", engine_factory=CountingEngine)

    assert result.document is None
    assert (tmp_path / "talk.frames_ocr.json").is_file()


def test_a_frames_folder_with_no_lecture_beside_it_is_refused(tmp_path: Path, no_s2t):
    build_lecture(tmp_path, frame_count=1)
    (tmp_path / "talk.json").unlink()

    with pytest.raises(ocr_mod.OcrTargetError) as excinfo:
        ocr_mod.run_ocr(tmp_path / "frames", engine_factory=CountingEngine)

    assert "which lecture" in str(excinfo.value)
    assert not list(tmp_path.glob("*.frames_ocr.json"))


def test_two_lectures_beside_one_frames_folder_are_refused(tmp_path: Path, no_s2t):
    build_lecture(tmp_path, frame_count=1)
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ocr_mod.OcrTargetError) as excinfo:
        ocr_mod.run_ocr(tmp_path / "frames", engine_factory=CountingEngine)

    assert "other" in str(excinfo.value) and "talk" in str(excinfo.value)


def test_sidecars_beside_a_folder_are_not_mistaken_for_lectures(tmp_path: Path):
    build_lecture(tmp_path, frame_count=1)
    for name in ("talk.frames.json", "talk.frames_ocr.json",
                 "talk.corrections.json", "_course.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert ocr_mod.folder_stems(tmp_path / "frames") == ["talk"]


def test_progress_is_reported_and_stays_ascii(tmp_path: Path, capsys, no_s2t):
    document = build_lecture(tmp_path, frame_count=3)

    ocr_mod.run_ocr(document, engine_factory=CountingEngine, progress_interval=0)

    printed = capsys.readouterr().out
    assert "[ocr] 1/3" in printed
    assert "[ocr] 3/3" in printed
    assert not any(marker in printed for marker in ("→", "≥", "✓", "✗"))


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------
def test_missing_rapidocr_exits_3(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):
        raise _deps.MissingDependency("rapidocr", _deps.INSTALL_HINTS["rapidocr"])

    build_lecture(tmp_path, frame_count=1)
    monkeypatch.setattr(_deps, "require_rapidocr", boom)
    monkeypatch.setitem(_deps._CHECKS, "rapidocr", boom)

    assert cli_entry(["ocr", str(tmp_path / "talk.json")]) == 3


def test_cli_ocr_writes_the_cache_and_the_document(tmp_path: Path, monkeypatch, no_s2t):
    document = build_lecture(tmp_path, frame_count=2)
    monkeypatch.setitem(_deps._CHECKS, "rapidocr", lambda *a, **k: object())
    monkeypatch.setattr(ocr_mod, "load_engine", CountingEngine)

    code = cli_entry(["ocr", str(document)])

    assert code == 0
    assert (tmp_path / "talk.frames_ocr.json").is_file()
    assert read_json(document)["segments"][0]["frame_ocr"]


def test_cli_ocr_on_a_missing_target_is_a_usage_error(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(_deps._CHECKS, "rapidocr", lambda *a, **k: object())

    assert cli_entry(["ocr", str(tmp_path / "nope.json")]) == 2


def test_cli_ocr_on_a_frames_folder_writes_the_lecture_cache(
    tmp_path: Path, monkeypatch, no_s2t
):
    document = build_lecture(tmp_path, frame_count=2)
    monkeypatch.setitem(_deps._CHECKS, "rapidocr", lambda *a, **k: object())
    monkeypatch.setattr(ocr_mod, "load_engine", CountingEngine)

    assert cli_entry(["ocr", str(tmp_path / "frames")]) == 0

    assert (tmp_path / "talk.frames_ocr.json").is_file()
    assert not (tmp_path / "frames.frames_ocr.json").exists()
    assert read_json(document)["segments"][0]["frame_ocr"]


def test_cli_ocr_on_an_ambiguous_frames_folder_exits_2(
    tmp_path: Path, monkeypatch, capsys, no_s2t
):
    build_lecture(tmp_path, frame_count=1)
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(_deps._CHECKS, "rapidocr", lambda *a, **k: object())
    monkeypatch.setattr(ocr_mod, "load_engine", CountingEngine)

    assert cli_entry(["ocr", str(tmp_path / "frames")]) == 2

    printed = capsys.readouterr().out
    assert "[error] ocr:" in printed
    assert not list(tmp_path.glob("*.frames_ocr.json"))

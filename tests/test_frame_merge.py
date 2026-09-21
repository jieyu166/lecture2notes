"""Task 5.3 -- frames are merged into the canonical JSON by time range.

Two rules, and the second is the one that gets written wrong every time.

A segment takes the frames whose timestamp falls in ``[start_sec, end_sec)``
and its lead ``frame`` is the first of them. A segment with no frame of its own
does *not* get an empty ``frame``: it inherits the latest earlier frame, because
that is literally what was on screen when the segment began. ``frames`` stays
empty, so a later check can still distinguish "nothing changed here" from
"capture never ran".

The acceptance case is segment 5 spanning 1260 to 1440 with manifest frames at
1254 and 1441: ``frames`` is ``[]`` and ``frame`` is the 1254-second one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes.frames.capture import merge_into_document
from lecture2notes.frames.manifest import merge_frames_into_segments
from lecture2notes.schema.io import read_json


def row(second: float, name: str) -> dict:
    return {
        "timestamp_sec": float(second),
        "frame": "frames/%s" % name,
        "sha256": "0" * 64,
    }


def segment(index: int, start: float, end: float) -> dict:
    return {
        "index": index,
        "start_sec": float(start),
        "end_sec": float(end),
        "title": "segment %d" % index,
        "frame": None,
        "frames": [],
    }


# --------------------------------------------------------------------------
# the acceptance case
# --------------------------------------------------------------------------
def test_segment_without_its_own_frame_inherits_the_latest_earlier_one():
    document = {"segments": [segment(5, 1260, 1440)]}
    frames = [row(1254, "talk-2054.png"), row(1441, "talk-2401.png")]

    merged = merge_frames_into_segments(document, frames)
    result = merged["segments"][0]

    assert result["frames"] == []
    assert result["frame"] == "frames/talk-2054.png"


def test_a_frame_exactly_on_the_start_belongs_to_the_segment():
    """The range is half-open: start is inside, end is not."""
    document = {"segments": [segment(1, 100, 200), segment(2, 200, 300)]}
    frames = [row(100, "a.png"), row(200, "b.png")]

    merged = merge_frames_into_segments(document, frames)

    assert merged["segments"][0]["frames"] == ["frames/a.png"]
    assert merged["segments"][1]["frames"] == ["frames/b.png"]


def test_the_lead_frame_is_the_first_one_inside_the_range():
    document = {"segments": [segment(1, 0, 100)]}
    frames = [row(80, "late.png"), row(10, "early.png"), row(40, "middle.png")]

    merged = merge_frames_into_segments(document, frames)
    result = merged["segments"][0]

    assert result["frames"] == ["frames/early.png", "frames/middle.png", "frames/late.png"]
    assert result["frame"] == "frames/early.png"


def test_a_segment_before_every_frame_falls_back_to_the_first():
    document = {"segments": [segment(1, 0, 50)]}
    frames = [row(90, "later.png")]

    merged = merge_frames_into_segments(document, frames)

    assert merged["segments"][0]["frames"] == []
    assert merged["segments"][0]["frame"] == "frames/later.png"


def test_an_empty_manifest_leaves_every_frame_null():
    document = {"segments": [segment(1, 0, 50)]}

    merged = merge_frames_into_segments(document, [])

    assert merged["segments"][0]["frames"] == []
    assert merged["segments"][0]["frame"] is None


def test_the_source_document_is_not_mutated_in_its_top_level():
    document = {"stem": "talk", "segments": [segment(1, 0, 50)]}

    merged = merge_frames_into_segments(document, [row(10, "a.png")])

    assert merged is not document
    assert merged["stem"] == "talk"


# --------------------------------------------------------------------------
# the write-back
# --------------------------------------------------------------------------
def _write_document(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_merge_into_document_rewrites_the_file(tmp_path: Path):
    path = _write_document(
        tmp_path / "talk.json", {"schema_version": "2.0", "segments": [segment(5, 1260, 1440)]}
    )

    merge_into_document(path, [row(1254, "talk-2054.png"), row(1441, "talk-2401.png")])

    reloaded = read_json(path)
    assert reloaded["segments"][0]["frames"] == []
    assert reloaded["segments"][0]["frame"] == "frames/talk-2054.png"
    assert reloaded["schema_version"] == "2.0"


def test_the_rewrite_leaves_no_bom_and_no_crlf(tmp_path: Path):
    path = _write_document(tmp_path / "talk.json", {"segments": [segment(1, 0, 50)]})

    merge_into_document(path, [row(10, "a.png")])

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw


def test_a_failed_rewrite_leaves_the_original_intact(tmp_path: Path, monkeypatch):
    """The merge runs after a capture that may have taken minutes."""
    path = _write_document(tmp_path / "talk.json", {"segments": [segment(1, 0, 50)]})
    before = path.read_bytes()

    import lecture2notes.schema.io as io_mod

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(io_mod.json, "dump", boom)
    with pytest.raises(OSError):
        merge_into_document(path, [row(10, "a.png")])

    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["talk.json"]


def test_capture_merges_when_a_document_sits_beside_the_video(tmp_path: Path, monkeypatch):
    """Capture wires the merge itself; staging must not touch the document."""
    from lecture2notes.frames import capture as capture_mod

    pytest.importorskip("PIL", reason="Pillow writes the injected frames")
    from fixtures import synthetic

    video = tmp_path / "talk.mp4"
    video.write_bytes(b"injected extraction")
    _write_document(
        tmp_path / "talk.json",
        {"segments": [segment(1, 0, 20), segment(2, 20, 40)]},
    )
    monkeypatch.setattr(capture_mod._deps, "require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(capture_mod._deps, "require_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(capture_mod.scene_mode, "get_duration", lambda path: 40.0)

    shade = {0: 10, 10: 90, 20: 170, 30: 250}

    def fake_extract(source, second, out_path, width):
        synthetic.gray_image(Path(out_path), shade[int(second)], size=(64, 36))
        return True

    result = capture_mod.capture(
        video, mode="interval", every=10, extract=fake_extract
    )

    assert result.merged_into == tmp_path / "talk.json"
    reloaded = read_json(tmp_path / "talk.json")
    assert reloaded["segments"][0]["frames"] == ["frames/talk-0000.png", "frames/talk-0010.png"]
    assert reloaded["segments"][1]["frame"] == "frames/talk-0020.png"


def test_staged_capture_does_not_touch_the_document(tmp_path: Path, monkeypatch):
    from lecture2notes.frames import capture as capture_mod

    pytest.importorskip("PIL", reason="Pillow writes the injected frames")
    from fixtures import synthetic

    video = tmp_path / "talk.mp4"
    video.write_bytes(b"injected extraction")
    document = _write_document(tmp_path / "talk.json", {"segments": [segment(1, 0, 40)]})
    before = document.read_bytes()
    monkeypatch.setattr(capture_mod._deps, "require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(capture_mod._deps, "require_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(capture_mod.scene_mode, "get_duration", lambda path: 40.0)

    def fake_extract(source, second, out_path, width):
        synthetic.gray_image(Path(out_path), int(second) * 6, size=(64, 36))
        return True

    result = capture_mod.capture(
        video, mode="interval", every=10, stage=True, extract=fake_extract
    )

    assert result.merged_into is None
    assert document.read_bytes() == before

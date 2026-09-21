"""Smoke tests for the rebuild preflight ported from rebuild_course.py."""

from __future__ import annotations

import pytest

from lecture2notes.acceptance.rebuild import (
    frame_paths,
    is_lecture_document,
    pair_lectures,
    planned_outputs,
    run_preflight,
)


def _lecture(folder, lecture_id):
    (folder / ("%s.mp4" % lecture_id)).write_bytes(b"video")
    (folder / ("%s.srt" % lecture_id)).write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nhello\n", encoding="utf-8"
    )
    (folder / ("%s.json" % lecture_id)).write_text('{"segments": []}', encoding="utf-8")


def test_is_lecture_document_excludes_derived_json(tmp_path):
    assert is_lecture_document(tmp_path / "talk.json") is True
    assert is_lecture_document(tmp_path / "talk.frames.json") is False
    assert is_lecture_document(tmp_path / "talk.frames_ocr.json") is False
    assert is_lecture_document(tmp_path / "talk.audit.json") is False
    assert is_lecture_document(tmp_path / "_titles.json") is False
    assert is_lecture_document(tmp_path / "talk.md") is False


def test_pair_lectures_groups_one_video_subtitle_and_document(tmp_path):
    _lecture(tmp_path, "one")
    _lecture(tmp_path, "two")
    (tmp_path / "one.frames.json").write_text("[]", encoding="utf-8")
    (tmp_path / "one.raw.srt").write_text("ignored", encoding="utf-8")

    pairs = pair_lectures(tmp_path)

    assert [pair.lecture_id for pair in pairs] == ["one", "two"]
    assert pairs[0].video.name == "one.mp4"
    assert pairs[0].subtitle.name == "one.srt"
    assert pairs[0].document.name == "one.json"


def test_pair_lectures_raises_on_an_incomplete_set(tmp_path):
    (tmp_path / "lonely.mp4").write_bytes(b"video")

    with pytest.raises(ValueError, match="pairing conflict"):
        pair_lectures(tmp_path)


def test_planned_outputs_marks_create_versus_overwrite(tmp_path):
    _lecture(tmp_path, "one")
    (tmp_path / "one.pbf").write_text("[Bookmark]\n", encoding="utf-8")
    pair = pair_lectures(tmp_path)[0]

    planned = {item.path.rsplit(".", 1)[-1]: item.action for item in planned_outputs(pair, tmp_path)}

    assert planned["pbf"] == "overwrite"
    assert planned["md"] == "create"


def test_run_preflight_is_read_only_and_reports_missing_tools(tmp_path):
    _lecture(tmp_path, "one")
    before = sorted((path.name, path.stat().st_mtime_ns) for path in tmp_path.iterdir())

    result = run_preflight(
        tmp_path,
        command_lookup=lambda name: None,
        module_available=lambda name: None,
        minimum_free_bytes=0,
    )

    codes = {finding.code for finding in result.findings}
    assert "ffmpeg_missing" in codes
    assert "ffprobe_missing" in codes
    assert "rapidocr_onnxruntime_missing" in codes
    assert result.ok is False
    assert result.exit_code() == 2
    assert sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.iterdir()) == before


def test_run_preflight_passes_when_every_dependency_is_present(tmp_path):
    _lecture(tmp_path, "one")

    result = run_preflight(
        tmp_path,
        command_lookup=lambda name: "C:/fake/%s.exe" % name,
        module_available=lambda name: object(),
        minimum_free_bytes=0,
    )

    assert result.findings == []
    assert result.ok is True
    assert result.exit_code() == 0
    assert len(result.planned) == 4
    assert result.to_dict()["pairs"][0]["lecture_id"] == "one"


def test_run_preflight_flags_a_lecture_count_mismatch_only_when_asked(tmp_path):
    _lecture(tmp_path, "one")
    kwargs = dict(
        command_lookup=lambda name: "x",
        module_available=lambda name: object(),
        minimum_free_bytes=0,
    )

    assert run_preflight(tmp_path, **kwargs).findings == []
    codes = {
        finding.code
        for finding in run_preflight(tmp_path, expected_lecture_count=11, **kwargs).findings
    }
    assert "lecture_count" in codes


def test_frame_paths_reads_both_frame_spellings():
    document = {
        "segments": [
            {"frames": [{"path": "frames/a.png", "time": 1.0}]},
            {"frames": ["frames/b.png"]},
        ]
    }

    assert frame_paths(document) == ["frames/a.png", "frames/b.png"]

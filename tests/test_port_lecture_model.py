"""Smoke tests for the canonical document model ported from lecture_model.py."""

from __future__ import annotations

import json

import pytest

from lecture2notes.schema.model import (
    Finding,
    assert_times_unchanged,
    normalize_lecture,
    segment_end,
    segment_start,
    time_signature,
    validate_lecture_schema,
    write_json_atomic,
)


def _legacy_document():
    return {
        "segments": [
            {
                "index": 1,
                "start": 0,
                "end": 120,
                "title": "Opening",
                "summary_zh": "summary",
                "bullets_zh": ["one", "two"],
                "frames": ["frames/talk-0000.png"],
                "frame_ocr": [{"frame": "frames/talk-0000.png", "text": "slide text"}],
            }
        ]
    }


def test_normalize_lecture_upgrades_times_bullets_and_frames():
    result = normalize_lecture(_legacy_document())
    segment = result["segments"][0]

    assert segment["start_sec"] == 0 and segment["end_sec"] == 120
    assert "start" not in segment and "end" not in segment
    assert segment["takeaways_zh"] == ["one", "two"]
    assert "bullets_zh" not in segment
    assert segment["editorial_notes_zh"] == []
    assert segment["frames"] == [
        {"time": None, "ocr": "slide text", "path": "frames/talk-0000.png"}
    ]


def test_segment_accessors_read_both_spellings():
    assert segment_start({"start": 12}) == 12.0
    assert segment_start({"start_sec": 12, "start": 99}) == 12.0
    assert segment_end({"end": 34}) == 34.0
    assert time_signature({"segments": [{"start_sec": 0, "end_sec": 5}]}) == ((0.0, 5.0),)


def test_assert_times_unchanged_rejects_a_retyped_timestamp():
    before = {"segments": [{"index": 1, "start_sec": 1200, "end_sec": 1500}]}
    after = {"segments": [{"index": 1, "start_sec": 1200.0, "end_sec": 1500}]}

    assert_times_unchanged(before, before)
    with pytest.raises(ValueError, match="time changed"):
        assert_times_unchanged(before, after)


def test_validate_lecture_schema_reports_a_missing_frame_file(tmp_path):
    document = {
        "segments": [
            {
                "index": 1,
                "start_sec": 0,
                "end_sec": 60,
                "title": "Opening",
                "summary_zh": "summary",
                "takeaways_zh": ["a", "b", "c", "d"],
                "editorial_notes_zh": [],
                "frames": [{"time": 10.0, "ocr": "", "path": "frames/gone.png"}],
            }
        ]
    }
    findings = validate_lecture_schema(document, tmp_path)
    codes = {finding.code for finding in findings}

    assert "frame_missing" in codes
    assert all(isinstance(finding, Finding) for finding in findings)


def test_validate_lecture_schema_rejects_an_escaping_frame_path(tmp_path):
    document = {
        "segments": [
            {
                "index": 1,
                "start_sec": 0,
                "end_sec": 60,
                "title": "Opening",
                "summary_zh": "summary",
                "takeaways_zh": ["a", "b", "c", "d"],
                "editorial_notes_zh": [],
                "frames": [{"time": 10.0, "ocr": "", "path": "../outside.png"}],
            }
        ]
    }
    codes = {finding.code for finding in validate_lecture_schema(document, tmp_path)}

    assert "frame_path" in codes


def test_write_json_atomic_writes_utf8_without_bom_and_lf_only(tmp_path):
    target = tmp_path / "doc.json"
    write_json_atomic(target, {"title": "講座", "segments": []})
    raw = target.read_bytes()

    assert raw[:3] != b"\xef\xbb\xbf"
    assert b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8"))["title"] == "講座"
    assert "講" in raw.decode("utf-8")  # non-ASCII is not escaped


def test_write_json_atomic_leaves_the_original_intact_on_failure(tmp_path):
    target = tmp_path / "doc.json"
    write_json_atomic(target, {"ok": True})
    before = target.read_bytes()

    with pytest.raises(ValueError):
        write_json_atomic(target, {"bad": float("nan")})

    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))

"""Smoke tests for the skeleton renderer ported from render_v4_note.py."""

from __future__ import annotations

import hashlib

from lecture2notes.notes.render import bullet_texts, render_note, write_note


def _document():
    return {
        "title": "Demo lecture",
        "segments": [
            {
                "index": 1,
                "start_sec": 0,
                "end_sec": 60.5,
                "title": "Opening",
                "summary_zh": "the opening summary",
                "takeaways_zh": ["first", "second"],
                "editorial_notes_zh": ["an editorial note"],
                "frames": [{"time": 12.25, "ocr": "", "path": "frames/a.png"}],
            }
        ],
    }


def test_render_note_projects_only_document_content():
    text = render_note(_document())

    assert text.startswith("# Demo lecture\n")
    assert "## 1. Opening" in text
    assert "> 0.000s-60.500s" in text
    assert "the opening summary" in text
    assert "- first" in text and "- second" in text
    assert "> [!note] Editorial" in text
    assert "> - an editorial note" in text
    assert "![[frames/a.png]] <!-- 12.250s -->" in text
    assert text.endswith("\n")


def test_render_note_is_byte_identical_across_runs():
    first = hashlib.sha256(render_note(_document()).encode("utf-8")).hexdigest()
    second = hashlib.sha256(render_note(_document()).encode("utf-8")).hexdigest()

    assert first == second


def test_render_note_skips_a_frame_without_a_usable_time():
    document = _document()
    document["segments"][0]["frames"] = [
        {"time": None, "ocr": "", "path": "frames/a.png"},
        {"time": 1.0, "ocr": "", "path": ""},
    ]

    assert "![[" not in render_note(document)


def test_bullet_texts_reads_strings_and_v2_objects():
    assert bullet_texts({"takeaways_zh": ["a", "b"]}) == ["a", "b"]
    assert bullet_texts({"bullets_zh": ["legacy"]}) == ["legacy"]
    assert bullet_texts(
        {"bullets_zh": [{"text": "object", "t": 1.0, "kind": "quote"}, {"no_text": 1}]}
    ) == ["object"]
    assert bullet_texts({}) == []


def test_write_note_writes_utf8_without_bom_and_lf_only(tmp_path):
    document = _document()
    document["title"] = "講座"
    path = write_note(tmp_path / "demo.v4.md", document)
    raw = path.read_bytes()

    assert raw[:3] != b"\xef\xbb\xbf"
    assert b"\r\n" not in raw
    assert raw.decode("utf-8").startswith("# 講座")

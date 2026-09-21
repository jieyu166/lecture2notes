"""Smoke tests for the content rules ported from lecture_content_rules.py."""

from __future__ import annotations

from lecture2notes.notes.rules import (
    GENERIC_TITLE,
    comparison_text,
    han_count,
    normalize_text,
    transcript_copy_findings,
    validate_segment_content,
)


def test_han_count_counts_only_han_characters():
    assert han_count("abc123") == 0
    assert han_count("講座 lecture") == 2
    assert han_count("") == 0


def test_comparison_text_folds_width_case_and_punctuation():
    assert comparison_text("Ｌｕｎｇ-ＲＡＤＳ ４") == comparison_text("lung rads 4")
    assert comparison_text("a, b. c!") == "abc"


def test_normalize_text_drops_invisible_formatting_characters():
    assert normalize_text("a‎b") == "ab"          # left-to-right mark
    assert normalize_text("a​b") == "ab"          # zero-width space
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"    # line endings fold to LF


def test_generic_title_pattern_matches_a_placeholder_title():
    assert GENERIC_TITLE.fullmatch("Overview")
    assert GENERIC_TITLE.fullmatch("重點")
    assert not GENERIC_TITLE.fullmatch("Rotator cuff tear on MRI")


def test_transcript_copy_findings_flags_a_pasted_summary():
    line = "這一段講的是膩關節訊號與影像表現的對應關係與判讀流程"
    assert han_count(line) >= 20

    copied = transcript_copy_findings(line, [], line)
    clean = transcript_copy_findings("an original summary", [], line)

    assert [finding.code for finding in copied] == ["transcript_copy"]
    assert clean == []


def test_transcript_copy_findings_flags_a_pasted_takeaway():
    line = "這一段講的是膩關節訊號與影像表現的對應關係與判讀流程"
    findings = transcript_copy_findings("original", [line], line)

    assert "takeaway_transcript_copy" in {finding.code for finding in findings}


def test_validate_segment_content_reports_structural_problems():
    findings = validate_segment_content(
        {
            "title": "Overview",
            "summary_zh": "too short",
            "takeaways_zh": ["one", "two"],
            "editorial_notes_zh": [],
        },
        "",
    )
    codes = {finding.code for finding in findings}

    assert "title_focus" in codes
    assert "summary_length" in codes
    assert "takeaway_count" in codes
    assert all(finding.severity == "error" for finding in findings)


def test_validate_segment_content_flags_an_unfinished_marker():
    codes = {
        finding.code
        for finding in validate_segment_content(
            {
                "title": "Rotator cuff tear on MRI",
                "summary_zh": "TODO",
                "takeaways_zh": ["a", "b", "c", "d"],
                "editorial_notes_zh": [],
            },
            "",
        )
    }

    assert "unfinished_marker" in codes


def test_validate_segment_content_rejects_a_non_mapping():
    findings = validate_segment_content(["not", "a", "segment"], "")

    assert [finding.code for finding in findings] == ["segment_type"]

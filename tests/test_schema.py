"""Canonical JSON schema v2: the validator and the `l2n check json` stage.

The five rows of the boundary table in the lecture-json-schema specification
each have a test here, named after the row. Everything is built by mutating one
valid fixture, so a test can only fail for the reason it is about.

No real lecture content appears in any fixture: the text is placeholder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import run_cli

from lecture2notes.acceptance.check import LEGACY_MESSAGE, StageReport, check_json
from lecture2notes.schema.io import write_json_atomic
from lecture2notes.schema.model import (
    Finding,
    MAX_SUMMARY_CHARS,
    MIN_SUMMARY_CHARS,
    SCHEMA_VERSION,
    validate_document,
)

FIXTURES = Path(__file__).parent / "fixtures"
VALID = FIXTURES / "lecture_v2_valid.json"

#: `<severity> <rule-or-field> <location>: <message>`
FINDING_LINE = re.compile(r"^(error|warn|info) \S+ \S+: .+$")


def load_valid() -> dict:
    return json.loads(VALID.read_text(encoding="utf-8"))


def materialize(document: dict, tmp_path: Path, name: str = "sample-talk.json") -> Path:
    """Write ``document`` into ``tmp_path`` together with the frames it names."""
    for segment in document.get("segments", []):
        names = list(segment.get("frames") or [])
        if segment.get("frame"):
            names.append(segment["frame"])
        for frame in names:
            target = tmp_path / frame
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"placeholder frame")
    return write_json_atomic(tmp_path / name, document)


def codes(document: dict) -> list:
    return [finding.code for finding in validate_document(document)]


def messages(document: dict) -> str:
    return "\n".join(finding.message for finding in validate_document(document))


# -- the document the rest of the tests mutate -----------------------------

def test_the_valid_fixture_has_no_findings():
    document = load_valid()
    assert document["schema_version"] == SCHEMA_VERSION
    assert validate_document(document) == []


def test_check_json_accepts_the_valid_fixture(tmp_path):
    path = materialize(load_valid(), tmp_path)
    report = check_json(path)

    assert report.findings == []
    assert report.exit_code() == 0
    assert report.summary_line() == "json: 0 errors, 0 warnings"


# -- boundary table, row by row --------------------------------------------

def test_boundary_summary_of_99_characters_is_an_error():
    document = load_valid()
    document["overall_summary_zh"] = "佔" * 99

    assert "summary_length" in codes(document)
    assert "overall_summary_zh length 99 < 100" in messages(document)


@pytest.mark.parametrize("length", [MIN_SUMMARY_CHARS, 300, MAX_SUMMARY_CHARS])
def test_boundary_summary_bounds_are_inclusive(length):
    document = load_valid()
    document["overall_summary_zh"] = "佔" * length

    assert validate_document(document) == []


def test_boundary_summary_of_501_characters_is_an_error():
    document = load_valid()
    document["overall_summary_zh"] = "佔" * 501

    assert "overall_summary_zh length 501 > 500" in messages(document)


def test_boundary_thirteen_takeaways_is_an_error():
    document = load_valid()
    document["takeaways_zh"] = ["佔位重點 %d" % n for n in range(13)]

    assert "takeaway_count" in codes(document)
    assert "takeaways_zh count 13 > 12" in messages(document)


@pytest.mark.parametrize("count", [6, 9, 12])
def test_boundary_takeaway_bounds_are_inclusive(count):
    document = load_valid()
    document["takeaways_zh"] = ["佔位重點 %d" % n for n in range(count)]

    assert validate_document(document) == []


def test_boundary_five_takeaways_is_an_error():
    document = load_valid()
    document["takeaways_zh"] = ["佔位重點 %d" % n for n in range(5)]

    assert "takeaways_zh count 5 < 6" in messages(document)


def test_boundary_a_bare_string_bullet_is_a_legacy_error():
    document = load_valid()
    document["segments"][1]["bullets_zh"][0] = "佔位字串條列"

    findings = validate_document(document)
    assert [finding.code for finding in findings] == ["bullets_zh"]
    finding = findings[0]
    assert finding.segment_index == 2
    assert finding.location == "segments[2].bullets_zh[0]"
    assert finding.message == "segment 2 bullets_zh[0] is not an object (legacy?)"


def test_boundary_start_time_disagreeing_with_start_sec_is_an_error():
    document = load_valid()
    segment = document["segments"][0]
    segment["start_sec"] = 3722
    segment["start_time"] = "01:02:03"

    findings = [f for f in validate_document(document) if f.code == "clock_mismatch"]
    assert len(findings) == 1
    assert findings[0].location == "segments[1].start_time"
    assert "01:02:03 is 3723 seconds but start_sec is 3722" in findings[0].message


def test_an_hh_mm_ss_string_matching_its_seconds_is_accepted():
    """The same hour-long timestamp, spelled correctly, raises nothing."""
    document = load_valid()
    segment = document["segments"][0]
    segment["start_sec"] = 3723
    segment["start_time"] = "01:02:03"

    assert "clock_mismatch" not in codes(document)


# -- structure: index continuity and joined boundaries ---------------------

def test_index_sequence_1_2_4_names_the_offending_segment():
    document = load_valid()
    document["segments"][2]["index"] = 4

    findings = [f for f in validate_document(document) if f.code == "index"]
    assert len(findings) == 1
    assert findings[0].segment_index == 3
    assert findings[0].message == "segment 3 index is 4, expected 3"


def test_a_gap_between_two_segments_is_reported_on_both_sides():
    document = load_valid()
    document["segments"][2]["start_sec"] = 130
    document["segments"][2]["start_time"] = "00:02:10"

    findings = [f for f in validate_document(document) if f.code == "contiguity"]
    assert len(findings) == 1
    assert findings[0].message == (
        "segment 2 end_sec 120 does not meet segment 3 start_sec 130"
    )


def test_the_last_segment_must_end_at_the_media_duration():
    document = load_valid()
    document["duration_sec"] = 240

    findings = [f for f in validate_document(document) if f.code == "contiguity"]
    assert len(findings) == 1
    assert "does not equal floor(duration_sec) 240" in findings[0].message


def test_a_fractional_duration_is_compared_by_its_floor():
    document = load_valid()
    document["duration_sec"] = 180.7

    assert validate_document(document) == []


# -- the rest of the key set ------------------------------------------------

def test_a_missing_top_level_key_is_reported_by_name():
    document = load_valid()
    for key in ("source", "profile", "corrections", "unverified_terms"):
        document.pop(key)

    reported = set(codes(document))
    assert {"source", "profile", "corrections", "unverified_terms"} <= reported


def test_subtitle_origin_must_be_asr_or_official():
    document = load_valid()
    document["source"]["subtitle"]["origin"] = "guessed"

    assert "source.subtitle.origin must be one of asr/official" in messages(document)


def test_an_offset_model_must_carry_both_coefficients():
    document = load_valid()
    document["source"]["subtitle"]["offset_model"] = {"a": 0.5}

    assert "source.subtitle.offset_model.b must be a number" in messages(document)


def test_a_bullet_kind_outside_the_vocabulary_is_an_error():
    document = load_valid()
    document["segments"][0]["bullets_zh"][0]["kind"] = "paraphrase"

    assert "kind must be one of synthesis/quote" in messages(document)


def test_a_segment_missing_the_frame_key_is_an_error():
    document = load_valid()
    document["segments"][0].pop("frame")

    assert "segment 1 is missing the frame key" in messages(document)


def test_an_empty_segment_list_is_an_error():
    document = load_valid()
    document["segments"] = []

    assert "segments" in codes(document)


# -- file-backed rules ------------------------------------------------------

# -- questions_zh, the optional cross-segment question set -----------------

def test_a_document_without_questions_is_still_valid():
    """The field is optional: documents written before it existed still pass."""
    document = load_valid()
    assert "questions_zh" not in document
    assert validate_document(document) == []


def test_a_well_formed_question_set_is_accepted():
    document = load_valid()
    document["questions_zh"] = [
        {"text": "若某個案例具備甲但缺少乙，這篇的結論還成立嗎？", "segments": [1, 2]},
    ]

    assert validate_document(document) == []


def test_a_question_set_that_is_not_a_list_is_an_error():
    document = load_valid()
    document["questions_zh"] = "一題"

    assert "questions_zh" in codes(document)


def test_a_question_without_text_is_an_error():
    document = load_valid()
    document["questions_zh"] = [{"segments": [1]}]

    assert "questions_zh" in codes(document)
    assert "questions_zh[0].text" in messages(document)


def test_a_question_with_no_segments_is_an_error():
    document = load_valid()
    document["questions_zh"] = [{"text": "甲為什麼成立？", "segments": []}]

    assert "questions_zh" in codes(document)
    assert "segments" in messages(document)


def test_a_question_naming_a_segment_that_does_not_exist_is_an_error():
    document = load_valid()
    document["questions_zh"] = [{"text": "甲為什麼成立？", "segments": [99]}]
    report = messages(document)

    assert "questions_zh" in codes(document)
    assert "99" in report and "does not exist" in report


def test_check_json_reports_a_frame_that_is_not_on_disk(tmp_path):
    document = load_valid()
    path = materialize(document, tmp_path)
    (tmp_path / document["segments"][1]["frame"]).unlink()

    report = check_json(path)
    assert [f.code for f in report.findings] == ["frame_missing"]
    assert report.exit_code() == 2


def test_check_json_reports_a_segment_with_no_frame(tmp_path):
    document = load_valid()
    document["segments"][2]["frame"] = None
    document["segments"][2]["frames"] = []
    document["segments"][2]["frame_ocr"] = []
    path = materialize(document, tmp_path)

    report = check_json(path)
    assert [f.code for f in report.findings] == ["frame"]
    assert report.findings[0].message == "segment 3 has no frame"


def test_check_json_refuses_a_frame_path_that_escapes_the_folder(tmp_path):
    document = load_valid()
    document["segments"][0]["frame"] = "../outside.png"
    document["segments"][0]["frames"] = ["../outside.png"]
    document["segments"][0]["frame_ocr"] = []
    path = materialize(document, tmp_path)

    report = check_json(path)
    assert "frame_path" in [f.code for f in report.findings]


def test_check_json_reports_a_utf8_bom(tmp_path):
    document = load_valid()
    path = materialize(document, tmp_path)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    report = check_json(path)
    assert "bom" in [f.code for f in report.findings]
    assert report.exit_code() == 2


def test_check_json_reports_unparseable_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    report = check_json(path)
    assert [f.code for f in report.findings] == ["parse"]


# -- the printed contract ---------------------------------------------------

def test_every_printed_finding_follows_the_stage_line_format(tmp_path):
    document = load_valid()
    document["segments"][2]["index"] = 4
    document["overall_summary_zh"] = "佔" * 99
    path = materialize(document, tmp_path)

    report = check_json(path)
    assert len(report.lines()) == len(report.findings)
    for line in report.lines():
        assert FINDING_LINE.match(line), line


def test_a_finding_without_a_location_falls_back_to_the_target_name():
    report = StageReport("json", "demo.json")
    report.findings.append(Finding("error", "parse", "broken"))

    assert report.lines() == ["error parse demo.json: broken"]


def test_stage_report_exit_codes():
    report = StageReport("json", "demo.json")
    assert report.exit_code() == 0
    report.add("warn", "rule", "here", "a warning")
    assert report.exit_code() == 1
    report.add("error", "rule", "here", "an error")
    assert report.exit_code() == 2
    assert report.summary_line() == "json: 1 errors, 1 warnings"


# -- the command ------------------------------------------------------------

def test_cli_check_json_exits_0_on_a_valid_document(tmp_path):
    path = materialize(load_valid(), tmp_path)

    result = run_cli(["check", "json", str(path)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[json] ok" in result.stdout
    assert "json: 0 errors, 0 warnings" in result.stdout


def test_cli_check_json_exits_2_and_lists_the_segment_indices(tmp_path):
    document = load_valid()
    document["segments"][2]["index"] = 4
    document["segments"][2]["start_sec"] = 130
    document["segments"][2]["start_time"] = "00:02:10"
    path = materialize(document, tmp_path)

    result = run_cli(["check", "json", str(path)])
    assert result.returncode == 2
    assert "segment 3 index is 4, expected 3" in result.stdout
    assert "segment 2 end_sec 120 does not meet segment 3 start_sec 130" in result.stdout
    assert "json: 2 errors, 0 warnings" in result.stdout
    assert "[json] ok" not in result.stdout


def test_cli_check_json_without_a_path_is_a_usage_error():
    result = run_cli(["check", "json"])
    assert result.returncode == 2


def test_cli_check_json_on_a_missing_file_exits_2(tmp_path):
    result = run_cli(["check", "json", str(tmp_path / "nope.json")])
    assert result.returncode == 2


def test_cli_check_without_a_stage_is_a_usage_error():
    result = run_cli(["check"])
    assert result.returncode == 2


def test_cli_check_prints_only_ascii_markers(tmp_path):
    path = materialize(load_valid(), tmp_path)

    result = run_cli(["check", "json", str(path)])
    for character in "→≥✓✗":
        assert character not in result.stdout


def test_legacy_message_is_the_documented_wording():
    assert LEGACY_MESSAGE % "old.json" == "legacy schema detected; run: l2n migrate old.json"

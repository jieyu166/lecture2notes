"""Task 15.6 -- `l2n scaffold` writes the shape; the model writes the meaning.

The previous answer to "where does the canonical JSON come from" was "write it
by hand from the reference". A field run did exactly that and was rejected four
times in a row on the shape alone -- a key name, a missing key, two counts --
before one sentence of the actual lecture had been considered. So the shape is
mechanical now, and these tests say so: every scaffold must satisfy the
structural half of the validator, and must fail nothing but the rules that are
about how much has been written.

`l2n condense` is here too, for the same reason in miniature: the module had
been in the package since the port with no way to run it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_cli

from lecture2notes import exit_codes
from lecture2notes.acceptance.check import check_json
from lecture2notes.cli.main import SUBCOMMANDS, main as cli_entry
from lecture2notes.schema import condense, scaffold
from lecture2notes.schema.model import (
    AI_DRAFT_MARK,
    DRAFT_KEY,
    DRAFT_MESSAGE,
    MIN_SUMMARY_CHARS,
    is_draft,
    validate_document,
)

CUE = "%d\n%02d:%02d:%02d,000 --> %02d:%02d:%02d,000\n%s\n\n"


def write_srt(path: Path, cues: int = 20, step: int = 30) -> Path:
    """A subtitle with *cues* cues of *step* seconds each, so the clock is known."""
    text = ""
    for index in range(cues):
        start, end = index * step, (index + 1) * step
        text += CUE % (
            index + 1,
            start // 3600, start % 3600 // 60, start % 60,
            end // 3600, end % 3600 // 60, end % 60,
            "第 %d 句逐字稿內容，供壓縮與分段測試使用。" % (index + 1),
        )
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture()
def subtitle(tmp_path: Path) -> Path:
    return write_srt(tmp_path / "talk.srt")


# --------------------------------------------------------------------------
# the acceptance case: the scaffold passes the structural validator
# --------------------------------------------------------------------------
def test_the_scaffold_has_no_structural_errors(subtitle: Path):
    scaffold.scaffold_file(subtitle, segments=6)

    document = json.loads(
        (subtitle.with_suffix(".json")).read_text(encoding="utf-8")
    )
    errors = [f for f in validate_document(document) if f.severity == "error"]

    assert errors == [], ["%s %s" % (f.code, f.message) for f in errors]


def test_every_semantic_field_is_a_marked_placeholder(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))

    assert AI_DRAFT_MARK in document["overall_summary_zh"]
    assert all(AI_DRAFT_MARK in item for item in document["takeaways_zh"])
    for segment in document["segments"]:
        assert AI_DRAFT_MARK in segment["title"]
        assert AI_DRAFT_MARK in segment["summary_zh"]
        assert all(AI_DRAFT_MARK in b["text"] for b in segment["bullets_zh"])


def test_the_segments_are_contiguous_and_end_at_the_duration(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=4)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))

    segments = document["segments"]
    assert len(segments) == 4
    assert segments[0]["start_sec"] == 0
    for before, after in zip(segments, segments[1:]):
        assert before["end_sec"] == after["start_sec"]
    assert segments[-1]["end_sec"] == int(document["duration_sec"])


def test_each_segment_points_at_its_slice_of_the_condensed_transcript(
    subtitle: Path,
):
    result = scaffold.scaffold_file(subtitle, segments=3, bucket_sec=60)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))

    references = [s[scaffold.TRANSCRIPT_KEY] for s in document["segments"]]
    assert all(ref.startswith("talk.condensed.txt#L") for ref in references)
    assert references[0] == "talk.condensed.txt#L1-L4"
    assert result.condensed_path.is_file()
    lines = result.condensed_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10  # 600 seconds at one line per minute


def test_the_scaffold_is_deterministic(tmp_path: Path):
    first = scaffold.build_scaffold("talk", 600, segments=5)
    second = scaffold.build_scaffold("talk", 600, segments=5)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_captured_frames_are_merged_in_when_the_manifest_is_there(subtitle: Path):
    folder = subtitle.parent
    (folder / "frames").mkdir()
    (folder / "talk.frames.json").write_text(
        json.dumps([
            {"timestamp_sec": 0.0, "frame": "frames/talk-0000.png", "sha256": "a"},
            {"timestamp_sec": 400.0, "frame": "frames/talk-0640.png", "sha256": "b"},
        ]),
        encoding="utf-8",
    )

    result = scaffold.scaffold_file(subtitle, segments=4)

    assert result.frames == 2
    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    assert document["segments"][0]["frame"] == "frames/talk-0000.png"
    # Nothing changed on screen during segment 2, so it inherits (task 15.4).
    assert document["segments"][1]["frames"] == []
    assert document["segments"][1]["frame"] == "frames/talk-0000.png"


def test_a_subtitle_with_no_cues_is_refused(tmp_path: Path):
    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        scaffold.scaffold_file(empty)


# --------------------------------------------------------------------------
# the draft flag
# --------------------------------------------------------------------------
def test_the_scaffold_declares_itself_a_draft(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))

    assert document[DRAFT_KEY] is True
    assert is_draft(document)


def test_a_draft_reports_the_length_rules_as_warnings(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    assert len(document["overall_summary_zh"]) < MIN_SUMMARY_CHARS

    findings = validate_document(document)

    by_code = {f.code: f.severity for f in findings}
    assert by_code["summary_length"] == "warn"
    assert by_code[DRAFT_KEY] == "warn"
    assert DRAFT_MESSAGE in [f.message for f in findings]


def test_removing_the_flag_restores_the_error(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    document.pop(DRAFT_KEY)

    severities = {f.code: f.severity for f in validate_document(document)}

    assert severities["summary_length"] == "error"
    assert DRAFT_KEY not in severities


def test_a_draft_still_fails_a_structural_rule_at_full_severity(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    document["segments"][1]["index"] = 9

    errors = [f for f in validate_document(document) if f.severity == "error"]

    assert any(f.code == "index" for f in errors), [f.code for f in errors]


def test_draft_must_be_a_boolean(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)
    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    document[DRAFT_KEY] = "true"

    codes = {f.code: f.severity for f in validate_document(document)}

    assert codes[DRAFT_KEY] == "error"


def test_check_json_reports_the_draft_and_exits_1(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)

    report = check_json(result.document_path, require_frames=False)

    assert report.errors == [], [f.message for f in report.errors]
    assert any(f.code == DRAFT_KEY for f in report.warnings)
    assert report.exit_code() == exit_codes.WARN
    assert any('remove "draft"' in line for line in report.lines())


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------
def test_scaffold_is_no_longer_a_stub(subtitle: Path, capsys):
    code = cli_entry(["scaffold", str(subtitle), "--segments", "3"])

    printed = capsys.readouterr().out
    assert code == exit_codes.OK
    assert "not implemented yet" not in printed
    assert subtitle.with_suffix(".json").is_file()


def test_scaffold_help_says_the_semantics_are_the_models_job():
    result = run_cli(["scaffold", "--help"])

    assert result.returncode == 0
    assert "LLM" in result.stdout
    assert "ai-draft" in result.stdout


def test_scaffold_refuses_to_clobber_an_existing_document(subtitle: Path, capsys):
    cli_entry(["scaffold", str(subtitle), "--segments", "3"])
    document = subtitle.with_suffix(".json")
    document.write_text("written by a model\n", encoding="utf-8")
    capsys.readouterr()

    assert cli_entry(["scaffold", str(subtitle)]) == exit_codes.OK
    assert document.read_text(encoding="utf-8") == "written by a model\n"
    assert "skip" in capsys.readouterr().out

    assert cli_entry(["scaffold", "--force", str(subtitle)]) == exit_codes.OK
    assert document.read_text(encoding="utf-8") != "written by a model\n"


def test_scaffold_on_a_missing_file_is_a_usage_error(tmp_path: Path):
    assert cli_entry(["scaffold", str(tmp_path / "nope.srt")]) == exit_codes.ERROR


def test_scaffold_rejects_a_segment_count_below_one(subtitle: Path):
    assert cli_entry(["scaffold", str(subtitle), "--segments", "0"]) == exit_codes.ERROR


def test_condense_writes_one_line_per_window(subtitle: Path, capsys):
    code = cli_entry(["condense", str(subtitle), "--window", "60"])

    printed = capsys.readouterr().out
    out = subtitle.with_name("talk.condensed.txt")
    assert code == exit_codes.OK
    assert out.is_file()
    assert len(out.read_text(encoding="utf-8").splitlines()) == 10
    assert "condense ->" in printed


def test_condense_honours_the_window(subtitle: Path):
    cli_entry(["condense", str(subtitle), "--window", "120"])

    out = subtitle.with_name("talk.condensed.txt")
    assert len(out.read_text(encoding="utf-8").splitlines()) == 5


def test_condense_writes_where_it_is_told(subtitle: Path, tmp_path: Path):
    target = tmp_path / "elsewhere" / "short.txt"

    assert cli_entry(["condense", str(subtitle), "-o", str(target)]) == exit_codes.OK
    assert target.is_file()


def test_condense_rejects_a_non_positive_window(subtitle: Path):
    assert cli_entry(["condense", str(subtitle), "--window", "0"]) == exit_codes.ERROR


def test_condense_on_a_missing_file_is_a_usage_error(tmp_path: Path):
    assert cli_entry(["condense", str(tmp_path / "nope.srt")]) == exit_codes.ERROR


def test_condense_is_a_subcommand_and_the_count_is_17():
    assert "condense" in SUBCOMMANDS
    assert len(SUBCOMMANDS) == 17
    assert condense.DEFAULT_BUCKET_SEC == 60


def test_the_scaffold_output_stays_ascii_in_its_markers(subtitle: Path, capsys):
    cli_entry(["scaffold", str(subtitle), "--segments", "3"])

    printed = capsys.readouterr().out
    assert not any(marker in printed for marker in "→≥✓✗")



def test_the_scaffold_picks_up_a_cached_ocr(subtitle: Path):
    """`l2n run` OCRs before the document exists, so the text sits in the cache.

    The next run skips OCR because the cache is there; unless scaffold folds it
    in, the written document would never carry `frame_ocr`.
    """
    folder = subtitle.parent
    (folder / "talk.frames.json").write_text(
        json.dumps([{"timestamp_sec": 0.0, "frame": "frames/talk-0000.png", "sha256": "a"}]),
        encoding="utf-8",
    )
    (folder / "talk.frames_ocr.json").write_text(
        json.dumps({"frames/talk-0000.png": {"fingerprint": "x", "chars": 5, "text": "SLIDE"}}),
        encoding="utf-8",
    )

    result = scaffold.scaffold_file(subtitle, segments=3)

    # Segments 2 and 3 inherit frame 0 (nothing changed on screen), and so
    # its text, exactly as `l2n ocr <stem>.json` would merge it.
    assert result.ocr_segments == 3
    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    assert document["segments"][0]["frame_ocr"] == [
        {"frame": "frames/talk-0000.png", "text": "SLIDE"}
    ]
    assert document["ocr_meta"]["frames_with_text"] >= 1


def test_the_scaffold_without_an_ocr_cache_reports_none(subtitle: Path):
    result = scaffold.scaffold_file(subtitle, segments=3)

    assert result.ocr_segments is None
    assert "ocr_meta" not in json.loads(result.document_path.read_text(encoding="utf-8"))

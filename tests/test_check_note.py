"""`l2n check note`: rules R1 to R8, one passing and one failing case each.

Every note here starts as a real skeleton render and is then damaged in exactly
one way, so a failure names the rule that broke rather than a fixture that
drifted. The R5 boundary table from the guideline is reproduced literally.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.acceptance import check
from lecture2notes.cli.main import main
from lecture2notes.notes import guideline
from lecture2notes.notes.render import render_skeleton

FIXTURE = Path(__file__).parent / "fixtures" / "note_doc.json"
STEM = "20240115 demo talk"

#: A run of characters long enough to exercise the 40-character threshold.
#: Deliberately not Chinese: the normalisation folds width and case, and a latin
#: run makes the arithmetic in the boundary table obvious.
LONG_RUN = ("kaimuzepholvarnijqetsudbyxcgw" * 10)[:200]


def _codes(report) -> list:
    return [finding.code for finding in report.findings]


@pytest.fixture()
def lecture(tmp_path):
    """A document, its transcript, its frame and a freshly rendered skeleton."""
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    json_path = tmp_path / ("%s.json" % STEM)
    json_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "demo-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    (tmp_path / ("%s.srt" % STEM)).write_text(
        "1\n00:00:00,000 --> 00:00:20,000\n%s\n\n" % LONG_RUN, encoding="utf-8"
    )

    note_path = tmp_path / ("%s.v4.md" % STEM)
    note_path.write_text(
        render_skeleton(document, style="faithful", stem=STEM),
        encoding="utf-8", newline="\n",
    )
    return json_path, note_path


def _edit(note_path: Path, transform):
    lines = note_path.read_text(encoding="utf-8").split("\n")
    note_path.write_text("\n".join(transform(lines)), encoding="utf-8", newline="\n")


def _insert_into_first_section(note_path: Path, text: str):
    def transform(lines):
        at = next(i for i, line in enumerate(lines)
                  if line.startswith("## 1、"))
        return lines[:at + 2] + [text, ""] + lines[at + 2:]
    _edit(note_path, transform)


# -- the clean case ---------------------------------------------------------
def test_a_freshly_rendered_skeleton_passes_every_rule(lecture):
    json_path, note_path = lecture
    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert report.findings == []
    assert report.exit_code() == exit_codes.OK
    assert report.summary_line() == "note: 0 errors, 0 warnings"


# -- R1 ---------------------------------------------------------------------
def test_r1_passes_when_every_section_is_present(lecture):
    json_path, note_path = lecture

    assert "R1" not in _codes(check.check_note_stage(json_path, note_path))


def test_r1_reports_a_missing_section(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [l for l in lines if l != "## 題目"])
    report = check.check_note_stage(json_path, note_path)

    assert "R1" in _codes(report)
    assert report.exit_code() == exit_codes.ERROR


def test_r1_reports_sections_in_the_wrong_order(lecture):
    json_path, note_path = lecture

    def transform(lines):
        evergreen = lines.index("# Evergreen Note")
        summary = lines.index("# Summary")
        lines[evergreen], lines[summary] = lines[summary], lines[evergreen]
        return lines

    _edit(note_path, transform)

    assert "R1" in _codes(check.check_note_stage(json_path, note_path))


# -- R2 ---------------------------------------------------------------------
def test_r2_passes_when_the_embed_resolves(lecture):
    json_path, note_path = lecture

    assert "R2" not in _codes(check.check_note_stage(json_path, note_path))


def test_r2_reports_an_embed_that_does_not_exist(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [
        l.replace("demo-0001.png", "demo-9999.png") for l in lines
    ])
    report = check.check_note_stage(json_path, note_path)

    assert "R2" in _codes(report)
    assert report.exit_code() == exit_codes.ERROR


# -- R3 ---------------------------------------------------------------------
def test_r3_passes_when_the_term_stays_in_references(lecture):
    json_path, note_path = lecture

    assert "R3" not in _codes(check.check_note_stage(json_path, note_path))


def test_r3_reports_an_unverified_term_that_leaked_into_the_body(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(
        note_path, "這裡提到 Placeholderinger's imaginary sign 這個說法。"
    )
    report = check.check_note_stage(json_path, note_path)

    assert "R3" in _codes(report)
    assert report.exit_code() == exit_codes.ERROR


def test_r3_reports_a_term_missing_from_references(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [
        l for l in lines if "Placeholderinger" not in l
    ])

    assert "R3" in _codes(check.check_note_stage(json_path, note_path))


# -- R4 ---------------------------------------------------------------------
def test_r4_passes_on_a_complete_correction_table(lecture):
    json_path, note_path = lecture

    assert "R4" not in _codes(check.check_note_stage(json_path, note_path))


def test_r4_reports_a_row_with_an_empty_cell(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [
        "| 佔位聽錯甲 |  | deterministic |" if l.startswith("| 佔位聽錯甲 |") else l
        for l in lines
    ])
    report = check.check_note_stage(json_path, note_path)

    assert "R4" in _codes(report)
    assert report.exit_code() == exit_codes.ERROR


def test_r4_reports_a_missing_table(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [l for l in lines if not l.startswith("|")])

    assert "R4" in _codes(check.check_note_stage(json_path, note_path))


# -- R5, including the guideline's boundary table ---------------------------
@pytest.mark.parametrize(
    "line, expected",
    [
        (LONG_RUN[:39], False),
        (LONG_RUN[:40], True),
        ("「%s」" % LONG_RUN[:120], False),
        ("> %s" % LONG_RUN[:120], False),
    ],
    ids=["39-unmarked", "40-unmarked", "120-in-quotation-marks", "120-blockquote"],
)
def test_r5_boundary_table(lecture, line, expected):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, line)
    report = check.check_note_stage(json_path, note_path)

    assert ("R5" in _codes(report)) is expected


def test_r5_is_a_warning_under_the_generic_profile(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, LONG_RUN[:60])
    report = check.check_note_stage(json_path, note_path)

    assert [f.severity for f in report.findings if f.code == "R5"] == ["warn"]
    assert report.exit_code() == exit_codes.WARN


def test_r5_names_the_section_and_quotes_the_run(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, LONG_RUN[:60])
    finding = next(f for f in check.check_note_stage(json_path, note_path).findings
                   if f.code == "R5")

    assert finding.location == "1、佔位段落一：名詞與判準"
    assert LONG_RUN[:guideline.FINDING_EXCERPT_CHARS] in finding.message


def test_r5_is_skipped_with_a_warning_when_there_is_no_transcript(lecture):
    json_path, note_path = lecture
    (json_path.parent / ("%s.srt" % STEM)).unlink()
    report = check.check_note_stage(json_path, note_path)

    warning = next(f for f in report.findings if f.code == "R5")
    assert warning.severity == "warn"
    assert "not checked" in warning.message


def test_r5_only_judges_the_part_of_a_line_that_is_not_quoted(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(
        note_path, "「%s」%s" % (LONG_RUN[:120], LONG_RUN[:50])
    )

    assert "R5" in _codes(check.check_note_stage(json_path, note_path))


# -- R6 ---------------------------------------------------------------------
def test_r6_passes_when_every_section_quotes_the_speaker(lecture):
    json_path, note_path = lecture

    assert "R6" not in _codes(
        check.check_note_stage(json_path, note_path, style="faithful")
    )


def test_r6_reports_a_section_with_no_quotation_in_faithful_style(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [
        l for l in lines if not l.startswith("> 「佔位原話二之一」")
    ])
    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "R6" in _codes(report)
    assert report.exit_code() == exit_codes.ERROR


def test_r6_is_skipped_in_concise_style(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [l for l in lines if not l.startswith("> 「")])

    assert "R6" not in _codes(
        check.check_note_stage(json_path, note_path, style="concise")
    )


# -- R7 ---------------------------------------------------------------------
def test_r7_passes_on_a_note_with_no_identifiers(lecture):
    json_path, note_path = lecture

    assert "R7" not in _codes(check.check_note_stage(json_path, note_path))


def test_r7_reports_a_line_matching_a_privacy_pattern(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, "與會醫師的信箱 someone@example.com")
    report = check.check_note_stage(json_path, note_path)

    assert "R7" in _codes(report)
    assert report.exit_code() == exit_codes.ERROR


# -- R8 ---------------------------------------------------------------------
def test_r8_passes_on_a_note_with_no_filler(lecture):
    json_path, note_path = lecture

    assert "R8" not in _codes(check.check_note_stage(json_path, note_path))


def test_r8_reports_placeholder_text(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, "- 影像判準：N/A")
    report = check.check_note_stage(json_path, note_path)

    finding = next(f for f in report.findings if f.code == "R8")
    assert finding.severity == "warn"
    assert "delete the section instead" in finding.message


# -- the report contract ----------------------------------------------------
def test_every_finding_line_carries_severity_rule_and_location(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, LONG_RUN[:60])
    report = check.check_note_stage(json_path, note_path)

    for line in report.lines():
        severity, rule, rest = line.split(" ", 2)
        assert severity in ("error", "warn", "info")
        assert rule.startswith("R")
        assert ": " in rest


def test_the_cli_prints_the_guideline_version_and_the_summary(lecture, capsys):
    json_path, note_path = lecture
    code = main(["check", "note", str(json_path), "--note", str(note_path),
                 "--style", "faithful"])
    out = capsys.readouterr().out

    assert code == exit_codes.OK
    assert guideline.VERSION_LINE in out
    assert "note: 0 errors, 0 warnings" in out


def test_the_cli_defaults_the_note_path_to_the_stem(lecture, capsys):
    json_path, _ = lecture

    assert main(["check", "note", str(json_path)]) == exit_codes.OK
    assert "note: 0 errors, 0 warnings" in capsys.readouterr().out


def test_the_cli_exits_two_on_an_error(lecture, capsys):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [l for l in lines if l != "## 學習驗證"])

    assert main(["check", "note", str(json_path)]) == exit_codes.ERROR
    assert "error R1" in capsys.readouterr().out

"""`l2n check note`: rules R1 to R10, one passing and one failing case each.

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
from lecture2notes.notes import render as render_mod
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


@pytest.fixture()
def expanded(lecture):
    """The same lecture, with each section rewritten enough to defeat R10.

    One appended sentence per section is the cheapest edit that makes the body
    differ from the render output, which is exactly what R10 measures. The
    sentence shares no run with the transcript, so R5 still finds nothing.
    """
    from conftest import expand_note

    json_path, note_path = lecture
    note_path.write_text(
        expand_note(note_path.read_text(encoding="utf-8")),
        encoding="utf-8", newline="\n",
    )
    return json_path, note_path


def _edit(note_path: Path, transform):
    lines = note_path.read_text(encoding="utf-8").split("\n")
    note_path.write_text("\n".join(transform(lines)), encoding="utf-8", newline="\n")


def _replace_question_section(note_path: Path, questions):
    """Swap the whole 題目 body for a handwritten list, R9's only input."""
    def transform(lines):
        at = lines.index("## 題目")
        stop = lines.index("## 學習驗證")
        return lines[:at + 1] + ["", *questions, ""] + lines[stop:]
    _edit(note_path, transform)


def _rewrite_first_section(note_path: Path, replacement):
    """Replace section 1's prose, keeping its heading, timespan, embed and quotes.

    What an expansion is supposed to do: the machine-projected parts stay, the
    rendered sentences go, the writer's own sentences take their place.
    """
    def transform(lines):
        at = next(i for i, line in enumerate(lines)
                  if line.startswith("## 1、"))
        end = next((i for i in range(at + 1, len(lines))
                    if lines[i].startswith("#")), len(lines))
        kept = [
            line for line in lines[at:end]
            if not line.strip()
            or line.startswith("#")
            or line.startswith("![[")
            or line.startswith("> ")
            or check.TIMESPAN_LINE.match(line.strip())
        ]
        return lines[:at] + kept + list(replacement) + [""] + lines[end:]
    _edit(note_path, transform)


def _insert_into_first_section(note_path: Path, text: str):
    def transform(lines):
        at = next(i for i, line in enumerate(lines)
                  if line.startswith("## 1、"))
        return lines[:at + 2] + [text, ""] + lines[at + 2:]
    _edit(note_path, transform)


# -- the clean case ---------------------------------------------------------
def test_a_freshly_rendered_skeleton_breaks_only_r10(lecture):
    """A skeleton satisfies every rule about shape, because its shape is right.

    That was the problem (task 15.10): R1 to R9 all pass on a file nobody has
    written. The skeleton is now exactly one finding per section -- R10 -- and
    nothing else, which is what "the shape is fine, the writing has not
    happened" should look like.
    """
    json_path, note_path = lecture
    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert set(_codes(report)) == {"R10"}
    assert all(f.severity == "warn" for f in report.findings)
    assert report.exit_code() == exit_codes.WARN


def test_an_expanded_note_passes_every_rule(expanded):
    json_path, note_path = expanded
    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert report.findings == [], report.lines()
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


# -- R9 ---------------------------------------------------------------------
def test_r9_passes_on_a_freshly_rendered_question_section(lecture):
    json_path, note_path = lecture

    assert "R9" not in _codes(check.check_note_stage(json_path, note_path))


def test_r9_reports_a_question_section_with_fewer_than_three_questions(lecture):
    json_path, note_path = lecture
    _replace_question_section(note_path, [
        "1. 關於甲的理由是什麼？",
        "2. %s 若某個案例缺少乙，結論還成立嗎？" % guideline.INFERENCE_MARK,
    ])
    report = check.check_note_stage(json_path, note_path)

    finding = next(f for f in report.findings if f.code == "R9")
    assert finding.severity == "warn"
    assert "at least %d" % guideline.MIN_QUESTIONS in finding.message


def test_r9_reports_a_question_section_with_no_inference_question(lecture):
    json_path, note_path = lecture
    _replace_question_section(note_path, [
        "1. 甲的定義是什麼？",
        "2. 乙的定義是什麼？",
        "3. 丙的定義是什麼？",
    ])
    report = check.check_note_stage(json_path, note_path)

    findings = [f for f in report.findings if f.code == "R9"]
    assert [f.severity for f in findings] == ["warn"]
    assert guideline.INFERENCE_MARK in findings[0].message


def test_r9_is_silent_when_the_section_is_missing_because_that_is_r1(lecture):
    json_path, note_path = lecture

    def transform(lines):
        at = lines.index("## 題目")
        stop = lines.index("## 學習驗證")
        return lines[:at] + lines[stop:]

    _edit(note_path, transform)
    report = check.check_note_stage(json_path, note_path)

    assert "R1" in _codes(report)
    assert "R9" not in _codes(report)


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


def test_the_cli_prints_the_guideline_version_and_the_summary(expanded, capsys):
    json_path, note_path = expanded
    code = main(["check", "note", str(json_path), "--note", str(note_path),
                 "--style", "faithful"])
    out = capsys.readouterr().out

    assert code == exit_codes.OK
    assert guideline.VERSION_LINE in out
    assert "note: 0 errors, 0 warnings" in out


def test_the_cli_defaults_the_note_path_to_the_stem(expanded, capsys):
    json_path, _ = expanded

    assert main(["check", "note", str(json_path)]) == exit_codes.OK
    assert "note: 0 errors, 0 warnings" in capsys.readouterr().out


def test_the_cli_exits_two_on_an_error(lecture, capsys):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [l for l in lines if l != "## 學習驗證"])

    assert main(["check", "note", str(json_path)]) == exit_codes.ERROR
    assert "error R1" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 15.7 -- R5 covers the prose a reader trusts, not only the Note body
# --------------------------------------------------------------------------
# The guideline's own appendix used to end with "R5 only looks at lines inside
# the Note section, so the equally bad bullet in Summary cannot be caught by a
# machine". A field run then shipped a note whose five Summary bullets were all
# verbatim transcript, with a clean check report.

def _insert_after_heading(note_path: Path, heading: str, text: str):
    def transform(lines):
        at = lines.index(heading)
        return lines[:at + 1] + ["", text] + lines[at + 1:]
    _edit(note_path, transform)


@pytest.mark.parametrize(
    "heading, location",
    [
        ("# Evergreen Note", "Evergreen Note"),
        ("# Summary", "Summary"),
        ("## 題目", "題目"),
    ],
)
def test_r5_reports_a_pasted_run_outside_the_note_body(lecture, heading, location):
    json_path, note_path = lecture
    _insert_after_heading(note_path, heading, LONG_RUN[:40])

    report = check.check_note_stage(json_path, note_path)

    hits = [f for f in report.findings if f.code == "R5"]
    assert [f.location for f in hits] == [location]
    assert hits[0].severity == "warn"


def test_r5_still_exempts_a_marked_quotation_outside_the_note_body(lecture):
    json_path, note_path = lecture
    _insert_after_heading(note_path, "# Summary", "「%s」" % LONG_RUN[:120])

    assert "R5" not in _codes(check.check_note_stage(json_path, note_path))


def test_r5_still_exempts_a_blockquote_outside_the_note_body(lecture):
    json_path, note_path = lecture
    _insert_after_heading(note_path, "# Summary", "> %s" % LONG_RUN[:120])

    assert "R5" not in _codes(check.check_note_stage(json_path, note_path))


def test_the_summary_region_stops_before_the_speaker_outline(lecture):
    json_path, note_path = lecture
    lines = check.note_lines(note_path.read_text(encoding="utf-8"))
    regions = check.note_regions(lines)

    start, end = regions["Summary"]
    assert lines[end].strip() == "## 講者骨架"
    assert all("講者骨架" not in lines[p] for p in range(start, end))


def test_the_skeleton_summary_quotes_takeaways_and_is_not_a_paste(tmp_path):
    """The skeleton's Summary is `takeaways_zh`, which nobody transcribed.

    R5 growing past the Note body must not turn every unexpanded skeleton into
    a finding: the takeaways are the model's own synthesis, so the transcript
    cannot contain them. This fixture makes the transcript long enough for R5
    to run at all, and asserts the skeleton still comes back clean.
    """
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

    summary_at = note_path.read_text(encoding="utf-8").index("# Summary")
    assert document["takeaways_zh"][0] in note_path.read_text(
        encoding="utf-8"
    )[summary_at:]

    report = check.check_note_stage(json_path, note_path, style="faithful")
    assert [f for f in report.findings if f.code == "R5"] == []


def test_the_guideline_records_the_wider_scope():
    from lecture2notes.notes.guideline import find_guideline_doc

    text = find_guideline_doc().read_text(encoding="utf-8")
    assert guideline.VERSION_LINE in text
    assert "R5 只看 Note 章節內的行" not in text


# --------------------------------------------------------------------------
# 15.8 -- count the markers nobody removed
# --------------------------------------------------------------------------
# The skeleton marks the two placeholders a model still has to write: the
# speaker outline rows and the drafted questions. Nothing counted them, so a
# note that had been read and one that had not looked identical in the report
# -- and the field run shipped the one that had not. (Until 16.4 the
# remember-three candidates carried a marker too, which made the count
# unreachable: those are the reader's, and no model finishes them.)

def test_a_freshly_rendered_skeleton_reports_its_ai_draft_count(lecture):
    json_path, note_path = lecture
    report = check.check_note_stage(json_path, note_path, style="faithful")

    text = note_path.read_text(encoding="utf-8")
    assert report.metrics["ai_draft_remaining"] == text.count(render_mod.AI_DRAFT_MARK)
    assert report.metrics["ai_draft_remaining"] > 0


def test_removing_every_marker_drives_the_count_to_zero(lecture):
    json_path, note_path = lecture
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace(render_mod.AI_DRAFT_MARK, ""),
        encoding="utf-8", newline="\n",
    )

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert report.metrics["ai_draft_remaining"] == 0


def test_the_count_is_printed_after_the_summary_and_changes_no_exit_code(
    expanded, capsys
):
    json_path, note_path = expanded

    code = main(["check", "note", str(json_path), "--note", str(note_path),
                 "--style", "faithful"])

    printed = [line for line in capsys.readouterr().out.split("\n") if line.strip()]
    at = printed.index("note: 0 errors, 0 warnings")
    after = printed[at + 1:]
    assert any(line.startswith("note: ai_draft_remaining=") for line in after)
    assert all(line.startswith("note: ") for line in after)
    assert code == exit_codes.OK


def test_the_count_line_is_ascii(lecture, capsys):
    json_path, note_path = lecture
    main(["check", "note", str(json_path), "--note", str(note_path)])

    printed = capsys.readouterr().out
    assert not any(marker in printed for marker in "\u2192\u2265\u2713\u2717")


# --------------------------------------------------------------------------
# 15.11 -- `check note` reads the style out of the note it is checking
# --------------------------------------------------------------------------
def test_check_note_uses_the_style_the_note_declares(lecture):
    """Rendered faithful, checked with no flag: R6 must still run.

    `check note` used to fall back to the profile, which is concise by
    default, so the rule faithful exists to add was silently skipped on every
    faithful note whose checker was invoked without `--style`.
    """
    json_path, note_path = lecture
    assert render_mod.read_style_marker(
        note_path.read_text(encoding="utf-8")
    ) == "faithful"
    _edit(note_path, lambda lines: [l for l in lines if not l.startswith("> 「")])

    report = check.check_note_stage(json_path, note_path)

    assert "R6" in _codes(report)


def test_an_explicit_style_still_wins_and_says_so(lecture, capsys):
    json_path, note_path = lecture

    report = check.check_note_stage(json_path, note_path, style="concise")

    mismatch = [f for f in report.findings if f.code == "style"]
    assert len(mismatch) == 1
    assert mismatch[0].severity == "warn"
    assert "style mismatch" in mismatch[0].message
    assert "faithful" in mismatch[0].message and "concise" in mismatch[0].message


def test_no_mismatch_when_the_flag_agrees_with_the_marker(lecture):
    json_path, note_path = lecture

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "style" not in _codes(report)


def test_a_note_without_a_marker_falls_back_to_the_profile(lecture):
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [l for l in lines if "l2n:style=" not in l])
    _edit(note_path, lambda lines: [l for l in lines if not l.startswith("> 「")])

    report = check.check_note_stage(json_path, note_path)

    # generic's default is concise, under which R6 does not run at all.
    assert "R6" not in _codes(report)
    assert "style" not in _codes(report)


# --------------------------------------------------------------------------
# 15.10 -- passing the check must not be achievable by doing nothing
# --------------------------------------------------------------------------
# A model with a fresh context, handed only the skill, filled in the speaker
# outline and the question answers and left every segment untouched. Under the
# 1.1 rules `check note` returned 0 errors, 0 warnings.

def test_r10_reports_every_segment_of_an_untouched_skeleton(lecture):
    json_path, note_path = lecture
    report = check.check_note_stage(json_path, note_path, style="faithful")

    hits = [f for f in report.findings if f.code == "R10"]
    segments = [f for f in hits if f.location.startswith("segment ")]
    assert [f.location for f in segments] == ["segment 1", "segment 2"]
    # Identity is still the top of the scale, and the message says so.
    assert all(
        "100% of the body is render output" in f.message for f in hits
    )


def test_r10_reports_an_evergreen_still_holding_the_rendered_line(lecture):
    json_path, note_path = lecture
    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "Evergreen Note" in [
        f.location for f in report.findings if f.code == "R10"
    ]


def test_r10_is_silent_on_a_section_somebody_rewrote(lecture):
    """The rendered sentences are gone, replaced by the writer's own."""
    json_path, note_path = lecture
    _rewrite_first_section(note_path, [
        "講者先交代為什麼這個判準值得建立，再拿兩個反例把邊界推出來。",
        "他的理由是：只看結果會把運氣算進能力，所以要先固定條件再比較。",
    ])

    report = check.check_note_stage(json_path, note_path, style="faithful")

    locations = [f.location for f in report.findings if f.code == "R10"]
    assert "segment 1" not in locations
    assert "segment 2" in locations


# --------------------------------------------------------------------------
# 16.2 -- residual ratio, because "identical" was one sentence away from free
# --------------------------------------------------------------------------
# The blind review of four expansions found a note that had added a real
# paragraph of its own reasoning to the front of every section and left the
# rendered sentences sitting underneath it, untouched. Nothing was identical,
# R10 said nothing, and the reader was told the same thing twice per section.

def test_r10_reports_a_section_that_only_gained_a_sentence(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, "推理鏈：先說清楚前提")

    report = check.check_note_stage(json_path, note_path, style="faithful")
    hits = [f for f in report.findings
            if f.code == "R10" and f.location == "segment 1"]

    assert hits, "adding a sentence in front of the skeleton is not expansion"
    assert "% of the body is render output" in hits[0].message
    assert "100%" not in hits[0].message


def test_the_r10_threshold_comes_from_the_profile(lecture, monkeypatch):
    """`check.r10_ratio` in outputs.toml moves the line, nothing else does."""
    json_path, note_path = lecture
    _insert_into_first_section(note_path, "推理鏈：先說清楚前提")

    monkeypatch.setattr(check.loader, "r10_ratio", lambda profile=None: 0.95)
    lenient = check.check_note_stage(json_path, note_path, style="faithful")
    assert "segment 1" not in [
        f.location for f in lenient.findings if f.code == "R10"
    ]

    monkeypatch.setattr(check.loader, "r10_ratio", lambda profile=None: 0.10)
    strict = check.check_note_stage(json_path, note_path, style="faithful")
    assert "segment 1" in [
        f.location for f in strict.findings if f.code == "R10"
    ]


def test_a_kept_quotation_is_not_counted_as_residue(lecture):
    """2.1 requires the speaker's words be kept verbatim; R10 must not punish it."""
    json_path, note_path = lecture
    _rewrite_first_section(note_path, [
        "講者先交代為什麼這個判準值得建立，再拿兩個反例把邊界推出來。",
        "他的理由是：只看結果會把運氣算進能力，所以要先固定條件再比較。",
    ])

    report = check.check_note_stage(json_path, note_path, style="faithful")

    # The three rendered blockquotes are still in the file untouched.
    assert note_path.read_text(encoding="utf-8").count("> 「佔位原話一") == 3
    assert "segment 1" not in [
        f.location for f in report.findings if f.code == "R10"
    ]


def test_r10_ignores_whitespace_only_edits(lecture):
    """Reflowing a paragraph is not writing it."""
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [
        line + "   " if line.startswith("## 1、") else line for line in lines
    ])

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "segment 1" in [f.location for f in report.findings if f.code == "R10"]


def test_the_unexpanded_ratio_is_measured_and_printed(lecture, capsys):
    json_path, note_path = lecture

    code = main(["check", "note", str(json_path), "--note", str(note_path),
                 "--style", "faithful"])

    printed = capsys.readouterr().out
    assert "note: unexpanded_segments=2/2" in printed
    assert code == exit_codes.WARN


def test_the_ratio_falls_as_sections_get_written(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, "本段已經改寫過，這一句不在骨架裡。")

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert report.metrics["unexpanded_segments"] == "1/2"


def test_an_expanded_note_reports_zero_unexpanded_segments(expanded):
    json_path, note_path = expanded
    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert report.metrics["unexpanded_segments"] == "0/2"
    assert "R10" not in _codes(report)


def test_r10_is_a_warning_the_profile_can_promote(lecture, monkeypatch):
    json_path, note_path = lecture
    monkeypatch.setattr(
        check.loader, "unexpanded_severity", lambda profile=None: "error"
    )

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert all(f.severity == "error" for f in report.findings if f.code == "R10")
    assert report.exit_code() == exit_codes.ERROR


def test_r10_compares_against_the_style_actually_in_force(lecture):
    """Concise drops the quote bullets, so the skeletons differ by style.

    Comparing a faithful note against a concise render would report every
    section as expanded, which is the failure mode this rule exists to close.
    """
    json_path, note_path = lecture

    faithful = check.check_note_stage(json_path, note_path, style="faithful")
    assert "R10" in _codes(faithful)


def test_the_audit_report_carries_the_unexpanded_ratio(lecture, tmp_path):
    from lecture2notes.acceptance import audit

    json_path, note_path = lecture
    report = check.check_note_stage(json_path, note_path, style="faithful")
    payload = audit.stage_report_payload([report], stem="x")

    assert payload["stages"]["note"]["unexpanded_segments"] == "2/2"


# --------------------------------------------------------------------------
# 15.12(b) -- [推論] has one spelling, and R9 says which
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        "1. （推論）若某個案例具備 A 但缺少 B，結論還成立嗎？",
        "1. [推理] 若某個案例具備 A 但缺少 B，結論還成立嗎？",
        "1. 【推論】若某個案例具備 A 但缺少 B，結論還成立嗎？",
        "1. 若某個案例具備 A 但缺少 B，結論還成立嗎？[推論]",
    ],
    ids=["full-width-parens", "wrong-word", "full-width-brackets", "at-the-end"],
)
def test_r9_accepts_only_the_documented_inference_spelling(lecture, question):
    json_path, note_path = lecture
    _replace_question_section(note_path, [
        question,
        "2. 不看筆記說出第一段的推理鏈。",
        "3. 不看筆記說出第二段的結論。",
    ])

    report = check.check_note_stage(json_path, note_path)

    assert "R9" in _codes(report)


def test_r9_accepts_the_marker_at_the_start_of_the_question(lecture):
    json_path, note_path = lecture
    _replace_question_section(note_path, [
        "1. [推論] 若某個案例具備 A 但缺少 B，結論還成立嗎？",
        "2. 不看筆記說出第一段的推理鏈。",
        "3. 不看筆記說出第二段的結論。",
    ])

    assert "R9" not in _codes(check.check_note_stage(json_path, note_path))


def test_r9_shows_the_format_it_wants(lecture):
    json_path, note_path = lecture
    _replace_question_section(note_path, [
        "1. 甲是什麼？", "2. 乙是什麼？", "3. 丙是什麼？",
    ])

    finding = next(f for f in check.check_note_stage(json_path, note_path).findings
                   if f.code == "R9")

    assert guideline.INFERENCE_MARK in finding.message
    assert "Write it as:" in finding.message


def test_question_text_strips_the_number_prefix():
    for line in ("1. [推論] x", "2) [推論] x", " 10、[推論] x"):
        assert check.question_text(line).startswith(guideline.INFERENCE_MARK)


def test_the_guideline_documents_the_single_spelling():
    text = guideline.find_guideline_doc().read_text(encoding="utf-8")

    assert "`[推論]` 的寫法只有一種" in text
    assert "1. [推論] 若某個案例具備 A 但缺少 B" in text


# --------------------------------------------------------------------------
# 16.3 -- R11: the same sentence in two sections
# --------------------------------------------------------------------------
# The blind review found one note carrying the same three sentences in
# Summary, in the Note body and again under "我應該記住的 3 件事". Every rule
# before this one reads a single section, so nothing saw it.

#: Long enough to clear DUPLICATE_SENTENCE_CHARS, and it appears nowhere in
#: the fixture's transcript, so R5 has nothing to say about it either.
REPEATED = (
    "廠商自己給的省成本百分比與第三方統計的市場規模，不能放在同一個證據等級上使用"
)


def _append_to_section(note_path: Path, heading: str, text: str):
    def transform(lines):
        at = lines.index(heading)
        return lines[:at + 1] + ["", text, ""] + lines[at + 1:]
    _edit(note_path, transform)


def test_r11_reports_a_sentence_repeated_in_two_sections(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, REPEATED)
    _append_to_section(note_path, "## 學習驗證", "> " + REPEATED)

    report = check.check_note_stage(json_path, note_path, style="faithful")
    hits = [f for f in report.findings if f.code == "R11"]

    assert len(hits) == 1, [f.message for f in report.findings]
    assert hits[0].severity == "warn"
    assert "Note (layer 1-3)" in hits[0].location
    assert "學習驗證" in hits[0].location
    assert REPEATED[:10] in hits[0].message


def test_r11_says_nothing_when_each_sentence_is_written_once(lecture):
    json_path, note_path = lecture
    _insert_into_first_section(note_path, REPEATED)

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "R11" not in _codes(report)


def test_r11_counts_a_quotation_repeated_in_another_section(lecture):
    """Unlike R10, a kept quotation is a sentence like any other here.

    2.1 asks for the speaker's words once. Saying them again three sections
    later is repetition, not compliance.
    """
    json_path, note_path = lecture
    _insert_into_first_section(note_path, "「%s」" % REPEATED)
    _append_to_section(note_path, "## 題目", "> 「%s」" % REPEATED)

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "R11" in _codes(report)


def test_r11_ignores_the_outline_and_the_references(lecture):
    """The outline is a machine projection; References repeats terms by design."""
    json_path, note_path = lecture
    _append_to_section(note_path, "## 講者骨架", REPEATED)
    _append_to_section(note_path, "### References", REPEATED)

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "R11" not in _codes(report)


def test_r11_forgives_the_one_pair_the_guideline_mandates(lecture):
    """7.1 makes Summary quote takeaways, and render projects Evergreen from them."""
    json_path, note_path = lecture
    _append_to_section(note_path, "# Evergreen Note", "**「%s」**" % REPEATED)
    _append_to_section(note_path, "# Summary", "- " + REPEATED)

    report = check.check_note_stage(json_path, note_path, style="faithful")
    assert "R11" not in _codes(report)

    # A third section is no longer the renderer's doing.
    _append_to_section(note_path, "## 學習驗證", "> " + REPEATED)
    again = check.check_note_stage(json_path, note_path, style="faithful")
    assert "R11" in _codes(again)


def test_r11_leaves_a_short_repeated_line_alone(lecture):
    """Two identical short lines are a coincidence, not a copied sentence."""
    json_path, note_path = lecture
    short = "這一點很重要"
    _insert_into_first_section(note_path, short)
    _append_to_section(note_path, "## 學習驗證", "> " + short)

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert "R11" not in _codes(report)


# --------------------------------------------------------------------------
# 16.4 -- `ai_draft_remaining=0` has to be reachable by the model alone
# --------------------------------------------------------------------------
def test_only_the_two_model_placeholders_carry_a_marker(lecture):
    """The reader's slots and the remember-three candidates carry none."""
    json_path, note_path = lecture
    lines = note_path.read_text(encoding="utf-8").split("\n")

    marked = [
        position for position, line in enumerate(lines)
        if render_mod.AI_DRAFT_MARK in line
    ]
    assert len(marked) == 2

    def owning_heading(position):
        return next(lines[i] for i in range(position, -1, -1)
                    if lines[i].startswith("#"))

    assert {owning_heading(p) for p in marked} == {"## 講者骨架", "## 題目"}

    verification_at = lines.index("## 學習驗證")
    assert render_mod.AI_DRAFT_MARK not in "\n".join(lines[verification_at:])
    assert render_mod.CANDIDATE_CALLOUT in "\n".join(lines[verification_at:])
    for slot in render_mod.SUMMARY_SLOTS:
        assert slot in "\n".join(lines)


def test_writing_the_two_placeholders_drives_the_count_to_zero(lecture):
    """Without the model touching the reader's slots or the candidates."""
    json_path, note_path = lecture
    _edit(note_path, lambda lines: [
        line for line in lines if render_mod.AI_DRAFT_MARK not in line
    ])

    report = check.check_note_stage(json_path, note_path, style="faithful")

    assert report.metrics["ai_draft_remaining"] == 0
    text = note_path.read_text(encoding="utf-8")
    assert render_mod.CANDIDATE_CALLOUT in text
    assert render_mod.SUMMARY_SLOTS[0] in text

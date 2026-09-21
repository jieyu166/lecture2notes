"""Hallucination loops: the ASR failure that produces a perfectly valid SRT.

A looping model emits the same phrase for every cue until the audio ends, with
correct numbering and correct timecodes. Nothing downstream can tell that apart
from speech, so `check transcribe` has to.

All SRT input here is generated in the test, so the occurrence counts the
assertions depend on are true by construction and no real transcript is stored
in the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.acceptance import check as check_mod
from lecture2notes.cli.main import main as run_main
from lecture2notes.engines import hallucination
from lecture2notes.engines.base import Cue, write_srt

LOOP_TEXT = "OK"
TAIL = 71


def cue(index: int, text: str, length: float = 2.0) -> Cue:
    start = index * length
    return Cue(start=start, end=start + length, text=text)


def normal_cues(count: int = 12):
    return [cue(i, "佔位文字第 %d 句。" % (i + 1)) for i in range(count)]


def looping_transcript(tmp_path: Path, lead: int = 12, tail: int = TAIL) -> Path:
    """`lead` ordinary cues followed by `tail` identical ones."""
    cues = normal_cues(lead) + [cue(lead + i, LOOP_TEXT) for i in range(tail)]
    return write_srt(tmp_path / "lecture.srt", cues)


# -- detection -------------------------------------------------------------
def test_a_run_of_seventy_one_identical_cues_is_one_loop():
    cues = normal_cues(12) + [cue(12 + i, LOOP_TEXT) for i in range(TAIL)]
    loops = hallucination.find_loops(cues)
    assert len(loops) == 1
    assert (loops[0].first_cue, loops[0].last_cue, loops[0].count) == (13, 83, TAIL)
    assert loops[0].text == LOOP_TEXT


def test_the_message_is_exactly_what_the_contract_says():
    loop = hallucination.Loop(first_cue=13, last_cue=83, count=TAIL, text=LOOP_TEXT)
    assert loop.message() == "hallucination loop cues 13..83 (71 identical)"


def test_the_threshold_is_thirty_and_is_inclusive():
    assert hallucination.THRESHOLD == 30
    exactly = [cue(i, "x") for i in range(30)]
    assert len(hallucination.find_loops(exactly)) == 1
    one_short = [cue(i, "x") for i in range(29)]
    assert hallucination.find_loops(one_short) == []


def test_ordinary_repetition_is_not_a_loop():
    """Real speech repeats a phrase; it does not repeat it thirty times."""
    cues = normal_cues(5) + [cue(5, "對"), cue(6, "對"), cue(7, "對")]
    assert hallucination.find_loops(cues) == []


def test_two_separate_loops_are_reported_separately():
    cues = (
        [cue(i, "A") for i in range(35)]
        + normal_cues(3)
        + [cue(100 + i, "B") for i in range(31)]
    )
    loops = hallucination.find_loops(cues)
    assert [(l.count, l.text) for l in loops] == [(35, "A"), (31, "B")]


def test_an_empty_transcript_has_no_loops():
    assert hallucination.find_loops([]) == []


def test_a_loop_at_the_very_end_is_found():
    """The tail is where loops actually happen, and the easiest case to miss."""
    cues = normal_cues(2) + [cue(2 + i, LOOP_TEXT) for i in range(TAIL)]
    loops = hallucination.find_loops(cues)
    assert loops[0].last_cue == len(cues)


def test_detection_works_straight_off_a_file(tmp_path):
    path = looping_transcript(tmp_path)
    loops = hallucination.find_loops_in_file(path)
    assert loops[0].count == TAIL
    assert hallucination.messages(loops) == [loops[0].message()]


# -- the acceptance report -------------------------------------------------
def test_check_transcribe_reports_the_loop_as_a_warning(tmp_path):
    path = looping_transcript(tmp_path)
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert report.errors == []
    assert "hallucination loop cues 13..83 (71 identical)" in report.warns


def test_a_clean_transcript_produces_no_warning(tmp_path):
    path = write_srt(tmp_path / "clean.srt", normal_cues(40))
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert report.errors == []
    assert report.warns == []


def test_check_transcribe_catches_an_inverted_cue(tmp_path):
    path = write_srt(tmp_path / "broken.srt",
                     [Cue(5.0, 5.0, "zero length"), Cue(6.0, 8.0, "fine")])
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert any("ends at or before it starts" in e for e in report.errors)


def test_check_transcribe_catches_overlapping_cues(tmp_path):
    path = tmp_path / "overlap.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:10,000\n甲\n\n"
        "2\n00:00:05,000 --> 00:00:12,000\n乙\n",
        encoding="utf-8", newline="\n",
    )
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert any("before the previous cue ended" in e for e in report.errors)


def test_touching_cues_are_not_an_overlap(tmp_path):
    path = write_srt(tmp_path / "touching.srt",
                     [Cue(0.0, 2.0, "甲"), Cue(2.0, 4.0, "乙")])
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert report.errors == []


def test_check_transcribe_catches_a_bom(tmp_path):
    path = tmp_path / "bom.srt"
    path.write_bytes(
        b"\xef\xbb\xbf1\n00:00:00,000 --> 00:00:02,000\n\xe7\x94\xb2\n"
    )
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert any("BOM" in e for e in report.errors)


def test_check_transcribe_catches_broken_numbering(tmp_path):
    path = tmp_path / "numbering.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n甲\n\n"
        "3\n00:00:02,000 --> 00:00:04,000\n乙\n",
        encoding="utf-8", newline="\n",
    )
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert any("numbering" in e for e in report.errors)


def test_a_lone_sidecar_without_its_raw_transcript_is_an_error(tmp_path):
    path = write_srt(tmp_path / "lecture.srt", normal_cues(5))
    (tmp_path / "lecture.corrections.json").write_text("{}", encoding="utf-8")
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert any("only one is present" in e for e in report.errors)


def test_both_audit_files_present_is_clean(tmp_path):
    path = write_srt(tmp_path / "lecture.srt", normal_cues(5))
    write_srt(tmp_path / "lecture.raw.srt", normal_cues(5))
    (tmp_path / "lecture.corrections.json").write_text("{}", encoding="utf-8")
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert report.errors == []
    assert any("raw transcript kept" in i for i in report.infos)


def test_neither_audit_file_is_not_a_warning(tmp_path):
    """A transcript produced without a correction table is a normal outcome."""
    path = write_srt(tmp_path / "lecture.srt", normal_cues(5))
    report = check_mod.Report(path.name)
    check_mod.check_transcribe(path, report)
    assert report.warns == []


# -- through the CLI -------------------------------------------------------
def test_cli_check_transcribe_prints_the_loop_and_exits_1(tmp_path, capsys):
    path = looping_transcript(tmp_path)
    code = run_main(["check", "transcribe", str(path)])
    captured = capsys.readouterr().out
    assert "hallucination loop cues 13..83 (71 identical)" in captured
    assert code == exit_codes.WARN


def test_cli_check_transcribe_prints_the_stage_summary_line(tmp_path, capsys):
    path = looping_transcript(tmp_path)
    run_main(["check", "transcribe", str(path)])
    assert "transcribe: 0 errors, 1 warnings" in capsys.readouterr().out


def test_cli_check_transcribe_exits_0_on_a_clean_file(tmp_path, capsys):
    path = write_srt(tmp_path / "clean.srt", normal_cues(40))
    assert run_main(["check", "transcribe", str(path)]) == exit_codes.OK
    assert "transcribe: 0 errors, 0 warnings" in capsys.readouterr().out


def test_cli_check_transcribe_exits_2_on_a_structural_error(tmp_path):
    path = write_srt(tmp_path / "broken.srt", [Cue(5.0, 5.0, "zero")])
    assert run_main(["check", "transcribe", str(path)]) == exit_codes.ERROR


def test_cli_check_reports_a_missing_file(tmp_path, capsys):
    code = run_main(["check", "transcribe", str(tmp_path / "nope.srt")])
    assert code == exit_codes.ERROR
    assert "no such file" in capsys.readouterr().out


def test_cli_check_rejects_an_unknown_stage(capsys):
    assert run_main(["check", "nonsense", "x.srt"]) == exit_codes.ERROR
    assert "unknown check stage" in capsys.readouterr().out


def test_cli_check_output_is_ascii_marked(tmp_path, capsys):
    """Message text may be Chinese; the markers never are."""
    path = looping_transcript(tmp_path)
    run_main(["check", "transcribe", str(path)])
    captured = capsys.readouterr().out
    for banned in ("→", "≥", "✓", "✗"):
        assert banned not in captured

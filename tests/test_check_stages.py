"""The four stage checks share one output contract, and a clean set passes all.

Two claims are pinned here that no per-stage test can make on its own.

The first is the contract itself: transcribe, frames, json and note all print one
line per finding as ``<severity> <rule-or-field> <location>: <message>``, then
``<stage>: N errors, M warnings``, and exit 0 / 1 / 2. The point of a shared
format is that a caller -- an agent, a CI step, `l2n run` -- can parse any stage
without knowing which one it is looking at, so the format is asserted by
splitting the line back into its four fields rather than by matching a string.

The second is the clean case. Every other acceptance test damages an input and
watches a rule fire; nothing establishes that a pipeline which did everything
right is reported as clean. Until the licensed fixture recording is in the repo
that set is synthesised (see ``synthetic_lecture`` in conftest), which is enough
for the contract: the checks read files, and a synthetic file is still a file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import SYNTHETIC_STEM, build_synthetic_lecture

from lecture2notes import exit_codes
from lecture2notes.acceptance import check
from lecture2notes.cli.main import main as cli_entry

pytest.importorskip("PIL", reason="Pillow paints the synthetic frames")

#: transcribe and note take a file that is not the stem's canonical JSON, so the
#: stages are listed with the suffix each one is pointed at.
STAGE_SUFFIX = {
    "transcribe": ".srt",
    "frames": ".frames.json",
    "json": ".json",
    "note": ".json",
}


def _fields(line: str):
    """Split a finding line back into (severity, rule, location, message)."""
    severity, rule, rest = line.split(" ", 2)
    location, message = rest.split(": ", 1)
    return severity, rule, location, message


def _report_for(stage: str, lecture) -> check.StageReport:
    if stage == "transcribe":
        return check.check_transcribe_stage(lecture.subtitle)
    if stage == "frames":
        return check.check_frames(lecture.manifest)
    if stage == "json":
        return check.check_json(lecture.document)
    return check.check_note_stage(
        lecture.document, lecture.note, transcript=lecture.subtitle, style="faithful"
    )


# -- the clean case ---------------------------------------------------------
@pytest.mark.parametrize("stage", sorted(STAGE_SUFFIX))
def test_every_stage_is_clean_on_a_complete_output_set(stage, synthetic_lecture):
    report = _report_for(stage, synthetic_lecture)

    assert report.findings == [], report.lines()
    assert report.exit_code() == exit_codes.OK
    assert report.summary_line() == "%s: 0 errors, 0 warnings" % stage


@pytest.mark.parametrize("stage", sorted(STAGE_SUFFIX))
def test_every_stage_exits_zero_through_the_cli(stage, synthetic_lecture, capsys):
    target = synthetic_lecture.path(STAGE_SUFFIX[stage])

    assert cli_entry(["check", stage, str(target)]) == exit_codes.OK
    printed = [l for l in capsys.readouterr().out.split("\n") if l.strip()]
    assert "%s: 0 errors, 0 warnings" % stage in printed


# -- the shared output contract ---------------------------------------------
@pytest.mark.parametrize("stage", sorted(STAGE_SUFFIX))
def test_the_summary_line_closes_the_findings_every_stage_prints(
    stage, synthetic_lecture, capsys
):
    """The tally is the last judgement; only measurements may follow it.

    Task 15.8 added `note: ai_draft_remaining=N` after the tally. It is a count
    the stage took, not a rule it applied, so it must not be mistaken for one:
    it comes after the summary and never moves the exit code.
    """
    report = _report_for(stage, synthetic_lecture)
    report.emit()

    printed = [line for line in capsys.readouterr().out.split("\n") if line.strip()]
    at = printed.index(report.summary_line())
    assert printed[at + 1:] == report.metric_lines()


def test_every_finding_carries_all_four_contract_fields(synthetic_lecture):
    """One damaged input per stage, checked only for the shape of its output."""
    lecture = synthetic_lecture
    lecture.subtitle.write_text(
        "1\n00:00:10,000 --> 00:00:05,000\nbackwards\n\n", encoding="utf-8"
    )
    (lecture.frames_dir / ("%s-0001.png" % lecture.stem)).write_bytes(b"different")
    document = json.loads(lecture.document.read_text(encoding="utf-8"))
    document["segments"][0]["frame"] = None
    document["segments"][0]["frames"] = []
    document["segments"][0]["frame_ocr"] = []
    lecture.document.write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    lecture.note.write_text(
        lecture.note.read_text(encoding="utf-8").replace("# Summary", "# Sumary"),
        encoding="utf-8",
    )

    for stage in sorted(STAGE_SUFFIX):
        report = _report_for(stage, lecture)
        assert report.findings, stage
        assert report.exit_code() == exit_codes.ERROR, stage
        for line in report.lines():
            severity, rule, location, message = _fields(line)
            assert severity in ("error", "warn", "info"), line
            assert rule and " " not in rule, line
            assert location, line
            assert message, line


@pytest.mark.parametrize(
    "severity, expected",
    [("error", exit_codes.ERROR), ("warn", exit_codes.WARN), (None, exit_codes.OK)],
)
def test_exit_code_follows_the_worst_severity(severity, expected):
    report = check.StageReport("json", "demo.json")
    if severity is not None:
        report.add(severity, "rule", "demo.json", "message")

    assert report.exit_code() == expected


# -- the frames scenario from the specification -----------------------------
def test_a_tampered_frame_reports_a_hash_drift_and_exits_two(
    synthetic_lecture, capsys
):
    frame = synthetic_lecture.frames_dir / ("%s-0002.png" % synthetic_lecture.stem)
    frame.write_bytes(frame.read_bytes() + b"tampered")

    code = cli_entry(["check", "frames", str(synthetic_lecture.manifest)])
    printed = capsys.readouterr().out

    assert code == exit_codes.ERROR
    finding = next(
        line for line in printed.split("\n") if line.startswith("error sha256 ")
    )
    severity, rule, location, message = _fields(finding)
    assert (severity, rule) == ("error", "sha256")
    assert location == "frames/%s-0002.png" % synthetic_lecture.stem
    words = message.split()
    assert words[0] == "manifest" and words[2] == "actual"
    assert words[1] != words[3]
    assert printed.strip().endswith("frames: 1 errors, 0 warnings")


# -- transcribe, through the stage report -----------------------------------
def test_an_inverted_cue_is_an_error_and_a_lone_sidecar_is_an_error(tmp_path):
    subtitle = tmp_path / "x.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nfirst\n\n"
        "2\n00:00:09,000 --> 00:00:08,000\nsecond\n\n",
        encoding="utf-8",
    )
    (tmp_path / "x.corrections.json").write_text("[]", encoding="utf-8")

    report = check.check_transcribe_stage(subtitle)
    codes = [finding.code for finding in report.findings]

    assert "cue_time" in codes
    assert "corrections" in codes
    assert report.exit_code() == exit_codes.ERROR


def test_a_hallucination_loop_is_a_warning_not_an_error(tmp_path):
    cues = []
    for number in range(1, 41):
        start, end = (number - 1) * 2.0, number * 2.0 - 0.1
        cues.append(
            "%d\n00:00:%06.3f --> 00:00:%06.3f\n重複的一句話\n"
            % (number, start, end)
        )
    subtitle = tmp_path / "loop.srt"
    subtitle.write_text("\n".join(cues).replace(".", ","), encoding="utf-8")

    report = check.check_transcribe_stage(subtitle)

    assert report.errors == []
    assert [f.code for f in report.warnings] == ["loop"]
    assert report.exit_code() == exit_codes.WARN


def test_a_lecture_built_without_a_video_still_checks_clean(tmp_path):
    """The three file-only stages never need ffmpeg, so they never skip for it."""
    lecture = build_synthetic_lecture(tmp_path, video=False)

    assert lecture.video is None
    assert lecture.stem == SYNTHETIC_STEM
    for stage in ("frames", "json", "note"):
        assert _report_for(stage, lecture).findings == [], stage

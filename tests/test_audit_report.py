"""`l2n check --all <stem> --report <path>`: the machine-readable audit report.

The console output of a stage check is for a person reading one run. The report
is for the other consumer -- an agent, a CI step, the next session -- and the
thing that makes it trustworthy is that it is assembled from the same
StageReport objects the console printed. There is no second pass over the
lecture, so the file and the screen cannot disagree.

What is pinned here: the report parses as JSON, ``summary.errors`` equals the
number of rows whose severity is error, every row carries the four contract
fields, the stamp names the guideline edition that produced it, and the exit
code is the maximum of the stages' own codes rather than the last one's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.acceptance import audit, check
from lecture2notes.cli.main import main as cli_entry
from lecture2notes.notes import guideline

pytest.importorskip("PIL", reason="Pillow paints the synthetic frames")


def _report(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(payload: dict) -> list:
    return [row for stage in payload["stages"].values() for row in stage["findings"]]


# -- the clean case ---------------------------------------------------------
def test_a_clean_lecture_reports_every_stage_with_no_findings(
    synthetic_lecture, tmp_path
):
    destination = tmp_path / "out" / "audit.json"

    code = cli_entry([
        "check", "--all", str(synthetic_lecture.document),
        "--report", str(destination), "--style", "faithful",
    ])
    payload = _report(destination)

    assert code == exit_codes.OK
    assert sorted(payload["stages"]) == ["frames", "json", "note", "transcribe"]
    assert payload["summary"] == {"errors": 0, "warnings": 0}
    assert payload["guideline_version"] == guideline.GUIDELINE_VERSION
    assert payload["stem"] == synthetic_lecture.stem


def test_the_stem_may_be_given_bare_without_any_suffix(synthetic_lecture, tmp_path):
    bare = synthetic_lecture.root / synthetic_lecture.stem
    destination = tmp_path / "audit.json"

    code = cli_entry([
        "check", "--all", str(bare), "--report", str(destination),
        "--style", "faithful",
    ])

    assert code == exit_codes.OK
    assert sorted(_report(destination)["stages"]) == [
        "frames", "json", "note", "transcribe"
    ]


# -- the scenario: the summary describes the findings -----------------------
def test_summary_errors_equals_the_number_of_error_findings(
    synthetic_lecture, tmp_path
):
    frame = synthetic_lecture.frames_dir / ("%s-0001.png" % synthetic_lecture.stem)
    frame.write_bytes(frame.read_bytes() + b"drift")
    synthetic_lecture.subtitle.write_text(
        "1\n00:00:10,000 --> 00:00:05,000\ninverted\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\noverlapping\n\n",
        encoding="utf-8",
    )
    destination = tmp_path / "audit.json"

    code = cli_entry([
        "check", "--all", str(synthetic_lecture.document),
        "--report", str(destination),
    ])
    payload = _report(destination)
    rows = _rows(payload)

    assert code == exit_codes.ERROR
    assert payload["summary"]["errors"] == sum(
        1 for row in rows if row["severity"] == "error"
    )
    assert payload["summary"]["warnings"] == sum(
        1 for row in rows if row["severity"] in audit.WARNING_SEVERITIES
    )
    assert payload["summary"]["errors"] >= 2


def test_every_row_carries_the_four_contract_fields(synthetic_lecture, tmp_path):
    synthetic_lecture.note.write_text("nothing here", encoding="utf-8")
    destination = tmp_path / "audit.json"

    cli_entry([
        "check", "--all", str(synthetic_lecture.document), "--report", str(destination)
    ])
    rows = _rows(_report(destination))

    assert rows
    for row in rows:
        assert tuple(row) == audit.FINDING_KEYS
        assert row["severity"] in ("error", "warn", "warning", "info")
        assert row["rule"] and row["location"] and row["message"]


# -- exit code is the maximum, not the last ---------------------------------
def test_the_exit_code_is_the_maximum_of_the_stage_codes():
    clean = check.StageReport("json", "a.json")
    warned = check.StageReport("transcribe", "a.srt")
    warned.add("warn", "loop", "a.srt", "a loop")
    failed = check.StageReport("frames", "a.frames.json")
    failed.add("error", "sha256", "frames/a.png", "manifest ab.. actual cd..")

    assert check.worst_exit_code([clean]) == exit_codes.OK
    assert check.worst_exit_code([clean, warned]) == exit_codes.WARN
    assert check.worst_exit_code([failed, warned, clean]) == exit_codes.ERROR
    assert check.worst_exit_code([warned, failed]) == exit_codes.ERROR
    assert check.worst_exit_code([]) == exit_codes.OK


def test_a_warning_only_lecture_exits_one_and_still_writes_a_report(
    synthetic_lecture, tmp_path
):
    """A hallucination loop is the one finding that warns without failing."""
    lines = []
    for number in range(1, 41):
        start, end = (number - 1) * 2, (number - 1) * 2 + 1
        lines.append(
            "%d\n00:00:%02d,000 --> 00:00:%02d,000\n重複的一句話\n"
            % (number, start, end)
        )
    synthetic_lecture.subtitle.write_text("\n".join(lines), encoding="utf-8")
    destination = tmp_path / "audit.json"

    code = cli_entry([
        "check", "--all", str(synthetic_lecture.document),
        "--report", str(destination), "--style", "faithful",
    ])
    payload = _report(destination)

    assert code == exit_codes.WARN
    assert payload["summary"]["errors"] == 0
    assert payload["summary"]["warnings"] >= 1
    assert [row["rule"] for row in payload["stages"]["transcribe"]["findings"]] == [
        "loop"
    ]


# -- applicability ----------------------------------------------------------
def test_only_the_stages_with_outputs_are_reported(synthetic_lecture, tmp_path):
    synthetic_lecture.note.unlink()
    synthetic_lecture.manifest.unlink()
    destination = tmp_path / "audit.json"

    cli_entry([
        "check", "--all", str(synthetic_lecture.document), "--report", str(destination)
    ])

    assert sorted(_report(destination)["stages"]) == ["json", "transcribe"]


def test_a_stem_with_no_outputs_at_all_is_an_error(tmp_path, capsys):
    code = cli_entry(["check", "--all", str(tmp_path / "nothing here")])

    assert code == exit_codes.ERROR
    assert "nothing here" in capsys.readouterr().out


def test_the_report_is_utf8_without_a_bom(synthetic_lecture, tmp_path):
    destination = tmp_path / "audit.json"
    cli_entry([
        "check", "--all", str(synthetic_lecture.document), "--report", str(destination)
    ])

    raw = destination.read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8"))["stages"]


def test_the_report_is_not_written_when_no_report_path_is_given(
    synthetic_lecture, tmp_path
):
    before = {p.name for p in synthetic_lecture.root.iterdir()}

    cli_entry(["check", "--all", str(synthetic_lecture.document), "--style", "faithful"])

    assert {p.name for p in synthetic_lecture.root.iterdir()} == before


# -- the derivative comparison: the note is judged on its spine, not its bytes
def test_an_expanded_note_is_not_a_derivative_mismatch(synthetic_lecture):
    """The note is expanded in place, so a byte comparison could never pass."""
    document = json.loads(synthetic_lecture.document.read_text(encoding="utf-8"))
    text = synthetic_lecture.note.read_text(encoding="utf-8")
    synthetic_lecture.note.write_text(
        text.replace(
            "### References", "這段是模型擴寫出來的內文。\n\n### References", 1
        ),
        encoding="utf-8",
    )

    assert audit.compare_derived(document, note_path=synthetic_lecture.note) == []


def test_a_note_that_lost_a_segment_is_a_structural_error(synthetic_lecture):
    document = json.loads(synthetic_lecture.document.read_text(encoding="utf-8"))
    lines = synthetic_lecture.note.read_text(encoding="utf-8").split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("## 3、"))
    stop = next(
        (i for i in range(at + 1, len(lines)) if lines[i].startswith("### ")),
        len(lines),
    )
    synthetic_lecture.note.write_text(
        "\n".join(lines[:at] + lines[stop:]), encoding="utf-8"
    )

    findings = audit.compare_derived(document, note_path=synthetic_lecture.note)

    assert [finding.code for finding in findings] == ["note_segment_mismatch"]


def test_a_missing_note_is_still_reported(synthetic_lecture):
    document = json.loads(synthetic_lecture.document.read_text(encoding="utf-8"))
    synthetic_lecture.note.unlink()

    findings = audit.compare_derived(document, note_path=synthetic_lecture.note)

    assert [finding.code for finding in findings] == ["note_missing"]


def test_the_spine_is_what_the_skeleton_writes(synthetic_lecture):
    document = json.loads(synthetic_lecture.document.read_text(encoding="utf-8"))
    text = synthetic_lecture.note.read_text(encoding="utf-8")

    for heading in audit.note_spine(document):
        assert "## %s" % heading in text

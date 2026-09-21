"""The guideline's appendix example still fails, and fails for the stated reason.

An example of a bad note is only worth shipping if it is still bad. This runs
the real check over the real files and asserts the two rules the appendix
claims they break, so a change to the rules that quietly lets the example
through fails here rather than in someone's vault.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.acceptance import check
from lecture2notes.cli.main import main

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = Path(__file__).parent / "fixtures" / "appendix_bad_note"
SAMPLE_JSON = SAMPLE_DIR / "finance-lecture.json"
SAMPLE_NOTE = SAMPLE_DIR / "finance-lecture.v4.md"
DOC = REPO_ROOT / "docs" / "note-writing-guideline.md"


@pytest.fixture(scope="module")
def report():
    return check.check_note_stage(SAMPLE_JSON, SAMPLE_NOTE)


def test_the_sample_reports_at_least_one_r5(report):
    findings = [f for f in report.findings if f.code == "R5"]

    assert len(findings) >= 1
    assert "1、利率與債券價格" == findings[0].location


def test_the_sample_reports_at_least_one_r4(report):
    findings = [f for f in report.findings if f.code == "R4"]

    assert len(findings) >= 1
    assert findings[0].severity == "error"
    assert "correct" in findings[0].message


def test_the_sample_does_not_pass(report, capsys):
    assert report.exit_code() == exit_codes.ERROR
    assert main(["check", "note", str(SAMPLE_JSON), "--note", str(SAMPLE_NOTE)]) == 2
    assert "note: " in capsys.readouterr().out


def test_the_appendix_excerpt_matches_the_runnable_sample():
    """The document quotes the fixture; neither may drift from the other."""
    doc = DOC.read_text(encoding="utf-8")
    note = SAMPLE_NOTE.read_text(encoding="utf-8")
    excerpt = doc.split("節錄：\n\n```markdown\n", 1)[1].split("\n```", 1)[0]

    for line in excerpt.split("\n"):
        if line.strip():
            assert line in note, line


def test_the_appendix_names_the_runnable_location():
    doc = DOC.read_text(encoding="utf-8")

    assert "tests/fixtures/appendix_bad_note/" in doc
    assert "tests/test_guideline_appendix.py" in doc

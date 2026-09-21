"""`l2n render --expand-prompt`: everything an agent needs, in one block.

The bundle has to work when it is pasted into a model that has never seen this
package, so the assertions are about self-containment: which version of the
rules, which three files to read, and the command that decides whether the
result is acceptable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes import exit_codes
from lecture2notes.cli.main import main
from lecture2notes.notes import guideline

FIXTURE = Path(__file__).parent / "fixtures" / "note_doc.json"


@pytest.fixture()
def lecture(tmp_path):
    """A document on disk named the way the pipeline names one."""
    stem = "20240115 demo talk"
    path = tmp_path / ("%s.json" % stem)
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_the_bundle_names_the_guideline_version(lecture, capsys):
    assert main(["render", "--expand-prompt", str(lecture)]) == exit_codes.OK
    out = capsys.readouterr().out

    assert guideline.VERSION_LINE in out
    assert guideline.VERSION_LINE == "guideline_version: %s" % guideline.GUIDELINE_VERSION


def test_the_bundle_names_the_note_the_transcript_and_the_json(lecture, capsys):
    main(["render", "--expand-prompt", str(lecture)])
    out = capsys.readouterr().out

    for suffix in (".v4.md", ".srt", ".json"):
        expected = str(lecture.with_name(lecture.stem + suffix))
        assert expected in out, suffix


def test_the_bundle_ends_with_the_acceptance_command(lecture, capsys):
    main(["render", "--expand-prompt", str(lecture)])
    out = capsys.readouterr().out

    assert "l2n check note" in out
    assert str(lecture) in out


def test_the_bundle_quotes_the_rules_when_the_document_is_on_disk(lecture, capsys):
    main(["render", "--expand-prompt", str(lecture)])
    out = capsys.readouterr().out

    assert guideline.GUIDELINE_DOC in out or guideline.MUST_FOLLOW_HEADING in out


def test_the_bundle_carries_the_effective_style(lecture, capsys):
    main(["render", "--expand-prompt", "--style", "faithful", str(lecture)])
    out = capsys.readouterr().out

    assert "style: faithful" in out


def test_expand_prompt_writes_nothing(lecture, capsys):
    before = sorted(p.name for p in lecture.parent.iterdir())
    main(["render", "--expand-prompt", str(lecture)])
    capsys.readouterr()

    assert sorted(p.name for p in lecture.parent.iterdir()) == before


def test_render_writes_the_skeleton_and_then_refuses_to_clobber_it(lecture, capsys):
    assert main(["render", str(lecture)]) == exit_codes.OK
    note = lecture.with_name(lecture.stem + ".v4.md")
    note.write_text("expanded by hand\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["render", str(lecture)]) == exit_codes.OK
    assert note.read_text(encoding="utf-8") == "expanded by hand\n"
    assert "skip" in capsys.readouterr().out

    assert main(["render", "--force", str(lecture)]) == exit_codes.OK
    assert note.read_text(encoding="utf-8") != "expanded by hand\n"

"""The guideline is a shipped, versioned document, and the code agrees with it.

The version string exists in two places by necessity: the document a person
reads, and the constant the check prints. These tests are what keeps the two
from drifting apart, which is the only failure mode that would make a report's
version line a lie.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture2notes.notes import guideline

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "note-writing-guideline.md"
SKILL_REFERENCE = REPO_ROOT / "skill" / "references" / "note-writing.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOC.is_file(), "docs/note-writing-guideline.md is missing"
    return DOC.read_text(encoding="utf-8")


def test_the_document_is_where_the_code_looks_for_it():
    assert guideline.find_guideline_doc() == DOC
    assert guideline.GUIDELINE_DOC == "docs/note-writing-guideline.md"


def test_the_document_carries_the_same_version_as_the_code(doc_text):
    assert guideline.VERSION_LINE in doc_text
    assert guideline.VERSION_LINE == "guideline_version: %s" % guideline.GUIDELINE_VERSION


def test_the_document_has_both_parts(doc_text):
    must_follow = doc_text.index("## %s" % guideline.MUST_FOLLOW_HEADING)
    machine = doc_text.index("## %s" % guideline.MACHINE_CHECKABLE_HEADING)

    assert must_follow < machine


def test_the_must_follow_part_can_be_extracted(doc_text):
    part = guideline.must_follow_text()

    assert part.startswith("## %s" % guideline.MUST_FOLLOW_HEADING)
    assert guideline.MACHINE_CHECKABLE_HEADING not in part
    assert "來源優先序" in part
    assert "不做的開關" in part


def test_every_rule_id_is_documented(doc_text):
    machine = guideline.section_text(doc_text, guideline.MACHINE_CHECKABLE_HEADING)

    for rule in guideline.RULES:
        assert "**%s**" % rule in machine, rule


def test_the_document_states_the_inference_question_pattern(doc_text):
    """R9 counts the marker; the wording that makes it worth counting is here."""
    part = guideline.must_follow_text()

    assert guideline.INFERENCE_MARK in part
    assert "若病人有 A 但沒有 B" in part
    assert guideline.ANSWER_CALLOUT in part
    assert "Karpicke" in part and "Dunlosky" in part


def test_the_document_carries_the_ramp_self_check(doc_text):
    """A made-up ramp sentence is worse than a note that admits it has none."""
    part = guideline.must_follow_text()

    assert "坡道句自檢" in part
    assert "> [!warning] 降級：本筆記只是資訊重排" in part
    assert "這篇為什麼值得開" in part and "前人卡在哪" in part


def test_the_document_describes_the_declaration_and_the_two_slots(doc_text):
    part = guideline.must_follow_text()

    assert "> 本筆記為模型產出的初稿，未經本人確認。" in part
    assert "開這篇之前我卡在___" in part
    assert "這篇沒回答到的是___" in part
    assert "> [!note]- 模型候選（未經本人確認）" in part


def test_the_r5_boundary_table_is_in_the_document(doc_text):
    machine = guideline.section_text(doc_text, guideline.MACHINE_CHECKABLE_HEADING)

    assert "| 39 |" in machine
    assert "| 40 |" in machine
    assert machine.count("| 120 |") == 2


def test_the_document_names_every_mandatory_section(doc_text):
    for heading, _ in guideline.MANDATORY_SECTIONS:
        assert "`%s`" % heading in doc_text, heading


def test_the_skill_reference_points_at_the_document():
    assert SKILL_REFERENCE.is_file(), "skill/references/note-writing.md is missing"
    text = SKILL_REFERENCE.read_text(encoding="utf-8")

    assert "docs/note-writing-guideline.md" in text
    assert guideline.VERSION_LINE in text
    assert "l2n check note" in text


def test_the_document_uses_no_console_unsafe_markers(doc_text):
    """The console rule is about `_out`, but these characters are banned here too."""
    for character in ("→", "≥", "✓", "✗"):
        assert character not in doc_text, repr(character)

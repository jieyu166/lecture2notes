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
REFERENCE_SEGMENTATION = REPO_ROOT / "skill" / "references" / "segmentation.md"


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


def test_the_document_states_the_speaker_outline_rules(doc_text):
    """The outline is worth having only if it records actions, not topics."""
    part = guideline.must_follow_text()

    assert "講者骨架" in part
    assert "用一個誤診案例開場" in part
    assert "講了椎間盤分級" in part
    assert "坡道" in part


def test_the_document_states_summary_quotes_the_takeaways(doc_text):
    """Rewriting the takeaways makes the note and the JSON disagree silently."""
    part = guideline.must_follow_text()

    assert "直接引用 `takeaways_zh`" in part
    assert "跨版本對照" in part
    assert "閱片連結" in part


def test_the_document_lists_the_six_kinds_of_speaker_aside(doc_text):
    part = guideline.must_follow_text()

    for kind in ("界定概念", "後果嚴重", "與認知相反", "遞進缺環", "轉折", "多面向印證"):
        assert kind in part, kind
    assert "只在 JSON 留一個時間碼" in part


def test_the_document_states_the_four_arc_check(doc_text):
    part = guideline.must_follow_text()

    assert "四段弧" in part
    assert "questions_zh" in part
    for arc in ("坡道", "背景", "正文", "昇華"):
        assert arc in part, arc


def test_the_document_carries_the_third_step_zero_layer(doc_text):
    """Step 0 asks what shape the output takes, not only whether to write."""
    part = guideline.must_follow_text()

    assert "產物型態" in part
    assert "以題材為單位，不以專科為單位" in part
    assert "不做完整筆記" in part


def test_the_document_requires_a_course_summary(doc_text):
    part = guideline.must_follow_text()

    assert "_course.json" in part
    assert "本系列在回答的問題" in part
    assert "最該先看的一場" in part
    assert "本系列各場主題獨立，無共同主線" in part


def test_the_document_names_every_mandatory_section(doc_text):
    for heading, _ in guideline.MANDATORY_SECTIONS:
        assert "`%s`" % heading in doc_text, heading


def test_the_skill_reference_points_at_the_document():
    assert SKILL_REFERENCE.is_file(), "skill/references/note-writing.md is missing"
    text = SKILL_REFERENCE.read_text(encoding="utf-8")

    assert "docs/note-writing-guideline.md" in text
    assert guideline.VERSION_LINE in text
    assert "l2n check note" in text


def test_the_segmentation_reference_asks_for_questions_and_the_four_arcs():
    """The LLM writes the questions; the reference is where it is told to."""
    assert REFERENCE_SEGMENTATION.is_file()
    text = REFERENCE_SEGMENTATION.read_text(encoding="utf-8")

    assert "questions_zh" in text
    assert "四段弧" in text
    for arc in ("坡道", "背景", "正文", "昇華"):
        assert arc in text, arc
    assert "缺哪一段就照實寫" in text


def test_the_document_uses_no_console_unsafe_markers(doc_text):
    """The console rule is about `_out`, but these characters are banned here too."""
    for character in ("\u2192", "\u2265", "\u2713", "\u2717", "\u2212"):
        assert character not in doc_text, repr(character)


def test_the_part_the_bundle_prints_survives_a_cp950_console():
    """`render --expand-prompt` prints the whole must-follow part; a character
    cp950 cannot encode turns into `?` on the console that runs the bundle."""
    guideline.must_follow_text().encode("cp950")


# --------------------------------------------------------------------------
# 16.4 -- the contradiction Codex found, and the distinction the review needed
# --------------------------------------------------------------------------
def test_the_document_is_at_the_version_that_resolved_the_contradiction(doc_text):
    assert guideline.GUIDELINE_VERSION == "1.4"
    assert doc_text.count(guideline.VERSION_LINE) >= 2  # header and appendix B


def test_zero_six_no_longer_says_the_marker_stays_in_the_file(doc_text):
    """Codex read 0.6 and the definition of done and found them contradictory.

    0.6 said the `<!-- ai-draft -->` marker survives in the file; the checklist
    said the note is finished when `ai_draft_remaining=0`. Whichever one an
    expansion followed, it was disobeying the other.
    """
    section = guideline.section_text(doc_text, "「我應該記住的 3 件事」")

    assert "那個標記會留在檔案裡" not in section
    assert "連同標記一起刪掉" in section
    assert "讀者的東西不帶標記" in section
    assert "ai_draft_remaining=0" in section


def test_the_document_defines_evergreen_as_a_one_sentence_summary(doc_text):
    """1.4 (B9): Evergreen sums up the lecture; own synthesis is marked.

    Two independent expansions left Evergreen as one week's number, and the
    1.3 answer -- "a transferable principle" -- pushed writers into saying one
    sentence more than the speaker did.
    """
    section = guideline.section_text(doc_text, "一句話總結本課程內容")

    assert section, "the guideline has no 4.1"
    assert "先寫 Evergreen" in doc_text
    assert "反例（半句逐字稿）" in section
    assert "正例（一句話總結）" in section
    assert "[本筆記整理]" in section
    assert "反例（單一數據點）" not in doc_text
    # The one machine-checkable claim the section makes about itself.
    assert "R10 通過不代表 Evergreen 合格" in section


def test_the_examples_are_invented_rather_than_quoted(doc_text):
    """The worked examples must not reproduce anybody's actual note."""
    section = guideline.section_text(doc_text, "一句話總結本課程內容")

    assert "為說明用途自擬" in section


def test_every_documented_rule_id_is_one_the_code_reports(doc_text):
    machine = guideline.section_text(doc_text, guideline.MACHINE_CHECKABLE_HEADING)

    assert "**R14**" in machine
    assert "**R15**" in machine
    assert "**R16**" not in machine, "the table has a rule the code does not have"


# --------------------------------------------------------------------------
# 1.4 -- the rules ported from the textbook-note workflow
# --------------------------------------------------------------------------
def test_the_version_information_says_the_two_rule_sets_are_kept_apart(doc_text):
    head = doc_text.split("## %s" % guideline.MUST_FOLLOW_HEADING, 1)[0]

    assert "與 obsidian-v4-cleanup task5 規則各自維護；本版對應 task5 2026-10 版" in head


def test_the_correctness_list_carries_every_review_error(doc_text):
    section = guideline.section_text(doc_text, "2.5 正確性清單")

    assert section, "the guideline has no 2.5"
    for name, _what in guideline.REVIEW_ERRORS:
        assert name in section, name
    assert section.count("| ") > 13 * 5  # thirteen rows, five cells each
    for header in ("反例", "正例"):
        assert header in section


def test_the_exceptions_to_no_fabrication_are_written_down(doc_text):
    part = guideline.must_follow_text()

    assert guideline.GLOSSARY_HEADING in part
    assert guideline.NOT_COVERED_NOTE in part
    for mark in guideline.BODY_INFERENCE_MARKS:
        assert mark in part, mark
    assert "沒有記號的推論就是編造" in part
    assert "讀者層級不放寬" in part


def test_section_one_decides_the_main_statement_not_a_winner(doc_text):
    part = guideline.must_follow_text()

    assert "排在前面的贏" not in part
    assert "決定本文的主說法" in part
    assert "講者口述如此，講義為 X" in part


def test_the_reading_section_is_kept_only_for_image_lectures(doc_text):
    part = guideline.must_follow_text()

    assert "只在講座含影像判讀時保留，否則刪掉整節" in part


def test_the_two_failure_patterns_1_4_adds_are_in_appendix_a(doc_text):
    # Not section_text: the appendix quotes whole notes in code fences, and
    # their `# Evergreen Note` lines would end the section early.
    appendix = doc_text.split("## 附錄 A", 1)[1].split("## 附錄 B", 1)[0]

    assert "樣態二：說過頭" in appendix
    assert "樣態三：夾帶因果" in appendix


def test_the_machine_table_names_scope_and_message_for_each_new_rule(doc_text):
    machine = guideline.section_text(doc_text, guideline.MACHINE_CHECKABLE_HEADING)

    for rule in ("R12", "R13", "R14"):
        row = next(line for line in machine.splitlines()
                   if line.startswith("| **%s**" % rule))
        assert "warning" in row
        assert "範圍" in row or "不存在" in row, rule
    assert "frame_ocr_email" in machine and "forbidden_term" in machine
    assert "l2n review-prompt" in machine

"""The deterministic skeleton: fixed sections, fixed order, identical bytes.

Everything here is decided by the document and the profile's templates, so the
assertions are about structure rather than wording: which sections exist, in
what order, what a section contains, and whether two runs agree byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from lecture2notes.notes import guideline
from lecture2notes.notes import render as render_mod
from lecture2notes.notes.render import (
    CANDIDATE_CALLOUT,
    DRAFT_DECLARATION,
    OUTLINE_SEPARATOR,
    SUMMARY_SLOTS,
    UNVERIFIED_MARK,
    render_skeleton,
    segment_shares,
    write_skeleton,
)

FIXTURE = Path(__file__).parent / "fixtures" / "note_doc.json"

#: A numbered question line, the same shape rule R9 counts.
QUESTION_LINE = re.compile(r"^\s*\d+[.)、]\s*\S")


@pytest.fixture()
def document():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _section(text: str, heading: str) -> str:
    """The body under one heading, up to the next heading of any level."""
    lines = text.split("\n")
    start = lines.index(heading)
    depth = len(heading) - len(heading.lstrip("#"))
    for position in range(start + 1, len(lines)):
        line = lines[position]
        if line.startswith("#") and len(line) - len(line.lstrip("#")) <= depth:
            return "\n".join(lines[start:position])
    return "\n".join(lines[start:])


def _outline_rows(text):
    """Only the data rows of the speaker outline, not its reading notes."""
    body = _section(text, "## 講者骨架")
    return [line for line in body.splitlines() if OUTLINE_SEPARATOR in line]


def _three_segments(document):
    """The fixture's two segments plus a third, with 1:2:3 running times."""
    first = dict(document["segments"][0])
    first.update({"start_sec": 0, "end_sec": 60})
    second = dict(document["segments"][1])
    second.update({"start_sec": 60, "end_sec": 180})
    third = dict(second)
    third.update({"title": "佔位段落三：把兩端接回去", "start_sec": 180, "end_sec": 360})
    return [first, second, third]


def test_same_document_renders_to_the_same_bytes(document):
    first = render_skeleton(document, stem="20240115 demo talk")
    second = render_skeleton(document, stem="20240115 demo talk")

    assert hashlib.sha256(first.encode("utf-8")).hexdigest() == hashlib.sha256(
        second.encode("utf-8")
    ).hexdigest()


def test_mandatory_sections_appear_in_the_mandated_order(document):
    text = render_skeleton(document)
    positions = [text.index("\n%s\n" % heading)
                 for heading in guideline.mandatory_headings()]

    assert positions == sorted(positions)


def test_frontmatter_comes_first(document):
    text = render_skeleton(document)

    assert text.startswith("---\n")
    assert text.split("---\n")[1].startswith('title: "')


def test_the_first_body_line_declares_the_note_is_a_model_draft(document):
    """A drafted list reads exactly like a written one; the file says which.

    Task 15.11 put the machine-readable style marker immediately after the
    frontmatter. It is an HTML comment, so it renders to nothing: the first
    line a reader sees is still the declaration, which is what 14.2 is about.
    """
    text = render_skeleton(document)
    body = text.split("---", 2)[2].lstrip()
    visible = [
        line for line in body.splitlines()
        if line.strip() and not line.strip().startswith("<!--")
    ]

    assert visible[0] == DRAFT_DECLARATION
    assert "> 本筆記為模型產出的初稿，未經本人確認。" in text


def test_summary_carries_two_sentence_openings_for_the_reader(document):
    body = _section(render_skeleton(document), "# Summary")

    for slot in SUMMARY_SLOTS:
        assert slot in body
    assert "開這篇之前我卡在___" in body
    assert "這篇沒回答到的是___" in body
    assert "我為什麼開這篇" not in body


def test_the_three_candidates_arrive_folded(document):
    body = _section(render_skeleton(document), "## 學習驗證")

    assert CANDIDATE_CALLOUT in body
    assert "> [!note]- 模型候選（未經本人確認）" in body
    for number in (1, 2, 3):
        assert "> %d. " % number in body


def test_evergreen_is_the_first_takeaway_in_bold_quotation_marks(document):
    body = _section(render_skeleton(document), "# Evergreen Note")

    assert "**「佔位核心觀念：兩個名詞常被混用，判準其實不同」**" in body


def test_summary_lists_every_takeaway(document):
    body = _section(render_skeleton(document), "# Summary")

    for item in document["takeaways_zh"]:
        assert "- %s" % item in body


def test_the_speaker_outline_has_one_row_per_segment(document):
    """Three segments, three rows: the outline is projected, not summarised."""
    document["segments"] = _three_segments(document)
    rows = _outline_rows(render_skeleton(document))

    assert len(rows) == 3
    for row in rows:
        timecode, share, sentence = row.split(OUTLINE_SEPARATOR)
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2} - \d{2}:\d{2}:\d{2}", timecode)
        assert re.fullmatch(r"\d+%", share)
        assert sentence


def test_the_outline_shares_add_up_to_a_hundred(document):
    document["segments"] = _three_segments(document)
    rows = _outline_rows(render_skeleton(document))
    shares = [int(row.split(OUTLINE_SEPARATOR)[1].rstrip("%")) for row in rows]

    assert len(shares) == 3
    assert abs(sum(shares) - 100) <= 1


def test_the_outline_shares_follow_the_running_time(document):
    """A segment that ran twice as long carries twice the share."""
    document["segments"] = _three_segments(document)

    assert segment_shares(document["segments"]) == [17, 33, 50]


def test_the_outline_sits_between_summary_and_the_note_body(document):
    text = render_skeleton(document)

    assert text.index("# Summary") < text.index("## 講者骨架") < text.index("# Note (layer 1-3)")


def test_the_outline_survives_a_document_with_no_usable_times(document):
    document["segments"] = [{"title": "無時間碼"}]
    body = _section(render_skeleton(document), "## 講者骨架")

    assert "100%" in body


def test_segment_section_orders_embed_summary_quotes_bullets(document):
    body = _section(render_skeleton(document, style="faithful"),
                    "## 1、佔位段落一：名詞與判準")
    order = [
        body.index("![[frames/demo-0001.png]]"),
        body.index("佔位段落摘要一。"),
        body.index("> 「佔位原話一之一」"),
        body.index("- 佔位綜整條列一之甲"),
    ]

    assert order == sorted(order)


def test_segment_heading_carries_the_time_span(document):
    body = _section(render_skeleton(document), "## 1、佔位段落一：名詞與判準")

    assert "(00:00:00 - 00:01:00)" in body


def test_a_segment_without_a_frame_emits_no_embed_line(document):
    body = _section(render_skeleton(document), "## 2、佔位段落二：追蹤與邊界")

    assert "![[" not in body
    assert body.split("\n\n")[2].startswith("佔位段落摘要二。")


def test_unverified_terms_appear_only_under_references(document):
    text = render_skeleton(document)
    term = document["unverified_terms"][0]
    note_body = _section(text, "# Note (layer 1-3)")
    references = _section(text, "### References")

    assert term in references
    assert UNVERIFIED_MARK in references
    assert term not in note_body.replace(references, "")


def test_references_carry_the_correction_table_and_the_source_block(document):
    references = _section(render_skeleton(document), "### References")

    assert "| heard | correct | source |" in references
    assert "| 佔位聽錯甲 | 佔位正確甲 | deterministic |" in references
    assert "- video: 20240115 demo talk.mp4" in references
    assert "- engine: breeze_ct2" in references
    assert "offset model" not in references


def test_the_question_section_is_never_empty(document):
    """Recall is answering, not rereading, so the section always carries drafts."""
    body = _section(render_skeleton(document), "## 題目")
    numbered = [line for line in body.splitlines() if QUESTION_LINE.match(line)]

    assert guideline.MIN_QUESTIONS <= len(numbered) <= guideline.MAX_QUESTIONS


def test_every_drafted_question_hides_its_answer_behind_a_callout(document):
    body = _section(render_skeleton(document), "## 題目")
    numbered = [line for line in body.splitlines() if QUESTION_LINE.match(line)]

    assert body.count(guideline.ANSWER_CALLOUT) == len(numbered)
    assert "> [!answer]-" in body


def test_at_least_one_question_is_an_inference_question(document):
    body = _section(render_skeleton(document), "## 題目")

    assert guideline.INFERENCE_MARK in body
    assert "[推論]" in body


def test_questions_cross_segments_rather_than_repeating_one(document):
    """A question answerable from a single section is rereading in disguise."""
    body = _section(render_skeleton(document), "## 題目")
    first, second = (segment["title"] for segment in document["segments"][:2])

    assert first in body and second in body


def test_questions_written_into_the_document_win_over_the_drafts(document):
    """`questions_zh` is where the segmentation step puts real questions."""
    document["questions_zh"] = [
        {"text": "甲與乙為什麼要放在一起看？", "segments": [1, 2]},
        {"text": "乙的判準在什麼情況下會失效？", "segments": [2]},
        {"text": "[推論] 若某案例有甲但沒有乙，結論還成立嗎？", "segments": [1, 2]},
    ]
    body = _section(render_skeleton(document), "## 題目")

    assert "1. 甲與乙為什麼要放在一起看？" in body
    assert "3. [推論] 若某案例有甲但沒有乙，結論還成立嗎？" in body
    assert "不看筆記說出" not in body


def test_a_short_question_set_is_topped_up_rather_than_trusted(document):
    """Two questions and no inference question is not a usable set."""
    document["questions_zh"] = [{"text": "甲的理由是什麼？", "segments": [1]}]
    body = _section(render_skeleton(document), "## 題目")
    numbered = [line for line in body.splitlines() if QUESTION_LINE.match(line)]

    assert "1. 甲的理由是什麼？" in body
    assert guideline.MIN_QUESTIONS <= len(numbered) <= guideline.MAX_QUESTIONS
    assert guideline.INFERENCE_MARK in body


def test_learning_verification_drafts_three_things_behind_a_fold(document):
    """Collapsed, and deliberately without an ai-draft marker (task 16.4).

    The marker means "a model still has to write this", and the count of
    markers left is the definition of done. These three candidates are the
    reader's to keep, delete or rewrite, so marking them would have put a
    number nobody can drive to zero into the report.
    """
    body = _section(render_skeleton(document), "## 學習驗證")

    assert "### 我應該記住的 3 件事" in body
    assert render_mod.CANDIDATE_CALLOUT in body
    assert "<!-- ai-draft -->" not in body
    assert "3. 佔位重點：講者對長期追蹤明說沒有經驗" in body


def test_write_skeleton_is_utf8_without_bom_and_lf_only(tmp_path, document):
    path = write_skeleton(tmp_path / "demo.v4.md", document)
    raw = path.read_bytes()

    assert raw[:3] != b"\xef\xbb\xbf"
    assert b"\r\n" not in raw


def test_render_never_reads_the_clock(document):
    """The date comes from the stem, not from today."""
    text = render_skeleton(document, stem="20240115 demo talk")

    assert "date: 2024-01-15" in text


# --------------------------------------------------------------------------
# `l2n render`: the line it prints has to name the profile it actually used
# --------------------------------------------------------------------------
def _render_document(tmp_path: Path, document, monkeypatch) -> Path:
    """A minimal renderable document on disk, in an overlay-free workspace.

    HOME and the working directory are repointed at tmp_path so that a
    `.lecture2notes/` overlay on the machine running the tests cannot
    decide which profile these assertions see.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "20240115 demo talk.json"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    return path


def test_render_reports_the_profile_the_command_line_chose(
    tmp_path, document, capsys, monkeypatch
):
    """`--profile radiology` decides the template, so it decides the message.

    The document's own `profile` field is the lowest-ranked of the layers that
    can name a profile. Printing it made `render --profile radiology` report
    "profile generic" while rendering from the radiology template: the one line
    a person reads to confirm what just happened said the opposite of the truth.
    """
    from lecture2notes import exit_codes
    from lecture2notes.cli.main import main

    document["profile"] = "generic"
    path = _render_document(tmp_path, document, monkeypatch)

    assert main(["render", str(path), "--profile", "radiology"]) == exit_codes.OK

    assert "profile radiology" in capsys.readouterr().out


def test_render_reports_the_document_profile_when_nothing_overrides_it(
    tmp_path, document, capsys, monkeypatch
):
    from lecture2notes import exit_codes
    from lecture2notes.cli.main import main

    document["profile"] = "generic"
    path = _render_document(tmp_path, document, monkeypatch)

    assert main(["render", str(path)]) == exit_codes.OK

    assert "profile generic" in capsys.readouterr().out


def test_render_uses_the_template_of_the_profile_named_on_the_command_line(
    tmp_path, document, capsys, monkeypatch
):
    """The flag has to change the note, not only the line about the note.

    radiology ships a note template with 閱片 reading callouts that generic does
    not have. Rendering the same `profile: generic` document twice, once with
    the flag, is the only assertion that distinguishes "the resolver was
    consulted" from "the message was patched".
    """
    from lecture2notes import exit_codes
    from lecture2notes.cli.main import main

    document["profile"] = "generic"
    path = _render_document(tmp_path, document, monkeypatch)
    note = path.with_name(path.stem + ".v4.md")

    assert main(["render", str(path)]) == exit_codes.OK
    plain = note.read_text(encoding="utf-8")
    assert main(["render", str(path), "--profile", "radiology", "--force"]) == exit_codes.OK
    radiology = note.read_text(encoding="utf-8")

    assert "[!reading-case]" not in plain
    assert "[!reading-case]" in radiology, (
        "--profile radiology did not reach the template that defines the note"
    )
    assert "profile radiology" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 15.11 -- one source of truth for which contract a note was written to
# --------------------------------------------------------------------------
def test_the_note_records_the_style_it_was_rendered_with(document):
    for style in ("faithful", "concise"):
        text = render_skeleton(document, style=style)
        assert render_mod.style_marker(style) in text
        assert render_mod.read_style_marker(text) == style


def test_the_marker_sits_immediately_after_the_frontmatter(document):
    lines = render_skeleton(document, style="faithful").split("\n")

    fences = [i for i, line in enumerate(lines) if line.strip() == "---"]
    assert lines[fences[1] + 1] == render_mod.style_marker("faithful")


def test_the_marker_names_the_guideline_version(document):
    from lecture2notes.notes.guideline import GUIDELINE_VERSION

    assert "guideline=%s" % GUIDELINE_VERSION in render_skeleton(document)


def test_the_marker_does_not_break_determinism(document):
    first = render_skeleton(document, style="faithful")
    second = render_skeleton(document, style="faithful")

    assert first == second


def test_a_note_that_already_has_a_marker_does_not_get_a_second_one():
    text = "---\ntitle: x\n---\n<!-- l2n:style=faithful guideline=1.2 -->\nbody\n"

    assert render_mod.insert_style_marker(text, "concise") == text


def test_a_note_without_frontmatter_gets_the_marker_at_the_top():
    out = render_mod.insert_style_marker("# heading\n", "faithful")

    assert out.splitlines()[0] == render_mod.style_marker("faithful")

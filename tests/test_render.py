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
from lecture2notes.notes.render import (
    UNVERIFIED_MARK,
    render_skeleton,
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


def test_evergreen_is_the_first_takeaway_in_bold_quotation_marks(document):
    body = _section(render_skeleton(document), "# Evergreen Note")

    assert "**「佔位核心觀念：兩個名詞常被混用，判準其實不同」**" in body


def test_summary_lists_every_takeaway(document):
    body = _section(render_skeleton(document), "# Summary")

    for item in document["takeaways_zh"]:
        assert "- %s" % item in body


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


def test_learning_verification_drafts_three_things_behind_a_marker(document):
    body = _section(render_skeleton(document), "## 學習驗證")

    assert "### 我應該記住的 3 件事" in body
    assert "<!-- ai-draft -->" in body
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

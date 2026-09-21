"""`--style faithful|concise` and where the default comes from.

The two styles differ in exactly two places: how many quotes a section keeps,
and whether a bullet that is itself a quotation survives. Everything else about
the skeleton is the same, which is what makes the choice safe to flip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes.notes.render import render_skeleton
from lecture2notes.profiles import loader

FIXTURE = Path(__file__).parent / "fixtures" / "note_doc.json"


@pytest.fixture()
def document():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _section(text: str, heading: str) -> str:
    lines = text.split("\n")
    start = lines.index(heading)
    depth = len(heading) - len(heading.lstrip("#"))
    for position in range(start + 1, len(lines)):
        line = lines[position]
        if line.startswith("#") and len(line) - len(line.lstrip("#")) <= depth:
            return "\n".join(lines[start:position])
    return "\n".join(lines[start:])


def _blockquotes(body: str):
    return [line for line in body.split("\n") if line.startswith("> ")]


def test_concise_keeps_one_quote_out_of_three(document):
    body = _section(render_skeleton(document, style="concise"),
                    "## 1、佔位段落一：名詞與判準")

    assert len(_blockquotes(body)) == 1
    assert "佔位原話一之一" in body
    assert "佔位原話一之二" not in body


def test_faithful_keeps_every_quote(document):
    body = _section(render_skeleton(document, style="faithful"),
                    "## 1、佔位段落一：名詞與判準")

    assert len(_blockquotes(body)) == 3


def test_concise_drops_a_bullet_whose_kind_is_quote(document):
    body = _section(render_skeleton(document, style="concise"),
                    "## 1、佔位段落一：名詞與判準")

    assert "- 佔位綜整條列一之甲" in body
    assert "佔位原話條列一之乙" not in body


def test_faithful_keeps_a_quote_bullet_and_marks_it(document):
    body = _section(render_skeleton(document, style="faithful"),
                    "## 1、佔位段落一：名詞與判準")

    assert "- 「佔位原話條列一之乙」 (00:00:12)" in body


def test_the_default_style_comes_from_the_profile(document):
    assert loader.note_style("generic") == "concise"
    assert render_skeleton(document) == render_skeleton(document, style="concise")


def test_an_explicit_style_overrides_the_profile(document):
    assert loader.note_style("generic", "faithful") == "faithful"
    assert render_skeleton(document, style="faithful") != render_skeleton(document)


def test_an_unknown_style_falls_back_to_the_profile_default(document):
    assert render_skeleton(document, style="loud") == render_skeleton(
        document, style="concise"
    )

"""Frontmatter is a template, not a code path.

The public generic profile emits four keys. Everything past those four is one
person's vault convention, and the only way to add one is to edit a template
file -- which the last test enforces by grepping the package for two field names
that must never appear in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture2notes.notes.render import (
    frontmatter_block,
    frontmatter_keys,
    render_frontmatter,
    render_skeleton,
)
from lecture2notes.profiles import loader

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "note_doc.json"
OVERLAY = FIXTURES / "overlay.note.frontmatter.yaml"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

#: Field names that belong to a user's vault, never to this package.
VAULT_ONLY_FIELDS = ("noteVer", "DateRev")


@pytest.fixture()
def document():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_generic_frontmatter_has_exactly_four_keys(document):
    block = render_frontmatter(document, stem="20240115 demo talk")

    assert frontmatter_keys(block) == ["title", "date", "source", "tags"]


def test_generic_frontmatter_values_come_from_the_document(document):
    block = render_frontmatter(document, stem="20240115 demo talk")

    assert 'title: "佔位講座：兩個觀念與一組判準"' in block
    assert "date: 2024-01-15" in block
    assert 'source: "20240115 demo talk.mp4"' in block
    assert "tags: []" in block


def test_an_overlay_template_adds_its_own_fields(document):
    template = OVERLAY.read_text(encoding="utf-8")
    block = render_frontmatter(document, template, stem="20240115 demo talk")
    keys = frontmatter_keys(block)

    for field in ("noteVer", "DateRev", "subspecialty"):
        assert field in keys
    assert "noteVer: v4" in block
    assert "DateRev: 2024-01-15" in block


def test_an_overlay_reaches_the_rendered_note(document):
    text = render_skeleton(
        document, frontmatter=OVERLAY.read_text(encoding="utf-8")
    )

    assert text.startswith("---\n")
    assert "noteVer: v4" in text.split("---\n")[1]


def test_an_unknown_placeholder_renders_empty_rather_than_crashing(document):
    block = render_frontmatter(document, "---\nx: \"{{nope}}\"\n---\n")

    assert block == '---\nx: ""\n---\n'


def test_the_template_comment_header_is_not_rendered():
    template = loader.frontmatter_template("generic") or ""

    assert template.lstrip().startswith("#")
    assert frontmatter_block(template).startswith("---\n")


def test_the_package_never_names_a_vault_only_field():
    """`grep -rn "noteVer\\|DateRev" src/` must find nothing."""
    hits = []
    for path in SRC_ROOT.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for field in VAULT_ONLY_FIELDS:
            if field in text:
                hits.append("%s: %s" % (path.relative_to(SRC_ROOT).as_posix(), field))

    assert not hits, hits

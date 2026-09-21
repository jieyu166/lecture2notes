"""The packaged skill stays a router, not a second copy of the documentation.

Three things go stale silently and are therefore asserted here: the 80-line
budget (a SKILL.md that grows into a manual stops being read), the routing
table's coverage of every CLI stage (a stage nobody routes to is a stage the
agent reimplements in prose), and the existence of the six reference files the
table points at.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCES_DIR = SKILL_DIR / "references"

MAX_LINES = 80

REFERENCE_FILES = (
    "transcription.md",
    "segmentation.md",
    "frames-and-notes.md",
    "note-writing.md",
    "outputs-and-batch.md",
    "profiles-and-overlay.md",
)

#: Every stage the routing table must send the reader somewhere for.
CLI_STAGES = (
    "transcribe",
    "calibrate-subs",
    "frames",
    "ocr",
    "condense",
    "scaffold",
    "render",
    "viewer",
    "pbf",
    "hub",
    "publish",
    "check",
)

#: The description must name each of these areas of work.
DESCRIPTION_TOPICS = ("transcription", "segmentation", "frames", "notes", "viewer", "course hub")


def _skill_text() -> str:
    assert SKILL_MD.exists(), "skill/SKILL.md is missing"
    return SKILL_MD.read_text(encoding="utf-8")


def _frontmatter(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "skill/SKILL.md has no YAML frontmatter"
    return m.group(1)


def _routing_rows(text: str) -> list[str]:
    """Table rows of the routing table: every markdown row that links a reference."""
    rows = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("|") and "references/" in line
    ]
    assert rows, "no routing-table rows found in skill/SKILL.md"
    return rows


def test_skill_md_is_at_most_80_lines():
    lines = _skill_text().splitlines()
    assert len(lines) <= MAX_LINES, (
        "skill/SKILL.md is %d lines; the budget is %d. Move detail into a "
        "reference instead of growing the router." % (len(lines), MAX_LINES)
    )


def test_frontmatter_name_is_lecture2notes():
    assert re.search(r"^name:\s*lecture2notes\s*$", _frontmatter(_skill_text()), re.M)


@pytest.mark.parametrize("topic", DESCRIPTION_TOPICS)
def test_description_names_every_area(topic):
    front = _frontmatter(_skill_text())
    m = re.search(r"^description:\s*(.+)$", front, re.M)
    assert m, "skill/SKILL.md frontmatter has no description"
    assert topic in m.group(1), (
        "description does not name %r; the agent picks this skill by its "
        "description alone" % topic
    )


@pytest.mark.parametrize("stage", CLI_STAGES)
def test_routing_table_mentions_every_cli_stage(stage):
    rows = "\n".join(_routing_rows(_skill_text()))
    assert "l2n %s" % stage in rows, (
        "no routing-table row mentions `l2n %s`; an unrouted stage is one the "
        "agent will reinvent in prose" % stage
    )


@pytest.mark.parametrize("filename", REFERENCE_FILES)
def test_reference_file_exists(filename):
    path = REFERENCES_DIR / filename
    assert path.is_file(), "skill/references/%s is missing" % filename
    assert path.read_text(encoding="utf-8").strip(), "%s is empty" % filename


def test_routing_table_links_only_the_six_references():
    linked = set(re.findall(r"references/([A-Za-z0-9._-]+\.md)", _skill_text()))
    assert linked == set(REFERENCE_FILES), (
        "routing table links %s but the six references are %s"
        % (sorted(linked), sorted(REFERENCE_FILES))
    )


def test_skill_md_keeps_six_hard_rules():
    text = _skill_text()
    body = text.split("## HARD RULES", 1)
    assert len(body) == 2, "skill/SKILL.md has no HARD RULES section"
    section = body[1].split("\n## ", 1)[0]
    numbered = re.findall(r"^\d+\.\s", section, re.M)
    assert len(numbered) == 6, (
        "HARD RULES has %d numbered rules, expected 6" % len(numbered)
    )


def test_completion_conditions_require_l2n_check():
    text = _skill_text()
    assert "## 完成條件" in text, "skill/SKILL.md has no completion-conditions section"
    section = text.split("## 完成條件", 1)[1]
    assert "l2n check" in section, (
        "completion conditions do not require running `l2n check` on the artifacts"
    )


def test_skill_md_is_ascii_safe_for_console_markers():
    # The project's console output must survive cp950; arrows and check marks
    # in a document that gets echoed into a terminal are the usual culprits.
    forbidden = set("→≥≤✓✗✔✘")
    for path in [SKILL_MD, *(REFERENCES_DIR / f for f in REFERENCE_FILES)]:
        found = forbidden & set(path.read_text(encoding="utf-8"))
        assert not found, "%s contains forbidden symbol(s) %s" % (
            path.name, sorted(found)
        )


def test_segmentation_reference_states_the_division_of_labour():
    text = (REFERENCES_DIR / "segmentation.md").read_text(encoding="utf-8")
    assert "l2n scaffold" in text
    assert "schema_version" in text and '"2.0"' in text
    # The point of the file: the model writes the semantics, the CLI does the
    # mechanics. Both halves must be stated.
    assert "語言模型" in text
    assert "l2n check json" in text


# --------------------------------------------------------------------------
# 15.3 -- a JSON example that does not validate is worse than no example
# --------------------------------------------------------------------------
def _json_documents(path: Path) -> list:
    """Every fenced ```json block in *path* that is a whole JSON object."""
    import json as _json

    blocks = re.findall(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)
    out = []
    for block in blocks:
        body = block.strip()
        if not body.startswith("{"):
            # A fragment showing one key in context, not a document.
            continue
        out.append(_json.loads(body))
    return out


def test_the_segmentation_example_passes_the_schema_validator():
    """The document the agent copies must survive `l2n check json`.

    A field run pasted this example, filled it in and was rejected on four
    separate rules: the segment bullets used the top-level key name, the
    mandatory `frame` key was absent, and both `takeaways_zh` counts were under
    the minimum. An example that does not validate costs more than no example,
    because it is trusted.
    """
    from lecture2notes.schema.model import validate_document

    documents = _json_documents(REFERENCES_DIR / "segmentation.md")
    assert documents, "segmentation.md carries no complete JSON example"
    for document in documents:
        errors = [f for f in validate_document(document) if f.severity == "error"]
        assert not errors, "the documented example is invalid: %s" % [
            "%s %s" % (f.code, f.message) for f in errors
        ]


def test_the_segmentation_example_states_the_bullet_count_the_checker_enforces():
    text = (REFERENCES_DIR / "segmentation.md").read_text(encoding="utf-8")
    from lecture2notes.acceptance.check import MIN_BULLETS

    assert "bullets_zh" in text
    assert "至少 %d" % MIN_BULLETS in text, (
        "segmentation.md must state the same bullet minimum the checker uses"
    )
    assert "恰好 4 條" not in text, (
        "the old wording contradicted the checker, which warns below %d"
        % MIN_BULLETS
    )


# --------------------------------------------------------------------------
# 15.12(c) -- the two places OCR text lives, and which one the note reads
# --------------------------------------------------------------------------
def test_frames_reference_separates_the_ocr_cache_from_the_document_field():
    """A field run read the cache and found the document empty, or vice versa.

    `<stem>.frames_ocr.json` is a cache keyed on frame path; the note stage
    reads `frame_ocr` inside the canonical JSON. The reference used to name
    only the second and show a command that could produce only the first.
    """
    text = (REFERENCES_DIR / "frames-and-notes.md").read_text(encoding="utf-8")

    assert "<stem>.frames_ocr.json" in text
    assert "frame_ocr" in text
    assert "快取" in text
    assert "HARD RULE 3" in text


def test_the_guideline_says_the_same_thing_in_section_one():
    doc = REPO_ROOT / "docs" / "note-writing-guideline.md"
    text = doc.read_text(encoding="utf-8")
    body = text.split("### 1. 來源優先序與證據", 1)[1].split("\n### ", 1)[0]

    assert "<stem>.frames_ocr.json" in body
    assert "frame_ocr" in body
    assert "不可以抄進筆記" in body

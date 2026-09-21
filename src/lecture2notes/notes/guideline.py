"""The note-writing guideline as the code sees it.

``docs/note-writing-guideline.md`` is the document a person reads; this module is
the part of it a program needs: the version string, the section names a note must
carry, and the rule identifiers the check reports. The version lives here and
nowhere else -- the document quotes it, and a test asserts the two agree -- so
there is exactly one place to bump when the rules change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

#: Bump this whenever a rule changes meaning. `l2n check note` prints it, so a
#: report always says which edition of the rules produced it.
GUIDELINE_VERSION = "1.0"

#: The exact line the document carries and the check prints.
VERSION_LINE = "guideline_version: %s" % GUIDELINE_VERSION

#: Where the document lives, relative to the repository root.
GUIDELINE_DOC = "docs/note-writing-guideline.md"

#: The two parts the document must keep separate, named the same in both places.
MUST_FOLLOW_HEADING = "LLM 必須遵守"
MACHINE_CHECKABLE_HEADING = "機器可檢查"

#: Mandatory sections, in the order a note must carry them (rule R1). Each entry
#: is (heading line as rendered, short name used in findings).
MANDATORY_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("# Evergreen Note", "Evergreen Note"),
    ("# Summary", "Summary"),
    ("# Note (layer 1-3)", "Note (layer 1-3)"),
    ("### References", "References"),
    ("## 題目", "題目"),
    ("## 學習驗證", "學習驗證"),
)

#: A run at least this long (whitespace and punctuation removed) that also
#: appears in the transcript, and is not marked as a quotation, is R5.
TRANSCRIPT_RUN_CHARS = 40

#: How much of the offending run a finding quotes back.
FINDING_EXCERPT_CHARS = 20

#: Rule identifiers, so a typo in a finding fails a test rather than a reader.
RULES: Tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")


def find_guideline_doc(start: Optional[Path] = None) -> Optional[Path]:
    """Locate ``docs/note-writing-guideline.md`` by walking up from the package.

    Present in a source checkout, absent from a wheel that ships only the
    package. Callers treat None as "print the path, not the text".
    """
    here = Path(start) if start is not None else Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        candidate = parent / GUIDELINE_DOC
        if candidate.is_file():
            return candidate
    return None


def section_text(markdown: str, heading_contains: str) -> str:
    """The body of the first heading whose text contains *heading_contains*.

    Stops at the next heading of the same or a shallower level, which is what
    "this part of the document" means in a Markdown file.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    start: Optional[int] = None
    level = 0
    for position, line in enumerate(lines):
        match = _HEADING.match(line)
        if not match:
            continue
        if start is None:
            if heading_contains in match.group(1):
                start = position
                level = len(line) - len(line.lstrip("#"))
            continue
        if len(line) - len(line.lstrip("#")) <= level:
            return "\n".join(lines[start:position]).rstrip() + "\n"
    if start is None:
        return ""
    return "\n".join(lines[start:]).rstrip() + "\n"


def must_follow_text(doc: Optional[Path] = None) -> str:
    """The "LLM 必須遵守" part of the shipped document, or an empty string."""
    path = doc if doc is not None else find_guideline_doc()
    if path is None or not Path(path).is_file():
        return ""
    return section_text(
        Path(path).read_text(encoding="utf-8"), MUST_FOLLOW_HEADING
    )


def mandatory_headings() -> List[str]:
    return [heading for heading, _ in MANDATORY_SECTIONS]


def expand_prompt(
    json_path: Path,
    note_path: Optional[Path] = None,
    transcript_path: Optional[Path] = None,
    style: str = "concise",
    doc: Optional[Path] = None,
) -> str:
    """The instruction bundle an agent needs to expand one skeleton.

    Printed by ``l2n render --expand-prompt``. It names the version of the rules,
    quotes the rules themselves when the document is on disk, lists the three
    files to read, and ends with the command that decides whether the expansion
    was acceptable. Everything an agent needs is in this one block of text, so
    the bundle can be pasted into any model without this package being installed
    on the other side.
    """
    json_path = Path(json_path)
    note = Path(note_path) if note_path else json_path.with_name(
        json_path.stem + ".v4.md"
    )
    transcript = Path(transcript_path) if transcript_path else json_path.with_name(
        json_path.stem + ".srt"
    )
    document = doc if doc is not None else find_guideline_doc()

    parts: List[str] = [
        VERSION_LINE,
        "",
        "# 擴寫任務",
        "",
        "把下面這份骨架筆記就地擴寫成完整筆記。章節順序與 frontmatter 不得更動。",
        "style: %s" % style,
        "",
        "## 要讀的檔案",
        "",
        "1. 骨架筆記（就地改寫這一份）: %s" % note,
        "2. 逐字稿: %s" % transcript,
        "3. 正式 JSON: %s" % json_path,
        "4. 官方講義（若有，請一併讀入；講義優先序高於逐字稿）",
        "",
        "## 撰寫規範",
        "",
        "規範文件: %s" % (document if document else GUIDELINE_DOC),
    ]
    rules = must_follow_text(document)
    if rules:
        parts.extend(["", rules.rstrip()])
    parts.extend([
        "",
        "## 完成後必做",
        "",
        "l2n check note %s --note %s" % (json_path, note),
        "",
        "check 回報任何 error 就回頭修，不要交出去。",
    ])
    return "\n".join(parts) + "\n"


__all__ = [
    "FINDING_EXCERPT_CHARS",
    "GUIDELINE_DOC",
    "GUIDELINE_VERSION",
    "MACHINE_CHECKABLE_HEADING",
    "MANDATORY_SECTIONS",
    "MUST_FOLLOW_HEADING",
    "RULES",
    "TRANSCRIPT_RUN_CHARS",
    "VERSION_LINE",
    "expand_prompt",
    "find_guideline_doc",
    "mandatory_headings",
    "must_follow_text",
    "section_text",
]

"""Deterministic skeleton note, projected only from the canonical JSON.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/render_v4_note.py

Nothing here calls a model. Every line in the output comes from a key in the
document, so rendering the same JSON twice produces the same bytes; that is what
lets an audit compare a published note against its source and get a real answer.
The LLM expansion step reads this skeleton and writes back over it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from lecture2notes.profiles import loader
from lecture2notes.schema.model import segment_end, segment_start


def text_of(value: Any) -> str:
    return value if isinstance(value, str) else ""


def strings_of(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def bullet_texts(segment: Mapping[str, Any]) -> List[str]:
    """Read bullets from either the legacy string list or the v2 object list."""
    raw = segment.get("takeaways_zh")
    if raw is None:
        raw = segment.get("bullets_zh")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            out.append(item["text"])
    return out


def render_note(data: Mapping[str, Any]) -> str:
    """Render the skeleton note as Markdown."""
    title = text_of(data.get("title")) or "Lecture"
    lines: List[str] = ["# %s" % title, ""]
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        start = segment_start(segment)
        end = segment_end(segment)
        lines.extend([
            "## %d. %s" % (position, text_of(segment.get("title"))),
            "",
            "> %.3fs-%.3fs" % (start, end),
            "",
            text_of(segment.get("summary_zh")),
            "",
            "### Takeaways",
        ])
        lines.extend("- %s" % item for item in bullet_texts(segment))

        editorial = strings_of(segment.get("editorial_notes_zh"))
        if editorial:
            lines.extend(["", "> [!note] Editorial"])
            lines.extend("> - %s" % item for item in editorial)

        frames = segment.get("frames") if isinstance(segment.get("frames"), list) else []
        lines.extend(["", "### Frames"])
        for frame in frames:
            if not isinstance(frame, Mapping):
                continue
            path = text_of(frame.get("path")).replace("\\", "/")
            if not path:
                continue
            try:
                timestamp = float(frame.get("time"))
            except (TypeError, ValueError):
                continue
            lines.append("![[%s]] <!-- %.3fs -->" % (path, timestamp))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_note(path: Path, data: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_note(data), encoding="utf-8", newline="\n")
    return destination


# ==========================================================================
# the skeleton note: layer one of the two-layer note generation
# ==========================================================================
# `render_note` above is the legacy projection the audit compares a note
# against; it stays as it was ported. Everything below is the skeleton the
# product actually ships: fixed sections in a fixed order, every line projected
# from a key in the document, no model involved. An agent then expands this file
# in place under the writing guideline, and `l2n check note` decides whether the
# expansion stayed inside the contract.

#: Styles `--style` accepts. faithful keeps every quote; concise keeps one.
STYLES = ("faithful", "concise")

#: Placeholder syntax shared by the frontmatter and the note template.
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: A stem that starts with a date, as the hospital and course exports write it.
STEM_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})")

#: The correction table's header. `check note` looks for these column names, so
#: they are defined once and rendered from here.
CORRECTION_COLUMNS = ("heard", "correct", "source")

#: How an unverified term is marked, in the note and in the check's message.
UNVERIFIED_MARK = "未寫入本文"

#: Marks the three things the model drafted for the reader to keep, delete or
#: rewrite. It survives in the file, so a note whose marker is still untouched is
#: measurably one that nobody has read.
AI_DRAFT_MARK = "<!-- ai-draft -->"

#: How many candidates the model drafts under "我應該記住的 3 件事".
REMEMBER_COUNT = 3

#: The learning-verification checklist. Fixed text, so two renders agree.
VERIFICATION_ITEMS = (
    "能用自己的話說出本講座的核心觀念，不看筆記也說得完整",
    "能指出本講座中最容易混淆的一組觀念，並說明它們的差異",
    "能說出本講座的結論在什麼情況下不成立，或講者明說沒有把握的地方",
)


def fill(template: str, context: Mapping[str, Any]) -> str:
    """Substitute ``{{name}}`` from *context*; an unknown name renders empty.

    Unknown placeholders are empty rather than fatal on purpose: a user overlay
    template is allowed to ask for a field this package knows nothing about, and
    losing the line is better than losing the note.
    """
    return PLACEHOLDER.sub(
        lambda match: str(context.get(match.group(1), "") or ""), template
    )


def frontmatter_block(template: str) -> str:
    """The ``---`` fenced block of a frontmatter template, comments discarded.

    A template file may carry a comment header explaining its placeholders; only
    what lies between the fences becomes the note's frontmatter.
    """
    lines = template.replace("\r\n", "\n").split("\n")
    try:
        opening = next(i for i, line in enumerate(lines) if line.strip() == "---")
    except StopIteration:
        return ""
    for position in range(opening + 1, len(lines)):
        if lines[position].strip() == "---":
            return "\n".join(lines[opening:position + 1]) + "\n"
    return ""


def frontmatter_keys(block: str) -> List[str]:
    """Top-level YAML keys of a rendered frontmatter block, in order."""
    keys: List[str] = []
    for line in block.replace("\r\n", "\n").split("\n"):
        if line.strip() in ("---", ""):
            continue
        if line[:1].isspace() or line.lstrip().startswith("#"):
            continue
        name, separator, _ = line.partition(":")
        if separator and name.strip():
            keys.append(name.strip())
    return keys


def clock(seconds: Any) -> str:
    """``HH:MM:SS``, or an empty string when there is no usable number."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)


def _stem_of(data: Mapping[str, Any], stem: Optional[str] = None) -> str:
    if stem:
        return stem
    return text_of(data.get("stem"))


def _date_of(data: Mapping[str, Any], stem: str) -> str:
    """The lecture's date: the document's own field, else the stem's prefix.

    Never today's date. A skeleton that changes because it was rendered on a
    different day is not deterministic, and determinism is the whole point.
    """
    explicit = text_of(data.get("date"))
    if explicit:
        return explicit
    match = STEM_DATE.match(stem)
    if match:
        return "%s-%s-%s" % match.groups()
    return ""


def _subtitle(data: Mapping[str, Any]) -> Mapping[str, Any]:
    source = data.get("source")
    if not isinstance(source, Mapping):
        return {}
    subtitle = source.get("subtitle")
    return subtitle if isinstance(subtitle, Mapping) else {}


def frontmatter_context(
    data: Mapping[str, Any], stem: Optional[str] = None
) -> Dict[str, str]:
    """Every value a frontmatter template may ask for, already as text."""
    resolved_stem = _stem_of(data, stem)
    source = data.get("source") if isinstance(data.get("source"), Mapping) else {}
    subtitle = _subtitle(data)
    tags = strings_of(data.get("tags"))
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    duration = data.get("duration_sec")
    try:
        duration_min = "%d" % int(round(float(duration) / 60.0))
    except (TypeError, ValueError):
        duration_min = ""
    return {
        "title": text_of(data.get("title")),
        "date": _date_of(data, resolved_stem),
        "stem": resolved_stem,
        "profile": text_of(data.get("profile")),
        "source_video": text_of(source.get("video")),
        "source_subtitle": text_of(subtitle.get("path")),
        "source_origin": text_of(subtitle.get("origin")),
        "source_engine": text_of(subtitle.get("engine")),
        "source_lang": text_of(subtitle.get("lang")),
        "duration_min": duration_min,
        "segment_count": str(len(segments)),
        "tags": ", ".join('"%s"' % tag for tag in tags),
    }


def render_frontmatter(
    data: Mapping[str, Any],
    template: Optional[str] = None,
    stem: Optional[str] = None,
    profile: str = loader.BUILTIN_PROFILE,
) -> str:
    """Render the frontmatter from a template file, never from code."""
    text = template if template is not None else loader.frontmatter_template(profile)
    if not text:
        return ""
    return fill(frontmatter_block(text), frontmatter_context(data, stem))


# -- the body ---------------------------------------------------------------
def quote_items(segment: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """``quotes_zh`` as objects, accepting the 1.x bare-string spelling."""
    raw = segment.get("quotes_zh")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append({"text": item, "t": None})
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            if item["text"]:
                out.append({"text": item["text"], "t": item.get("t")})
    return out


def bullet_items(segment: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """``bullets_zh`` as objects, carrying the kind so concise can drop quotes."""
    raw = segment.get("bullets_zh")
    if raw is None:
        raw = segment.get("takeaways_zh")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append({"text": item, "t": None, "kind": "synthesis"})
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            if item["text"]:
                out.append({
                    "text": item["text"],
                    "t": item.get("t"),
                    "kind": str(item.get("kind") or "synthesis"),
                })
    return out


def _timestamp_suffix(value: Any) -> str:
    stamp = clock(value)
    return " (%s)" % stamp if stamp else ""


def render_segment(
    segment: Mapping[str, Any], position: int, style: str = "concise"
) -> List[str]:
    """One ``## n、title`` section: embed, summary, quotes, bullets, in order."""
    lines = ["## %d、%s" % (position, text_of(segment.get("title")))]
    span = "%s - %s" % (
        clock(segment.get("start_sec")), clock(segment.get("end_sec"))
    )
    if span.strip(" -"):
        lines.extend(["", "(%s)" % span])

    frame = segment.get("frame")
    if isinstance(frame, str) and frame:
        lines.extend(["", "![[%s]]" % frame.replace("\\", "/")])

    summary = text_of(segment.get("summary_zh"))
    if summary:
        lines.extend(["", summary])

    quotes = quote_items(segment)
    if style == "concise":
        quotes = quotes[:1]
    for quote in quotes:
        lines.extend([
            "", "> 「%s」%s" % (quote["text"], _timestamp_suffix(quote.get("t")))
        ])

    bullets = bullet_items(segment)
    if style == "concise":
        bullets = [item for item in bullets if item["kind"] != "quote"]
    if bullets:
        lines.append("")
        for bullet in bullets:
            if bullet["kind"] == "quote":
                lines.append(
                    "- 「%s」%s" % (bullet["text"], _timestamp_suffix(bullet.get("t")))
                )
            else:
                lines.append("- %s" % bullet["text"])
    return lines


def render_references(data: Mapping[str, Any]) -> List[str]:
    """The correction table, the unverified terms, and the source block."""
    lines = ["| %s |" % " | ".join(CORRECTION_COLUMNS),
             "| %s |" % " | ".join("---" for _ in CORRECTION_COLUMNS)]
    corrections = data.get("corrections")
    rows = corrections if isinstance(corrections, list) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        lines.append("| %s |" % " | ".join(
            text_of(row.get(column)) or "-" for column in CORRECTION_COLUMNS
        ))

    lines.extend(["", "#### unverified_terms", ""])
    terms = strings_of(data.get("unverified_terms"))
    if terms:
        lines.extend("- %s（%s）" % (term, UNVERIFIED_MARK) for term in terms)
    else:
        lines.append("- （無）")

    lines.extend(["", "#### source", ""])
    source = data.get("source") if isinstance(data.get("source"), Mapping) else {}
    subtitle = _subtitle(data)
    lines.append("- video: %s" % (text_of(source.get("video")) or "-"))
    lines.append("- subtitle: %s" % (text_of(subtitle.get("path")) or "-"))
    lines.append("- subtitle origin: %s" % (text_of(subtitle.get("origin")) or "-"))
    lines.append("- engine: %s" % (text_of(subtitle.get("engine")) or "-"))
    if subtitle.get("offset_model") is not None:
        lines.append("- offset model: %s" % subtitle.get("offset_model"))
    return lines


def render_questions(segments: Sequence[Any]) -> List[str]:
    """One placeholder question per segment, for the expansion to rewrite."""
    lines: List[str] = []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        title = text_of(segment.get("title"))
        lines.append(
            "%d. 關於「%s」，講者的理由是什麼？結論在什麼情況下不成立？"
            % (position, title)
        )
    if not lines:
        lines.append("1. （尚無段落，擴寫時請補上題目）")
    return lines


def render_verification(takeaways: Sequence[str]) -> List[str]:
    """"我應該記住的 3 件事" as a draft, then the reader's own checklist.

    The three lines are projected from ``takeaways_zh`` rather than invented, and
    they are fenced by :data:`AI_DRAFT_MARK` because the reader's only job here
    is to keep, delete or rewrite them. A note whose marker is still in place is
    one that nobody has read, and that is worth being able to count.
    """
    lines = ["### 我應該記住的 3 件事", "", AI_DRAFT_MARK, ""]
    drafts = list(takeaways[:REMEMBER_COUNT])
    while len(drafts) < REMEMBER_COUNT:
        drafts.append("（素材不足，讀者自行補上或刪除本行）")
    lines.extend("%d. %s" % (number, text) for number, text in enumerate(drafts, 1))
    lines.extend([
        "",
        "保留／刪除／改寫上面三條是讀者的工作；一個字都沒改，這份筆記就等於沒有人碰過。",
        "",
        "### 自我檢核",
        "",
    ])
    lines.extend("- [ ] %s" % item for item in VERIFICATION_ITEMS)
    return lines


def render_skeleton(
    data: Mapping[str, Any],
    style: Optional[str] = None,
    stem: Optional[str] = None,
    profile: str = loader.BUILTIN_PROFILE,
    template: Optional[str] = None,
    frontmatter: Optional[str] = None,
) -> str:
    """The deterministic skeleton note, projected only from *data*.

    No clock is read, no model is called, and nothing outside *data* and the
    profile's template files reaches the output, so rendering the same document
    with the same profile twice produces the same bytes.
    """
    chosen = loader.note_style(profile, style)
    if chosen not in STYLES:
        chosen = loader.DEFAULT_NOTE_STYLE
    body = template if template is not None else loader.note_template(profile)
    if not body:
        raise ValueError(
            "profile %r ships no %s" % (profile, loader.NOTE_TEMPLATE)
        )

    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    takeaways = strings_of(data.get("takeaways_zh"))

    evergreen = "**「%s」**" % takeaways[0] if takeaways else "**「（待擴寫）」**"
    summary = "\n".join("- %s" % item for item in takeaways) or "- （待擴寫）"

    section_lines: List[str] = []
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        if section_lines:
            section_lines.append("")
        section_lines.extend(render_segment(segment, position, chosen))

    context = {
        "frontmatter": render_frontmatter(data, frontmatter, stem, profile),
        "evergreen": evergreen,
        "summary": summary,
        "segments": "\n".join(section_lines) or "（尚無段落）",
        "references": "\n".join(render_references(data)),
        "questions": "\n".join(render_questions(segments)),
        "verification": "\n".join(render_verification(takeaways)),
        "title": text_of(data.get("title")),
    }
    text = fill(body.replace("\r\n", "\n"), context)
    return text.rstrip("\n") + "\n"


def write_skeleton(path: Path, data: Mapping[str, Any], **kwargs: Any) -> Path:
    """Write the skeleton as UTF-8 without a BOM and with LF endings."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_skeleton(data, **kwargs), encoding="utf-8", newline="\n"
    )
    return destination


__all__ = [
    "AI_DRAFT_MARK",
    "CORRECTION_COLUMNS",
    "PLACEHOLDER",
    "REMEMBER_COUNT",
    "STYLES",
    "UNVERIFIED_MARK",
    "VERIFICATION_ITEMS",
    "bullet_items",
    "bullet_texts",
    "clock",
    "fill",
    "frontmatter_block",
    "frontmatter_context",
    "frontmatter_keys",
    "quote_items",
    "render_frontmatter",
    "render_note",
    "render_questions",
    "render_references",
    "render_segment",
    "render_skeleton",
    "render_verification",
    "strings_of",
    "text_of",
    "write_note",
    "write_skeleton",
]

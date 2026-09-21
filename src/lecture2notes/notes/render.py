"""Deterministic skeleton note, projected only from the canonical JSON.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/render_v4_note.py

Nothing here calls a model. Every line in the output comes from a key in the
document, so rendering the same JSON twice produces the same bytes; that is what
lets an audit compare a published note against its source and get a real answer.
The LLM expansion step reads this skeleton and writes back over it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Mapping, Sequence

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


__all__ = ["bullet_texts", "render_note", "strings_of", "text_of", "write_note"]

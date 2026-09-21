"""Data layer of the synced lecture viewer: cues, blocks and their time bindings.

Ported from rad-workflow
skills/lecture-to-notes/scripts/build_lecture_viewer.py (``frame_seconds``,
``read_text``, ``parse_srt``, ``find_sibling``, ``build_blocks``).

The HTML, CSS and player script of the original are deliberately not ported: the
viewer template is rewritten against schema v2 in a later step, and carrying the
v1 template across only to replace it would mean two rewrites and one stale copy.
What is ported is the part the template consumes, which is where the honesty
about timing lives.

Synchronisation is exact for the transcript layer, because every cue has a real
timecode. Summary bullets in a v1 document have no timecode of their own, only
the segment they belong to, so their times are interpolated evenly inside the
segment and flagged ``estimated``. The flag is not decoration: a reader who does
not know which times are guesses will trust the wrong ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.engines.base import Cue, parse_srt, parse_srt_text, read_subtitle_text
from lecture2notes.frames.manifest import frame_seconds

VIDEO_EXT = (".mp4", ".m4v", ".webm", ".mov", ".mkv")
#: Block kinds, in the order the viewer layers them.
KINDS = ("slide", "summary", "transcript")


def find_sibling(base: Path, stem: str, extensions: Sequence[str]) -> Optional[Path]:
    """Find a companion file: exact stem first, then a stem prefix.

    ``.raw.srt`` is excluded. It is the uncorrected original that the correction
    step leaves behind, and it sorts before ``.srt``, so a glob that does not
    exclude it silently picks the version with the known errors in it.
    """
    base = Path(base)

    def usable(path: Path) -> bool:
        return path.exists() and ".raw." not in path.name.lower()

    for ext in extensions:
        exact = base / ("%s%s" % (stem, ext))
        if usable(exact):
            return exact
        for candidate in sorted(base.glob("%s*%s" % (stem, ext))):
            if usable(candidate):
                return candidate
    hits = [
        path for path in sorted(base.iterdir())
        if path.suffix.lower() in extensions and usable(path)
    ]
    return hits[0] if len(hits) == 1 else None


def segment_view(segment: Mapping[str, Any]) -> Dict[str, Any]:
    """One segment as the viewer needs it."""
    start = float(segment.get("start_sec") or 0)
    end = float(segment.get("end_sec") or start)
    return {
        "id": "s%s" % segment.get("index"),
        "index": segment.get("index"),
        "title": segment.get("title") or "",
        "start": start,
        "end": end,
        "summary": segment.get("summary_zh") or "",
        "frames": segment.get("frames") or (
            [segment["frame"]] if segment.get("frame") else []
        ),
    }


def build_blocks(
    data: Mapping[str, Any],
    cues: Sequence[Cue],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ``(segments, blocks)``: every clickable line with its time.

    Slide OCR text becomes its own block timed from the frame filename, so
    clicking a line of slide text jumps to the moment that slide appeared.
    """
    segments: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    for segment in data.get("segments", []):
        if not isinstance(segment, Mapping):
            continue
        view = segment_view(segment)
        segments.append(view)
        sid, start, end = view["id"], view["start"], view["end"]

        for entry in segment.get("frame_ocr") or []:
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            at = float(frame_seconds(entry.get("frame", ""), start))
            blocks.append({
                "id": "%sf%d" % (sid, len(blocks)),
                "seg": sid, "kind": "slide",
                "start": at, "end": at + 1,
                "text": text, "est": False,
                "frame": str(entry.get("frame", "")),
            })

        bullets = [b for b in (segment.get("bullets_zh") or []) if str(b).strip()]
        span = max(end - start, 0.001)
        for position, bullet in enumerate(bullets):
            text = bullet["text"] if isinstance(bullet, Mapping) else str(bullet)
            explicit = bullet.get("t") if isinstance(bullet, Mapping) else None
            if isinstance(explicit, (int, float)):
                block_start = float(explicit)
                block_end = min(block_start + span / max(len(bullets), 1), end)
                estimated = False
            else:
                block_start = round(start + span * position / max(len(bullets), 1), 2)
                block_end = round(start + span * (position + 1) / max(len(bullets), 1), 2)
                estimated = True
            blocks.append({
                "id": "%sb%d" % (sid, position),
                "seg": sid, "kind": "summary",
                "start": block_start, "end": block_end,
                "text": text, "est": estimated,
            })

    for position, cue in enumerate(cues):
        owner = next(
            (s for s in segments if s["start"] <= cue.start < s["end"]), None
        )
        if owner is None and segments:
            owner = min(segments, key=lambda s: abs(s["start"] - cue.start))
        blocks.append({
            "id": "t%d" % position,
            "seg": owner["id"] if owner else "",
            "kind": "transcript",
            "start": cue.start, "end": cue.end,
            "text": cue.text, "est": False,
        })
    return segments, blocks


def estimated_block_count(blocks: Sequence[Mapping[str, Any]]) -> int:
    """How many blocks carry an interpolated rather than a real time."""
    return sum(1 for block in blocks if block.get("est"))


__all__ = [
    "KINDS",
    "VIDEO_EXT",
    "build_blocks",
    "estimated_block_count",
    "find_sibling",
    "parse_srt",
    "parse_srt_text",
    "read_subtitle_text",
    "segment_view",
]

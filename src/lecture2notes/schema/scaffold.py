"""A structurally valid v2 document whose every semantic field is a placeholder.

`l2n scaffold` exists because the previous answer to "where does the JSON come
from" was "write it by hand from the reference". A field run did that and was
rejected four times in a row on the shape -- key names, a missing key, two
counts -- before a single sentence of the actual lecture had been considered.
The shape is mechanical and belongs here; the semantics are not and stay with
the language model.

So this module writes the skeleton: equal-time segments, real timecodes, the
frames already captured merged in, and `<!-- ai-draft -->` in every field a
person or a model has to replace. The document carries ``"draft": true``, which
`l2n check json` reports as a warning and which relaxes the rules that are
about how much has been written -- a draft has not been written yet, and
failing it for that says nothing.

Removing ``"draft"`` is the act of claiming the document is finished, and from
that moment the full rules apply again.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.engines.base import parse_srt_text, read_subtitle_text
from lecture2notes.schema import condense
from lecture2notes.schema.builder import build_document, frame_pairs
from lecture2notes.schema.model import AI_DRAFT_MARK, DRAFT_KEY

#: What `l2n condense` writes beside a subtitle, and what a scaffolded segment
#: points its reader at.
CONDENSED_SUFFIX = ".condensed.txt"

#: The manifest `l2n frames` leaves, read when it is already there.
MANIFEST_SUFFIX = ".frames.json"

#: How many segments are cut when the caller does not say. Six is the middle of
#: the 3-to-10-minute band the segmentation reference asks for, over a talk of
#: the length this tool is usually pointed at; it is a starting point to move,
#: not a recommendation.
DEFAULT_SEGMENTS = 6

#: Where a segment says its share of the condensed transcript lives. Not a
#: schema v2 key: the validator ignores keys it does not know, and this one is
#: for the writer, who otherwise has to find the right minutes by hand.
TRANSCRIPT_KEY = "transcript_condensed"

#: Every placeholder carries this, so `check note`'s ai-draft count and a plain
#: grep both find the fields nobody has touched yet.
TITLE_DRAFT = "%s 第 %d 段的標題（這一段在講什麼，不是「第三部分」）"
SUMMARY_DRAFT = "%s 第 %d 段摘要，兩三句。"
BULLET_DRAFT = "%s 第 %d 段重點 %d"
OVERALL_DRAFT = "%s 全片摘要待填（100 至 500 字）。"
TAKEAWAY_DRAFT = "%s 全片重點 %d"

#: How many placeholder bullets each segment gets. The checker warns below two.
DRAFT_BULLETS = 2
#: How many placeholder takeaways the document gets. The checker wants six.
DRAFT_TAKEAWAYS = 6


def subtitle_duration(path: Path) -> float:
    """Where the last cue ends, which is the only duration an SRT knows."""
    cues = parse_srt_text(read_subtitle_text(Path(path)))
    if not cues:
        return 0.0
    return max(float(cue.end) for cue in cues)


def segment_starts(duration_sec: float, count: int) -> List[float]:
    """``count`` equal-time starts over ``duration_sec``, the first at zero.

    Equal time is not a claim about where the topic turns. It is a starting
    grid: moving a boundary is one edit, and inventing the whole array is not.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    if duration_sec <= 0:
        raise ValueError("duration_sec must be positive")
    span = float(duration_sec) / count
    return [float(int(round(index * span))) for index in range(count)]


def condensed_line_range(
    start_sec: float, end_sec: float, bucket_sec: int
) -> Tuple[int, int]:
    """The 1-based line range of ``<stem>.condensed.txt`` covering a segment.

    The condensed file is one line per bucket in time order, so a segment's
    lines are decided by arithmetic rather than by reading the file.
    """
    first = int(start_sec) // bucket_sec + 1
    last = max(int(math.ceil(float(end_sec) / bucket_sec)), first)
    return first, last


def transcript_reference(
    stem: str, start_sec: float, end_sec: float, bucket_sec: int
) -> str:
    """``talk.condensed.txt#L4-L9``: where to read this segment's transcript."""
    first, last = condensed_line_range(start_sec, end_sec, bucket_sec)
    return "%s%s#L%d-L%d" % (stem, CONDENSED_SUFFIX, first, last)


def _placeholder_segments(starts: Sequence[float]) -> List[Any]:
    return [
        (
            start,
            TITLE_DRAFT % (AI_DRAFT_MARK, number),
            SUMMARY_DRAFT % (AI_DRAFT_MARK, number),
            [
                BULLET_DRAFT % (AI_DRAFT_MARK, number, position)
                for position in range(1, DRAFT_BULLETS + 1)
            ],
        )
        for number, start in enumerate(starts, 1)
    ]


def build_scaffold(
    stem: str,
    duration_sec: float,
    segments: int = DEFAULT_SEGMENTS,
    frames: Optional[Sequence[Tuple[float, str]]] = None,
    bucket_sec: int = condense.DEFAULT_BUCKET_SEC,
    subtitle_name: Optional[str] = None,
    profile: str = "generic",
) -> Dict[str, Any]:
    """The draft document, as a plain dict. Pure: same input, same bytes."""
    starts = segment_starts(duration_sec, segments)
    document = build_document(
        stem=stem,
        overall_summary=OVERALL_DRAFT % AI_DRAFT_MARK,
        takeaways=[
            TAKEAWAY_DRAFT % (AI_DRAFT_MARK, number)
            for number in range(1, DRAFT_TAKEAWAYS + 1)
        ],
        segments=_placeholder_segments(starts),
        duration_sec=float(int(duration_sec)),
        frames=list(frames or []),
        title="%s %s" % (AI_DRAFT_MARK, stem),
        source={"subtitle": {"path": subtitle_name or "%s.srt" % stem}},
        profile=profile,
    )
    # Written after `build_document` rather than through it: `draft` and the
    # transcript pointer are this stage's own, and the builder stays the one
    # thing that decides the v2 shape.
    document[DRAFT_KEY] = True
    for segment in document["segments"]:
        segment[TRANSCRIPT_KEY] = transcript_reference(
            stem, segment["start_sec"], segment["end_sec"], bucket_sec
        )
    return document


def manifest_frames(folder: Path, stem: str) -> List[Tuple[float, str]]:
    """The frames `l2n frames` already captured, or an empty list."""
    from lecture2notes.frames.manifest import read_manifest

    path = Path(folder) / (stem + MANIFEST_SUFFIX)
    if not path.is_file():
        return []
    return frame_pairs(read_manifest(path))


class ScaffoldResult:
    """What one scaffold run produced, in things a caller can assert on."""

    def __init__(
        self,
        document_path: Path,
        condensed_path: Path,
        segments: int,
        duration_sec: float,
        frames: int,
    ) -> None:
        self.document_path = document_path
        self.condensed_path = condensed_path
        self.segments = segments
        self.duration_sec = duration_sec
        self.frames = frames


def scaffold_file(
    subtitle: Path,
    segments: int = DEFAULT_SEGMENTS,
    bucket_sec: int = condense.DEFAULT_BUCKET_SEC,
    destination: Optional[Path] = None,
    profile: str = "generic",
) -> ScaffoldResult:
    """Write ``<stem>.json`` and ``<stem>.condensed.txt`` beside ``subtitle``."""
    from lecture2notes.schema.io import write_json_atomic

    subtitle = Path(subtitle)
    stem = subtitle.stem
    folder = subtitle.parent
    duration = subtitle_duration(subtitle)
    if duration <= 0:
        raise ValueError(
            "%s has no cues, so there is nothing to cut into segments"
            % subtitle.name
        )
    condensed = condense.condense_file(
        subtitle, folder / (stem + CONDENSED_SUFFIX), bucket_sec
    )
    frames = manifest_frames(folder, stem)
    document = build_scaffold(
        stem=stem,
        duration_sec=math.ceil(duration),
        segments=segments,
        frames=frames,
        bucket_sec=bucket_sec,
        subtitle_name=subtitle.name,
        profile=profile,
    )
    target = Path(destination) if destination else folder / ("%s.json" % stem)
    write_json_atomic(target, document)
    return ScaffoldResult(
        document_path=target,
        condensed_path=condensed,
        segments=segments,
        duration_sec=float(int(math.ceil(duration))),
        frames=len(frames),
    )


def is_draft(data: Mapping[str, Any]) -> bool:
    """Deprecated alias; the rule lives with the validator that applies it."""
    from lecture2notes.schema.model import is_draft as _is_draft

    return _is_draft(data)


__all__ = [
    "AI_DRAFT_MARK",
    "BULLET_DRAFT",
    "CONDENSED_SUFFIX",
    "DEFAULT_SEGMENTS",
    "DRAFT_BULLETS",
    "DRAFT_TAKEAWAYS",
    "MANIFEST_SUFFIX",
    "OVERALL_DRAFT",
    "ScaffoldResult",
    "SUMMARY_DRAFT",
    "TAKEAWAY_DRAFT",
    "TITLE_DRAFT",
    "TRANSCRIPT_KEY",
    "build_scaffold",
    "condensed_line_range",
    "is_draft",
    "manifest_frames",
    "scaffold_file",
    "segment_starts",
    "subtitle_duration",
    "transcript_reference",
]

"""Build a canonical lecture document from segment decisions.

Consolidated from a segment-builder procedure script written for this project.
The original took a folder, shelled out to ffprobe for the duration, read the
frame manifest from beside the video and wrote the JSON in one call. That is
convenient and untestable, so the two halves are split: :func:`build_document`
is pure and takes everything it needs as arguments, and :func:`build` is the
thin file-system wrapper around it.

The input shape is deliberately minimal: a list of ``(start_sec, title, summary,
bullets)``. End times are derived, never supplied, which is what guarantees the
segments are contiguous instead of merely claiming to be.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from lecture2notes import _deps, _out
from lecture2notes.frames.manifest import read_manifest
from lecture2notes.schema.model import write_json_atomic

VIDEO_EXT = (".mp4", ".m4a", ".webm", ".mov", ".mkv")
#: Used when the media duration cannot be read: the last segment still needs an end.
TAIL_PADDING_SEC = 300

#: ``(start_sec, title, summary_zh, bullets)``
SegmentInput = Tuple[float, str, str, Sequence[str]]


def clock(sec: float) -> str:
    total = int(sec)
    return "%02d:%02d:%02d" % (total // 3600, total % 3600 // 60, total % 60)


def probe_duration(folder: Path, stem: str) -> float:
    """Media duration for ``<stem>.<ext>`` in ``folder``, or 0.0 if unreadable."""
    ffprobe = _deps.require_ffprobe()
    for ext in VIDEO_EXT:
        path = Path(folder) / (stem + ext)
        if not path.exists():
            continue
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            return float(result.stdout.strip())
        except (TypeError, ValueError):
            continue
    return 0.0


def frame_pairs(manifest_rows: Iterable[Mapping[str, Any]]) -> List[Tuple[float, str]]:
    """``(timestamp_sec, frame_path)`` in time order."""
    return sorted(
        (float(row["timestamp_sec"]), str(row["frame"]))
        for row in manifest_rows
        if row.get("timestamp_sec") is not None and row.get("frame")
    )


def build_document(
    stem: str,
    overall_summary: str,
    takeaways: Sequence[str],
    segments: Sequence[SegmentInput],
    duration_sec: float,
    frames: Optional[Sequence[Tuple[float, str]]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the document. Pure: same input, same bytes.

    A segment ends where the next one starts, and the last one ends at the media
    duration, so ``end_sec`` and the next ``start_sec`` are equal by
    construction. A segment with no frame inside its own range inherits the last
    frame from before it, which is what was actually on screen; ``frames`` stays
    empty in that case so a check can still distinguish "nothing changed" from
    "capture never ran".
    """
    if not segments:
        raise ValueError("at least one segment is required")
    frames = list(frames or [])
    if duration_sec <= 0:
        duration_sec = float(segments[-1][0]) + TAIL_PADDING_SEC

    built: List[Dict[str, Any]] = []
    for index, (start, seg_title, summary, bullets) in enumerate(segments, 1):
        end = segments[index][0] if index < len(segments) else duration_sec
        inside = [path for sec, path in frames if float(start) <= sec < float(end)]
        if inside:
            frame = inside[0]
        else:
            prior = [path for sec, path in frames if sec < float(start)]
            frame = prior[-1] if prior else (frames[0][1] if frames else None)
        built.append({
            "index": index,
            "start_time": clock(start),
            "end_time": clock(end),
            "start_sec": int(start),
            "end_sec": int(end),
            "title": seg_title,
            "summary_zh": summary,
            "bullets_zh": list(bullets),
            "frame": frame,
            # ``frames`` lists only what actually falls inside the segment, so an
            # inherited lead frame never makes it look as if capture found
            # something here.
            "frames": inside,
        })

    return {
        "stem": stem,
        "title": title or stem,
        "duration_sec": int(duration_sec),
        "overall_summary_zh": overall_summary,
        "takeaways_zh": list(takeaways),
        "segments": built,
    }


def contiguous(document: Mapping[str, Any]) -> bool:
    """Does every segment end exactly where the next one starts?"""
    segments = document.get("segments") or []
    return all(
        segments[i]["end_sec"] == segments[i + 1]["start_sec"]
        for i in range(len(segments) - 1)
    )


def build(
    folder: Path,
    stem: str,
    overall_summary: str,
    takeaways: Sequence[str],
    segments: Sequence[SegmentInput],
    duration_sec: Optional[float] = None,
    manifest_path: Optional[Path] = None,
    title: Optional[str] = None,
) -> Path:
    """Write ``<folder>/<stem>.json``, merging the frame manifest if there is one."""
    folder = Path(folder)
    if duration_sec is None:
        duration_sec = probe_duration(folder, stem)
    manifest = Path(manifest_path) if manifest_path else folder / ("%s.frames.json" % stem)
    frames = frame_pairs(read_manifest(manifest)) if manifest.exists() else []

    document = build_document(
        stem, overall_summary, takeaways, segments, float(duration_sec), frames, title
    )
    destination = write_json_atomic(folder / ("%s.json" % stem), document)
    _out.ok(
        "%s: %d segments, %d min, summary %d chars, %d takeaways"
        % (stem, len(document["segments"]), round(duration_sec / 60),
           len(overall_summary), len(takeaways))
    )
    if frames:
        with_frame = sum(1 for s in document["segments"] if s["frames"])
        _out.say("info", "%d frames merged into %d/%d segments"
                 % (len(frames), with_frame, len(document["segments"])))
    _out.say("info", "contiguous: %s" % ("yes" if contiguous(document) else "NO, gaps found"))
    return destination


__all__ = [
    "SegmentInput",
    "TAIL_PADDING_SEC",
    "VIDEO_EXT",
    "build",
    "build_document",
    "clock",
    "contiguous",
    "frame_pairs",
    "probe_duration",
]

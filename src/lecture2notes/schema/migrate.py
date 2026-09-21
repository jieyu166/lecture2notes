"""Upgrade a 1.x lecture document to canonical schema v2.

A 1.x document has no ``schema_version``. It is not wrong, it is simply older:
bullets are bare strings with no time and no kind, there is no record of which
subtitle or engine produced it, and the correction table lives beside the file
instead of inside it. Rather than teaching every reader both shapes forever,
the shapes are collapsed here once, in one direction, with the original kept
next to it as ``<file>.bak``.

Nothing is thrown away. Keys this schema does not know about are carried over
unchanged after the canonical ones, so a migration is never a silent deletion.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from lecture2notes.schema.io import write_json_atomic
from lecture2notes.schema.model import (
    SCHEMA_VERSION,
    Bullet,
    FrameOcr,
    Quote,
    Source,
    finite_number,
    format_clock,
    is_legacy,
)

#: Written into ``source`` when a legacy document says nothing about its media.
DEFAULT_PROFILE = "generic"
DEFAULT_VIDEO_EXT = ".mp4"
DEFAULT_SUBTITLE_EXT = ".srt"

#: Canonical top-level key order; anything else follows in its original order.
TOP_LEVEL_ORDER = (
    "schema_version",
    "stem",
    "title",
    "duration_sec",
    "source",
    "profile",
    "overall_summary_zh",
    "takeaways_zh",
    "segments",
    "corrections",
    "unverified_terms",
)

#: Canonical per-segment key order.
SEGMENT_ORDER = (
    "index",
    "start_time",
    "end_time",
    "start_sec",
    "end_sec",
    "title",
    "summary_zh",
    "bullets_zh",
    "quotes_zh",
    "frame",
    "frames",
    "frame_ocr",
    "editorial_notes_zh",
)


@dataclass
class MigrationResult:
    """What :func:`migrate_file` did."""

    path: Path
    backup: Optional[Path]
    changed: bool
    segments: int = 0

    @property
    def summary(self) -> str:
        if not self.changed:
            return "%s is already schema %s" % (self.path.name, SCHEMA_VERSION)
        return "%s upgraded to schema %s (%d segments), backup %s" % (
            self.path.name, SCHEMA_VERSION, self.segments,
            self.backup.name if self.backup else "-",
        )


def _ordered(payload: Dict[str, Any], order: Any) -> Dict[str, Any]:
    """``payload`` with the known keys first, then whatever else it carries."""
    result: Dict[str, Any] = {}
    for key in order:
        if key in payload:
            result[key] = payload.pop(key)
    result.update(payload)
    return result


def _legacy_frame_entries(
    segment: Mapping[str, Any], top_level_ocr: Mapping[str, Any]
) -> Any:
    """``(frame paths, frame_ocr entries)`` from either 1.x frame spelling.

    1.x wrote frames either as plain relative paths, with the OCR text in a
    top-level ``frame_ocr`` map, or as objects carrying their own ``ocr``. Both
    collapse to v2's paths-plus-``frame_ocr``-array.
    """
    paths: List[str] = []
    ocr: List[Dict[str, Any]] = []
    seen = set()

    existing = segment.get("frame_ocr")
    if isinstance(existing, list):
        for item in existing:
            entry = FrameOcr.from_any(item).to_dict()
            if entry["frame"] and entry["frame"] not in seen:
                seen.add(entry["frame"])
                ocr.append(entry)
    elif isinstance(existing, Mapping):
        for name, text in existing.items():
            if str(name) not in seen:
                seen.add(str(name))
                ocr.append({"frame": str(name), "text": str(text or "")})

    for frame in segment.get("frames") or []:
        if isinstance(frame, Mapping):
            path = str(frame.get("path") or "").replace("\\", "/")
            text = frame.get("ocr")
        else:
            path = str(frame).replace("\\", "/")
            text = top_level_ocr.get(str(frame))
        if not path:
            continue
        paths.append(path)
        if path not in seen and text:
            seen.add(path)
            ocr.append({"frame": path, "text": str(text)})

    return paths, ocr


def _migrate_segment(
    segment: Mapping[str, Any], position: int, top_level_ocr: Mapping[str, Any]
) -> Dict[str, Any]:
    payload = deepcopy(dict(segment))

    start = finite_number(payload.pop("start", payload.get("start_sec")))
    end = finite_number(payload.pop("end", payload.get("end_sec")))
    start = 0.0 if start is None else start
    end = start if end is None else end
    payload["start_sec"] = start
    payload["end_sec"] = end
    payload["index"] = position
    payload["start_time"] = format_clock(start)
    payload["end_time"] = format_clock(end)
    payload["title"] = str(payload.get("title") or "")
    payload["summary_zh"] = str(payload.get("summary_zh") or "")

    # 1.x normalisation renamed bullets_zh to takeaways_zh inside a segment;
    # v2 puts takeaways back at the top level only, so the segment list is
    # always bullets_zh whichever spelling it arrived under.
    bullets = payload.pop("bullets_zh", None)
    legacy_bullets = payload.pop("takeaways_zh", None)
    if not isinstance(bullets, list):
        bullets = legacy_bullets if isinstance(legacy_bullets, list) else []
    payload["bullets_zh"] = [Bullet.from_any(item).to_dict() for item in bullets]

    quotes = payload.get("quotes_zh")
    payload["quotes_zh"] = [
        Quote.from_any(item).to_dict() for item in (quotes if isinstance(quotes, list) else [])
    ]

    paths, ocr = _legacy_frame_entries(payload, top_level_ocr)
    payload["frames"] = paths
    payload["frame_ocr"] = ocr
    frame = payload.get("frame")
    if isinstance(frame, Mapping):
        frame = frame.get("path")
    if isinstance(frame, str) and frame:
        payload["frame"] = frame.replace("\\", "/")
    else:
        payload["frame"] = paths[0] if paths else None

    notes = payload.get("editorial_notes_zh")
    payload["editorial_notes_zh"] = [
        str(item) for item in (notes if isinstance(notes, list) else [])
    ]

    return _ordered(payload, SEGMENT_ORDER)


def normalize_legacy(data: Mapping[str, Any], stem: str = "") -> Dict[str, Any]:
    """Return ``data`` as a schema v2 document, filling in the 1.x gaps."""
    payload = deepcopy(dict(data))
    resolved_stem = str(payload.get("stem") or stem or "")
    payload["schema_version"] = SCHEMA_VERSION
    payload["stem"] = resolved_stem
    if not payload.get("title"):
        payload["title"] = resolved_stem
    if not payload.get("profile"):
        payload["profile"] = DEFAULT_PROFILE

    top_level_ocr = payload.pop("frame_ocr", None)
    top_level_ocr = top_level_ocr if isinstance(top_level_ocr, Mapping) else {}

    segments = payload.get("segments")
    segments = segments if isinstance(segments, list) else []
    payload["segments"] = [
        _migrate_segment(segment if isinstance(segment, Mapping) else {}, position, top_level_ocr)
        for position, segment in enumerate(segments, 1)
    ]

    duration = finite_number(payload.get("duration_sec"))
    if duration is None or duration <= 0:
        duration = float(payload["segments"][-1]["end_sec"]) if payload["segments"] else 0.0
    payload["duration_sec"] = duration

    payload["source"] = Source.from_any(payload.get("source"), resolved_stem).to_dict()
    if not payload["source"].get("video"):
        payload["source"]["video"] = resolved_stem + DEFAULT_VIDEO_EXT
    if not payload["source"]["subtitle"].get("path"):
        payload["source"]["subtitle"]["path"] = resolved_stem + DEFAULT_SUBTITLE_EXT

    takeaways = payload.get("takeaways_zh")
    payload["takeaways_zh"] = [
        str(item) for item in (takeaways if isinstance(takeaways, list) else [])
    ]

    corrections = payload.get("corrections")
    payload["corrections"] = [
        dict(item) for item in (corrections if isinstance(corrections, list) else [])
        if isinstance(item, Mapping)
    ]
    terms = payload.get("unverified_terms")
    payload["unverified_terms"] = [
        str(item) for item in (terms if isinstance(terms, list) else [])
    ]

    return _ordered(payload, TOP_LEVEL_ORDER)


def migrate_file(path: Union[str, Path]) -> MigrationResult:
    """Upgrade ``path`` in place, leaving the original bytes at ``<path>.bak``.

    A document that is already v2 is left untouched and no backup is written,
    so running this twice is not a way to lose the previous backup.
    """
    target = Path(path)
    raw = target.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    if not is_legacy(data):
        return MigrationResult(target, None, False, len(data.get("segments") or []))

    backup = target.with_name(target.name + ".bak")
    backup.write_bytes(raw)
    upgraded = normalize_legacy(data, stem=target.stem)
    write_json_atomic(target, upgraded)
    return MigrationResult(target, backup, True, len(upgraded.get("segments") or []))


__all__ = [
    "DEFAULT_PROFILE",
    "MigrationResult",
    "SEGMENT_ORDER",
    "TOP_LEVEL_ORDER",
    "migrate_file",
    "normalize_legacy",
]

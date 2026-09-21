"""Canonical lecture document: data structures, normalisation and validation.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/lecture_model.py
(the worktree copy, which is the more complete of the two).

This is the ported baseline. The schema v2 work replaces the counts and the key
set; everything that is a policy number rather than a structural rule is a module
constant here so that replacement is an edit in one place.

Two invariants are worth naming because breaking them is silent:

* Writes are atomic and BOM-free. A partially written canonical JSON is
  indistinguishable from a complete one, and a BOM makes the browser-side player
  fail to parse a file that looks fine in an editor.
* Times are never rewritten by a content pass. ``assert_times_unchanged`` is what
  a rewrite step runs before it is allowed to replace the source.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Mapping, Optional, Tuple

#: How many frames a segment is expected to carry.
MIN_FRAMES_PER_SEGMENT = 1
MAX_FRAMES_PER_SEGMENT = 4
#: How many takeaways a segment is expected to carry.
REQUIRED_TAKEAWAYS = 4
#: How far outside its segment a frame's timestamp may sit.
DEFAULT_FRAME_TOLERANCE = 0.25


@dataclass(frozen=True)
class Finding:
    """One machine-checkable problem, at a known severity and location."""

    severity: str
    code: str
    message: str
    segment_index: Optional[int] = None
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "segment_index": self.segment_index,
            "path": self.path,
        }


def _frame_time(path: str) -> Optional[float]:
    stem = Path(path).stem
    marker = stem.rsplit("_", 1)[-1]
    try:
        return float(marker.replace("-", "."))
    except ValueError:
        return None


def segment_start(segment: Mapping[str, Any]) -> float:
    if "start_sec" in segment:
        return float(segment["start_sec"])
    return float(segment["start"])


def segment_end(segment: Mapping[str, Any]) -> float:
    if "end_sec" in segment:
        return float(segment["end_sec"])
    return float(segment["end"])


def normalize_lecture(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Bring an older document onto the canonical key set, without loss.

    Frames become objects carrying their own time and OCR text, top-level and
    per-segment OCR maps are folded into them, and ``start``/``end`` become
    ``start_sec``/``end_sec``.
    """
    result = json.loads(json.dumps(data, ensure_ascii=False))
    top_level_ocr = result.pop("frame_ocr", {}) or {}
    if not isinstance(top_level_ocr, Mapping):
        top_level_ocr = {}

    segments = result.get("segments", [])
    if not isinstance(segments, list):
        return result

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment["start_sec"] = segment_start(segment)
        segment["end_sec"] = segment_end(segment)
        segment.pop("start", None)
        segment.pop("end", None)

        if "takeaways_zh" not in segment:
            bullets = segment.pop("bullets_zh", [])
            segment["takeaways_zh"] = list(bullets) if isinstance(bullets, list) else bullets
        else:
            segment.pop("bullets_zh", None)
        segment.setdefault("editorial_notes_zh", [])

        segment_ocr: Dict[str, Any] = {}
        legacy_segment_ocr = segment.pop("frame_ocr", []) or []
        if isinstance(legacy_segment_ocr, list):
            for item in legacy_segment_ocr:
                if not isinstance(item, Mapping):
                    continue
                frame_path = item.get("frame")
                if isinstance(frame_path, str):
                    segment_ocr[frame_path] = item.get("text", "")

        frames = segment.get("frames", [])
        if not isinstance(frames, list):
            segment["frames"] = frames
            continue

        normalized_frames: List[Any] = []
        for frame in frames:
            if isinstance(frame, str):
                normalized_path = frame.replace("\\", "/")
                normalized_frames.append({
                    "time": _frame_time(normalized_path),
                    "ocr": segment_ocr.get(frame, top_level_ocr.get(frame, "")),
                    "path": normalized_path,
                })
            elif isinstance(frame, Mapping):
                raw_path = frame.get("path", "")
                normalized_path = (
                    raw_path.replace("\\", "/") if isinstance(raw_path, str) else raw_path
                )
                normalized_frame: Dict[str, Any] = {
                    "time": frame.get("time"),
                    "path": normalized_path,
                }
                if "ocr" in frame:
                    normalized_frame["ocr"] = frame["ocr"]
                normalized_frames.append(normalized_frame)
            else:
                normalized_frames.append(frame)
        segment["frames"] = normalized_frames

    return result


def time_signature(data: Mapping[str, Any]) -> Tuple[Tuple[float, float], ...]:
    """Every segment's ``(start, end)``; the fingerprint a rewrite must preserve."""
    segments = data.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")
    return tuple((segment_start(segment), segment_end(segment)) for segment in segments)


def _segment_label(segment: Any, fallback_index: int) -> Any:
    if isinstance(segment, Mapping) and "index" in segment:
        return segment["index"]
    return fallback_index


def _same_json_scalar(old_value: Any, new_value: Any) -> bool:
    return type(old_value) is type(new_value) and old_value == new_value


def _segment_time_value(segment: Mapping[str, Any], canonical: str, alias: str) -> Any:
    if canonical in segment:
        return segment[canonical]
    return segment[alias]


def assert_times_unchanged(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    """Raise unless every segment index and time is byte-identical.

    Type is compared as well as value: ``1200`` becoming ``1200.0`` is a change
    that would show up as a diff everywhere downstream.
    """
    old_segments = before.get("segments", [])
    new_segments = after.get("segments", [])
    if not isinstance(old_segments, list) or not isinstance(new_segments, list):
        raise ValueError("segments must be lists")
    if len(old_segments) != len(new_segments):
        raise ValueError(
            "segment count changed: %d != %d" % (len(old_segments), len(new_segments))
        )

    for position, (old_segment, new_segment) in enumerate(zip(old_segments, new_segments)):
        label = _segment_label(old_segment, position)
        if not isinstance(old_segment, Mapping) or not isinstance(new_segment, Mapping):
            raise ValueError("segment %s must remain an object" % label)

        old_has_index = "index" in old_segment
        new_has_index = "index" in new_segment
        old_index = old_segment.get("index")
        new_index = new_segment.get("index")
        if old_has_index != new_has_index or (
            old_has_index and not _same_json_scalar(old_index, new_index)
        ):
            raise ValueError(
                "segment %s index changed: %r != %r" % (label, old_index, new_index)
            )

        try:
            old_start = _segment_time_value(old_segment, "start_sec", "start")
            new_start = _segment_time_value(new_segment, "start_sec", "start")
            old_end = _segment_time_value(old_segment, "end_sec", "end")
            new_end = _segment_time_value(new_segment, "end_sec", "end")
        except KeyError as exc:
            raise ValueError("segment %s has invalid time data" % label) from exc

        if not _same_json_scalar(old_start, new_start):
            raise ValueError(
                "segment %s time changed: start changed from %r to %r"
                % (label, old_start, new_start)
            )
        if not _same_json_scalar(old_end, new_end):
            raise ValueError(
                "segment %s time changed: end changed from %r to %r"
                % (label, old_end, new_end)
            )


def safe_relative_frame_path(value: Any) -> Optional[PurePosixPath]:
    """Only a relative, non-escaping path may name a frame."""
    if not isinstance(value, str):
        return None
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        not normalized
        or any(ord(character) < 32 for character in normalized)
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
    ):
        return None
    return posix


def finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def path_within_base(base_dir: Path, relative_path: PurePosixPath) -> Optional[Path]:
    base = Path(base_dir).resolve()
    candidate = (base / Path(*relative_path.parts)).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def validate_lecture_schema(
    data: Mapping[str, Any],
    base_dir: Path,
    frame_tolerance_seconds: float = DEFAULT_FRAME_TOLERANCE,
    required_takeaways: int = REQUIRED_TAKEAWAYS,
    min_frames: int = MIN_FRAMES_PER_SEGMENT,
    max_frames: int = MAX_FRAMES_PER_SEGMENT,
) -> List[Finding]:
    """Structural validation of a normalised document, including frame existence."""
    findings: List[Finding] = []
    base_dir = Path(base_dir)
    tolerance = finite_number(frame_tolerance_seconds)
    if tolerance is None or tolerance < 0:
        raise ValueError("frame_tolerance_seconds must be finite and non-negative")

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return [Finding("error", "segments_missing", "segments must be a non-empty list")]

    for position, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            findings.append(
                Finding("error", "segment_type", "segment must be an object", position)
            )
            continue

        start = finite_number(segment.get("start_sec", segment.get("start")))
        end = finite_number(segment.get("end_sec", segment.get("end")))
        valid_range = start is not None and end is not None and start >= 0.0 and end > start
        if not valid_range:
            findings.append(
                Finding("error", "time_range", "invalid segment time range", position)
            )

        if not isinstance(segment.get("title"), str):
            findings.append(
                Finding("error", "title_type", "title must be a string", position)
            )
        if not isinstance(segment.get("summary_zh"), str):
            findings.append(
                Finding("error", "summary_type", "summary_zh must be a string", position)
            )

        takeaways = segment.get("takeaways_zh")
        if not isinstance(takeaways, list) or len(takeaways) != required_takeaways:
            findings.append(Finding(
                "error", "takeaway_count",
                "takeaways_zh must contain exactly %d items" % required_takeaways,
                position,
            ))
        elif not all(isinstance(item, str) for item in takeaways):
            findings.append(Finding(
                "error", "takeaway_type", "takeaways_zh must be a string list", position
            ))

        editorial = segment.get("editorial_notes_zh")
        if not isinstance(editorial, list) or not all(
            isinstance(item, str) for item in editorial
        ):
            findings.append(Finding(
                "error", "editorial_type",
                "editorial_notes_zh must be a string list", position,
            ))

        frames = segment.get("frames")
        if not isinstance(frames, list) or not min_frames <= len(frames) <= max_frames:
            findings.append(Finding(
                "error", "frame_count",
                "frames must contain %d to %d items" % (min_frames, max_frames),
                position,
            ))
            if not isinstance(frames, list):
                continue

        for frame in frames:
            if not isinstance(frame, Mapping):
                findings.append(
                    Finding("error", "frame_type", "frame must be an object", position)
                )
                continue

            raw_path = frame.get("path", "")
            finding_path = raw_path if isinstance(raw_path, str) else str(raw_path)
            pure = safe_relative_frame_path(raw_path)
            candidate = path_within_base(base_dir, pure) if pure is not None else None
            if pure is None or candidate is None:
                findings.append(Finding(
                    "error", "frame_path", "frame path must be a safe relative path",
                    position, finding_path,
                ))

            timestamp = finite_number(frame.get("time"))
            if timestamp is None or timestamp < 0.0 or (
                valid_range and (timestamp < start - tolerance or timestamp > end + tolerance)
            ):
                findings.append(Finding(
                    "error", "frame_time",
                    "frame timestamp must be finite, non-negative, and inside segment bounds",
                    position, finding_path,
                ))

            if "ocr" not in frame:
                findings.append(Finding(
                    "error", "frame_ocr_missing", "frame ocr key is required",
                    position, finding_path,
                ))
            elif not isinstance(frame["ocr"], str):
                findings.append(Finding(
                    "error", "frame_ocr_type", "frame ocr must be a string",
                    position, finding_path,
                ))

            if candidate is not None and not candidate.is_file():
                findings.append(Finding(
                    "error", "frame_missing", "frame file does not exist",
                    position, finding_path,
                ))

    return findings


def load_lecture(path: Path) -> Dict[str, Any]:
    """Read and normalise a canonical document, tolerating a stray BOM on input."""
    return normalize_lecture(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def write_json_atomic(path: Path, data: Mapping[str, Any]) -> Path:
    """Write UTF-8 without BOM, LF only, through a same-directory temp file.

    The temp file is re-read and parsed before the rename, so a truncated or
    non-serialisable write can never replace a good file.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".%s." % destination.name, suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        json.loads(Path(temp_name).read_text(encoding="utf-8"))
        os.replace(temp_name, destination)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return destination


__all__ = [
    "DEFAULT_FRAME_TOLERANCE",
    "Finding",
    "MAX_FRAMES_PER_SEGMENT",
    "MIN_FRAMES_PER_SEGMENT",
    "REQUIRED_TAKEAWAYS",
    "assert_times_unchanged",
    "finite_number",
    "load_lecture",
    "normalize_lecture",
    "path_within_base",
    "safe_relative_frame_path",
    "segment_end",
    "segment_start",
    "time_signature",
    "validate_lecture_schema",
    "write_json_atomic",
]

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
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes.schema.io import read_json
from lecture2notes.schema.io import write_json_atomic as _write_json_atomic

#: How many frames a segment is expected to carry.
MIN_FRAMES_PER_SEGMENT = 1
MAX_FRAMES_PER_SEGMENT = 4
#: How many takeaways a segment is expected to carry.
REQUIRED_TAKEAWAYS = 4
#: How far outside its segment a frame's timestamp may sit.
DEFAULT_FRAME_TOLERANCE = 0.25


@dataclass(frozen=True)
class Finding:
    """One machine-checkable problem, at a known severity and location.

    ``location`` is the JSON path inside the document (``segments[3].index``)
    when there is one; the stage report falls back to the target's name when it
    is absent, so every printed line has the three fields the acceptance
    contract requires.
    """

    severity: str
    code: str
    message: str
    segment_index: Optional[int] = None
    path: Optional[str] = None
    location: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "segment_index": self.segment_index,
            "path": self.path,
            "location": self.location,
        }

    def line(self, default_location: str = "-") -> str:
        """``<severity> <rule-or-field> <location>: <message>``."""
        return "%s %s %s: %s" % (
            self.severity, self.code, self.location or default_location, self.message
        )


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
        # An empty `frames` beside a non-null `frame` is the inherited-frame
        # shape `merge_frames_into_segments` writes: nothing changed on screen
        # during this segment, so it shows the last slide from before it began.
        # Requiring one frame per segment here made the number of detected
        # scenes an upper bound on the number of segments, which is backwards:
        # segments come from where the topic turns, not from where the slide
        # deck happens to advance.
        inherited = isinstance(frames, list) and not frames and bool(
            isinstance(segment.get("frame"), str) and segment.get("frame")
        )
        floor = 0 if inherited else min_frames
        if not isinstance(frames, list) or not floor <= len(frames) <= max_frames:
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
    return normalize_lecture(read_json(path))


def write_json_atomic(path: Path, data: Mapping[str, Any]) -> Path:
    """Deprecated alias for :func:`lecture2notes.schema.io.write_json_atomic`.

    The implementation moved to ``schema/io.py`` so that every JSON write in the
    package shares one set of rules; this alias keeps the historical import site
    working.
    """
    return _write_json_atomic(path, data)


# ==========================================================================
# schema v2
# ==========================================================================
# Everything above this line is the ported 1.x shape, still used by the audit
# and rebuild paths. Everything below is schema v2: the documented, versioned
# contract that ``l2n check json`` enforces and that ``l2n migrate`` produces.

#: The only version this package writes, and the only one the validator accepts.
SCHEMA_VERSION = "2.0"

#: Inclusive bounds on the overall summary, in characters.
MIN_SUMMARY_CHARS = 100
MAX_SUMMARY_CHARS = 500
#: Inclusive bounds on the top-level takeaway count.
MIN_TAKEAWAYS = 6
MAX_TAKEAWAYS = 12
#: A bullet is either the writer's own synthesis or a marked quotation.
BULLET_KINDS = ("synthesis", "quote")
#: Where a subtitle came from: our own ASR, or one shipped with the talk.
SUBTITLE_ORIGINS = ("asr", "official")
#: Slack allowed when comparing two segment boundaries, to absorb float noise.
BOUNDARY_EPSILON = 1e-6


def format_clock(seconds: float) -> str:
    """``HH:MM:SS`` for a number of seconds; the fractional part is dropped."""
    total = int(seconds)
    return "%02d:%02d:%02d" % (total // 3600, total % 3600 // 60, total % 60)


def parse_clock(value: Any) -> Optional[float]:
    """``MM:SS`` or ``HH:MM:SS``, optionally with a fractional second."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    total = 0.0
    for number in numbers:
        total = total * 60 + number
    return total


def is_legacy(data: Any) -> bool:
    """A document without ``schema_version`` is 1.x and must be migrated."""
    return not isinstance(data, Mapping) or "schema_version" not in data


# -- dataclasses -----------------------------------------------------------
# These are the in-memory shape of a v2 document. They are plain dataclasses
# rather than a validation library on purpose: the validator below has to report
# *every* problem in an arbitrary dict together with its location, which is not
# what a parse-or-raise model gives you, and a runtime dependency is not worth
# one constructor.

@dataclass
class Bullet:
    """One summary point. ``t`` is when it was said, or None if synthesised."""

    text: str
    t: Optional[float] = None
    kind: str = "synthesis"

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "t": self.t, "kind": self.kind}

    @classmethod
    def from_any(cls, value: Any) -> "Bullet":
        """Accept a v2 object or a 1.x bare string."""
        if isinstance(value, Mapping):
            return cls(
                text=str(value.get("text", "")),
                t=finite_number(value.get("t")),
                kind=str(value.get("kind") or "synthesis"),
            )
        return cls(text=str(value), t=None, kind="synthesis")


@dataclass
class Quote:
    """A speaker's own words, kept verbatim, with the time they were said."""

    text: str
    t: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "t": self.t}

    @classmethod
    def from_any(cls, value: Any) -> "Quote":
        if isinstance(value, Mapping):
            return cls(text=str(value.get("text", "")), t=finite_number(value.get("t")))
        return cls(text=str(value), t=None)


@dataclass
class FrameOcr:
    """The text OCR found on one frame."""

    frame: str
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"frame": self.frame, "text": self.text}

    @classmethod
    def from_any(cls, value: Any) -> "FrameOcr":
        if isinstance(value, Mapping):
            return cls(frame=str(value.get("frame", "")), text=str(value.get("text", "")))
        return cls(frame=str(value), text="")


@dataclass
class Subtitle:
    """Where the transcript came from, and how its clock was corrected."""

    path: str
    origin: str = "asr"
    engine: Optional[str] = None
    lang: Optional[str] = None
    #: ``{"a": float, "b": float}`` for ``offset(t) = a + b*t``, or None.
    offset_model: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "origin": self.origin,
            "engine": self.engine,
            "lang": self.lang,
            "offset_model": dict(self.offset_model) if self.offset_model else None,
        }

    @classmethod
    def from_any(cls, value: Any, stem: str = "") -> "Subtitle":
        if not isinstance(value, Mapping):
            return cls(path="%s.srt" % stem)
        model = value.get("offset_model")
        return cls(
            path=str(value.get("path") or ("%s.srt" % stem)),
            origin=str(value.get("origin") or "asr"),
            engine=value.get("engine"),
            lang=value.get("lang"),
            offset_model=dict(model) if isinstance(model, Mapping) else None,
        )


@dataclass
class Source:
    """The media this document was derived from."""

    video: str
    subtitle: Subtitle

    def to_dict(self) -> Dict[str, Any]:
        return {"video": self.video, "subtitle": self.subtitle.to_dict()}

    @classmethod
    def from_any(cls, value: Any, stem: str = "") -> "Source":
        if not isinstance(value, Mapping):
            return cls(video="%s.mp4" % stem, subtitle=Subtitle.from_any(None, stem))
        return cls(
            video=str(value.get("video") or ("%s.mp4" % stem)),
            subtitle=Subtitle.from_any(value.get("subtitle"), stem),
        )


@dataclass
class Segment:
    """One contiguous span of the lecture."""

    index: int
    start_sec: float
    end_sec: float
    start_time: str
    end_time: str
    title: str
    summary_zh: str
    bullets_zh: List[Bullet] = field(default_factory=list)
    quotes_zh: List[Quote] = field(default_factory=list)
    frame: Optional[str] = None
    frames: List[str] = field(default_factory=list)
    frame_ocr: List[FrameOcr] = field(default_factory=list)
    editorial_notes_zh: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "title": self.title,
            "summary_zh": self.summary_zh,
            "bullets_zh": [bullet.to_dict() for bullet in self.bullets_zh],
            "quotes_zh": [quote.to_dict() for quote in self.quotes_zh],
            "frame": self.frame,
            "frames": list(self.frames),
            "frame_ocr": [entry.to_dict() for entry in self.frame_ocr],
            "editorial_notes_zh": list(self.editorial_notes_zh),
        }

    @classmethod
    def from_any(cls, value: Mapping[str, Any], index: int) -> "Segment":
        start = finite_number(value.get("start_sec", value.get("start"))) or 0.0
        end = finite_number(value.get("end_sec", value.get("end"))) or 0.0
        bullets = value.get("bullets_zh")
        if not isinstance(bullets, list):
            legacy = value.get("takeaways_zh")
            bullets = legacy if isinstance(legacy, list) else []
        return cls(
            index=int(value.get("index") or index),
            start_sec=start,
            end_sec=end,
            start_time=str(value.get("start_time") or format_clock(start)),
            end_time=str(value.get("end_time") or format_clock(end)),
            title=str(value.get("title") or ""),
            summary_zh=str(value.get("summary_zh") or ""),
            bullets_zh=[Bullet.from_any(item) for item in bullets],
            quotes_zh=[Quote.from_any(item) for item in (value.get("quotes_zh") or [])],
            frame=value.get("frame"),
            frames=[str(item) for item in (value.get("frames") or [])],
            frame_ocr=[FrameOcr.from_any(item) for item in (value.get("frame_ocr") or [])],
            editorial_notes_zh=[
                str(item) for item in (value.get("editorial_notes_zh") or [])
            ],
        )


@dataclass
class LectureDocument:
    """A whole canonical lecture document at schema version 2.0."""

    stem: str
    title: str
    duration_sec: float
    source: Source
    profile: str = "generic"
    overall_summary_zh: str = ""
    takeaways_zh: List[str] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    corrections: List[Dict[str, Any]] = field(default_factory=list)
    unverified_terms: List[str] = field(default_factory=list)
    ocr_meta: Optional[Dict[str, Any]] = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        """The canonical key order, which is what ends up on disk."""
        payload: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "stem": self.stem,
            "title": self.title,
            "duration_sec": self.duration_sec,
            "source": self.source.to_dict(),
            "profile": self.profile,
            "overall_summary_zh": self.overall_summary_zh,
            "takeaways_zh": list(self.takeaways_zh),
            "segments": [segment.to_dict() for segment in self.segments],
            "corrections": [dict(item) for item in self.corrections],
            "unverified_terms": list(self.unverified_terms),
        }
        if self.ocr_meta is not None:
            payload["ocr_meta"] = dict(self.ocr_meta)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], stem: str = "") -> "LectureDocument":
        """Build the dataclass from a dict. Lenient; validate separately."""
        resolved_stem = str(data.get("stem") or stem)
        segments = data.get("segments")
        segments = segments if isinstance(segments, list) else []
        ocr_meta = data.get("ocr_meta")
        return cls(
            stem=resolved_stem,
            title=str(data.get("title") or resolved_stem),
            duration_sec=finite_number(data.get("duration_sec")) or 0.0,
            source=Source.from_any(data.get("source"), resolved_stem),
            profile=str(data.get("profile") or "generic"),
            overall_summary_zh=str(data.get("overall_summary_zh") or ""),
            takeaways_zh=[str(item) for item in (data.get("takeaways_zh") or [])],
            segments=[
                Segment.from_any(item if isinstance(item, Mapping) else {}, position)
                for position, item in enumerate(segments, 1)
            ],
            corrections=[
                dict(item)
                for item in (data.get("corrections") or [])
                if isinstance(item, Mapping)
            ],
            unverified_terms=[str(item) for item in (data.get("unverified_terms") or [])],
            ocr_meta=dict(ocr_meta) if isinstance(ocr_meta, Mapping) else None,
            schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
        )


# -- validation ------------------------------------------------------------

def _error(
    code: str, location: str, message: str, segment_index: Optional[int] = None
) -> Finding:
    return Finding("error", code, message, segment_index, None, location)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_subtitle(subtitle: Any, findings: List[Finding]) -> None:
    where = "source.subtitle"
    if not isinstance(subtitle, Mapping):
        findings.append(_error("source", where, "source.subtitle must be an object"))
        return
    if not isinstance(subtitle.get("path"), str) or not subtitle.get("path"):
        findings.append(_error(
            "source", where + ".path", "source.subtitle.path must be a non-empty string"
        ))
    origin = subtitle.get("origin")
    if origin not in SUBTITLE_ORIGINS:
        findings.append(_error(
            "source", where + ".origin",
            "source.subtitle.origin must be one of %s, got %r"
            % ("/".join(SUBTITLE_ORIGINS), origin),
        ))
    for key in ("engine", "lang"):
        value = subtitle.get(key, None)
        if value is not None and not isinstance(value, str):
            findings.append(_error(
                "source", "%s.%s" % (where, key),
                "source.subtitle.%s must be a string or null" % key,
            ))
    model = subtitle.get("offset_model", None)
    if model is None:
        return
    if not isinstance(model, Mapping):
        findings.append(_error(
            "source", where + ".offset_model",
            "source.subtitle.offset_model must be an object {a, b} or null",
        ))
        return
    for key in ("a", "b"):
        if not _is_number(model.get(key)):
            findings.append(_error(
                "source", "%s.offset_model.%s" % (where, key),
                "source.subtitle.offset_model.%s must be a number" % key,
            ))


def _validate_top_level(data: Mapping[str, Any], findings: List[Finding]) -> None:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        findings.append(_error(
            "schema_version", "schema_version",
            "schema_version must be %r, got %r" % (SCHEMA_VERSION, version),
        ))

    for key in ("stem", "title", "profile"):
        if not isinstance(data.get(key), str) or not data.get(key):
            findings.append(_error(key, key, "%s must be a non-empty string" % key))

    duration = data.get("duration_sec")
    if not _is_number(duration) or float(duration) <= 0:
        findings.append(_error(
            "duration_sec", "duration_sec", "duration_sec must be a positive number"
        ))

    source = data.get("source")
    if not isinstance(source, Mapping):
        findings.append(_error("source", "source", "source must be an object"))
    else:
        if not isinstance(source.get("video"), str) or not source.get("video"):
            findings.append(_error(
                "source", "source.video", "source.video must be a non-empty string"
            ))
        _validate_subtitle(source.get("subtitle"), findings)

    summary = data.get("overall_summary_zh")
    if not isinstance(summary, str):
        findings.append(_error(
            "summary_length", "overall_summary_zh", "overall_summary_zh must be a string"
        ))
    elif len(summary) < MIN_SUMMARY_CHARS:
        findings.append(_error(
            "summary_length", "overall_summary_zh",
            "overall_summary_zh length %d < %d" % (len(summary), MIN_SUMMARY_CHARS),
        ))
    elif len(summary) > MAX_SUMMARY_CHARS:
        findings.append(_error(
            "summary_length", "overall_summary_zh",
            "overall_summary_zh length %d > %d" % (len(summary), MAX_SUMMARY_CHARS),
        ))

    takeaways = data.get("takeaways_zh")
    if not isinstance(takeaways, list):
        findings.append(_error(
            "takeaway_count", "takeaways_zh", "takeaways_zh must be an array of strings"
        ))
    else:
        if len(takeaways) < MIN_TAKEAWAYS:
            findings.append(_error(
                "takeaway_count", "takeaways_zh",
                "takeaways_zh count %d < %d" % (len(takeaways), MIN_TAKEAWAYS),
            ))
        elif len(takeaways) > MAX_TAKEAWAYS:
            findings.append(_error(
                "takeaway_count", "takeaways_zh",
                "takeaways_zh count %d > %d" % (len(takeaways), MAX_TAKEAWAYS),
            ))
        for position, item in enumerate(takeaways):
            if not isinstance(item, str):
                findings.append(_error(
                    "takeaway_type", "takeaways_zh[%d]" % position,
                    "takeaways_zh[%d] must be a string" % position,
                ))

    corrections = data.get("corrections")
    if not isinstance(corrections, list):
        findings.append(_error(
            "corrections", "corrections", "corrections must be an array of objects"
        ))
    else:
        for position, item in enumerate(corrections):
            where = "corrections[%d]" % position
            if not isinstance(item, Mapping):
                findings.append(_error("corrections", where, "%s must be an object" % where))
                continue
            for key in ("heard", "correct", "source"):
                if not isinstance(item.get(key), str):
                    findings.append(_error(
                        "corrections", "%s.%s" % (where, key),
                        "%s.%s must be a string" % (where, key),
                    ))

    terms = data.get("unverified_terms")
    if not isinstance(terms, list) or not all(isinstance(item, str) for item in terms):
        findings.append(_error(
            "unverified_terms", "unverified_terms",
            "unverified_terms must be an array of strings",
        ))

    if "ocr_meta" in data and not isinstance(data.get("ocr_meta"), Mapping):
        findings.append(_error("ocr_meta", "ocr_meta", "ocr_meta must be an object"))

    _validate_questions(data, findings)


def _validate_questions(data: Mapping[str, Any], findings: List[Finding]) -> None:
    """`questions_zh` is optional, but a malformed one is an error.

    Optional because a document written before the segmentation step drafted
    questions is still a valid document. An error when present and wrong,
    because a question that points at a segment which does not exist is worse
    than no question: it renders into the note and reads like it was checked.

    Shape: ``[{"text": "...", "segments": [1, 3]}]``. The indexes are the
    1-based ``segments[].index`` the rest of the schema already uses, and more
    than one of them is the point -- a question answerable from a single
    section is rereading with extra steps.
    """
    if "questions_zh" not in data:
        return
    questions = data.get("questions_zh")
    if not isinstance(questions, list):
        findings.append(_error(
            "questions_zh", "questions_zh",
            "questions_zh must be an array of objects",
        ))
        return
    segments = data.get("segments")
    count = len(segments) if isinstance(segments, list) else 0
    for position, item in enumerate(questions):
        where = "questions_zh[%d]" % position
        if not isinstance(item, Mapping):
            findings.append(_error(
                "questions_zh", where, "%s must be an object" % where
            ))
            continue
        if not isinstance(item.get("text"), str) or not item.get("text").strip():
            findings.append(_error(
                "questions_zh", where + ".text",
                "%s.text must be a non-empty string" % where,
            ))
        indexes = item.get("segments")
        if not isinstance(indexes, list) or not indexes:
            findings.append(_error(
                "questions_zh", where + ".segments",
                "%s.segments must be a non-empty array of segment indexes" % where,
            ))
            continue
        for index in indexes:
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 1 <= index <= count
            ):
                findings.append(_error(
                    "questions_zh", where + ".segments",
                    "%s.segments names segment %r, which does not exist" % (
                        where, index
                    ),
                ))


def _validate_bullets(
    segment: Mapping[str, Any], number: int, findings: List[Finding]
) -> None:
    where = "segments[%d].bullets_zh" % number
    bullets = segment.get("bullets_zh")
    if not isinstance(bullets, list):
        findings.append(_error(
            "bullets_zh", where,
            "segment %d bullets_zh must be an array of objects" % number, number,
        ))
        return
    for position, bullet in enumerate(bullets):
        at = "%s[%d]" % (where, position)
        if not isinstance(bullet, Mapping):
            findings.append(_error(
                "bullets_zh", at,
                "segment %d bullets_zh[%d] is not an object (legacy?)" % (number, position),
                number,
            ))
            continue
        if not isinstance(bullet.get("text"), str) or not bullet.get("text"):
            findings.append(_error(
                "bullets_zh", at + ".text",
                "segment %d bullets_zh[%d].text must be a non-empty string"
                % (number, position),
                number,
            ))
        moment = bullet.get("t", None)
        if moment is not None and not _is_number(moment):
            findings.append(_error(
                "bullets_zh", at + ".t",
                "segment %d bullets_zh[%d].t must be a number or null" % (number, position),
                number,
            ))
        kind = bullet.get("kind")
        if kind not in BULLET_KINDS:
            findings.append(_error(
                "bullets_zh", at + ".kind",
                "segment %d bullets_zh[%d].kind must be one of %s, got %r"
                % (number, position, "/".join(BULLET_KINDS), kind),
                number,
            ))


def _validate_segment_lists(
    segment: Mapping[str, Any], number: int, findings: List[Finding]
) -> None:
    quotes = segment.get("quotes_zh")
    if not isinstance(quotes, list):
        findings.append(_error(
            "quotes_zh", "segments[%d].quotes_zh" % number,
            "segment %d quotes_zh must be an array of objects" % number, number,
        ))
    else:
        for position, quote in enumerate(quotes):
            at = "segments[%d].quotes_zh[%d]" % (number, position)
            if not isinstance(quote, Mapping):
                findings.append(_error(
                    "quotes_zh", at,
                    "segment %d quotes_zh[%d] must be an object with text and t"
                    % (number, position),
                    number,
                ))
                continue
            if not isinstance(quote.get("text"), str) or not quote.get("text"):
                findings.append(_error(
                    "quotes_zh", at + ".text",
                    "segment %d quotes_zh[%d].text must be a non-empty string"
                    % (number, position),
                    number,
                ))
            moment = quote.get("t", None)
            if moment is not None and not _is_number(moment):
                findings.append(_error(
                    "quotes_zh", at + ".t",
                    "segment %d quotes_zh[%d].t must be a number or null"
                    % (number, position),
                    number,
                ))

    if "frame" not in segment:
        findings.append(_error(
            "frame", "segments[%d].frame" % number,
            "segment %d is missing the frame key" % number, number,
        ))
    elif segment.get("frame") is not None and not isinstance(segment.get("frame"), str):
        findings.append(_error(
            "frame", "segments[%d].frame" % number,
            "segment %d frame must be a string or null" % number, number,
        ))

    frames = segment.get("frames")
    if not isinstance(frames, list) or not all(isinstance(item, str) for item in frames):
        findings.append(_error(
            "frames", "segments[%d].frames" % number,
            "segment %d frames must be an array of strings" % number, number,
        ))

    frame_ocr = segment.get("frame_ocr")
    if not isinstance(frame_ocr, list):
        findings.append(_error(
            "frame_ocr", "segments[%d].frame_ocr" % number,
            "segment %d frame_ocr must be an array of objects" % number, number,
        ))
    else:
        for position, entry in enumerate(frame_ocr):
            at = "segments[%d].frame_ocr[%d]" % (number, position)
            if (
                not isinstance(entry, Mapping)
                or not isinstance(entry.get("frame"), str)
                or not isinstance(entry.get("text"), str)
            ):
                findings.append(_error(
                    "frame_ocr", at,
                    "segment %d frame_ocr[%d] must be an object with frame and text"
                    % (number, position),
                    number,
                ))

    notes = segment.get("editorial_notes_zh")
    if not isinstance(notes, list) or not all(isinstance(item, str) for item in notes):
        findings.append(_error(
            "editorial_notes_zh", "segments[%d].editorial_notes_zh" % number,
            "segment %d editorial_notes_zh must be an array of strings" % number, number,
        ))


def _validate_segment_times(
    segment: Mapping[str, Any], number: int, findings: List[Finding]
) -> None:
    for key in ("start_sec", "end_sec"):
        if not _is_number(segment.get(key)):
            findings.append(_error(
                key, "segments[%d].%s" % (number, key),
                "segment %d %s must be a number" % (number, key), number,
            ))

    start, end = segment.get("start_sec"), segment.get("end_sec")
    if _is_number(start) and _is_number(end) and float(start) >= float(end):
        findings.append(_error(
            "time_range", "segments[%d]" % number,
            "segment %d start_sec %s is not before end_sec %s" % (number, start, end),
            number,
        ))

    for key, seconds in (("start_time", start), ("end_time", end)):
        value = segment.get(key)
        parsed = parse_clock(value)
        if not isinstance(value, str) or parsed is None:
            findings.append(_error(
                "clock_format", "segments[%d].%s" % (number, key),
                "segment %d %s=%r is not a HH:MM:SS clock value" % (number, key, value),
                number,
            ))
            continue
        if not _is_number(seconds):
            continue
        expected = format_clock(float(seconds))
        if value != expected:
            findings.append(_error(
                "clock_mismatch", "segments[%d].%s" % (number, key),
                "segment %d %s mismatch: %s is %d seconds but %s is %s"
                % (number, key, value, int(parsed), key.replace("_time", "_sec"), seconds),
                number,
            ))


def validate_document(data: Mapping[str, Any]) -> List[Finding]:
    """Every v2 rule violation in ``data``, as an ordered list of findings.

    Purely structural: nothing here touches the file system, so the same
    function validates a document that is still in memory. ``check json`` adds
    the file-backed rules (frame existence) on top of it.
    """
    findings: List[Finding] = []
    if not isinstance(data, Mapping):
        return [_error("document", "$", "document must be a JSON object")]

    _validate_top_level(data, findings)

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        findings.append(_error("segments", "segments", "segments must be a non-empty array"))
        return findings

    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            findings.append(_error(
                "segments", "segments[%d]" % position,
                "segment %d must be an object" % position, position,
            ))
            continue

        index = segment.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index != position:
            findings.append(_error(
                "index", "segments[%d].index" % position,
                "segment %d index is %r, expected %d" % (position, index, position),
                position,
            ))

        for key in ("title", "summary_zh"):
            if not isinstance(segment.get(key), str) or not str(segment.get(key)).strip():
                findings.append(_error(
                    key, "segments[%d].%s" % (position, key),
                    "segment %d %s must be a non-empty string" % (position, key), position,
                ))

        _validate_segment_times(segment, position, findings)
        _validate_bullets(segment, position, findings)
        _validate_segment_lists(segment, position, findings)

    # Boundaries: each segment ends exactly where the next one begins, and the
    # last one ends at the floored media duration. Checked after the per-segment
    # pass so each message can name both sides of the join.
    for position in range(len(segments) - 1):
        current, following = segments[position], segments[position + 1]
        if not isinstance(current, Mapping) or not isinstance(following, Mapping):
            continue
        end, start = current.get("end_sec"), following.get("start_sec")
        if not _is_number(end) or not _is_number(start):
            continue
        if abs(float(end) - float(start)) > BOUNDARY_EPSILON:
            findings.append(_error(
                "contiguity", "segments[%d].end_sec" % (position + 1),
                "segment %d end_sec %s does not meet segment %d start_sec %s"
                % (position + 1, end, position + 2, start),
                position + 1,
            ))

    last = segments[-1]
    duration = data.get("duration_sec")
    if isinstance(last, Mapping) and _is_number(last.get("end_sec")) and _is_number(duration):
        expected = float(int(float(duration)))
        if abs(float(last["end_sec"]) - expected) > BOUNDARY_EPSILON:
            findings.append(_error(
                "contiguity", "segments[%d].end_sec" % len(segments),
                "segment %d end_sec %s does not equal floor(duration_sec) %d"
                % (len(segments), last.get("end_sec"), int(expected)),
                len(segments),
            ))

    return findings


def load_document(path: Path) -> Dict[str, Any]:
    """Read a canonical document verbatim: no normalisation, BOM tolerated."""
    return read_json(path)


__all__ = [
    "BOUNDARY_EPSILON",
    "BULLET_KINDS",
    "Bullet",
    "DEFAULT_FRAME_TOLERANCE",
    "Finding",
    "FrameOcr",
    "LectureDocument",
    "MAX_FRAMES_PER_SEGMENT",
    "MAX_SUMMARY_CHARS",
    "MAX_TAKEAWAYS",
    "MIN_FRAMES_PER_SEGMENT",
    "MIN_SUMMARY_CHARS",
    "MIN_TAKEAWAYS",
    "Quote",
    "REQUIRED_TAKEAWAYS",
    "SCHEMA_VERSION",
    "SUBTITLE_ORIGINS",
    "Segment",
    "Source",
    "Subtitle",
    "assert_times_unchanged",
    "finite_number",
    "format_clock",
    "is_legacy",
    "load_document",
    "load_lecture",
    "normalize_lecture",
    "parse_clock",
    "path_within_base",
    "safe_relative_frame_path",
    "segment_end",
    "segment_start",
    "time_signature",
    "validate_document",
    "validate_lecture_schema",
    "write_json_atomic",
]

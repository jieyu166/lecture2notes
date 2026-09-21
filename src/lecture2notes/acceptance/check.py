"""Mechanical checks over a lecture document and its note.

Ported from rad-workflow skills/lecture-to-notes/scripts/check_lecture.py.
Inspired by drpwchen/lecture-to-notes scripts/audit_note.py @79053a3
(the fail-versus-warn severity split)

Before this existed, a half-finished pipeline was something a human noticed: the
document merged but the frames never captured, a rename leaving the note
pointing at images that are gone, timecodes overlapping. These are all
mechanically decidable, so they are decided mechanically. No judgement about
content quality is made here.

Exit codes: 0 clean, 1 warnings only, 2 at least one error.

One threshold is worth explaining. The overall summary's upper bound is 500
characters, not the 180 it started at, because a thirty-minute lecture with a
dozen segments cannot be summarised in 180 and every single run warned about it.
A warning that fires every time is the same as no warning at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import unquote

from lecture2notes import _out

MIN_SUMMARY_CHARS = 100
MAX_SUMMARY_CHARS = 500
MIN_TAKEAWAYS = 6
MAX_TAKEAWAYS = 12
MIN_BULLETS = 2
#: A gap larger than this between segments probably means a segment is missing.
MAX_SEGMENT_GAP_SEC = 60
#: Tolerance between a ``HH:MM:SS`` string and its seconds field.
CLOCK_TOLERANCE_SEC = 1

SKIP_JSON = re.compile(
    r"\.frames\.json$|\.frames_ocr\.json$|\.corrections\.json$|\.audit\.json$|^_"
)
MD_IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
WIKI_IMG = re.compile(r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
#: Where a bare wikilink filename may live, relative to the note.
NOTE_IMAGE_DIRS = ("", "frames", "images")


@dataclass
class Report:
    """Findings for one target, at three severities."""

    label: str
    errors: List[str] = field(default_factory=list)
    warns: List[str] = field(default_factory=list)
    infos: List[str] = field(default_factory=list)

    def err(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warns.append(message)

    def info(self, message: str) -> None:
        self.infos.append(message)

    @property
    def mark(self) -> str:
        return "error" if self.errors else ("warn" if self.warns else "ok")

    def lines(self) -> List[str]:
        """Every finding as one line each, in severity order."""
        out = ["[%s] %s" % (self.mark, self.label)]
        out.extend("  error  %s" % message for message in self.errors)
        out.extend("  warn   %s" % message for message in self.warns)
        out.extend("  info   %s" % message for message in self.infos)
        return out

    def emit(self) -> None:
        """Print one line per finding, through the shared ASCII-safe output."""
        _out.say(self.mark, self.label)
        for message in self.errors:
            _out.say("error", "  %s" % message)
        for message in self.warns:
            _out.say("warn", "  %s" % message)
        for message in self.infos:
            _out.say("info", "  %s" % message)

    def summary(self, stage: str) -> str:
        return "%s: %d errors, %d warnings" % (stage, len(self.errors), len(self.warns))


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


def check_document(path: Path, report: Report) -> Optional[Dict[str, Any]]:
    """Structure, lengths and segments of one document."""
    raw = Path(path).read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        report.err("file has a UTF-8 BOM; the browser player cannot parse it")
        raw = raw[3:]
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.err("JSON is unparseable: %s" % exc)
        return None

    for key in ("overall_summary_zh", "takeaways_zh", "segments"):
        if key not in data:
            report.err("missing required key %s" % key)

    summary = data.get("overall_summary_zh", "")
    if isinstance(summary, str) and summary and not (
        MIN_SUMMARY_CHARS <= len(summary) <= MAX_SUMMARY_CHARS
    ):
        report.warn("overall_summary_zh is %d characters (want %d-%d)"
                    % (len(summary), MIN_SUMMARY_CHARS, MAX_SUMMARY_CHARS))

    takeaways = data.get("takeaways_zh")
    if isinstance(takeaways, list) and not MIN_TAKEAWAYS <= len(takeaways) <= MAX_TAKEAWAYS:
        report.warn("takeaways_zh has %d items (want %d-%d)"
                    % (len(takeaways), MIN_TAKEAWAYS, MAX_TAKEAWAYS))

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        report.err("segments is missing or empty")
        return data

    check_segments(segments, Path(path).parent, report)
    return data


def check_segments(segments: Sequence[Any], base: Path, report: Report) -> None:
    """Index continuity, monotonic non-overlapping times, and frame existence."""
    previous_end: Optional[float] = None
    # Only a document that already has frames is required to have one per
    # segment; a document with none simply has not been through capture yet.
    any_frame = any(
        s.get("frame") or s.get("frames") for s in segments if isinstance(s, Mapping)
    )

    for position, segment in enumerate(segments, 1):
        tag = "seg#%d" % position
        if not isinstance(segment, Mapping):
            report.err("%s is not an object" % tag)
            continue
        if segment.get("index") != position:
            report.err("%s index is %r, expected %d" % (tag, segment.get("index"), position))

        start, end = segment.get("start_sec"), segment.get("end_sec")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            report.err("%s start_sec/end_sec missing or not numeric: %r / %r"
                       % (tag, start, end))
        else:
            if start >= end:
                report.err("%s start_sec %s >= end_sec %s" % (tag, start, end))
            if previous_end is not None:
                if start < previous_end:
                    report.err("%s overlaps the previous segment (start %s < end %s)"
                               % (tag, start, previous_end))
                elif start - previous_end > MAX_SEGMENT_GAP_SEC:
                    report.warn("%s leaves a %.0f second gap after the previous segment"
                                % (tag, start - previous_end))
            previous_end = end
            for field_name, seconds in (("start_time", start), ("end_time", end)):
                parsed = parse_clock(segment.get(field_name))
                if parsed is None:
                    report.err("%s %s=%r is not a clock value"
                               % (tag, field_name, segment.get(field_name)))
                elif abs(parsed - seconds) > CLOCK_TOLERANCE_SEC:
                    report.err("%s %s=%s disagrees with %s_sec=%s"
                               % (tag, field_name, segment.get(field_name),
                                  field_name[:-5], seconds))

        for field_name in ("title", "summary_zh"):
            if not str(segment.get(field_name) or "").strip():
                report.err("%s %s is empty" % (tag, field_name))
        bullets = segment.get("bullets_zh")
        if bullets is None:
            bullets = segment.get("takeaways_zh")
        if not isinstance(bullets, list) or len(bullets) < MIN_BULLETS:
            report.warn("%s has fewer than %d bullets" % (tag, MIN_BULLETS))

        frames = segment.get("frames") or (
            [segment["frame"]] if segment.get("frame") else []
        )
        if any_frame and not frames:
            report.err("%s has no frame although other segments do" % tag)
        for frame in frames:
            name = frame["path"] if isinstance(frame, Mapping) else str(frame)
            if not (Path(base) / unquote(str(name))).exists():
                report.err("%s frame does not exist: %s" % (tag, name))


def note_image_refs(markdown: str) -> List[str]:
    """Image references in a note, both embed spellings, external links excluded."""
    refs: List[str] = []
    for match in MD_IMG.finditer(markdown):
        target = unquote(match.group(1).split(" ")[0].strip("<>"))
        if target.lower().endswith(IMG_EXT) and "://" not in target:
            refs.append(target)
    for match in WIKI_IMG.finditer(markdown):
        target = match.group(1).strip()
        if target.lower().endswith(IMG_EXT):
            refs.append(target)
    return refs


def check_note(note: Path, report: Report) -> None:
    """Every image the note embeds must resolve to a file."""
    note = Path(note)
    text = note.read_text(encoding="utf-8", errors="replace")
    refs = note_image_refs(text)
    if not refs:
        report.warn("%s embeds no images" % note.name)
        return
    missing: List[str] = []
    for ref in refs:
        if (note.parent / ref).exists():
            continue
        name = Path(ref).name
        if any(
            (note.parent / sub / name).exists() if sub else (note.parent / name).exists()
            for sub in NOTE_IMAGE_DIRS
        ):
            continue
        missing.append(ref)
    for ref in sorted(set(missing)):
        report.err("%s embeds an image that does not exist: %s" % (note.name, ref))
    report.info("%s embeds %d images, %d missing" % (note.name, len(refs), len(set(missing))))


def collect_targets(target: Path) -> List[Path]:
    target = Path(target)
    if target.is_file():
        return [target]
    return sorted(
        path for path in target.iterdir()
        if path.suffix.lower() == ".json" and not SKIP_JSON.search(path.name)
    )


def exit_code(reports: Sequence[Report], strict: bool = False) -> int:
    """0 clean, 1 warnings, 2 errors. ``strict`` promotes warnings to errors."""
    if any(report.errors for report in reports):
        return 2
    if any(report.warns for report in reports):
        return 2 if strict else 1
    return 0


def check_path(target: Path, note: Optional[Path] = None) -> List[Report]:
    """Check one document or every document in a folder, pairing notes by name."""
    reports: List[Report] = []
    target = Path(target)
    for document in collect_targets(target):
        report = Report(document.name)
        data = check_document(document, report)
        if data is not None:
            paired = Path(note) if note else None
            if paired is None and target.is_dir():
                candidate = document.with_name("%s.v4.md" % document.stem)
                paired = candidate if candidate.exists() else None
            if paired is not None:
                if paired.exists():
                    check_note(paired, report)
                else:
                    report.err("note does not exist: %s" % paired)
        reports.append(report)
    return reports


__all__ = [
    "CLOCK_TOLERANCE_SEC",
    "MAX_SEGMENT_GAP_SEC",
    "MAX_SUMMARY_CHARS",
    "MAX_TAKEAWAYS",
    "MIN_BULLETS",
    "MIN_SUMMARY_CHARS",
    "MIN_TAKEAWAYS",
    "Report",
    "check_document",
    "check_note",
    "check_path",
    "check_segments",
    "collect_targets",
    "exit_code",
    "note_image_refs",
    "parse_clock",
]

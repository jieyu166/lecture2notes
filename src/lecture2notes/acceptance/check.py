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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote

from lecture2notes import _out
from lecture2notes.engines.base import SRT_TIME
from lecture2notes.frames.manifest import read_manifest, sha256_file
from lecture2notes.notes import guideline, render, rules
from lecture2notes.profiles import loader
from lecture2notes.schema.model import (
    AI_DRAFT_MARK,
    MAX_SUMMARY_CHARS,
    MAX_TAKEAWAYS,
    MIN_SUMMARY_CHARS,
    MIN_TAKEAWAYS,
    Finding,
    is_legacy,
    path_within_base,
    safe_relative_frame_path,
    validate_document,
)

MIN_BULLETS = 2
#: A gap larger than this between segments probably means a segment is missing.
MAX_SEGMENT_GAP_SEC = 60
#: Tolerance between a ``HH:MM:SS`` string and its seconds field.
CLOCK_TOLERANCE_SEC = 1
#: Subtitle cues may touch; a millisecond of rounding is not an overlap.
CUE_OVERLAP_TOLERANCE_SEC = 0.002

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
        # Only a segment with neither a captured frame nor an inherited one is
        # a finding: `frames == []` with `frame` set is the "slide did not
        # change here" shape, not a gap.
        if any_frame and not frames:
            report.err("%s has no frame although other segments do" % tag)
        for frame in frames:
            name = frame["path"] if isinstance(frame, Mapping) else str(frame)
            if not (Path(base) / unquote(str(name))).exists():
                report.err("%s frame does not exist: %s" % (tag, name))


# -- the transcribe stage --------------------------------------------------
#: Suffixes the transcribe stage leaves next to `<stem>.srt`.
RAW_SUFFIX = ".raw.srt"
CORRECTIONS_SUFFIX = ".corrections.json"
#: A cue this short is almost always a timing artefact rather than speech.
MIN_CUE_SEC = 0.05


def check_transcribe(path: Path, report: Report) -> List[Any]:
    """Verify one SRT and the audit trail the transcribe stage leaves with it.

    Two different kinds of finding live here. SRT structure is an *error*: a file
    with overlapping or inverted cues breaks every stage downstream. A
    hallucination loop is a *warning*: the transcript is still usable up to the
    loop, and only the user can decide whether to re-run or trim the tail.

    The raw copy and the correction sidecar are reported as info, not warnings.
    A transcript produced without a correction table legitimately has neither,
    and a warning that fires on a correct run teaches people to ignore warnings.
    Having exactly one of the pair, though, is an error: the stage writes both or
    neither, so a lone sidecar means something deleted the evidence.
    """
    from lecture2notes.engines import hallucination
    from lecture2notes.engines.base import parse_srt_text

    path = Path(path)
    raw_bytes = path.read_bytes()
    if raw_bytes[:3] == b"\xef\xbb\xbf":
        report.err("SRT has a UTF-8 BOM; players and parsers mis-read the first cue")
    text = raw_bytes.decode("utf-8", "replace").replace("\r", "")

    cues = parse_srt_text(text)
    if not cues:
        report.err("no cues could be parsed from %s" % path.name)
        return []

    previous_end: Optional[float] = None
    for number, cue in enumerate(cues, 1):
        if cue.end <= cue.start:
            report.err("cue %d ends at or before it starts (%.3f to %.3f)"
                       % (number, cue.start, cue.end))
        elif cue.duration() < MIN_CUE_SEC:
            report.warn("cue %d lasts only %.3f seconds" % (number, cue.duration()))
        if previous_end is not None and cue.start < previous_end - CUE_OVERLAP_TOLERANCE_SEC:
            report.err("cue %d starts at %.3f, before the previous cue ended (%.3f)"
                       % (number, cue.start, previous_end))
        previous_end = max(previous_end or 0.0, cue.end)
        if not cue.text.strip():
            report.err("cue %d has no text" % number)

    numbers = cue_index_numbers(text)
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        report.err("cue numbering is not 1..%d in order" % len(numbers))

    loops = hallucination.find_loops(cues)
    for loop in loops:
        report.warn(loop.message())

    raw = path.with_name(path.stem + RAW_SUFFIX)
    sidecar = path.with_name(path.stem + CORRECTIONS_SUFFIX)
    if raw.exists() and sidecar.exists():
        report.info("correction table applied; raw transcript kept as %s" % raw.name)
    elif raw.exists() or sidecar.exists():
        report.err(
            "the transcribe stage writes %s and %s together; only one is present"
            % (raw.name, sidecar.name)
        )
    else:
        report.info("no correction table was applied to %s" % path.name)

    report.info("%d cues, %.1f to %.1f minutes"
                % (len(cues), cues[0].start / 60.0, cues[-1].end / 60.0))
    return loops


#: Location prefix for a per-cue finding, so every line carries a location that
#: points at one cue rather than at the whole file.
def _cue_location(name: str, number: int) -> str:
    return "%s:cue %d" % (name, number)


def check_transcribe_stage(path: Path) -> StageReport:
    """The ``transcribe`` stage check over one ``<stem>.srt``.

    The same rules :func:`check_transcribe` applies, reported through
    :class:`StageReport` so that the four stages share one output contract and
    one finding shape. The older ``Report`` form stays for callers that want the
    grouped per-document printout.

    Severity split, unchanged from the ported version: structure is an error
    because every later stage reads these times; a hallucination loop is a
    warning because the transcript is still usable up to the loop and only a
    person can decide whether to re-run or trim; a missing correction sidecar is
    only an error when its twin is present, since a run with no correction table
    legitimately has neither.
    """
    from lecture2notes.engines import hallucination
    from lecture2notes.engines.base import parse_srt_text

    target = Path(path)
    report = StageReport("transcribe", target.name)
    name = target.name

    try:
        raw_bytes = target.read_bytes()
    except OSError as exc:
        report.add("error", "parse", name, "subtitle is unreadable: %s" % exc)
        return report
    if raw_bytes[:3] == b"\xef\xbb\xbf":
        report.add(
            "error", "bom", name,
            "SRT has a UTF-8 BOM; players and parsers mis-read the first cue",
        )
    text = raw_bytes.decode("utf-8", "replace").replace("\r", "")

    cues = parse_srt_text(text)
    if not cues:
        report.add("error", "parse", name, "no cues could be parsed")
        return report

    previous_end: Optional[float] = None
    for number, cue in enumerate(cues, 1):
        location = _cue_location(name, number)
        if cue.end <= cue.start:
            report.add(
                "error", "cue_time", location,
                "cue ends at or before it starts (%.3f to %.3f)"
                % (cue.start, cue.end),
            )
        elif cue.duration() < MIN_CUE_SEC:
            report.add(
                "warn", "cue_time", location,
                "cue lasts only %.3f seconds" % cue.duration(),
            )
        if (
            previous_end is not None
            and cue.start < previous_end - CUE_OVERLAP_TOLERANCE_SEC
        ):
            report.add(
                "error", "cue_order", location,
                "cue starts at %.3f, before the previous cue ended (%.3f)"
                % (cue.start, previous_end),
            )
        previous_end = max(previous_end or 0.0, cue.end)
        if not cue.text.strip():
            report.add("error", "cue_text", location, "cue has no text")

    numbers = cue_index_numbers(text)
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        report.add(
            "error", "numbering", name,
            "cue numbering is not 1..%d in order" % len(numbers),
        )

    for loop in hallucination.find_loops(cues):
        report.add("warn", "loop", name, loop.message())

    raw = target.with_name(target.stem + RAW_SUFFIX)
    sidecar = target.with_name(target.stem + CORRECTIONS_SUFFIX)
    if raw.exists() != sidecar.exists():
        report.add(
            "error", "corrections", name,
            "the transcribe stage writes %s and %s together; only one is present"
            % (raw.name, sidecar.name),
        )
    return report


def cue_index_numbers(text: str) -> List[int]:
    """The cue index lines: a bare number immediately before a timecode line.

    Recognised by what follows rather than by position, because a bare number can
    also be a line of subtitle text.
    """
    lines = text.split("\n")
    found: List[int] = []
    for position, line in enumerate(lines):
        stripped = line.strip()
        if stripped.isdigit() and any(
            SRT_TIME.search(nxt) for nxt in lines[position + 1:position + 2]
        ):
            found.append(int(stripped))
    return found


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


# ==========================================================================
# stage acceptance: check json
# ==========================================================================
# The stage checks share one output contract: one line per finding as
# ``<severity> <rule-or-field> <location>: <message>``, then
# ``<stage>: N errors, M warnings``, then exit 0 / 1 / 2. ``Report`` above is
# the older per-document shape and stays for the note path; ``StageReport`` is
# the contract shape, and it carries Findings so the structured audit report
# can be assembled from the same objects.

#: What a legacy document is told to do. The wording is part of the contract.
LEGACY_MESSAGE = "legacy schema detected; run: l2n migrate %s"


@dataclass
class StageReport:
    """Findings for one stage check over one target."""

    stage: str
    target: str
    findings: List[Finding] = field(default_factory=list)
    #: Counts a stage measured but did not judge. They are printed after the
    #: summary line and carried into the audit report, and they never move the
    #: exit code: "three ai-draft markers left" is a fact about how far the
    #: work got, not a rule anybody broke.
    metrics: Dict[str, Any] = field(default_factory=dict)

    def measure(self, name: str, value: Any) -> None:
        """Record a count to print after the summary. Never a finding."""
        self.metrics[name] = value

    def metric_lines(self) -> List[str]:
        return ["%s: %s=%s" % (self.stage, name, value)
                for name, value in self.metrics.items()]

    def add(
        self,
        severity: str,
        code: str,
        location: str,
        message: str,
        segment_index: Optional[int] = None,
    ) -> None:
        self.findings.append(
            Finding(severity, code, message, segment_index, None, location)
        )

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    def lines(self) -> List[str]:
        """Every finding in the contract's line format."""
        return [finding.line(self.target) for finding in self.findings]

    def summary_line(self) -> str:
        return "%s: %d errors, %d warnings" % (
            self.stage, len(self.errors), len(self.warnings)
        )

    def emit(self) -> None:
        """Print the findings and the summary through the ASCII-safe writer."""
        if not self.findings:
            _out.stage(self.stage, "ok")
        for text in self.lines():
            _out.line(text)
        _out.line(self.summary_line())
        # After the summary, so a script that reads the last line for the
        # tally keeps working and a person reading down the page sees the
        # counts last.
        for text in self.metric_lines():
            _out.line(text)

    def exit_code(self) -> int:
        if self.errors:
            return 2
        return 1 if self.warnings else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "target": self.target,
            "findings": [finding.to_dict() for finding in self.findings],
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            **dict(self.metrics),
        }


def _referenced_frames(segment: Mapping[str, Any]) -> List[str]:
    """Every frame path one segment points at, in document order, deduplicated."""
    names: List[str] = []
    frame = segment.get("frame")
    if isinstance(frame, str) and frame:
        names.append(frame)
    for item in segment.get("frames") or []:
        if isinstance(item, str) and item:
            names.append(item)
    for item in segment.get("frame_ocr") or []:
        if isinstance(item, Mapping) and isinstance(item.get("frame"), str):
            if item["frame"]:
                names.append(item["frame"])
    seen = set()
    unique: List[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def check_json_frames(
    data: Mapping[str, Any], base_dir: Path, report: StageReport
) -> None:
    """The file-backed half of ``check json``: frames exist and are referenced.

    Split from the structural validator on purpose: the schema rules are decided
    from the document alone, these need the directory it lives in.
    """
    segments = data.get("segments")
    if not isinstance(segments, list):
        return
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        # `frame` is the one that must be there; `frames` may legitimately be
        # empty. A segment during which the slide never changed inherits the
        # last frame from before it began (see
        # `frames.manifest.merge_frames_into_segments`), and that is a complete
        # answer to "what was on screen here". Demanding a captured frame per
        # segment would cap the segment count at the number of detected scene
        # changes -- a 20 minute talk on nine slides is not a nine-segment talk.
        if not segment.get("frame"):
            report.add(
                "error", "frame", "segments[%d].frame" % position,
                "segment %d has no frame" % position, position,
            )
        for name in _referenced_frames(segment):
            relative = safe_relative_frame_path(unquote(name))
            resolved = (
                path_within_base(base_dir, relative) if relative is not None else None
            )
            if resolved is None:
                report.add(
                    "error", "frame_path", "segments[%d]" % position,
                    "segment %d frame path is not a safe relative path: %s"
                    % (position, name),
                    position,
                )
                continue
            if not resolved.is_file():
                report.add(
                    "error", "frame_missing", name,
                    "segment %d references a frame that does not exist" % position,
                    position,
                )


#: How much of a digest the ``sha256`` finding prints. Four hex characters
#: identify a mismatch at a glance without turning one finding into a 140
#: character line; the full pair is available from
#: ``frames.manifest.verify_manifest``.
DIGEST_PREVIEW = 4


def _digest_preview(digest: str) -> str:
    return "%s.." % str(digest)[:DIGEST_PREVIEW]


def check_frames(path: Path) -> StageReport:
    """The ``frames`` stage check over one ``<stem>.frames.json``.

    Three rules, all mechanical: every listed file exists and still hashes to
    the value the manifest recorded, timestamps strictly increase, and there is
    at least one frame. The hash rule is the reason the manifest carries
    ``sha256`` at all -- a frame regenerated after the manifest was written
    looks perfectly fine on disk and silently un-anchors every reference to it.
    """
    target = Path(path)
    report = StageReport("frames", target.name)

    try:
        rows = read_manifest(target)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.add("error", "parse", target.name, "manifest is unreadable: %s" % exc)
        return report

    if not rows:
        report.add(
            "error", "frames", target.name,
            "manifest lists no frames; capture produced nothing",
        )
        return report

    base_dir = target.parent
    previous: Optional[float] = None
    for position, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            report.add("error", "row", "frames[%d]" % position, "row is not an object")
            continue
        name = str(row.get("frame") or "")
        location = name or "frames[%d]" % position

        second = row.get("timestamp_sec")
        if isinstance(second, bool) or not isinstance(second, (int, float)):
            report.add(
                "error", "timestamp_sec", location,
                "timestamp_sec is missing or not a number: %r" % (second,),
            )
        else:
            if previous is not None and float(second) <= previous:
                report.add(
                    "error", "timestamp", location,
                    "timestamp %g does not increase after %g" % (second, previous),
                )
            previous = float(second)

        relative = safe_relative_frame_path(unquote(name))
        resolved = (
            path_within_base(base_dir, relative) if relative is not None else None
        )
        if resolved is None:
            report.add(
                "error", "frame_path", location,
                "frame path is not a safe relative path: %s" % (name or "(empty)"),
            )
            continue
        if not resolved.is_file():
            report.add("error", "frame_missing", location, "file does not exist")
            continue
        expected = row.get("sha256")
        if not isinstance(expected, str) or not expected:
            report.add("warn", "sha256", location, "manifest row carries no sha256")
            continue
        actual = sha256_file(resolved)
        if actual != expected:
            report.add(
                "error", "sha256", location,
                "manifest %s actual %s"
                % (_digest_preview(expected), _digest_preview(actual)),
            )
    return report


def check_json(path: Path, require_frames: bool = True) -> StageReport:
    """The ``json`` stage check over one canonical document.

    Three layers, in this order, because each one only makes sense if the
    previous passed: the file is readable JSON without a BOM, it is schema v2
    rather than legacy, and it satisfies every v2 rule plus the frames on disk.
    """
    target = Path(path)
    report = StageReport("json", target.name)

    raw = target.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        report.add(
            "error", "bom", target.name,
            "file starts with a UTF-8 BOM; canonical JSON must not have one",
        )
        raw = raw[3:]
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.add("error", "parse", target.name, "JSON is unparseable: %s" % exc)
        return report

    if is_legacy(data):
        report.add(
            "error", "schema_version", target.name, LEGACY_MESSAGE % target.name
        )
        return report

    report.findings.extend(validate_document(data))
    if require_frames:
        check_json_frames(data, target.parent, report)
    return report


# ==========================================================================
# stage acceptance: check note
# ==========================================================================
# R1 to R10 of the note-writing guideline. Everything here is decided from the
# note's text, the document it came from and the transcript beside it; nothing
# judges whether the writing is any good, because that is what the other half of
# the guideline -- the half a person has to follow -- is for.
#
# The rules that matter most are the ones a reader cannot see. A note that
# quietly pastes the transcript, or that names a term nobody can verify, looks
# exactly like a good one. That is why these are machine-checked at all.

#: The Obsidian embed spelling the skeleton emits.
NOTE_EMBED = re.compile(r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")

#: A 「...」 span. R5 subtracts these from a line before comparing it, so a line
#: that marks half of itself as a quotation is judged only on the other half.
QUOTED_SPAN = re.compile(r"「[^」]*」")

#: Filler a section gets when there was nothing to say. The guideline's answer is
#: to delete the section instead, so this is a warning rather than an error.
PLACEHOLDER_TEXT = (
    "N/A", "n/a", "N.A.", "TBD", "tbd",
    "不適用", "無資料", "無相關資料", "（略）", "(略)", "待補", "暫無",
)

#: Rendered by `notes.render`; `check note` looks for the same header.
CORRECTION_HEADER = ("heard", "correct")

#: What R5 compares against when the profile does not promote it.
R5_DEFAULT_SEVERITY = "warn"

#: A numbered question line inside the 題目 section. R9 counts these.
QUESTION_LINE = re.compile(r"^\s*\d+[.)、]\s*\S")


def _heading_depth(line: str) -> int:
    stripped = line.lstrip("#")
    depth = len(line) - len(stripped)
    return depth if depth and stripped[:1] in (" ", "\t") else 0


def note_lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def heading_index(lines: Sequence[str], heading: str) -> int:
    """Line number of an exact heading, or -1. First occurrence wins."""
    for position, line in enumerate(lines):
        if line.strip() == heading:
            return position
    return -1


def section_bounds(
    lines: Sequence[str], start: int, stop_at_depth: Optional[int] = None
) -> int:
    """Where the section opened at *start* ends: the next heading that closes it."""
    depth = stop_at_depth if stop_at_depth is not None else _heading_depth(lines[start])
    for position in range(start + 1, len(lines)):
        found = _heading_depth(lines[position])
        if found and found <= depth:
            return position
    return len(lines)


#: Prose sections outside the Note body that R5 also judges, as
#: ``(region name, heading, heading that ends it or None)``. A transcript
#: sentence pasted into Summary is the same failure as one pasted into a
#: segment; the note's own appendix records a Summary bullet that was "just as
#: bad" and that the machine could not see.
R5_EXTRA_REGIONS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("Evergreen Note", "# Evergreen Note", None),
    ("Summary", "# Summary", "## 講者骨架"),
    ("題目", "## 題目", None),
)


def note_regions(lines: Sequence[str]) -> Dict[str, Tuple[int, int]]:
    """The regions the rules talk about, by name.

    ``note`` and ``references`` are the two R3 and R4 are about; the rest are
    the prose sections R5 grew to cover. The Note body deliberately stops at
    ``### References`` even though that heading is nested deeper: References is
    where unverified terms and the correction table are allowed to live, and R3
    is exactly the rule that they must not be anywhere else.
    """
    regions: Dict[str, Tuple[int, int]] = {}
    note_at = heading_index(lines, "# Note (layer 1-3)")
    references_at = heading_index(lines, "### References")
    if note_at >= 0:
        end = section_bounds(lines, note_at)
        if 0 <= references_at < end:
            end = references_at
        regions["note"] = (note_at + 1, end)
    if references_at >= 0:
        regions["references"] = (references_at + 1,
                                 section_bounds(lines, references_at))
    for name, heading, stop_heading in R5_EXTRA_REGIONS:
        start = heading_index(lines, heading)
        if start < 0:
            continue
        end = section_bounds(lines, start)
        if stop_heading:
            stop = heading_index(lines, stop_heading)
            if 0 <= stop < end:
                end = stop
        regions[name] = (start + 1, end)
    return regions


def segment_sections(
    lines: Sequence[str], bounds: Tuple[int, int]
) -> List[Tuple[str, int, int]]:
    """Each ``## n、title`` section inside the Note body, as (title, start, end)."""
    start, end = bounds
    heads = [
        position for position in range(start, end)
        if _heading_depth(lines[position]) == 2
    ]
    out: List[Tuple[str, int, int]] = []
    for number, position in enumerate(heads):
        stop = heads[number + 1] if number + 1 < len(heads) else end
        out.append((lines[position].lstrip("#").strip(), position + 1, stop))
    return out


def is_marked_quote(line: str) -> bool:
    """A blockquote, or a line whose content is wrapped in 「」."""
    body = line.strip()
    for marker in ("- ", "* ", "+ "):
        if body.startswith(marker):
            body = body[len(marker):].lstrip()
    if body.startswith(">"):
        return True
    return bool(QUOTED_SPAN.search(body))


def unquoted_residue(line: str) -> str:
    """The part of a line that is not inside 「」, normalised for comparison."""
    return rules.comparison_text(QUOTED_SPAN.sub("", line))


def transcript_text(path: Path) -> str:
    """Every cue's text, concatenated. Cue boundaries are not word boundaries."""
    from lecture2notes.engines.base import parse_srt_text, read_subtitle_text

    cues = parse_srt_text(read_subtitle_text(Path(path)))
    return "".join(cue.text for cue in cues)


def transcript_runs(text: str, size: int) -> set:
    """Every window of *size* characters in the normalised transcript."""
    normalized = rules.comparison_text(text)
    if len(normalized) < size:
        return set()
    return {normalized[i:i + size] for i in range(len(normalized) - size + 1)}


def pasted_run(residue: str, runs: set, size: int) -> Optional[str]:
    """The first window of *residue* that also occurs in the transcript."""
    for i in range(0, max(len(residue) - size + 1, 0)):
        window = residue[i:i + size]
        if window in runs:
            return window
    return None


def correction_rows(lines: Sequence[str], bounds: Tuple[int, int]):
    """The correction table under References as (line number, cells) pairs.

    Returns None when there is no table at all, which is itself an R4 finding
    and a different one from a table with an empty cell.
    """
    start, end = bounds
    header_at = -1
    for position in range(start, end):
        cells = _table_cells(lines[position])
        if cells and all(
            any(column == cell.strip().lower() for cell in cells)
            for column in CORRECTION_HEADER
        ):
            header_at = position
            break
    if header_at < 0:
        return None
    rows: List[Tuple[int, List[str]]] = []
    for position in range(header_at + 1, end):
        cells = _table_cells(lines[position])
        if not cells:
            break
        if all(set(cell.strip()) <= set("-: ") for cell in cells):
            continue
        rows.append((position, cells))
    return rows


def _table_cells(line: str) -> List[str]:
    body = line.strip()
    if not body.startswith("|"):
        return []
    return [cell.strip() for cell in body.strip("|").split("|")]


def _resolve_embed(note: Path, ref: str) -> bool:
    target = unquote(ref.strip()).replace("\\", "/")
    if (note.parent / target).exists():
        return True
    if "/" in target:
        return False
    return any(
        (note.parent / sub / target).exists() if sub else (note.parent / target).exists()
        for sub in NOTE_IMAGE_DIRS
    )


def check_note_stage(
    json_path: Path,
    note_path: Path,
    transcript: Optional[Path] = None,
    style: Optional[str] = None,
    profile: Optional[str] = None,
) -> StageReport:
    """`l2n check note`: rules R1 to R10 of the note-writing guideline."""
    json_path = Path(json_path)
    note_path = Path(note_path)
    report = StageReport("note", note_path.name)

    data: Dict[str, Any] = {}
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            report.add("error", "parse", json_path.name,
                       "JSON is unparseable: %s" % exc)
            return report
    else:
        report.add("error", "parse", json_path.name, "no such file")
        return report
    if not note_path.is_file():
        report.add("error", "R1", note_path.name, "the note does not exist")
        return report

    effective_profile = profile or str(data.get("profile") or loader.BUILTIN_PROFILE)
    if not loader.profile_dir(effective_profile).is_dir():
        effective_profile = loader.BUILTIN_PROFILE

    text = note_path.read_text(encoding="utf-8", errors="replace")
    lines = note_lines(text)

    # The note records the contract it was written to. Without `--style`, that
    # is a better answer than the profile's default: the profile says what the
    # next render would do, the marker says what this note actually is.
    declared = render.read_style_marker(text)
    if declared not in render.STYLES:
        declared = None
    if style:
        effective_style = loader.note_style(effective_profile, style)
        if declared and declared != effective_style:
            report.add(
                "warn", "style", note_path.name,
                "style mismatch: the note was rendered with --style %s, this "
                "check ran with --style %s" % (declared, effective_style),
            )
    elif declared:
        effective_style = declared
    else:
        effective_style = loader.note_style(effective_profile, None)
    regions = note_regions(lines)

    _rule_sections(lines, note_path, report)
    _rule_embeds(text, note_path, report)
    _rule_unverified(data, lines, regions, note_path, report)
    _rule_corrections(lines, regions, note_path, report)
    _rule_transcript_paste(
        lines, regions, json_path, note_path, transcript, effective_profile, report
    )
    _rule_faithful_quotes(lines, regions, effective_style, note_path, report)
    _rule_privacy(lines, effective_profile, note_path, report)
    _rule_placeholder(lines, regions, note_path, report)
    _rule_questions(lines, note_path, report)
    _rule_unexpanded(
        data, lines, regions, json_path, note_path,
        effective_style, effective_profile, report,
    )
    _rule_duplicate_sentences(lines, note_path, report)
    report.measure("ai_draft_remaining", count_ai_drafts(lines))
    return report


def comparable_body(lines: Sequence[str]) -> str:
    """A section's text with every space removed, for an identity comparison.

    Kept for the callers that want the strictest possible reading of "nobody
    touched this". R10 itself no longer uses it; see :func:`residual_ratio`.
    """
    return "".join("".join(line.split()) for line in lines)


#: A rendered sentence shorter than this (normalised) is not evidence of
#: anything: short strings match by coincidence. Four rather than something
#: safer because :func:`_covered_chars` counts positions -- a coincidental
#: four-character match moves the ratio by four characters, not by a sentence.
R10_MIN_PIECE_CHARS = 4

#: The ``(00:01:02)`` the renderer appends to a quotation. R10 drops it with
#: the quotation it belongs to: a bullet that is nothing but the speaker's own
#: words and the time they said them is on neither side of the ratio.
QUOTE_TIMESTAMP = re.compile(r"\(\s*\d{1,2}:\d{2}(?::\d{2})?\s*\)")


def r10_text(line: str) -> str:
    """One line as R10 compares it: no quotations, no timecodes, normalised."""
    return rules.comparison_text(
        QUOTE_TIMESTAMP.sub("", QUOTED_SPAN.sub("", line))
    )


#: The timespan line the renderer projects from the document, e.g.
#: ``(00:04:10 - 00:09:55)``. It is data, not prose, and it is supposed to
#: survive the expansion unchanged, so R10 must not count it either way.
TIMESPAN_LINE = re.compile(r"^\(\s*[\d:]+\s*-\s*[\d:]+\s*\)$")


def prose_lines(lines: Sequence[str]) -> List[str]:
    """The lines of a section that are the writer's own prose.

    Dropped: headings, blank lines, image embeds, the projected timespan, and
    blockquotes. The last one matters most -- a blockquote is the speaker's
    own words, which 2.1 requires be kept verbatim, so leaving it in place is
    compliance rather than laziness and R10 must not read it as residue.

    Bullet markers are stripped so ``- foo`` and ``foo`` compare the same: an
    expansion that turns a bullet into a sentence has not rewritten anything.
    """
    out: List[str] = []
    for line in lines:
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        for marker in ("- ", "* ", "+ "):
            if body.startswith(marker):
                body = body[len(marker):].lstrip()
                break
        if body.startswith(">") or body.startswith("!["):
            continue
        if TIMESPAN_LINE.match(body):
            continue
        out.append(body)
    return out


def _covered_chars(body: str, pieces: Sequence[str]) -> int:
    """How many characters of *body* are covered by any of *pieces*.

    Positions rather than a sum of lengths, so a rendered sentence that
    happens to contain a shorter rendered bullet cannot be counted twice.
    Every occurrence is marked, not only the first: a note that pastes the
    same rendered sentence into two paragraphs has two paragraphs of residue.
    """
    if not body:
        return 0
    covered = bytearray(len(body))
    for piece in pieces:
        start = body.find(piece)
        while start >= 0:
            for index in range(start, start + len(piece)):
                covered[index] = 1
            start = body.find(piece, start + 1)
    return sum(covered)


def residual_ratio(
    body_lines: Sequence[str],
    skeleton_lines: Sequence[str],
    keep_quotes: bool = False,
) -> float:
    """How much of a section's body is still, character for character, render output.

    The skeleton section is split into the sentences `l2n render` writes -- the
    ``summary_zh`` line and each bullet -- and each one is searched for inside
    the note's own body. The ratio is covered characters over body characters,
    both normalised the way every other copy check in this file normalises
    (case-folded, width-folded, punctuation and whitespace dropped).

    Identity still scores 1.0, which is why this can replace the identity test
    outright. What it also catches is the shape the blind review found: every
    rendered sentence still there, with one new sentence written in front of
    it. That note reads as expanded to an identity test and reads as the same
    lecture told twice to a person.

    ``keep_quotes`` is False everywhere except Evergreen. In a segment body a
    「」 span is a quotation the guideline asked for, so it belongs in neither
    the numerator nor the denominator; Evergreen's 「」 is the renderer's own
    typography around the takeaway, and stripping it would leave nothing to
    measure.
    """
    normalise = rules.comparison_text if keep_quotes else r10_text
    body = "".join(normalise(line) for line in prose_lines(body_lines))
    if not body:
        return 0.0
    pieces = [
        piece for piece in (normalise(line) for line in prose_lines(skeleton_lines))
        if len(piece) >= R10_MIN_PIECE_CHARS
    ]
    if not pieces:
        return 0.0
    return min(_covered_chars(body, pieces) / float(len(body)), 1.0)


def _rule_unexpanded(
    data: Mapping[str, Any],
    lines: Sequence[str],
    regions: Mapping[str, Tuple[int, int]],
    json_path: Path,
    note: Path,
    style: str,
    profile: str,
    report: StageReport,
) -> None:
    """R10: a section whose body is still mostly what `l2n render` produced.

    Every other rule can be satisfied by a note nobody wrote. A test with a
    fresh context was handed the skill, filled in the speaker outline and the
    question answers, and `check note` returned 0 errors 0 warnings on a
    document whose every segment was still the renderer's output. Passing the
    check has to mean more than that.

    1.2 asked for identity, and the blind review of four expansions showed how
    thin a defence that is: one note kept every rendered sentence and wrote a
    new one in front of it. Nothing was identical any more, R10 went quiet, and
    the reader got the same lecture twice in a row. So the question is no
    longer "is this byte for byte the skeleton" but "how much of this paragraph
    is still the skeleton" -- residue at or above
    :func:`loader.r10_ratio` (60% by default) is reported, and an untouched
    section still scores 100%.

    The comparison is against a re-render of the same document at the same
    style, so it cannot drift from what `render` actually writes.
    """
    if "note" not in regions:
        return
    try:
        skeleton = render.render_skeleton(
            data, style=style, stem=json_path.stem, profile=profile
        )
    except (ValueError, OSError):  # no template for this profile
        return
    severity = "error" if loader.unexpanded_severity(profile) == "error" else "warn"
    threshold = loader.r10_ratio(profile)
    skeleton_lines = note_lines(skeleton)
    skeleton_regions = note_regions(skeleton_lines)

    mine = segment_sections(lines, regions["note"])
    theirs = (
        segment_sections(skeleton_lines, skeleton_regions["note"])
        if "note" in skeleton_regions else []
    )
    unexpanded = 0
    for position, (_title, start, end) in enumerate(mine):
        if position >= len(theirs):
            break
        _, other_start, other_end = theirs[position]
        ratio = residual_ratio(
            lines[start:end], skeleton_lines[other_start:other_end]
        )
        if ratio >= threshold:
            unexpanded += 1
            report.add(
                severity, "R10", "segment %d" % (position + 1),
                unexpanded_message(ratio),
                position + 1,
            )
    report.measure("unexpanded_segments", "%d/%d" % (unexpanded, len(mine)))

    # Evergreen is one line projected from the first takeaway. Left as it was
    # rendered, the note's single most prominent claim is a placeholder. Its
    # 「」 is the renderer's typography rather than a quotation the guideline
    # asked for, so unlike a segment body it is measured with the quotes in.
    name = "Evergreen Note"
    if name in regions and name in skeleton_regions:
        ratio = residual_ratio(
            lines[slice(*regions[name])],
            skeleton_lines[slice(*skeleton_regions[name])],
            keep_quotes=True,
        )
        if ratio >= threshold:
            report.add(severity, "R10", name, unexpanded_message(ratio))


def unexpanded_message(ratio: float) -> str:
    """What R10 says, with the number that made it say it.

    The percentage is the finding's whole diagnostic value: "identical" told a
    writer to start over, "82%" tells them a fifth of the paragraph is theirs
    and the rest is not.
    """
    return "unexpanded skeleton (%d%% of the body is render output)" % round(
        ratio * 100
    )


# --------------------------------------------------------------------------
# R11: the same sentence in two sections
# --------------------------------------------------------------------------
# The blind review's flattest observation: one note carried the same three
# sentences in Summary, in the Note body and again under "我應該記住的 3 件事",
# word for word. Nothing checked for it, because every rule up to here looks at
# one section at a time. A reader hits the third copy and stops trusting that
# anything in the file was chosen.

#: Where R11 looks, as ``(name, heading, heading that ends it or None)``.
#: 講者骨架 and References are deliberately out: the outline is one line per
#: segment projected from the document, and References is the appendix whose
#: whole job is to repeat terms that appear elsewhere. Frontmatter is out
#: because it is before the first heading and is the profile's, not the
#: writer's.
R11_REGIONS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("Evergreen Note", "# Evergreen Note", "# Summary"),
    ("Summary", "# Summary", "## 講者骨架"),
    ("Note (layer 1-3)", "# Note (layer 1-3)", "### References"),
    ("題目", "## 題目", "## 學習驗證"),
    ("學習驗證", "## 學習驗證", None),
)

#: The one pair of sections allowed to hold the same sentence. Guideline 7.1
#: requires Summary to quote ``takeaways_zh`` verbatim, and `l2n render`
#: projects Evergreen from the first of those takeaways -- so a note that has
#: not yet rewritten Evergreen would report R11 for doing exactly what the
#: renderer did. R10 already reports that note, and says something more useful.
R11_ALLOWED_PAIR = frozenset(("Evergreen Note", "Summary"))

#: Where one sentence ends and the next begins, full-width or half.
SENTENCE_BREAK = re.compile(r"[。！？；!?;]+")

#: An ordered-list marker: ``1. ``, ``2) ``, ``3、``. Stripped so a Summary
#: bullet and a numbered copy of it under the learning check compare equal.
ORDERED_MARKER = re.compile(r"^\d+[.)、]\s*")


def r11_regions(lines: Sequence[str]) -> Dict[str, Tuple[int, int]]:
    """The sections R11 compares, in reading order, skipping absent ones."""
    found: Dict[str, Tuple[int, int]] = {}
    for name, heading, stop_heading in R11_REGIONS:
        start = heading_index(lines, heading)
        if start < 0:
            continue
        end = section_bounds(lines, start)
        if stop_heading:
            stop = heading_index(lines, stop_heading)
            if 0 <= stop < end:
                end = stop
        found[name] = (start + 1, end)
    return found


def region_sentences(lines: Sequence[str]) -> List[Tuple[str, str]]:
    """``(normalised, as written)`` for every sentence in a region's lines.

    A quotation is a sentence like any other here, unlike R10. Keeping the
    speaker's words is required once; saying them again three sections later
    is the repetition this rule is about.

    List markers come off, bulleted and numbered alike. The blind review's
    exact case was a Summary written as ``- 某句`` and reproduced under the
    learning check as ``> 1. 某句``; a rule that compared those as different
    strings would have missed the one thing it was built for.
    """
    out: List[Tuple[str, str]] = []
    for line in lines:
        body = line.strip()
        if not body or body.startswith("#") or body.startswith("|"):
            continue
        if body.startswith("<!--"):
            continue
        while body.startswith(">"):
            body = body[1:].lstrip()
        for marker in ("- ", "* ", "+ "):
            if body.startswith(marker):
                body = body[len(marker):].lstrip()
                break
        else:
            numbered = ORDERED_MARKER.match(body)
            if numbered:
                body = body[numbered.end():].lstrip()
        if body.startswith("!["):
            continue
        for piece in SENTENCE_BREAK.split(body):
            written = piece.strip()
            if not written:
                continue
            normalised = rules.comparison_text(written)
            if len(normalised) >= guideline.DUPLICATE_SENTENCE_CHARS:
                out.append((normalised, written))
    return out


def _rule_duplicate_sentences(
    lines: Sequence[str], note: Path, report: StageReport
) -> None:
    """R11: one sentence, word for word, in two or more sections.

    Warning rather than error, and reported once per sentence rather than once
    per copy, so a note that repeats one line does not bury the rest of the
    report.
    """
    seen: Dict[str, Tuple[str, List[str]]] = {}
    for name, bounds in r11_regions(lines).items():
        for normalised, written in region_sentences(lines[slice(*bounds)]):
            entry = seen.setdefault(normalised, (written, []))
            if name not in entry[1]:
                entry[1].append(name)
    for written, names in seen.values():
        if len(names) < 2 or set(names) == R11_ALLOWED_PAIR:
            continue
        # Emphasis and the quotation marks around it are how one copy happened
        # to be typeset, not part of the sentence; they only make the excerpt
        # harder to match against the file by eye.
        excerpt = written.strip("*「」 ")[: guideline.FINDING_EXCERPT_CHARS]
        report.add(
            "warn", "R11", " + ".join(names),
            "the same sentence is in %d sections: 「%s...」" % (len(names), excerpt),
        )


def count_ai_drafts(lines: Sequence[str]) -> int:
    """How many `<!-- ai-draft -->` markers the note still carries.

    Not a rule: a note may legitimately keep a drafted candidate the reader
    decided to keep. It is the one number that says whether anybody has been
    through the file at all, and until now nothing counted it -- the field run
    shipped a note with every marker still in place and a clean report.
    """
    return sum(line.count(AI_DRAFT_MARK) for line in lines)


def _rule_sections(lines: Sequence[str], note: Path, report: StageReport) -> None:
    """R1: every mandatory section exists, in the mandated order."""
    seen: List[int] = []
    for heading, name in guideline.MANDATORY_SECTIONS:
        position = heading_index(lines, heading)
        if position < 0:
            report.add("error", "R1", note.name,
                       "mandatory section is missing: %s" % heading)
            continue
        if seen and position < seen[-1]:
            report.add("error", "R1", "%s:%d" % (note.name, position + 1),
                       "section %s is out of order" % name)
        seen.append(position)


def _rule_embeds(text: str, note: Path, report: StageReport) -> None:
    """R2: every embed resolves to a file that exists beside the note."""
    for ref in sorted(set(match.group(1) for match in NOTE_EMBED.finditer(text))):
        if not _resolve_embed(note, ref):
            report.add("error", "R2", ref,
                       "the note embeds a file that does not exist")


def _rule_unverified(
    data: Mapping[str, Any],
    lines: Sequence[str],
    regions: Mapping[str, Tuple[int, int]],
    note: Path,
    report: StageReport,
) -> None:
    """R3: an unverified term belongs under References and nowhere else."""
    terms = [t for t in (data.get("unverified_terms") or []) if isinstance(t, str) and t]
    if not terms:
        return
    body = "\n".join(lines[slice(*regions["note"])]) if "note" in regions else ""
    references = (
        "\n".join(lines[slice(*regions["references"])])
        if "references" in regions else ""
    )
    for term in terms:
        if term not in references:
            report.add("error", "R3", "unverified_terms[%r]" % term,
                       "the term is not listed under References")
        if term in body:
            report.add("error", "R3", "unverified_terms[%r]" % term,
                       "the term appears in the Note body; it must stay in References")


def _rule_corrections(
    lines: Sequence[str],
    regions: Mapping[str, Tuple[int, int]],
    note: Path,
    report: StageReport,
) -> None:
    """R4: the correction table exists and no row is half-filled."""
    if "references" not in regions:
        report.add("error", "R4", note.name,
                   "there is no References section to hold the correction table")
        return
    rows = correction_rows(lines, regions["references"])
    if rows is None:
        report.add("error", "R4", note.name,
                   "the References correction table is missing (columns %s)"
                   % ", ".join(CORRECTION_HEADER))
        return
    for position, cells in rows:
        for column, index in zip(CORRECTION_HEADER, range(len(CORRECTION_HEADER))):
            value = cells[index].strip() if index < len(cells) else ""
            if not value or value == "-":
                report.add("error", "R4", "%s:%d" % (note.name, position + 1),
                           "correction row has an empty %s cell" % column)


def _rule_transcript_paste(
    lines: Sequence[str],
    regions: Mapping[str, Tuple[int, int]],
    json_path: Path,
    note: Path,
    transcript: Optional[Path],
    profile: str,
    report: StageReport,
) -> None:
    """R5: no unmarked run of transcript long enough to be a paste."""
    if "note" not in regions:
        return
    path = Path(transcript) if transcript else json_path.with_name(
        json_path.stem + ".srt"
    )
    if not path.is_file():
        report.add("warn", "R5", path.name,
                   "no transcript beside the document; R5 was not checked")
        return
    size = guideline.TRANSCRIPT_RUN_CHARS
    runs = transcript_runs(transcript_text(path), size)
    if not runs:
        report.add("warn", "R5", path.name,
                   "the transcript is shorter than %d characters; R5 was not checked"
                   % size)
        return
    severity = "error" if loader.transcript_paste_severity(profile) == "error" else "warn"
    sections = list(segment_sections(lines, regions["note"]) or [
        ("Note (layer 1-3)", *regions["note"])
    ])
    # Evergreen, Summary and the answers under 題目 are prose a reader trusts
    # exactly as much as the Note body, and the guideline's own appendix
    # records a pasted Summary bullet that "the machine cannot catch". It can
    # now. The exemptions do not change: a 「」 span or a blockquote is still a
    # marked quotation wherever it appears.
    for name, _heading, _stop in R5_EXTRA_REGIONS:
        if name in regions:
            sections.append((name, *regions[name]))
    for title, start, end in sections:
        for position in range(start, end):
            line = lines[position]
            if not line.strip() or line.lstrip().startswith(">"):
                continue
            hit = pasted_run(unquoted_residue(line), runs, size)
            if hit:
                report.add(
                    severity, "R5", title,
                    "%d or more characters copied from the transcript without "
                    "quotation marks: %s..."
                    % (size, hit[:guideline.FINDING_EXCERPT_CHARS]),
                )
                break


def _rule_faithful_quotes(
    lines: Sequence[str],
    regions: Mapping[str, Tuple[int, int]],
    style: str,
    note: Path,
    report: StageReport,
) -> None:
    """R6: in faithful style every section quotes the speaker at least once."""
    if style != "faithful" or "note" not in regions:
        return
    for title, start, end in segment_sections(lines, regions["note"]):
        if not any(is_marked_quote(lines[p]) for p in range(start, end)):
            report.add("error", "R6", title,
                       "faithful style needs at least one quotation in this section")


def _rule_privacy(
    lines: Sequence[str], profile: str, note: Path, report: StageReport
) -> None:
    """R7: nothing matching the profile's personal-data patterns."""
    for pattern in loader.privacy_patterns(profile):
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            report.add("error", "R7", "privacy.toml",
                       "pattern %r does not compile: %s" % (pattern, exc))
            continue
        for position, line in enumerate(lines):
            if compiled.search(line):
                report.add("error", "R7", "%s:%d" % (note.name, position + 1),
                           "line matches the privacy pattern %r" % pattern)
                break


def _rule_placeholder(
    lines: Sequence[str],
    regions: Mapping[str, Tuple[int, int]],
    note: Path,
    report: StageReport,
) -> None:
    """R8: filler text where a section should simply have been deleted."""
    if "note" not in regions:
        return
    for title, start, end in segment_sections(lines, regions["note"]) or [
        ("Note (layer 1-3)", *regions["note"])
    ]:
        for position in range(start, end):
            hit = next((t for t in PLACEHOLDER_TEXT if t in lines[position]), None)
            if hit:
                report.add("warn", "R8", title,
                           "placeholder text %r; delete the section instead" % hit)
                break


def question_text(line: str) -> str:
    """One numbered question line with its ``1.`` / ``2)`` prefix removed."""
    return re.sub(r"^\s*\d+[.)\u3001]\s*", "", line).strip()


def _rule_questions(
    lines: Sequence[str], note: Path, report: StageReport
) -> None:
    """R9: the question section is neither short nor purely definitional.

    A warning rather than an error, because the count is a proxy: three
    questions with one inference question among them is the smallest shape that
    still forces retrieval instead of rereading. A missing section is R1's
    business, so this rule stays silent about it.
    """
    start = heading_index(lines, "## 題目")
    if start < 0:
        return
    body = lines[start + 1:section_bounds(lines, start)]
    questions = [line for line in body if QUESTION_LINE.match(line)]
    count = len(questions)
    if count < guideline.MIN_QUESTIONS:
        report.add("warn", "R9", note.name,
                   "the question section carries %d questions; at least %d are "
                   "needed" % (count, guideline.MIN_QUESTIONS))
    if not any(question_text(line).startswith(guideline.INFERENCE_MARK)
               for line in questions):
        report.add("warn", "R9", note.name,
                   "no question starts with %s; a definition question is "
                   "answered by rereading. Write it as: %s"
                   % (guideline.INFERENCE_MARK,
                      guideline.INFERENCE_PREFIX_HINT % guideline.INFERENCE_MARK))


# ==========================================================================
# stage acceptance: check --all
# ==========================================================================
# One stem, every stage that has something to check. "Applicable" is decided by
# what is on disk rather than by a flag: a lecture whose note has not been
# written yet is not failing the note stage, it simply has not reached it, and
# reporting an error for a file the pipeline has not produced yet would make
# `check --all` useless as a progress check.

#: Suffix each stage's target carries, in pipeline order. ``note`` is checked
#: against the canonical JSON, so it shares ``json``'s target and names the note
#: separately.
STAGE_SUFFIXES: Tuple[Tuple[str, str], ...] = (
    ("transcribe", ".srt"),
    ("frames", ".frames.json"),
    ("json", ".json"),
    ("note", ".v4.md"),
)


def stem_path(target: Path) -> Path:
    """The stem behind any of a lecture's files, given any one of them.

    Accepts the bare stem (``lectures/2024 talk``) as well as a real file, so a
    caller can pass whatever it has. The suffix list is ordered longest first:
    ``.frames.json`` must be stripped whole, not left as ``.frames``.
    """
    path = Path(target)
    for suffix in (".frames.json", ".frames_ocr.json", ".v4.md", ".json", ".srt"):
        if path.name.endswith(suffix):
            return path.with_name(path.name[: -len(suffix)])
    return path


def stage_targets(target: Path) -> Dict[str, Path]:
    """Each stage's target file for one stem, whether or not it exists."""
    base = stem_path(target)
    return {
        stage: base.with_name(base.name + suffix)
        for stage, suffix in STAGE_SUFFIXES
    }


def check_all(
    target: Path, style: Optional[str] = None, profile: Optional[str] = None
) -> List[StageReport]:
    """Run every stage check whose target exists, in pipeline order."""
    targets = stage_targets(target)
    reports: List[StageReport] = []
    if targets["transcribe"].is_file():
        reports.append(check_transcribe_stage(targets["transcribe"]))
    if targets["frames"].is_file():
        reports.append(check_frames(targets["frames"]))
    if targets["json"].is_file():
        reports.append(check_json(targets["json"]))
        if targets["note"].is_file():
            reports.append(check_note_stage(
                targets["json"],
                targets["note"],
                transcript=(
                    targets["transcribe"]
                    if targets["transcribe"].is_file() else None
                ),
                style=style,
                profile=profile,
            ))
    return reports


def worst_exit_code(reports: Sequence[StageReport]) -> int:
    """The maximum of the stage exit codes, which is the contract for --all."""
    return max([report.exit_code() for report in reports] or [0])


__all__ = [
    "STAGE_SUFFIXES",
    "check_all",
    "stage_targets",
    "stem_path",
    "worst_exit_code",
    "CLOCK_TOLERANCE_SEC",
    "CORRECTIONS_SUFFIX",
    "CORRECTION_HEADER",
    "NOTE_EMBED",
    "PLACEHOLDER_TEXT",
    "QUESTION_LINE",
    "QUOTED_SPAN",
    "question_text",
    "comparable_body",
    "count_ai_drafts",
    "R5_EXTRA_REGIONS",
    "check_note_stage",
    "correction_rows",
    "heading_index",
    "is_marked_quote",
    "note_lines",
    "note_regions",
    "pasted_run",
    "section_bounds",
    "segment_sections",
    "transcript_runs",
    "transcript_text",
    "unquoted_residue",
    "CUE_OVERLAP_TOLERANCE_SEC",
    "DIGEST_PREVIEW",
    "MIN_CUE_SEC",
    "RAW_SUFFIX",
    "MAX_SEGMENT_GAP_SEC",
    "MAX_SUMMARY_CHARS",
    "MAX_TAKEAWAYS",
    "MIN_BULLETS",
    "MIN_SUMMARY_CHARS",
    "MIN_TAKEAWAYS",
    "LEGACY_MESSAGE",
    "Report",
    "StageReport",
    "check_document",
    "check_frames",
    "check_json",
    "check_json_frames",
    "check_note",
    "check_path",
    "check_segments",
    "check_transcribe",
    "check_transcribe_stage",
    "cue_index_numbers",
    "collect_targets",
    "exit_code",
    "note_image_refs",
    "parse_clock",
]

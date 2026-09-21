"""Structured audit: one report object covering schema, content and derivatives.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/lecture_audit.py
Inspired by drpwchen/lecture-to-notes scripts/audit_note.py @79053a3

The mechanical check prints for a human. This produces a record: a JSON report
with counts, findings and the hash of every input it looked at, so a run can be
compared against a later one.

The derivative comparison is the part that matters. A viewer, a chapter file and
a note are all projections of the canonical JSON, and the only way to know they
still agree with it is to regenerate them and compare. Anything else is trusting
that whoever edited the JSON last also re-ran everything.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from lecture2notes.notes.render import render_note
from lecture2notes.notes.rules import validate_segment_content
from lecture2notes.outputs.pbf import pbf_text
from lecture2notes.schema.model import (
    Finding,
    segment_end,
    segment_start,
    validate_lecture_schema,
)

#: The viewer embeds its source segments here so the audit can read them back.
SNAPSHOT_RE = re.compile(
    r'<script id="canonical-snapshot" type="application/json">(.*?)</script>',
    re.DOTALL,
)
#: A segment carrying fewer frames than this is flagged, not failed.
FRAME_TARGET = 4


@dataclass
class AuditReport:
    lecture_id: str
    stage: str
    findings: List[Finding] = field(default_factory=list)
    input_hashes: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    @property
    def errors(self) -> int:
        return sum(finding.severity == "error" for finding in self.findings)

    @property
    def warnings(self) -> int:
        return sum(finding.severity == "warning" for finding in self.findings)

    def exit_code(self) -> int:
        if self.errors:
            return 2
        return 1 if self.warnings else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lecture_id": self.lecture_id,
            "stage": self.stage,
            "ok": self.ok,
            "findings": [asdict(finding) for finding in self.findings],
            "input_hashes": self.input_hashes,
            "summary": {"errors": self.errors, "warnings": self.warnings},
        }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_snapshot(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The chapter spine: index, times and title of every segment."""
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    snapshot: List[Dict[str, Any]] = []
    for position, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            continue
        snapshot.append({
            "index": segment.get("index", position + 1),
            "start_sec": segment_start(segment),
            "end_sec": segment_end(segment),
            "title": segment.get("title"),
        })
    return snapshot


def snapshot_from_payload(payload: Any) -> Optional[List[Dict[str, Any]]]:
    """Read a snapshot written as a bare list or as ``{"segments": [...]}``."""
    raw = payload.get("segments") if isinstance(payload, Mapping) else payload
    if not isinstance(raw, list):
        return None
    output: List[Dict[str, Any]] = []
    for position, segment in enumerate(raw):
        if not isinstance(segment, Mapping):
            return None
        try:
            output.append({
                "index": segment.get("index", position + 1),
                "start_sec": segment_start(segment),
                "end_sec": segment_end(segment),
                "title": segment.get("title"),
            })
        except (KeyError, TypeError, ValueError):
            return None
    return output


def compare_derived(
    data: Mapping[str, Any],
    viewer_path: Optional[Path] = None,
    pbf_path: Optional[Path] = None,
    note_path: Optional[Path] = None,
) -> List[Finding]:
    """Check each derived file against what the canonical JSON would produce."""
    findings: List[Finding] = []
    if viewer_path is not None:
        viewer_path = Path(viewer_path)
        if not viewer_path.is_file():
            findings.append(Finding(
                "error", "viewer_missing", "viewer output is missing", path=str(viewer_path)
            ))
        else:
            try:
                match = SNAPSHOT_RE.search(viewer_path.read_text(encoding="utf-8"))
                actual = snapshot_from_payload(json.loads(match.group(1))) if match else None
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                actual = None
            if actual != canonical_snapshot(data):
                findings.append(Finding(
                    "error", "viewer_snapshot_mismatch",
                    "viewer chapter order, times or titles differ from the canonical JSON",
                    path=str(viewer_path),
                ))
    if pbf_path is not None:
        pbf_path = Path(pbf_path)
        if not pbf_path.is_file():
            findings.append(Finding(
                "error", "pbf_missing", "chapter file is missing", path=str(pbf_path)
            ))
        else:
            try:
                actual_text = pbf_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                actual_text = ""
            if actual_text != pbf_text(data):
                findings.append(Finding(
                    "error", "pbf_mismatch",
                    "chapter order, times or titles differ from the canonical JSON",
                    path=str(pbf_path),
                ))
    if note_path is not None:
        note_path = Path(note_path)
        if not note_path.is_file():
            findings.append(Finding(
                "error", "note_missing", "note is missing", path=str(note_path)
            ))
        else:
            try:
                actual_text = note_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                actual_text = ""
            if actual_text != render_note(data):
                findings.append(Finding(
                    "error", "note_content_mismatch",
                    "note content differs from the canonical JSON",
                    path=str(note_path),
                ))
    return findings


def audit_lecture(
    data: Mapping[str, Any],
    base_dir: Path,
    transcripts: Optional[Mapping[int, str]] = None,
    viewer_path: Optional[Path] = None,
    pbf_path: Optional[Path] = None,
    note_path: Optional[Path] = None,
    check_content: bool = True,
    frame_target: int = FRAME_TARGET,
) -> AuditReport:
    """Schema, content, and derivative findings collected in one pass."""
    findings: List[Finding] = list(validate_lecture_schema(data, Path(base_dir)))
    transcript_map = transcripts or {}
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    for position, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            continue
        if check_content:
            for finding in validate_segment_content(segment, transcript_map.get(position, "")):
                findings.append(Finding(
                    finding.severity, finding.code, finding.message, position, finding.path
                ))
        frames = segment.get("frames") if isinstance(segment.get("frames"), list) else []
        if 1 <= len(frames) < frame_target:
            findings.append(Finding(
                "warning", "frame_below_target",
                "segment has fewer than %d frames" % frame_target, position,
            ))
    findings.extend(compare_derived(data, viewer_path, pbf_path, note_path))

    input_hashes = {
        label: file_sha256(path)
        for label, path in (
            ("viewer", viewer_path), ("pbf", pbf_path), ("note", note_path)
        )
        if path is not None and Path(path).is_file()
    }
    lecture_id = str(data.get("lecture_id") or data.get("stem") or data.get("title") or "unknown")
    return AuditReport(lecture_id, "audit", findings, input_hashes)


def audit_course(
    report_paths: Sequence[Path],
    homepage_path: Optional[Path] = None,
    expected_viewer_names: Sequence[str] = (),
    expected_count: Optional[int] = None,
) -> AuditReport:
    """Roll up per-lecture reports and check the course homepage links.

    ``expected_count`` is optional: a course has whatever number of lectures it
    has, and only a caller that knows the number should assert it.
    """
    findings: List[Finding] = []
    reports: List[Dict[str, Any]] = []
    for path in report_paths:
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            findings.append(Finding(
                "error", "course_report_invalid",
                "a lecture audit report is unreadable", path=str(path),
            ))
            continue
        if not isinstance(value, dict):
            findings.append(Finding(
                "error", "course_report_invalid",
                "a lecture audit report is invalid", path=str(path),
            ))
            continue
        reports.append(value)

    lecture_ids = [str(report.get("lecture_id") or "") for report in reports]
    if expected_count is not None and (
        len(reports) != expected_count or len(set(lecture_ids)) != expected_count
    ):
        findings.append(Finding(
            "error", "course_lecture_count",
            "expected %d unique lecture reports, got %d" % (expected_count, len(set(lecture_ids))),
        ))
    for report in reports:
        if report.get("ok") is not True:
            findings.append(Finding(
                "error", "course_lecture_failed",
                "lecture %s failed its audit" % report.get("lecture_id"),
            ))

    hashes: Dict[str, str] = {}
    if homepage_path is not None:
        homepage_path = Path(homepage_path)
        if not homepage_path.is_file():
            findings.append(Finding(
                "error", "homepage_missing", "the course homepage is missing",
                path=str(homepage_path),
            ))
        else:
            homepage = homepage_path.read_text(encoding="utf-8")
            for viewer_name in expected_viewer_names:
                if viewer_name not in homepage:
                    findings.append(Finding(
                        "error", "homepage_link_missing",
                        "the homepage does not link to %s" % viewer_name,
                    ))
            hashes["homepage"] = file_sha256(homepage_path)
    return AuditReport("course", "course-audit", findings, hashes)


def write_report(path: Path, report: AuditReport, guideline_version: str = "") -> Path:
    """Write the audit as JSON, stamped with the guideline version it applied."""
    payload = report.to_dict()
    payload["guideline_version"] = guideline_version
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


__all__ = [
    "AuditReport",
    "FRAME_TARGET",
    "SNAPSHOT_RE",
    "audit_course",
    "audit_lecture",
    "canonical_snapshot",
    "compare_derived",
    "file_sha256",
    "snapshot_from_payload",
    "write_report",
]

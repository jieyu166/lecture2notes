"""Read-only preflight for a rebuild: say what would happen before anything does.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/rebuild_course.py
(``LectureInputs``, ``PreflightResult``, ``pair_lectures``, ``run_preflight``,
``probe_replace_semantics``).

A rebuild overwrites a course folder. Finding out during the rebuild that
ffmpeg is missing, or that two files claim the same lecture, or that the disk is
full, means finding out with the folder half-replaced. So every one of those
questions is answered first, and the preflight writes nothing: the caller gets a
list of what exists, what would be created and what would be overwritten.

Two things from the source are not ported, because they encoded one specific
course rather than a rule: a fixed lecture count and a fixed existing-frame
count. Both are parameters here, and both default to "do not check".

Pairing is strict on purpose. One video, one subtitle and one document per
lecture; anything else raises instead of picking. Guessing which of two files is
the real one is how a rebuild silently publishes the wrong recording.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from lecture2notes.notes.rewrite import contains_sensitive_data
from lecture2notes.schema.model import Finding, load_lecture

#: JSON files in a course folder that are outputs, not lecture documents.
DERIVATIVE_JSON_NAMES = {
    "course-run.json", "course.audit.json", "preflight.json",
    "manifest.json", "report.json", "package.json",
}
DERIVATIVE_JSON_SUFFIXES = (
    ".frames.json", ".frames_ocr.json", ".audit.json", ".package.json",
    ".report.json", ".manifest.json", ".corrections.json", ".transaction.json",
    ".publish.json", ".offset.json", ".curation.json",
)
#: Free space a rebuild needs before it starts.
DEFAULT_MIN_FREE_BYTES = 5 * 1024 ** 3
#: External tools a rebuild cannot proceed without.
REQUIRED_COMMANDS = ("ffmpeg", "ffprobe")


@dataclass(frozen=True)
class LectureInputs:
    """The three files that make one lecture."""

    lecture_id: str
    video: Path
    subtitle: Path
    document: Path

    def to_dict(self) -> Dict[str, str]:
        return {
            "lecture_id": self.lecture_id,
            "video": str(self.video),
            "subtitle": str(self.subtitle),
            "document": str(self.document),
        }


@dataclass
class PlannedFile:
    """One file the rebuild would touch, and whether it is there now."""

    path: str
    exists: bool
    action: str  # "create" or "overwrite"


@dataclass
class PreflightResult:
    pairs: List[LectureInputs] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    planned: List[PlannedFile] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    def exit_code(self) -> int:
        if any(finding.severity == "error" for finding in self.findings):
            return 2
        return 1 if self.findings else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "pairs": [pair.to_dict() for pair in self.pairs],
            "findings": [finding.to_dict() for finding in self.findings],
            "planned": [
                {"path": item.path, "exists": item.exists, "action": item.action}
                for item in self.planned
            ],
        }


def is_lecture_document(path: Path) -> bool:
    name = Path(path).name.casefold()
    return (
        Path(path).suffix.casefold() == ".json"
        and name not in DERIVATIVE_JSON_NAMES
        and not name.endswith(DERIVATIVE_JSON_SUFFIXES)
        and not name.startswith("_")
    )


def pair_lectures(root: Path) -> List[LectureInputs]:
    """Group a folder into lectures, raising on any ambiguity."""
    groups: Dict[str, Dict[str, List[Path]]] = {}
    for path in sorted(Path(root).iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".mp4", ".srt", ".json"}:
            continue
        if suffix == ".json" and not is_lecture_document(path):
            continue
        if suffix == ".srt" and ".raw." in path.name:
            continue
        lecture_id = path.stem.split()[0]
        groups.setdefault(lecture_id, {}).setdefault(suffix, []).append(path)

    pairs: List[LectureInputs] = []
    for lecture_id, files in sorted(groups.items()):
        conflicts = {suffix: paths for suffix, paths in files.items() if len(paths) != 1}
        if set(files) != {".mp4", ".srt", ".json"} or conflicts:
            detail = {suffix: [str(p) for p in paths] for suffix, paths in files.items()}
            raise ValueError("lecture %s pairing conflict: %s" % (lecture_id, detail))
        pairs.append(LectureInputs(
            lecture_id, files[".mp4"][0], files[".srt"][0], files[".json"][0]
        ))
    return pairs


def planned_outputs(pair: LectureInputs, root: Path) -> List[PlannedFile]:
    """The files a rebuild would write for one lecture, with their current state."""
    stem = pair.document.stem
    targets = [
        Path(root) / ("%s.viewer.html" % stem),
        Path(root) / ("%s.pbf" % stem),
        Path(root) / ("%s.v4.md" % stem),
        Path(root) / ("%s.audit.json" % stem),
    ]
    return [
        PlannedFile(str(path), path.exists(), "overwrite" if path.exists() else "create")
        for path in targets
    ]


def existing_parent(path: Path) -> Path:
    current = Path(path)
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def probe_replace_semantics(live_root: Path, staging_root: Path) -> None:
    """Prove a cross-root replace really works, using throwaway files only.

    Opt-in, because it is the one part of the preflight that writes anything. The
    filenames carry a fresh UUID so the probe can never collide with real data.
    """
    token = uuid.uuid4().hex
    live_probe = Path(live_root) / (".l2n-live-probe-%s.tmp" % token)
    staged_probe = Path(staging_root) / (".l2n-stage-probe-%s.tmp" % token)
    try:
        live_probe.write_bytes(b"old-probe")
        staged_probe.write_bytes(b"new-probe")
        if hashlib.sha256(live_probe.read_bytes()).digest() != hashlib.sha256(b"old-probe").digest():
            raise IOError("live probe verification failed")
        if hashlib.sha256(staged_probe.read_bytes()).digest() != hashlib.sha256(b"new-probe").digest():
            raise IOError("staging probe verification failed")
        os.replace(staged_probe, live_probe)
        if hashlib.sha256(live_probe.read_bytes()).digest() != hashlib.sha256(b"new-probe").digest():
            raise IOError("cross-root replace verification failed")
    finally:
        for path in (staged_probe, live_probe):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def run_preflight(
    root: Path,
    staging_root: Optional[Path] = None,
    backup_root: Optional[Path] = None,
    expected_lecture_count: Optional[int] = None,
    command_lookup: Callable[[str], Optional[str]] = shutil.which,
    module_available: Callable[[str], Any] = importlib.util.find_spec,
    required_modules: Sequence[str] = ("rapidocr_onnxruntime",),
    minimum_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    allow_replace_probe: bool = False,
    scan_sensitive: bool = True,
) -> PreflightResult:
    """Inspect the roots, the sources and the dependencies. Writes nothing.

    The lookups are injected so this can be tested without installing anything.
    """
    root = Path(root)
    findings: List[Finding] = []
    if not root.is_dir():
        return PreflightResult(
            [], [Finding("error", "root_missing", "course root is not readable", path=str(root))]
        )
    if not os.access(root, os.W_OK):
        findings.append(Finding(
            "error", "live_root_not_writable",
            "the course root is not writable", path=str(root),
        ))
    try:
        pairs = pair_lectures(root)
    except ValueError as exc:
        findings.append(Finding("error", "pairing_conflict", str(exc)))
        return PreflightResult([], findings)
    if expected_lecture_count is not None and len(pairs) != expected_lecture_count:
        findings.append(Finding(
            "error", "lecture_count",
            "expected %d paired lectures, found %d" % (expected_lecture_count, len(pairs)),
        ))

    for command in REQUIRED_COMMANDS:
        if command_lookup(command) is None:
            findings.append(Finding(
                "error", "%s_missing" % command, "%s is not available" % command
            ))
    for module_name in required_modules:
        if module_available(module_name) is None:
            findings.append(Finding(
                "error", "%s_missing" % module_name,
                "the Python module %s is not available" % module_name,
            ))

    for label, target in (("staging", staging_root), ("backup", backup_root)):
        if target is None:
            continue
        parent = existing_parent(Path(target))
        if not parent.exists() or not os.access(parent, os.W_OK):
            findings.append(Finding(
                "error", "%s_not_writable" % label,
                "the %s parent is not writable" % label, path=str(parent),
            ))
        elif shutil.disk_usage(parent).free < minimum_free_bytes:
            findings.append(Finding(
                "error", "%s_space" % label,
                "%s has less than %d free bytes" % (label, minimum_free_bytes),
                path=str(parent),
            ))

    if scan_sensitive:
        for pair in pairs:
            text = pair.subtitle.read_text(encoding="utf-8-sig", errors="replace")
            text += "\n" + pair.document.read_text(encoding="utf-8-sig", errors="replace")
            if contains_sensitive_data(text):
                findings.append(Finding(
                    "warning", "sensitive_input",
                    "lecture %s contains personal-data patterns; remote rewriting is refused"
                    % pair.lecture_id,
                    path=str(pair.document),
                ))

    if allow_replace_probe:
        if staging_root is None:
            findings.append(Finding(
                "error", "replace_probe_staging_missing",
                "the replace probe needs a staging root",
            ))
        elif not any(finding.severity == "error" for finding in findings):
            try:
                Path(staging_root).mkdir(parents=True, exist_ok=True)
                probe_replace_semantics(root, Path(staging_root))
            except Exception as exc:
                findings.append(Finding("error", "replace_probe_failed", str(exc)))

    planned: List[PlannedFile] = []
    for pair in pairs:
        planned.extend(planned_outputs(pair, root))
    return PreflightResult(pairs, findings, planned)


def frame_paths(document: Mapping[str, Any]) -> List[str]:
    """Every frame path in a document, for a caller counting or verifying them."""
    out: List[str] = []
    for segment in document.get("segments", []):
        if not isinstance(segment, dict):
            continue
        for frame in segment.get("frames", []):
            if isinstance(frame, dict) and isinstance(frame.get("path"), str):
                out.append(frame["path"])
            elif isinstance(frame, str):
                out.append(frame)
    return out


def load_document(path: Path) -> Dict[str, Any]:
    return load_lecture(Path(path))


__all__ = [
    "DEFAULT_MIN_FREE_BYTES",
    "DERIVATIVE_JSON_NAMES",
    "DERIVATIVE_JSON_SUFFIXES",
    "LectureInputs",
    "PlannedFile",
    "PreflightResult",
    "REQUIRED_COMMANDS",
    "existing_parent",
    "frame_paths",
    "is_lecture_document",
    "load_document",
    "pair_lectures",
    "planned_outputs",
    "probe_replace_semantics",
    "run_preflight",
]

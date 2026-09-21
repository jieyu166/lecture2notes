"""Promote staged candidate frames into the formal frame set.

``--stage`` writes candidates to ``staging/frames/`` and leaves the canonical
document untouched. ``--curate`` is the gate between that staging area and
``frames/``: every candidate is re-hashed against the row the capture wrote for
it, and a file that no longer hashes to its manifest value is rejected rather
than copied. Only what survives is written back into ``<stem>.frames.json`` and
merged into the canonical JSON, so the document can only ever reference a frame
whose bytes were verified at promotion time.

Two kinds of rejection exist and they are not the same thing:

* an *integrity* rejection (``hash mismatch``, ``missing``, ``unsafe path``)
  means the staging area cannot be trusted. Every remaining candidate is still
  processed -- a half-curated lecture is worse than a fully curated one with a
  reported hole -- but the command ends with exit 2.
* a *curation* rejection (``over max-per-segment``) is the feature working. Four
  frames per segment is a design decision, not a failure, and it does not change
  the exit code.

This module is deliberately separate from :mod:`lecture2notes.frames.curator`.
That one is the ported quality-ranking curator, which takes a richer candidate
shape (perceptual hash, luma, sharpness) from a different producer; this one
works on the manifest rows that :mod:`lecture2notes.frames.capture` writes.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote

from lecture2notes import _out
from lecture2notes.frames.manifest import FRAMES_DIRNAME, sha256_file
from lecture2notes.schema.model import path_within_base, safe_relative_frame_path

#: Rejection reasons. The strings are part of the acceptance contract.
REASON_HASH_MISMATCH = "hash mismatch"
REASON_MISSING = "missing"
REASON_UNSAFE_PATH = "unsafe path"
REASON_NO_HASH = "no sha256 in manifest"
REASON_OVER_CAP = "over max-per-segment"
REASON_COPY_FAILED = "copy verification failed"

#: A rejection for one of these means the run failed, not that curation worked.
BLOCKING_REASONS = (
    REASON_HASH_MISMATCH,
    REASON_MISSING,
    REASON_UNSAFE_PATH,
    REASON_NO_HASH,
    REASON_COPY_FAILED,
)

#: How many frames a segment keeps.
DEFAULT_MAX_PER_SEGMENT = 4

CURATION_SCHEMA = "lecture-frame-curation-v2"


@dataclass
class CurationResult:
    """Promoted manifest rows, rejected candidates, and the report to write."""

    promoted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    max_per_segment: int = DEFAULT_MAX_PER_SEGMENT

    @property
    def blocked(self) -> bool:
        """Did anything fail integrity, as opposed to simply not being chosen?"""
        return any(row.get("reason") in BLOCKING_REASONS for row in self.rejected)

    def exit_code(self) -> int:
        return 2 if self.blocked else 0

    def report(self) -> Dict[str, Any]:
        """The ``<stem>.curation.json`` body."""
        return {
            "schema": CURATION_SCHEMA,
            "max_per_segment": self.max_per_segment,
            "promoted_count": len(self.promoted),
            "rejected_count": len(self.rejected),
            "promoted": self.promoted,
            "rejected": self.rejected,
        }

    def lines(self) -> List[str]:
        """One contract-format line per rejection, for the console."""
        return [
            "%s curation %s: %s"
            % (
                "error" if row.get("reason") in BLOCKING_REASONS else "warn",
                row.get("frame", "-"),
                row.get("detail") or row.get("reason", "rejected"),
            )
            for row in self.rejected
        ]


def segment_of(segments: Sequence[Mapping[str, Any]], second: float) -> Optional[int]:
    """The 1-based index of the segment covering ``second``, by ``[start, end)``."""
    for position, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            continue
        try:
            start = float(segment.get("start_sec"))
            end = float(segment.get("end_sec"))
        except (TypeError, ValueError):
            continue
        if start <= second < end:
            index = segment.get("index")
            return int(index) if isinstance(index, int) else position
    return None


def _reject(
    row: Mapping[str, Any],
    reason: str,
    segment: Optional[int] = None,
    detail: str = "",
) -> Dict[str, Any]:
    rejection: Dict[str, Any] = {
        "frame": str(row.get("frame", "")),
        "timestamp_sec": row.get("timestamp_sec"),
        "reason": reason,
        "segment": segment,
    }
    if detail:
        rejection["detail"] = detail
    return rejection


def verify_candidate(
    row: Mapping[str, Any], staging_base: Path
) -> Tuple[Optional[Path], Optional[str], str]:
    """Resolve and re-hash one staged candidate.

    Returns ``(path, reason, detail)``; ``reason`` is None when the candidate is
    fit to promote.
    """
    relative = safe_relative_frame_path(unquote(str(row.get("frame", ""))))
    if relative is None:
        return None, REASON_UNSAFE_PATH, "not a safe relative path"
    resolved = path_within_base(staging_base, relative)
    if resolved is None:
        return None, REASON_UNSAFE_PATH, "resolves outside the lecture directory"
    if not resolved.is_file():
        return None, REASON_MISSING, "staged file does not exist"
    expected = row.get("sha256")
    if not isinstance(expected, str) or not expected:
        return None, REASON_NO_HASH, "manifest row carries no sha256 to verify against"
    actual = sha256_file(resolved)
    if actual != expected:
        return (
            None,
            REASON_HASH_MISMATCH,
            "manifest %s.. actual %s.." % (expected[:4], actual[:4]),
        )
    return resolved, None, ""


def curate(
    rows: Sequence[Mapping[str, Any]],
    base_dir: Path,
    document: Optional[Mapping[str, Any]] = None,
    max_per_segment: int = DEFAULT_MAX_PER_SEGMENT,
    frames_dirname: str = FRAMES_DIRNAME,
    progress_interval: float = 5.0,
) -> CurationResult:
    """Verify every staged candidate and copy the survivors into ``frames/``.

    ``document`` supplies the segment boundaries the cap is applied within. With
    no document every candidate shares one bucket, which still caps the total
    rather than silently promoting an unbounded set.
    """
    base = Path(base_dir)
    formal_dir = base / frames_dirname
    segments = []
    if isinstance(document, Mapping) and isinstance(document.get("segments"), list):
        segments = document["segments"]

    result = CurationResult(max_per_segment=int(max_per_segment))

    # Verify first, cap second. A tampered candidate must not consume one of the
    # four slots and push a good frame out: the acceptance case requires the
    # remaining candidates to be processed exactly as they would have been.
    ordered = sorted(
        (row for row in rows if isinstance(row, Mapping)),
        key=lambda row: float(row.get("timestamp_sec") or 0.0),
    )
    verified: List[Tuple[Optional[int], Dict[str, Any], Path]] = []
    for row in ordered:
        try:
            second = float(row.get("timestamp_sec"))
        except (TypeError, ValueError):
            result.rejected.append(
                _reject(row, REASON_MISSING, None, "timestamp_sec is not a number")
            )
            continue
        index = segment_of(segments, second) if segments else None
        path, reason, detail = verify_candidate(row, base)
        if reason is not None:
            result.rejected.append(_reject(row, reason, index, detail))
            continue
        verified.append((index, dict(row), path))  # type: ignore[arg-type]

    progress = _out.Progress("curate", len(verified), interval=progress_interval)
    taken: Dict[Optional[int], int] = {}
    formal_dir.mkdir(parents=True, exist_ok=True)
    for index, row, source in verified:
        if taken.get(index, 0) >= result.max_per_segment:
            result.rejected.append(
                _reject(
                    row, REASON_OVER_CAP, index,
                    "segment already has %d frames" % result.max_per_segment,
                )
            )
            progress.advance()
            continue
        digest = str(row["sha256"])
        name = Path(unquote(str(row["frame"]))).name
        destination = formal_dir / name
        shutil.copy2(source, destination)
        if sha256_file(destination) != digest:
            destination.unlink(missing_ok=True)
            result.rejected.append(
                _reject(row, REASON_COPY_FAILED, index, "copy did not hash to %s.." % digest[:4])
            )
            progress.advance()
            continue
        promoted = dict(row)
        promoted["frame"] = "%s/%s" % (frames_dirname, name)
        promoted["staged_from"] = str(row["frame"])
        promoted["segment"] = index
        result.promoted.append(promoted)
        taken[index] = taken.get(index, 0) + 1
        progress.advance()
    return result


__all__ = [
    "BLOCKING_REASONS",
    "CURATION_SCHEMA",
    "DEFAULT_MAX_PER_SEGMENT",
    "REASON_COPY_FAILED",
    "REASON_HASH_MISMATCH",
    "REASON_MISSING",
    "REASON_NO_HASH",
    "REASON_OVER_CAP",
    "REASON_UNSAFE_PATH",
    "CurationResult",
    "curate",
    "segment_of",
    "verify_candidate",
]

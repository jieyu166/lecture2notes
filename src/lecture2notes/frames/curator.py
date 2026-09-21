"""Promote staged candidate frames into the formal frame set.

Ported from rad-workflow
.worktrees/rebuild-nr-viewer/skills/lecture-to-notes/scripts/frame_curator.py
Inspired by drpwchen/lecture-to-notes scripts/extract_slides.py @79053a3
(perceptual-hash near-duplicate rejection)

Candidates never enter the published frame directory directly. They land in a
staging root, each one carrying the SHA-256 of the file it was measured from; a
candidate whose file no longer hashes to that value is rejected rather than
promoted, which is the whole reason for the two-step. The curated document is a
copy: the source JSON handed in is never written.
"""

from __future__ import annotations

import copy
import math
import shutil
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from lecture2notes import _deps, _out
from lecture2notes.frames.manifest import sha256_file

#: How many frames a segment keeps at most.
FORMAL_FRAME_TARGET = 4
#: A frame this dark or this bright carries no information.
MIN_LUMA = 2.0
MAX_LUMA = 253.0
#: Below this edge energy the frame is out of focus or blank.
MIN_SHARPNESS = 1.0
#: Perceptual-hash distance at or below which two frames are the same picture.
DUPLICATE_DISTANCE = 2
#: Frames may sit this far outside a segment's bounds and still belong to it.
DEFAULT_FRAME_TOLERANCE = 0.25
#: Grayscale sample used for quality measurement.
QUALITY_SIZE = 32


class FrameCurationError(ValueError):
    """Raised when a lecture cannot be given at least one formal frame per segment."""


def safe_relative_path(value: Any) -> Optional[PurePosixPath]:
    """Accept only a relative, non-escaping, control-character-free path.

    A candidate manifest is data, and data that names ``..`` or an absolute path
    must not be allowed to write outside the staging root.
    """
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
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def resolve_within(root: Path, relative: PurePosixPath) -> Optional[Path]:
    """Resolve a relative path under ``root``, or None if it escapes."""
    base = Path(root).resolve()
    candidate = (base / Path(*relative.parts)).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def perceptual_hash(raw_luma: bytes) -> str:
    """Median-threshold hash of a grayscale sample, as hex."""
    if not raw_luma:
        return ""
    midpoint = sum(raw_luma) / len(raw_luma)
    bits = "".join("1" if value >= midpoint else "0" for value in raw_luma)
    return "%0*x" % ((len(bits) + 3) // 4, int(bits, 2))


def hamming_distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return max(len(left), len(right))
    return sum(one != two for one, two in zip(left, right))


def quality_from_pixels(pixels: Sequence[int], size: int = QUALITY_SIZE) -> Dict[str, Any]:
    """Luma, edge energy and perceptual hash from a square grayscale sample.

    Pure: the caller decides how the pixels were obtained.
    """
    if len(pixels) != size * size:
        return {"readable": False, "luma": 0.0, "sharpness": 0.0, "phash": ""}
    luma = sum(pixels) / len(pixels)
    horizontal = sum(
        abs(pixels[row * size + col] - pixels[row * size + col - 1])
        for row in range(size) for col in range(1, size)
    )
    vertical = sum(
        abs(pixels[row * size + col] - pixels[(row - 1) * size + col])
        for row in range(1, size) for col in range(size)
    )
    return {
        "readable": True,
        "luma": round(luma, 6),
        "sharpness": round((horizontal + vertical) / (2 * size * (size - 1)), 6),
        "phash": perceptual_hash(bytes(pixels)),
    }


def measure_frame_quality(path: Path, size: int = QUALITY_SIZE) -> Dict[str, Any]:
    """Measure a frame using ffmpeg only, so no image decoder is required.

    A frame that cannot be decoded stays a candidate but is marked unreadable, so
    the curator rejects it with a reason rather than crashing on it.
    """
    ffmpeg = _deps.require_ffmpeg()
    command = [
        ffmpeg, "-v", "error", "-i", str(path), "-frames:v", "1",
        "-vf", "scale=%d:%d:flags=bilinear,format=gray" % (size, size),
        "-f", "rawvideo", "-",
    ]
    try:
        process = subprocess.run(command, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        # The frame still becomes an unreadable candidate, as before; the run
        # just stops swallowing the reason it could not be measured.
        _out.say("warn", "curate: ffmpeg could not measure %s (%s)" % (path.name, exc))
        process = None
    pixels = process.stdout if process is not None and process.returncode == 0 else b""
    return quality_from_pixels(list(pixels), size)


def candidate_score(candidate: Mapping[str, Any]) -> float:
    """Rank candidates: sharper is better, more text is better, extremes are worse."""
    quality = candidate["quality"]
    sharpness = float(quality["sharpness"])
    luma = float(quality["luma"])
    ocr = candidate.get("ocr", "")
    text_bonus = min(len(ocr) if isinstance(ocr, str) else 0, 200) / 100
    return sharpness + text_bonus - abs(luma - 128.0) / 128


def is_duplicate(
    candidate: Mapping[str, Any],
    selected: Iterable[Mapping[str, Any]],
    distance: int = DUPLICATE_DISTANCE,
) -> bool:
    candidate_hash = candidate["quality"]["phash"]
    return any(
        hamming_distance(candidate_hash, other["quality"]["phash"]) <= distance
        for other in selected
    )


def merge_candidates(*groups: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Combine candidate sources, collapsing only exact path/time duplicates."""
    seen: set = set()
    merged: List[Dict[str, Any]] = []
    for group in groups:
        for candidate in group:
            if not isinstance(candidate, Mapping):
                continue
            relative = safe_relative_path(candidate.get("path"))
            timestamp = finite_number(candidate.get("time"))
            if relative is None or timestamp is None:
                continue
            key = (relative.as_posix(), timestamp)
            if key in seen:
                continue
            seen.add(key)
            merged.append(copy.deepcopy(dict(candidate)))
    return merged


def validate_candidate(
    candidate: Mapping[str, Any],
    staging_root: Path,
    start: float,
    end: float,
    tolerance: float = DEFAULT_FRAME_TOLERANCE,
) -> Tuple[Optional[Path], Optional[str]]:
    """Return the staged file, or None plus the reason it was rejected."""
    relative = safe_relative_path(candidate.get("path"))
    timestamp = finite_number(candidate.get("time"))
    if relative is None:
        return None, "candidate_path"
    if timestamp is None or timestamp < start - tolerance or timestamp > end + tolerance:
        return None, "candidate_time"
    path = resolve_within(Path(staging_root), relative)
    if path is None or not path.is_file():
        return None, "candidate_missing"
    declared = candidate.get("asset_sha256")
    if not isinstance(declared, str) or declared != sha256_file(path):
        return None, "candidate_hash"
    quality = candidate.get("quality")
    if not isinstance(quality, Mapping):
        return None, "candidate_quality"
    if quality.get("readable") is False:
        return None, "candidate_unreadable"
    luma = finite_number(quality.get("luma"))
    sharpness = finite_number(quality.get("sharpness"))
    phash = quality.get("phash")
    if luma is None or sharpness is None or not isinstance(phash, str) or not phash:
        return None, "candidate_quality"
    if luma <= MIN_LUMA or luma >= MAX_LUMA:
        return None, "candidate_blank"
    if sharpness <= MIN_SHARPNESS:
        return None, "candidate_blurred"
    if candidate.get("placeholder") is True or candidate.get("relevant") is False:
        return None, "candidate_unrelated"
    return path, None


def segment_range(segment: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
    start = finite_number(segment.get("start_sec", segment.get("start")))
    end = finite_number(segment.get("end_sec", segment.get("end")))
    if start is None or end is None or end <= start:
        return None
    return start, end


def finding(severity: str, code: str, segment: Any = None, candidate: Any = None) -> Dict[str, Any]:
    record: Dict[str, Any] = {"severity": severity, "code": code}
    if segment is not None:
        record["segment"] = segment
    if candidate is not None:
        record["candidate"] = candidate
    return record


def select_for_segment(
    candidates: Sequence[Mapping[str, Any]],
    staging_root: Path,
    start: float,
    end: float,
    tolerance: float = DEFAULT_FRAME_TOLERANCE,
    max_per_segment: int = FORMAL_FRAME_TARGET,
) -> Tuple[List[Tuple[Dict[str, Any], Path]], List[Dict[str, Any]]]:
    """Rank, deduplicate and cap the candidates that belong to one segment.

    Returns the selection in time order plus a finding for every rejection, so
    the caller can report what was dropped and why.
    """
    findings: List[Dict[str, Any]] = []
    valid: List[Tuple[Dict[str, Any], Path]] = []
    for candidate in candidates:
        path, reason = validate_candidate(candidate, staging_root, start, end, tolerance)
        if path is None:
            findings.append(finding(
                "warning", reason or "candidate_invalid",
                candidate=candidate.get("identity"),
            ))
            continue
        valid.append((dict(candidate), path))

    ranked = sorted(
        valid,
        key=lambda item: (
            -candidate_score(item[0]),
            float(item[0]["time"]),
            str(item[0]["path"]),
            str(item[0].get("identity", "")),
        ),
    )
    selected: List[Tuple[Dict[str, Any], Path]] = []
    for candidate, path in ranked:
        if is_duplicate(candidate, (item[0] for item in selected)):
            findings.append(finding(
                "warning", "candidate_duplicate", candidate=candidate.get("identity")
            ))
            continue
        selected.append((candidate, path))
        if len(selected) >= max_per_segment:
            break
    selected.sort(key=lambda item: float(item[0]["time"]))
    return selected, findings


def curate(
    document: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
    staging_root: Path,
    tolerance: float = DEFAULT_FRAME_TOLERANCE,
    max_per_segment: int = FORMAL_FRAME_TARGET,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Promote candidates and return ``(curated_copy, curation_manifest)``.

    Nothing is written to the source document. A segment that ends up with no
    valid candidate raises instead of silently shipping a hole.
    """
    staging = Path(staging_root).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    curated = copy.deepcopy(dict(document))
    segments = curated.get("segments")
    if not isinstance(segments, list) or not segments:
        raise FrameCurationError("segments_missing")

    candidate_list = merge_candidates(candidates)
    findings: List[Dict[str, Any]] = []
    manifest_segments: List[Dict[str, Any]] = []
    formal_root = staging / "frames"
    progress = _out.Progress("curate", len(segments))

    for position, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise FrameCurationError("segment_invalid:%d" % position)
        label = segment.get("index", position + 1)
        bounds = segment_range(segment)
        if bounds is None:
            raise FrameCurationError("segment_time_invalid:%s" % label)
        start, end = bounds
        selected, segment_findings = select_for_segment(
            candidate_list, staging, start, end, tolerance, max_per_segment
        )
        for item in segment_findings:
            item["segment"] = label
            findings.append(item)
        if not selected:
            raise FrameCurationError("no_valid_frame:segment=%s" % label)

        formal_frames: List[Dict[str, Any]] = []
        chosen: List[Dict[str, Any]] = []
        for ordinal, (candidate, source_path) in enumerate(selected, start=1):
            digest = sha256_file(source_path)
            suffix = source_path.suffix.lower() or ".png"
            name = "segment-%03d-%02d-%s%s" % (int(label), ordinal, digest[:12], suffix)
            destination = formal_root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            if sha256_file(destination) != digest:
                raise FrameCurationError("formal_frame_hash_mismatch:%s" % name)
            ocr = candidate.get("ocr") if isinstance(candidate.get("ocr"), str) else ""
            if not ocr:
                findings.append(finding(
                    "warning", "ocr_empty", label, candidate.get("identity")
                ))
            relative = "frames/%s" % name
            formal_frames.append({
                "time": float(candidate["time"]),
                "ocr": ocr,
                "path": relative,
            })
            chosen.append({
                "identity": candidate.get("identity"),
                "time": float(candidate["time"]),
                "path": relative,
                "asset_sha256": digest,
                "source": candidate.get("source"),
            })

        segment["frames"] = formal_frames
        segment.pop("frame", None)
        segment.pop("frame_ocr", None)
        if len(formal_frames) < max_per_segment:
            findings.append(finding("warning", "frame_below_target", label))
        manifest_segments.append({"index": label, "selected": chosen})
        progress.advance()

    manifest = {
        "schema": "lecture-frame-curation-v1",
        "frame_tolerance_seconds": tolerance,
        "max_per_segment": max_per_segment,
        "segments": manifest_segments,
        "findings": findings,
    }
    return curated, manifest


__all__ = [
    "DEFAULT_FRAME_TOLERANCE",
    "DUPLICATE_DISTANCE",
    "FORMAL_FRAME_TARGET",
    "FrameCurationError",
    "MAX_LUMA",
    "MIN_LUMA",
    "MIN_SHARPNESS",
    "candidate_score",
    "curate",
    "finding",
    "finite_number",
    "hamming_distance",
    "is_duplicate",
    "measure_frame_quality",
    "merge_candidates",
    "perceptual_hash",
    "quality_from_pixels",
    "resolve_within",
    "safe_relative_path",
    "segment_range",
    "select_for_segment",
    "validate_candidate",
]

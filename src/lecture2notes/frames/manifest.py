"""One manifest format for every capture mode, and the time-range frame merge.

Ported from rad-workflow skills/lecture-to-notes/scripts/slide_frames.py
(``_mk``, the manifest writer and the segment merge) and from this project's
interval-sampling procedure script, which deliberately emitted the same record
shape so that OCR, the viewer and the hub did not have to care which mode
produced the frames.

The one addition here is ``sha256``: a manifest that cannot prove a frame is
still the frame it described is not an acceptance artefact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import unquote

#: Frames live beside the video in this directory.
FRAMES_DIRNAME = "frames"


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def timestamp_display(sec: float) -> str:
    total = int(sec)
    return "%02d:%02d:%02d" % (total // 3600, total % 3600 // 60, total % 60)


def timestamp_file(sec: float) -> str:
    """``MMSS`` for the filename; minutes run past 99 on a long recording."""
    total = int(sec)
    return "%02d%02d" % (total // 60, total % 60)


def frame_name(stem: str, sec: float, suffix: str = ".png") -> str:
    return "%s-%s%s" % (stem, timestamp_file(sec), suffix)


def frame_seconds(name: str, fallback: float = 0.0) -> float:
    """Recover seconds from a ``<stem>-MMSS.png`` filename.

    The minute field grows past two digits once a recording passes 99 minutes
    (``-14901.png`` is 149:01), so the last two digits are always the seconds and
    everything before them is the minutes. Hard-coding four digits silently
    mis-times every frame in a long lecture.
    """
    import re

    match = re.search(r"-(\d+)\.\w+$", str(name))
    if not match:
        return fallback
    digits = match.group(1)
    if len(digits) < 3:
        return fallback
    return int(digits[:-2]) * 60 + int(digits[-2:])


def make_record(
    sec: float,
    frame_path: str,
    filename: str,
    digest: Optional[str] = None,
) -> Dict[str, Any]:
    """One manifest row. ``frame`` is relative to the video's directory."""
    record: Dict[str, Any] = {
        "timestamp_sec": float(round(sec, 3)),
        "timestamp_display": timestamp_display(sec),
        "timestamp_file": timestamp_file(sec),
        "frame": frame_path.replace("\\", "/"),
        "filename": filename,
    }
    if digest is not None:
        record["sha256"] = digest
    return record


def record_for_file(sec: float, path: Path, base_dir: Path) -> Dict[str, Any]:
    """Build a row for a frame that already exists on disk, hashing it."""
    path = Path(path)
    relative = path.relative_to(Path(base_dir)).as_posix()
    return make_record(sec, relative, path.name, sha256_file(path))


def write_manifest(path: Path, records: Sequence[Mapping[str, Any]]) -> Path:
    """UTF-8 without BOM, LF only: a BOM here breaks the browser-side player."""
    destination = Path(path)
    destination.write_text(
        json.dumps(list(records), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def read_manifest(path: Path) -> List[Dict[str, Any]]:
    """Accept both the bare list and the ``{"frames": [...]}`` wrapper."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rows = data.get("frames") if isinstance(data, Mapping) else data
    return list(rows or [])


def verify_manifest(path: Path, base_dir: Path) -> List[Dict[str, Any]]:
    """Report every row whose file is missing or whose hash no longer matches."""
    problems: List[Dict[str, Any]] = []
    for row in read_manifest(path):
        relative = unquote(str(row.get("frame", "")))
        target = Path(base_dir) / relative
        if not target.is_file():
            problems.append({"frame": relative, "code": "missing"})
            continue
        expected = row.get("sha256")
        if expected:
            actual = sha256_file(target)
            if actual != expected:
                problems.append({
                    "frame": relative,
                    "code": "sha256",
                    "manifest": expected,
                    "actual": actual,
                })
    return problems


def merge_frames_into_segments(
    data: Mapping[str, Any],
    frames: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Assign frames to segments by ``[start_sec, end_sec)`` and pick a lead frame.

    A segment with no frame of its own is not left blank: it inherits the last
    frame from before it started, which is literally what was on screen when the
    segment began. Only then does ``frames`` stay empty, so a later check can
    still tell "nothing changed here" from "capture never ran".
    """
    result = dict(data)
    pairs = sorted(
        (float(frame["timestamp_sec"]), str(frame["frame"]))
        for frame in frames
        if frame.get("frame") is not None and frame.get("timestamp_sec") is not None
    )
    segments = result.get("segments")
    if not isinstance(segments, list):
        return result
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = float(segment.get("start_sec") or 0)
        end = float(segment.get("end_sec") or 10 ** 9)
        inside = [path for sec, path in pairs if start <= sec < end]
        segment["frames"] = inside
        if inside:
            segment["frame"] = inside[0]
        else:
            prior = [path for sec, path in pairs if sec < start]
            segment["frame"] = prior[-1] if prior else (pairs[0][1] if pairs else None)
    return result


__all__ = [
    "FRAMES_DIRNAME",
    "frame_name",
    "frame_seconds",
    "make_record",
    "merge_frames_into_segments",
    "read_manifest",
    "record_for_file",
    "sha256_file",
    "timestamp_display",
    "timestamp_file",
    "verify_manifest",
    "write_manifest",
]

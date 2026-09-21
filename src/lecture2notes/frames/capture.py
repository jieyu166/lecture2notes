"""Run a capture mode end to end: detect, grab, dedup, hash, merge.

``scene`` and ``interval`` differ only in how the sample times are chosen. Once
a time exists, everything after it is shared: grab the frame with an accurate
seek, hash it, and write one manifest row in the format
:mod:`lecture2notes.frames.manifest` defines. Nothing downstream -- OCR, the
viewer, the hub, ``check frames`` -- can tell which mode produced a manifest,
and that is deliberate: a recording of a screen share and a recording of a slide
deck must be interchangeable from the segment layer up.

Two behaviours are worth stating rather than leaving in the code.

The scene detector's cut time is nudged forward by
:data:`lecture2notes.frames.scene.SEEK_PADDING` before the grab, so the frame
lands after a fade rather than inside one. The interval sampler does not nudge:
its times carry no such meaning.

Deduplication belongs to interval mode only. A scene detector has already
decided that something changed; running a second similarity filter over its
output would drop the slow builds (a diagram gaining one arrow per click) that
are exactly what the detector was asked to find.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from lecture2notes import _deps, _out
from lecture2notes.frames import interval as interval_mode
from lecture2notes.frames import scene as scene_mode
from lecture2notes.frames.manifest import (
    FRAMES_DIRNAME,
    frame_name,
    merge_frames_into_segments,
    record_for_file,
    write_manifest,
)
from lecture2notes.schema.io import read_json, write_json_atomic

MODES = ("scene", "interval")
#: Candidates land under this directory when ``--stage`` is given.
STAGING_DIRNAME = "staging"
MANIFEST_SUFFIX = ".frames.json"
CURATION_SUFFIX = ".curation.json"
DOCUMENT_SUFFIX = ".json"


@dataclass
class CaptureResult:
    """What one capture run produced, in numbers the caller can print or assert."""

    video: Path
    stem: str
    base_dir: Path
    frames_dir: Path
    manifest_path: Path
    mode: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    evaluated: int = 0
    dropped: int = 0
    failed: int = 0
    staged: bool = False
    merged_into: Optional[Path] = None

    @property
    def kept(self) -> int:
        return len(self.records)

    def summary(self) -> str:
        """The console line: sample points evaluated, frames kept, frames dropped."""
        return "%s mode: %d points evaluated, %d frames kept, %d duplicates dropped" % (
            self.mode, self.evaluated, self.kept, self.dropped
        )


def manifest_path_for(base_dir: Path, stem: str) -> Path:
    return Path(base_dir) / (stem + MANIFEST_SUFFIX)


def document_path_for(base_dir: Path, stem: str) -> Path:
    return Path(base_dir) / (stem + DOCUMENT_SUFFIX)


def curation_path_for(base_dir: Path, stem: str) -> Path:
    return Path(base_dir) / (stem + CURATION_SUFFIX)


def frames_dir_for(base_dir: Path, stage: bool = False) -> Path:
    """``frames/`` normally; ``staging/frames/`` while candidates are unproven."""
    base = Path(base_dir)
    return base / STAGING_DIRNAME / FRAMES_DIRNAME if stage else base / FRAMES_DIRNAME


def plan_seconds(
    video: Path,
    mode: str = "scene",
    every: float = interval_mode.DEFAULT_INTERVAL,
    detector: str = "adaptive",
    threshold: Optional[float] = None,
) -> List[float]:
    """The sample times this mode wants, before any frame is written."""
    if mode not in MODES:
        raise ValueError("unknown capture mode: %s" % mode)
    if mode == "interval":
        duration = scene_mode.get_duration(video)
        return [float(second) for second in interval_mode.plan_marks(duration, every)]
    marks = scene_mode.detect_scenes(video, detector=detector, threshold=threshold)
    seconds: List[float] = []
    for entry in marks:
        second = float(entry["timestamp_sec"])
        # The first frame is the title card; nudging it forward would skip it.
        seconds.append(second if second <= 0 else second + scene_mode.SEEK_PADDING)
    return seconds


def plan_frames(stem: str, seconds: Sequence[float]) -> List[Tuple[float, str]]:
    """Pair each sample time with its filename, dropping same-second collisions.

    Two cuts inside one second would otherwise write the same
    ``<stem>-<MMSS>.png`` twice, leaving a manifest with two rows pointing at one
    file and a ``check frames`` failure nobody can explain.
    """
    planned: List[Tuple[float, str]] = []
    seen: set = set()
    for second in sorted(float(value) for value in seconds):
        name = frame_name(stem, second)
        if name in seen:
            continue
        seen.add(name)
        planned.append((second, name))
    return planned


def capture(
    video: Path,
    mode: str = "scene",
    every: float = interval_mode.DEFAULT_INTERVAL,
    diff_min: float = interval_mode.DEFAULT_DIFF_MIN,
    width: int = scene_mode.DEFAULT_WIDTH,
    stage: bool = False,
    detector: str = "adaptive",
    threshold: Optional[float] = None,
    base_dir: Optional[Path] = None,
    merge: bool = True,
    document: Optional[Path] = None,
    extract: Optional[Callable[..., bool]] = None,
    progress_interval: float = 5.0,
) -> CaptureResult:
    """Capture frames from ``video`` and write the manifest.

    With ``stage=True`` the frames go to ``staging/frames/`` and the canonical
    document is left alone: an unproven candidate must never be referenced by
    the JSON that the note and the viewer are built from.
    """
    if mode not in MODES:
        raise ValueError("unknown capture mode: %s" % mode)
    source = Path(video)
    if not source.is_file():
        raise FileNotFoundError("no such video: %s" % source)
    _deps.require_ffmpeg()
    _deps.require_ffprobe()

    root = Path(base_dir) if base_dir is not None else source.parent
    stem = scene_mode.video_stem(source)
    frames_dir = frames_dir_for(root, stage)
    frames_dir.mkdir(parents=True, exist_ok=True)
    grab = extract or scene_mode.extract_frame

    planned = plan_frames(stem, plan_seconds(source, mode, every, detector, threshold))
    result = CaptureResult(
        video=source,
        stem=stem,
        base_dir=root,
        frames_dir=frames_dir,
        manifest_path=manifest_path_for(root, stem),
        mode=mode,
        evaluated=len(planned),
        staged=bool(stage),
    )

    progress = _out.Progress("frames", len(planned), interval=progress_interval)
    previous: Optional[Sequence[int]] = None
    for second, name in planned:
        target = frames_dir / name
        if not grab(source, second, target, width):
            result.failed += 1
            progress.advance()
            continue
        if mode == "interval":
            signature = interval_mode.signature_from_image(target)
            if not interval_mode.keep_frame(previous, signature, diff_min):
                target.unlink(missing_ok=True)
                result.dropped += 1
                progress.advance()
                continue
            previous = signature
        result.records.append(record_for_file(second, target, root))
        progress.advance()
    progress.finish()

    write_manifest(result.manifest_path, result.records)
    _out.stage("frames", result.summary())
    if result.failed:
        _out.warn("frames: %d sample points produced no image" % result.failed)

    if merge and not stage:
        target_document = (
            Path(document) if document is not None else document_path_for(root, stem)
        )
        if target_document.is_file():
            merge_into_document(target_document, result.records)
            result.merged_into = target_document
    return result


def merge_into_document(
    document_path: Path,
    records: Sequence[Mapping[str, Any]],
) -> Path:
    """Assign the manifest's frames to the document's segments and rewrite it.

    The rewrite is atomic (:func:`write_json_atomic`), because this runs after a
    capture that may have taken minutes: a half-written document here would cost
    the whole run.
    """
    path = Path(document_path)
    merged = merge_frames_into_segments(read_json(path), records)
    return write_json_atomic(path, merged)


__all__ = [
    "CURATION_SUFFIX",
    "DOCUMENT_SUFFIX",
    "MANIFEST_SUFFIX",
    "MODES",
    "STAGING_DIRNAME",
    "CaptureResult",
    "capture",
    "curation_path_for",
    "document_path_for",
    "frames_dir_for",
    "manifest_path_for",
    "merge_into_document",
    "plan_frames",
    "plan_seconds",
]

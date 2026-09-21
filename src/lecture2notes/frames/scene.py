"""Scene-change capture: grab the frame at every slide transition.

Ported from rad-workflow skills/lecture-to-notes/scripts/slide_frames.py.

PySceneDetect's adaptive detector is the primary method; when the package is
absent the ffmpeg ``scene`` filter stands in, which is coarser but always
available because ffmpeg is a hard requirement anyway. The fallback announces
itself: degrading in silence produces a lecture with four frames and no sign of
why.

The seek is deliberately 0.4 s after the detected cut, to land past a fade or a
transition animation rather than in the middle of one.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from lecture2notes import _deps, _out
from lecture2notes.frames.manifest import timestamp_display, timestamp_file

#: Seconds added to a detected cut before grabbing, to clear the transition.
SEEK_PADDING = 0.4
DEFAULT_WIDTH = 1280
DEFAULT_MIN_SCENE_LEN = 1.5
DEFAULT_ADAPTIVE_THRESHOLD = 3.0
DEFAULT_CONTENT_THRESHOLD = 27.0
DEFAULT_FFMPEG_THRESHOLD = 0.3
DETECTORS = ("adaptive", "content", "ffmpeg")

#: Characters of raw ffprobe output echoed when the duration will not parse.
DURATION_PREVIEW = 200

#: Printed verbatim when PySceneDetect is missing. The wording is part of the
#: frame-capture contract, so it is a constant rather than an inline literal: a
#: silent downgrade produces a lecture with four frames and no explanation, and
#: the acceptance scenario asserts on this exact line.
FALLBACK_MESSAGE = "[frames] scenedetect not installed, using ffmpeg scene filter"

PTS_TIME = re.compile(r"pts_time:([\d.]+)")
SUBTITLE_SUFFIX = re.compile(r"\.(zh(-TW)?|en|orig)$")
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")


def mark(sec: float) -> Dict[str, Any]:
    """A bare detection point, before a file has been written for it."""
    sec = round(float(sec), 3)
    return {
        "timestamp_sec": sec,
        "timestamp_display": timestamp_display(sec),
        "timestamp_file": timestamp_file(sec),
    }


def parse_ffmpeg_scene_output(stderr_text: str) -> List[float]:
    """Pull ``pts_time`` values out of an ffmpeg showinfo dump.

    Pure so the fallback detector can be tested without ffmpeg installed.
    """
    return [float(match.group(1)) for match in PTS_TIME.finditer(stderr_text)]


def video_stem(path: Path) -> str:
    """Strip a language suffix so ``talk.zh.mp4`` and ``talk.srt`` share a stem."""
    return SUBTITLE_SUFFIX.sub("", Path(path).stem)


def find_video(base: Path) -> Optional[Path]:
    """Find a same-stem video next to a subtitle or JSON file."""
    base = Path(base)
    stem = video_stem(base)
    for ext in VIDEO_EXT:
        for candidate in (base.parent / (stem + ext), base.parent / (base.stem + ext)):
            if candidate.exists():
                return candidate
    return None


def get_duration(video_path: Path) -> float:
    """Media duration in seconds, via ffprobe."""
    ffprobe = _deps.require_ffprobe()
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        # A silent 0.0 here becomes "0 frames captured" three layers down, with
        # nothing left to say why. Print what ffprobe actually said instead.
        raw = (result.stdout or "").strip() or "(empty stdout)"
        detail = (result.stderr or "").strip()
        _out.say("warn", "ffprobe duration unparseable for %s: %s%s" % (
            Path(video_path).name,
            raw[:DURATION_PREVIEW],
            ("; stderr: " + detail[:DURATION_PREVIEW]) if detail else "",
        ))
        return 0.0


def detect_ffmpeg(video_path: Path, threshold: float = DEFAULT_FFMPEG_THRESHOLD) -> List[Dict[str, Any]]:
    """Fallback detector built on the ffmpeg ``scene`` filter."""
    ffmpeg = _deps.require_ffmpeg()
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video_path),
         "-filter:v", "select='gt(scene,%s)',showinfo" % threshold, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    marks = [mark(0.0)]
    marks.extend(mark(sec) for sec in parse_ffmpeg_scene_output(result.stderr or ""))
    return marks


def detect_scenes(
    video_path: Path,
    detector: str = "adaptive",
    threshold: Optional[float] = None,
    min_scene_len: float = DEFAULT_MIN_SCENE_LEN,
) -> List[Dict[str, Any]]:
    """Detected cut points, newest-first-frame included.

    Falls back to ffmpeg with a visible message when PySceneDetect is missing.
    """
    if detector not in DETECTORS:
        raise ValueError("unknown detector: %s" % detector)
    if detector == "ffmpeg":
        return detect_ffmpeg(video_path, threshold or DEFAULT_FFMPEG_THRESHOLD)
    try:
        from scenedetect import SceneManager, open_video  # type: ignore
        from scenedetect.detectors import AdaptiveDetector, ContentDetector  # type: ignore
    except ImportError:
        # _out.line rather than _out.say: a downgrade must still be announced
        # under --quiet, and the line must carry no severity marker so it
        # matches the contract exactly.
        _out.line(FALLBACK_MESSAGE)
        _out.say("info", "install: %s" % _deps.INSTALL_HINTS["scenedetect"])
        return detect_ffmpeg(video_path, DEFAULT_FFMPEG_THRESHOLD)

    video = open_video(str(video_path))
    minimum = max(int(round(min_scene_len * video.frame_rate)), 1)
    manager = SceneManager()
    if detector == "content":
        manager.add_detector(ContentDetector(
            threshold=DEFAULT_CONTENT_THRESHOLD if threshold is None else threshold,
            min_scene_len=minimum,
        ))
    else:
        manager.add_detector(AdaptiveDetector(
            adaptive_threshold=DEFAULT_ADAPTIVE_THRESHOLD if threshold is None else threshold,
            min_scene_len=minimum,
        ))
    manager.detect_scenes(video=video)
    marks = [mark(start.get_seconds()) for start, _end in manager.get_scene_list()]
    if not marks or marks[0]["timestamp_sec"] > 0.5:
        marks.insert(0, mark(0.0))
    return marks


def extract_frame(
    video_path: Path,
    sec: float,
    out_path: Path,
    width: int = DEFAULT_WIDTH,
) -> bool:
    """Grab one frame with an accurate seek, scaled to ``width``."""
    ffmpeg = _deps.require_ffmpeg()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-ss", "%.3f" % sec, "-i", str(video_path), "-frames:v", "1",
         "-vf", "scale=%d:-2" % width, str(out_path)],
        capture_output=True,
    )
    return os.path.exists(out_path)


__all__ = [
    "DETECTORS",
    "FALLBACK_MESSAGE",
    "DEFAULT_WIDTH",
    "SEEK_PADDING",
    "detect_ffmpeg",
    "detect_scenes",
    "extract_frame",
    "find_video",
    "get_duration",
    "mark",
    "DURATION_PREVIEW",
    "parse_ffmpeg_scene_output",
    "video_stem",
]

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
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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

#: How often a long scan reports, in seconds of wall clock.
SCAN_PROGRESS_INTERVAL = 5.0

#: One line of ffmpeg's ``-progress`` stream. ``out_time_ms`` is microseconds in
#: some builds and milliseconds in others, so the clock form is what is parsed.
PROGRESS_OUT_TIME = re.compile(r"^out_time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)\s*$")
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


def scan_progress_seconds(line: str) -> Optional[float]:
    """Seconds of media scanned so far, from one ffmpeg ``-progress`` line."""
    match = PROGRESS_OUT_TIME.match(line.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def run_scan(
    command: List[str],
    total_sec: float,
    stage: str = "frames",
    interval: float = SCAN_PROGRESS_INTERVAL,
    popen: Optional[Callable[..., Any]] = None,
) -> str:
    """Run a scanning ffmpeg command, reporting progress, and return its stderr.

    Scene detection over a 20 minute talk takes around two minutes during which
    ffmpeg says nothing at all, and a silent two minutes is indistinguishable
    from a hang -- the field run's first reaction was to reach for Ctrl-C. The
    ``-progress`` stream goes to stdout so it can be read a line at a time,
    while stderr (which carries the ``showinfo`` dump this function returns)
    goes to a temporary file: reading both pipes from one thread deadlocks as
    soon as either one fills.
    """
    launch = popen or subprocess.Popen
    progress = _out.Progress(stage, int(max(total_sec, 0.0)), interval=interval)
    emitted = 0
    with tempfile.TemporaryFile(
        "w+", encoding="utf-8", errors="replace", newline=""
    ) as errors:
        process = launch(
            command,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if process.stdout is not None:
            for line in process.stdout:
                scanned = scan_progress_seconds(line)
                if scanned is None:
                    continue
                whole = int(scanned)
                if whole > emitted:
                    progress.advance(whole - emitted)
                    emitted = whole
            process.stdout.close()
        process.wait()
        if emitted:
            progress.finish()
        errors.seek(0)
        return errors.read()


def detect_ffmpeg(
    video_path: Path,
    threshold: float = DEFAULT_FFMPEG_THRESHOLD,
    total_sec: Optional[float] = None,
    progress_interval: float = SCAN_PROGRESS_INTERVAL,
    popen: Optional[Callable[..., Any]] = None,
) -> List[Dict[str, Any]]:
    """Fallback detector built on the ffmpeg ``scene`` filter."""
    ffmpeg = _deps.require_ffmpeg()
    total = get_duration(video_path) if total_sec is None else float(total_sec)
    stderr_text = run_scan(
        [ffmpeg, "-hide_banner", "-nostats", "-progress", "pipe:1",
         "-i", str(video_path),
         "-filter:v", "select='gt(scene,%s)',showinfo" % threshold, "-f", "null", "-"],
        total,
        interval=progress_interval,
        popen=popen,
    )
    marks = [mark(0.0)]
    marks.extend(mark(sec) for sec in parse_ffmpeg_scene_output(stderr_text))
    return marks


def detect_scenes(
    video_path: Path,
    detector: str = "adaptive",
    threshold: Optional[float] = None,
    min_scene_len: float = DEFAULT_MIN_SCENE_LEN,
    progress_interval: float = SCAN_PROGRESS_INTERVAL,
) -> List[Dict[str, Any]]:
    """Detected cut points, newest-first-frame included.

    Falls back to ffmpeg with a visible message when PySceneDetect is missing.
    """
    if detector not in DETECTORS:
        raise ValueError("unknown detector: %s" % detector)
    if detector == "ffmpeg":
        return detect_ffmpeg(
            video_path,
            threshold or DEFAULT_FFMPEG_THRESHOLD,
            progress_interval=progress_interval,
        )
    try:
        from scenedetect import SceneManager, open_video  # type: ignore
        from scenedetect.detectors import AdaptiveDetector, ContentDetector  # type: ignore
    except ImportError:
        # _out.line rather than _out.say: a downgrade must still be announced
        # under --quiet, and the line must carry no severity marker so it
        # matches the contract exactly.
        _out.line(FALLBACK_MESSAGE)
        _out.say("info", "install: %s" % _deps.INSTALL_HINTS["scenedetect"])
        return detect_ffmpeg(
            video_path, DEFAULT_FFMPEG_THRESHOLD, progress_interval=progress_interval
        )

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
    # PySceneDetect has no per-frame hook, but it does call back on every cut it
    # finds, and a cut carries the frame number it happened at. That is enough
    # to turn a silent scan into one that says how far through the media it has
    # read. The total is in seconds, so the line reads scanned/total.
    progress = _out.Progress(
        "frames", max(int(get_duration(video_path)), 0), interval=progress_interval
    )
    state = {"emitted": 0}

    def on_cut(_image: Any, frame_num: int) -> None:
        try:
            scanned = int(frame_num / float(video.frame_rate))
        except (TypeError, ValueError, ZeroDivisionError):
            return
        if scanned > state["emitted"]:
            progress.advance(scanned - state["emitted"])
            state["emitted"] = scanned

    try:
        manager.detect_scenes(video=video, callback=on_cut)
    except TypeError:  # pragma: no cover - PySceneDetect without a callback arg
        manager.detect_scenes(video=video)
    if state["emitted"]:
        progress.finish()
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
    "PROGRESS_OUT_TIME",
    "SCAN_PROGRESS_INTERVAL",
    "run_scan",
    "scan_progress_seconds",
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

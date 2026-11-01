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

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

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
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts")


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


#: Differences, in seconds, worth telling the user about before they trust
#: frame times: a video stream that starts this long after the container, or a
#: frame rate whose average is this far from the nominal one.
START_OFFSET_WARN_SEC = 1.0
#: A video stream starting this much after the container is corrected for in
#: PySceneDetect's times; less is the encoder's usual first-frame delay.
STREAM_SHIFT_MIN_SEC = 0.05
VFR_WARN_RATIO = 0.02


def _rate(text: Any) -> Optional[float]:
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def stream_timing(video_path: Path) -> Dict[str, Optional[float]]:
    """Start times and frame rates that decide whether frame times can be trusted.

    ``format_start`` is the container's start, ``video_start`` the video
    stream's; ``nominal_fps`` and ``average_fps`` are r_frame_rate and
    avg_frame_rate. Missing values are None. Never raises: this is a diagnosis.
    """
    import json as _json

    out: Dict[str, Optional[float]] = {
        "format_start": None, "video_start": None, "nominal_fps": None, "average_fps": None,
    }
    try:
        ffprobe = _deps.require_ffprobe()
        done = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=start_time:stream=start_time,r_frame_rate,avg_frame_rate",
             "-of", "json", str(video_path)],
            capture_output=True, text=True, timeout=60,
        )
        data = _json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError, SystemExit):
        return out
    stream = (data.get("streams") or [{}])[0]
    for key, value in (("format_start", (data.get("format") or {}).get("start_time")),
                       ("video_start", stream.get("start_time"))):
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            pass
    out["nominal_fps"] = _rate(stream.get("r_frame_rate"))
    out["average_fps"] = _rate(stream.get("avg_frame_rate"))
    return out


#: Frames ffprobe reads from the start of the file to see whether their size
#: matches the stream's. Cheap (no full decode of the file) and enough for the
#: case seen in the field: a webm-to-mp4 live recording whose first two frames
#: were 2468x1280 and every later one 2468x1186.
SIZE_PROBE_FRAMES = 30
RESOLUTION_MESSAGE = (
    "the video changes size (%s); PySceneDetect skips every frame that differs "
    "from the first and finds no cut, so it scans a fixed-size copy instead"
)


def frame_sizes(video_path: Path, count: int = SIZE_PROBE_FRAMES) -> Dict[str, Any]:
    """The stream's declared size and the sizes of its first ``count`` frames.

    ``{"stream": (w, h) or None, "frames": [(w, h), ...]}``. Never raises: a
    file ffprobe cannot read just reports nothing.
    """
    import json as _json

    out: Dict[str, Any] = {"stream": None, "frames": []}
    try:
        ffprobe = _deps.require_ffprobe()
        done = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-read_intervals", "%%+#%d" % int(count),
             "-show_entries", "stream=width,height:frame=width,height",
             "-of", "json", str(video_path)],
            capture_output=True, text=True, timeout=120,
        )
        data = _json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError, SystemExit):
        return out

    def size(entry: Any) -> Optional[tuple]:
        try:
            return (int(entry["width"]), int(entry["height"]))
        except (KeyError, TypeError, ValueError):
            return None

    out["stream"] = size((data.get("streams") or [{}])[0])
    out["frames"] = [s for s in (size(f) for f in data.get("frames") or []) if s]
    return out


def size_change(sizes: Mapping[str, Any]) -> Optional[str]:
    """``"2468x1280 -> 2468x1186"`` when the probed sizes disagree, else None."""
    seen: List[tuple] = []
    for item in [sizes.get("stream")] + list(sizes.get("frames") or []):
        if item and item not in seen:
            seen.append(item)
    if len(seen) < 2:
        return None
    return " -> ".join("%dx%d" % item for item in seen)


class _SizeErrors(logging.Handler):
    """Counts PySceneDetect's "frame has incorrect size" log records.

    The backstop for a size change past the probed opening: PySceneDetect logs
    it, skips the frame and carries on, and returns a scan with no cuts.
    """

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "incorrect size" in record.getMessage():
            self.count += 1


def timing_warnings(timing: Mapping[str, Optional[float]]) -> List[str]:
    """What about this file's clock could make scene times disagree with a player.

    Reported rather than corrected: which way to shift depends on how the file
    was made and which player reads it, and a correction applied in the wrong
    direction moves every frame further from its slide.
    """
    warnings: List[str] = []
    video_start, format_start = timing.get("video_start"), timing.get("format_start")
    if format_start is not None and abs(format_start) > START_OFFSET_WARN_SEC:
        warnings.append(
            "the file starts at %.2f s: a browser shows every moment %.2f s later than "
            "the times l2n records (transcript, segments and frames agree with each "
            "other); l2n viewer and render --media-base-url add it automatically, "
            "hand-written #t= links must add it too" % (format_start, format_start)
        )
    elif video_start is not None and abs(video_start - (format_start or 0.0)) > START_OFFSET_WARN_SEC:
        warnings.append(
            "the video stream starts %.2f s after the container; scene times are "
            "moved onto the container's clock (the one the transcript and players "
            "use). frames.json written before 0.3.1 is %.2f s early: rerun "
            "l2n frames --force" % (video_start - (format_start or 0.0),
                                    video_start - (format_start or 0.0))
        )
    if is_vfr(timing):
        warnings.append(
            "variable frame rate (r_frame_rate %.3f, avg_frame_rate %.3f): scene mode "
            "scans a constant-rate copy so cut times follow the player's clock; "
            "frames.json written by l2n 0.3.5 or earlier may be several seconds early "
            "on this file, rerun l2n frames --force" % (timing["nominal_fps"], timing["average_fps"])
        )
    return warnings


def is_vfr(timing: Mapping[str, Optional[float]]) -> bool:
    """Whether r_frame_rate and avg_frame_rate disagree by more than 2 %.

    PySceneDetect versions that turn a frame number into seconds by dividing by
    the nominal rate drift on such a file: a field recording (r 60, avg 29.8)
    came out 7 to 11 s early in the middle, against the browser.
    """
    nominal, average = timing.get("nominal_fps"), timing.get("average_fps")
    return bool(nominal and average and abs(nominal - average) / nominal > VFR_WARN_RATIO)


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


#: The fixed-size copy a size-changing video is scanned from: small (detectors
#: downscale to about this anyway) and at a constant 10 fps, so a frame number
#: divided by the rate is an exact time even when the source is VFR.
NORMALIZED_WIDTH = 640
NORMALIZED_FPS = 10


def normalized_copy(
    video_path: Path, workdir: Path, sizes: Optional[Mapping[str, Any]] = None
) -> Optional[Path]:
    """A fixed-size, constant-rate, silent copy of ``video_path`` for scanning.

    Every frame is scaled to one size (the stream's declared aspect at
    :data:`NORMALIZED_WIDTH`), so PySceneDetect sees no size change and runs
    with its own detectors and thresholds. Time 0 of the copy is the source's
    first video frame, the same origin PySceneDetect uses on the source, so the
    caller's container shift still applies. None when ffmpeg fails.
    """
    ffmpeg = _deps.require_ffmpeg()
    sizes = sizes or {}
    declared = sizes.get("stream") or (list(sizes.get("frames") or []) or [None])[0]
    width = NORMALIZED_WIDTH
    height = width * 9 // 16
    if declared and declared[0]:
        height = int(round(width * declared[1] / float(declared[0]) / 2.0)) * 2
    out = Path(workdir) / "scan.mp4"
    done = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path),
         "-an", "-sn", "-vf",
         # setpts first: time 0 of the copy is the first video frame, whatever
         # the container start; otherwise fps pads from the container start.
         "setpts=PTS-STARTPTS,scale=%d:%d,setsar=1,fps=%d" % (width, max(height, 2), NORMALIZED_FPS),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
         str(out)],
        capture_output=True, text=True,
    )
    if done.returncode != 0 or not out.is_file():
        _out.say("warn", "ffmpeg: %s" % (done.stderr or "").strip()[-300:])
        return None
    return out


def _scan_pyscenedetect(
    video_path: Path,
    SceneManager: Any,
    open_video: Any,
    AdaptiveDetector: Any,
    ContentDetector: Any,
    detector: str,
    threshold: Optional[float],
    min_scene_len: float,
    progress_interval: float,
    total_sec: int,
) -> tuple:
    """One PySceneDetect pass: ``(scene start seconds, incorrect-size frames)``."""
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
    progress = _out.Progress("frames", total_sec, interval=progress_interval)
    state = {"emitted": 0}

    def on_cut(_image: Any, frame_num: int) -> None:
        try:
            scanned = int(frame_num / float(video.frame_rate))
        except (TypeError, ValueError, ZeroDivisionError):
            return
        if scanned > state["emitted"]:
            progress.advance(scanned - state["emitted"])
            state["emitted"] = scanned

    size_errors = _SizeErrors()
    scenedetect_log = logging.getLogger("pyscenedetect")
    scenedetect_log.addHandler(size_errors)
    try:
        try:
            manager.detect_scenes(video=video, callback=on_cut)
        except TypeError:  # pragma: no cover - PySceneDetect without a callback arg
            manager.detect_scenes(video=video)
    finally:
        scenedetect_log.removeHandler(size_errors)
    if state["emitted"]:
        progress.finish()
    return [start.get_seconds() for start, _end in manager.get_scene_list()], size_errors.count


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

    def scan(path: Path) -> tuple:
        return _scan_pyscenedetect(
            path, SceneManager, open_video, AdaptiveDetector, ContentDetector,
            detector, threshold, min_scene_len, progress_interval,
            max(int(get_duration(video_path)), 0),
        )

    sizes = frame_sizes(video_path)
    timing = stream_timing(video_path)
    changed = size_change(sizes)
    variable = is_vfr(timing)
    if variable and not changed:
        _out.say("info", "variable frame rate: scanning a constant-rate copy")
    if not changed and not variable:
        starts, errors = scan(video_path)
        if errors:
            changed = "%d frames of another size" % errors
    if changed or variable:
        if changed:
            _out.warn(RESOLUTION_MESSAGE % changed)
        with tempfile.TemporaryDirectory(prefix="l2n-scan-") as scratch:
            copy = normalized_copy(video_path, Path(scratch), sizes)
            if copy is None:
                _out.warn("could not build the fixed-size copy; using the ffmpeg scene filter")
                return detect_ffmpeg(
                    video_path, DEFAULT_FFMPEG_THRESHOLD, progress_interval=progress_interval
                )
            starts, _ = scan(copy)
    # PySceneDetect counts seconds from the first video frame. When the video
    # stream starts after the container (a field file: video at 3.8 s, audio
    # and container at 0), every cut was reported that much early -- and the
    # frame extracted at that time was the slide before the change. ffmpeg's
    # own timestamps, the transcript and the player all use the container's
    # clock, so the cuts are moved onto it here.
    shift = (timing.get("video_start") or 0.0) - (timing.get("format_start") or 0.0)
    if abs(shift) < STREAM_SHIFT_MIN_SEC:
        shift = 0.0
    marks = [mark(round(start + shift, 3)) for start in starts]
    if shift and marks and abs(marks[0]["timestamp_sec"] - shift) < 1e-6:
        # The opening scene starts where the video does; it is the lecture's
        # opening frame, not a cut, and the 0.0 mark below stands for it.
        marks.pop(0)
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
    "RESOLUTION_MESSAGE",
    "NORMALIZED_FPS",
    "NORMALIZED_WIDTH",
    "SIZE_PROBE_FRAMES",
    "detect_ffmpeg",
    "frame_sizes",
    "is_vfr",
    "normalized_copy",
    "size_change",
    "detect_scenes",
    "extract_frame",
    "stream_timing",
    "timing_warnings",
    "find_video",
    "get_duration",
    "mark",
    "DURATION_PREVIEW",
    "parse_ffmpeg_scene_output",
    "video_stem",
]
